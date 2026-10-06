-- KasMap initial schema (Blueprint v0.1, sections 4 and 6a).
-- Layers: meta (sources, versions, jobs) · geo (admin areas) · poi (places)
--         feat (H3 features) · score (scores) · app (proprietary user data)
-- Geometry: EPSG:4326 everywhere; metric work happens in geography or local UTM.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS h3;
CREATE EXTENSION IF NOT EXISTS h3_postgis CASCADE;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS geo;
CREATE SCHEMA IF NOT EXISTS poi;
CREATE SCHEMA IF NOT EXISTS feat;
CREATE SCHEMA IF NOT EXISTS score;
CREATE SCHEMA IF NOT EXISTS app;

-- ───────────────────────────── META ─────────────────────────────

CREATE TABLE meta.source (
    id              text PRIMARY KEY,                 -- 'overture_places', 'osm'
    name            text NOT NULL,
    license         text NOT NULL,                    -- SPDX id where one exists
    attribution     text NOT NULL,
    commercial_ok   boolean NOT NULL,
    storage_ok      boolean NOT NULL,
    share_alike     boolean NOT NULL DEFAULT false,
    update_freq     text,
    priority        smallint NOT NULL DEFAULT 100,    -- lower wins when fields conflict
    license_url     text,
    notes           text
);

-- ───────────────────────────── GEO ──────────────────────────────

CREATE TABLE geo.admin_area (
    id                  bigserial PRIMARY KEY,
    level               text NOT NULL CHECK (level IN ('country','region','city','district')),
    parent_id           bigint REFERENCES geo.admin_area(id),
    country_iso2        char(2) NOT NULL,
    name                text NOT NULL,                -- primary name
    names               jsonb NOT NULL DEFAULT '{}',  -- {"ru": ..., "kk": ..., "en": ...}
    population          bigint,
    geom                geometry(MultiPolygon, 4326) NOT NULL,
    gers_id             text UNIQUE,                  -- Overture GERS id
    dataset_version_id  bigint
);
CREATE INDEX admin_area_geom_gix ON geo.admin_area USING gist (geom);
CREATE INDEX admin_area_country_level_idx ON geo.admin_area (country_iso2, level);
CREATE INDEX admin_area_name_trgm ON geo.admin_area USING gin (name gin_trgm_ops);

CREATE TABLE geo.country (
    id              bigserial PRIMARY KEY,
    iso2            char(2) NOT NULL UNIQUE,
    iso3            char(3) UNIQUE,
    name            text NOT NULL,
    names           jsonb NOT NULL DEFAULT '{}',
    continent       text,
    bbox            geometry(Polygon, 4326),
    geom            geometry(MultiPolygon, 4326),
    population      bigint,
    admin_area_id   bigint REFERENCES geo.admin_area(id),
    geofabrik_path  text,                              -- e.g. 'asia/kazakhstan'
    last_ingested_at timestamptz
);
CREATE INDEX country_geom_gix ON geo.country USING gist (geom);

-- ───────────────────────── META (versions, jobs) ─────────────────

CREATE TABLE meta.dataset_version (
    id              bigserial PRIMARY KEY,
    source_id       text NOT NULL REFERENCES meta.source(id),
    source_version  text NOT NULL,                    -- '2026-08-19.0', '2026-10-05' ...
    country_id      bigint NOT NULL REFERENCES geo.country(id),
    raw_uri         text,
    processed_uri   text,
    checksum        text,
    row_count       bigint,
    quality_score   numeric(4,3),
    quality_report  jsonb,
    pipeline_version text NOT NULL,
    ingested_at     timestamptz NOT NULL DEFAULT now(),
    status          text NOT NULL DEFAULT 'staged'
                    CHECK (status IN ('staged','active','superseded','failed')),
    UNIQUE (source_id, source_version, country_id, pipeline_version)
);

ALTER TABLE geo.admin_area
    ADD CONSTRAINT admin_area_dataset_version_fk
    FOREIGN KEY (dataset_version_id) REFERENCES meta.dataset_version(id);

CREATE TYPE meta.ingest_status AS ENUM
    ('PENDING','DOWNLOADING','PROCESSING','LOADED','FAILED');

CREATE TABLE meta.ingest_job (
    id                  bigserial PRIMARY KEY,
    job_key             text NOT NULL UNIQUE,         -- 'KZ:source:overture_places:2026-08-19.0:0.1.0'
    kind                text NOT NULL CHECK (kind IN ('source','resolve','features')),
    country_id          bigint NOT NULL REFERENCES geo.country(id),
    source_id           text REFERENCES meta.source(id),
    source_version      text,
    processing_version  text NOT NULL,
    depends_on          bigint[] NOT NULL DEFAULT '{}',
    status              meta.ingest_status NOT NULL DEFAULT 'PENDING',
    step                text,                          -- last completed step → restart point
    attempts            int NOT NULL DEFAULT 0,
    locked_by           text,
    locked_at           timestamptz,
    dataset_version_id  bigint REFERENCES meta.dataset_version(id),
    stats               jsonb NOT NULL DEFAULT '{}',
    error               jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    started_at          timestamptz,
    finished_at         timestamptz
);
CREATE INDEX ingest_job_claim_idx ON meta.ingest_job (status, created_at)
    WHERE status IN ('PENDING','FAILED');

CREATE TABLE meta.pipeline_run (
    id              bigserial PRIMARY KEY,
    job_id          bigint REFERENCES meta.ingest_job(id),
    step            text NOT NULL,
    worker          text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text NOT NULL CHECK (status IN ('running','ok','failed','skipped')),
    stats           jsonb,
    error           jsonb
);
CREATE INDEX pipeline_run_job_idx ON meta.pipeline_run (job_id, started_at);

-- ───────────────────────────── POI ──────────────────────────────

CREATE TABLE poi.category (
    id          text PRIMARY KEY,                     -- 'food.cafe.coffee_shop'
    parent_id   text REFERENCES poi.category(id),
    name        jsonb NOT NULL                        -- {"ru":..., "kk":..., "en":...}
);

CREATE TABLE poi.category_map (
    source_id        text NOT NULL REFERENCES meta.source(id),
    source_category  text NOT NULL,                   -- 'coffee_shop' / 'amenity=cafe'
    category_id      text NOT NULL REFERENCES poi.category(id),
    PRIMARY KEY (source_id, source_category)
);

CREATE TABLE poi.place (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                text NOT NULL,
    name_norm           text NOT NULL,
    brand               text,
    category_id         text REFERENCES poi.category(id),
    source_category     text,                          -- best raw category for unmapped ones
    country_iso2        char(2) NOT NULL,
    admin_area_id       bigint REFERENCES geo.admin_area(id),
    geom                geometry(Point, 4326) NOT NULL,
    h3_r9               h3index NOT NULL,
    h3_r8               h3index GENERATED ALWAYS AS (h3_cell_to_parent(h3_r9, 8)) STORED,
    h3_r7               h3index GENERATED ALWAYS AS (h3_cell_to_parent(h3_r9, 7)) STORED,
    h3_r6               h3index GENERATED ALWAYS AS (h3_cell_to_parent(h3_r9, 6)) STORED,
    address             jsonb,
    website             text,
    phone               text,
    opening_hours       jsonb,
    operating_status    text NOT NULL DEFAULT 'unknown'
                        CHECK (operating_status IN ('open','temporarily_closed','closed','unknown')),
    confidence          numeric(4,3) NOT NULL,
    source_count        smallint NOT NULL DEFAULT 1,
    first_seen_at       date NOT NULL,
    last_seen_at        date NOT NULL,
    closed_detected_at  date,
    content_hash        text NOT NULL,                 -- change detection for incremental updates
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX place_geom_gix      ON poi.place USING gist (geom);
CREATE INDEX place_h3_r9_idx     ON poi.place (h3_r9);
CREATE INDEX place_h3_r8_idx     ON poi.place (h3_r8);
CREATE INDEX place_h3_r7_idx     ON poi.place (h3_r7);
CREATE INDEX place_h3_r6_idx     ON poi.place (h3_r6);
CREATE INDEX place_cat_h3_idx    ON poi.place (category_id, h3_r9);
CREATE INDEX place_country_idx   ON poi.place (country_iso2);
CREATE INDEX place_name_trgm     ON poi.place USING gin (name_norm gin_trgm_ops);

-- Grey-zone matches for manual review; labelled pairs train the v2 matcher.
CREATE TABLE poi.match_review (
    id                  bigserial PRIMARY KEY,
    country_iso2        char(2) NOT NULL,
    a_source_id         text NOT NULL,
    a_record_id         text NOT NULL,
    b_source_id         text NOT NULL,
    b_record_id         text NOT NULL,
    distance_m          real NOT NULL,
    name_sim            real NOT NULL,
    label               text CHECK (label IN ('same','different')),
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (a_source_id, a_record_id, b_source_id, b_record_id)
);

CREATE TABLE poi.place_source (
    source_id           text NOT NULL REFERENCES meta.source(id),
    source_record_id    text NOT NULL,                -- GERS id / 'n123' / 'w456'
    dataset_version_id  bigint NOT NULL REFERENCES meta.dataset_version(id),
    place_id            uuid NOT NULL REFERENCES poi.place(id) ON DELETE CASCADE,
    match_score         numeric(4,3),
    match_method        text,                          -- 'seed' | 'rule:phone' | 'rule:name' ...
    raw                 jsonb,
    PRIMARY KEY (source_id, source_record_id, dataset_version_id)
);
CREATE INDEX place_source_place_idx ON poi.place_source (place_id);

-- ──────────────────────────── FEAT ──────────────────────────────

CREATE TABLE feat.feature_def (
    id          text PRIMARY KEY,                     -- 'poi_count', 'competitors_r500:food.cafe.coffee_shop'
    family      text NOT NULL CHECK (family IN
                ('demand','competition','saturation','access','cost','growth','risk','base')),
    unit        text,
    method      text,
    source_ids  text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE feat.h3_feature (
    h3                  h3index NOT NULL,
    resolution          smallint NOT NULL,
    feature_id          text NOT NULL REFERENCES feat.feature_def(id),
    value               double precision,
    feature_set_version int NOT NULL,
    computed_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (feature_id, feature_set_version, h3)
);
CREATE INDEX h3_feature_h3_idx ON feat.h3_feature (h3);

-- Cells whose inputs changed; feature jobs recompute only these (incremental updates).
CREATE TABLE feat.dirty_cell (
    h3          h3index PRIMARY KEY,
    reason      text NOT NULL,
    marked_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE feat.h3_embedding (
    h3                  h3index NOT NULL,
    feature_set_version int NOT NULL,
    vec                 vector(64) NOT NULL,
    PRIMARY KEY (h3, feature_set_version)
);

-- ──────────────────────────── SCORE ─────────────────────────────

CREATE TABLE score.scoring_profile (
    id          text NOT NULL,                        -- 'coffee_shop'
    version     int  NOT NULL,
    category_id text REFERENCES poi.category(id),
    weights     jsonb NOT NULL,
    transforms  jsonb NOT NULL,
    status      text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','retired')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, version)
);

CREATE TABLE score.location_score (
    h3                  h3index NOT NULL,
    profile_id          text NOT NULL,
    profile_version     int  NOT NULL,
    feature_set_version int  NOT NULL,
    score               numeric(5,2) NOT NULL,        -- percentile within city, 0–100
    components          jsonb NOT NULL,               -- per-factor contribution
    confidence          numeric(4,3),
    computed_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, profile_version, feature_set_version, h3),
    FOREIGN KEY (profile_id, profile_version) REFERENCES score.scoring_profile(id, version)
);
CREATE INDEX location_score_h3_idx ON score.location_score (h3);

-- ───────────────────────────── APP ──────────────────────────────
-- Personal data of users lives here; may need KZ-hosted storage (Blueprint §18, risk 4).

CREATE TABLE app.analysis (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid,
    org_id           uuid,
    created_at       timestamptz NOT NULL DEFAULT now(),
    request          jsonb NOT NULL,
    result_snapshot  jsonb,
    profile_id       text,
    profile_version  int
);

CREATE TABLE app.location_decision (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_id  uuid REFERENCES app.analysis(id),
    h3           h3index,
    geom         geometry(Point, 4326),
    decision     text NOT NULL CHECK (decision IN ('shortlisted','rejected','opened')),
    reason       text,
    decided_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.business_outcome (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    location_decision_id  uuid REFERENCES app.location_decision(id),
    place_id              uuid REFERENCES poi.place(id),
    opened_at             date,
    closed_at             date,
    revenue_band          text,
    reported_at           timestamptz NOT NULL DEFAULT now(),
    consent               jsonb NOT NULL
);

-- ─────────────────────── COVERAGE VIEWS ─────────────────────────

CREATE VIEW meta.coverage_by_country AS
SELECT c.iso2,
       c.name,
       c.continent,
       c.last_ingested_at,
       COALESCE(j.status_summary, 'NOT_QUEUED')         AS status,
       COALESCE(p.places, 0)                             AS places
FROM geo.country c
LEFT JOIN LATERAL (
    SELECT CASE
             WHEN count(*) FILTER (WHERE status = 'FAILED') > 0 THEN 'FAILED'
             WHEN count(*) FILTER (WHERE status IN ('DOWNLOADING','PROCESSING')) > 0 THEN 'PROCESSING'
             WHEN count(*) FILTER (WHERE status = 'PENDING') > 0 THEN 'PENDING'
             WHEN count(*) > 0 THEN 'LOADED'
           END AS status_summary
    FROM meta.ingest_job j
    WHERE j.country_id = c.id
) j ON true
LEFT JOIN LATERAL (
    SELECT count(*) AS places FROM poi.place p WHERE p.country_iso2 = c.iso2
) p ON true;

CREATE VIEW meta.source_freshness AS
SELECT source_id,
       max(source_version)                            AS latest_version,
       max(ingested_at)                               AS last_ingested_at,
       date_part('day', now() - max(ingested_at))     AS days_since_ingest
FROM meta.dataset_version
WHERE status = 'active'
GROUP BY source_id;
