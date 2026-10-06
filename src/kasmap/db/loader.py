"""Writes pipeline results into PostGIS.

Incremental by design (World Coverage Engine §20):
* place ids are stable — an entity keeps the id of the place its members belonged to;
* rows are rewritten only when their content hash changed;
* places not seen in the current run are kept (absence from a dataset ≠ closure);
* every H3 cell touched by an insert/change/disappearance is marked dirty for
  feature recomputation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from kasmap.db.connection import Jsonb

from kasmap.ingestion.base import SourceMeta
from kasmap.processing.dedup import Entity, PairDecision
from kasmap.processing.records import PlaceRecord


def upsert_sources(conn, metas: Iterable[SourceMeta]) -> None:
    for m in metas:
        conn.execute(
            """
            INSERT INTO meta.source (id, name, license, attribution, commercial_ok, storage_ok,
                                     share_alike, update_freq, priority, license_url, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, license = EXCLUDED.license,
                attribution = EXCLUDED.attribution, commercial_ok = EXCLUDED.commercial_ok,
                storage_ok = EXCLUDED.storage_ok, share_alike = EXCLUDED.share_alike,
                update_freq = EXCLUDED.update_freq, priority = EXCLUDED.priority,
                license_url = EXCLUDED.license_url, notes = EXCLUDED.notes
            """,
            (m.id, m.name, m.license, m.attribution, m.commercial_ok, m.storage_ok,
             m.share_alike, m.update_freq, m.priority, m.license_url, m.notes),
        )


def register_dataset_version(
    conn, *, source_id: str, version: str, country_id: int, pipeline_version: str,
    raw_uri: str | None, processed_uri: str | None, checksum: str | None,
    row_count: int | None, report: dict[str, Any] | None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO meta.dataset_version
            (source_id, source_version, country_id, pipeline_version, raw_uri, processed_uri,
             checksum, row_count, quality_score, quality_report, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'staged')
        ON CONFLICT (source_id, source_version, country_id, pipeline_version) DO UPDATE SET
            raw_uri = EXCLUDED.raw_uri, processed_uri = EXCLUDED.processed_uri,
            checksum = EXCLUDED.checksum, row_count = EXCLUDED.row_count,
            quality_score = EXCLUDED.quality_score, quality_report = EXCLUDED.quality_report
        RETURNING id
        """,
        (source_id, version, country_id, pipeline_version, raw_uri, processed_uri, checksum,
         row_count, (report or {}).get("score"), Jsonb(report) if report else None),
    ).fetchone()
    return int(row["id"])


def previous_row_count(conn, source_id: str, country_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT row_count FROM meta.dataset_version
        WHERE source_id = %s AND country_id = %s AND status = 'active'
        ORDER BY ingested_at DESC LIMIT 1
        """,
        (source_id, country_id),
    ).fetchone()
    return int(row["row_count"]) if row and row["row_count"] is not None else None


def activate_versions(conn, dataset_version_ids: Iterable[int]) -> None:
    ids = list(dataset_version_ids)
    conn.execute(
        """
        UPDATE meta.dataset_version old SET status = 'superseded'
          FROM meta.dataset_version new
         WHERE new.id = ANY(%s) AND old.source_id = new.source_id
           AND old.country_id = new.country_id AND old.id <> new.id AND old.status = 'active'
        """,
        (ids,),
    )
    conn.execute("UPDATE meta.dataset_version SET status = 'active' WHERE id = ANY(%s)", (ids,))


# ───────────────────────────── admin areas ─────────────────────────────

LEVEL_ORDER = {"district": 0, "city": 1, "region": 2, "country": 3}


def load_admin_areas(conn, iso2: str, dataset_version_id: int,
                     areas: Iterable[Mapping[str, Any]]) -> int:
    n = 0
    for a in areas:
        conn.execute(
            """
            INSERT INTO geo.admin_area (level, country_iso2, name, names, geom, gers_id,
                                        dataset_version_id)
            VALUES (%(level)s, %(iso2)s, %(name)s, %(names)s,
                    ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromWKB(%(wkb)s, 4326)), 3)),
                    %(gers_id)s, %(dv)s)
            ON CONFLICT (gers_id) DO UPDATE SET
                level = EXCLUDED.level, name = EXCLUDED.name, names = EXCLUDED.names,
                geom = EXCLUDED.geom, dataset_version_id = EXCLUDED.dataset_version_id
            """,
            {**a, "names": Jsonb(a.get("names") or {}), "iso2": iso2, "dv": dataset_version_id},
        )
        n += 1
    # Parent = the smallest higher-level area that contains the child's interior point.
    conn.execute(
        """
        UPDATE geo.admin_area child
           SET parent_id = (
               SELECT p.id FROM geo.admin_area p
                WHERE p.country_iso2 = child.country_iso2
                  AND p.level = ANY (CASE child.level
                                       WHEN 'district' THEN ARRAY['city','region','country']
                                       WHEN 'city'     THEN ARRAY['region','country']
                                       WHEN 'region'   THEN ARRAY['country'] END)
                  AND ST_Covers(p.geom, ST_PointOnSurface(child.geom))
                ORDER BY ST_Area(p.geom) ASC
                LIMIT 1)
         WHERE child.country_iso2 = %s AND child.level <> 'country'
        """,
        (iso2,),
    )
    return n


# ───────────────────────────── places ─────────────────────────────


def content_hash(r: PlaceRecord, confidence: float) -> str:
    payload = [
        r.name, r.category_id, r.source_category, round(r.lat, 6), round(r.lon, 6),
        r.phone_e164, r.website, r.brand, r.operating_status, r.address, r.opening_hours,
        round(confidence, 2),
    ]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                   default=str).encode()).hexdigest()


def assign_place_ids(
    entities: list[Entity], existing: Mapping[tuple[str, str], str]
) -> list[tuple[str, bool]]:
    """Stable ids: reuse the most common existing place_id among members.

    Returns (place_id, is_new) per entity, in entity order.  If two entities claim the
    same old id (a split), the one with more sources keeps it and the other gets a new id.
    """
    proposals: list[str | None] = []
    for e in entities:
        olds = Counter(
            existing[(m.record.source_id, m.record.source_record_id)]
            for m in e.members
            if (m.record.source_id, m.record.source_record_id) in existing
        )
        proposals.append(olds.most_common(1)[0][0] if olds else None)
    order = sorted(range(len(entities)), key=lambda i: -entities[i].source_count)
    taken: set[str] = set()
    result: list[tuple[str, bool] | None] = [None] * len(entities)
    for i in order:
        pid = proposals[i]
        if pid is not None and pid not in taken:
            taken.add(pid)
            result[i] = (pid, False)
        else:
            result[i] = (str(uuid.uuid4()), True)
    return [r for r in result if r is not None]


def load_entities(
    conn,
    *,
    iso2: str,
    entities: list[Entity],
    dataset_versions: Mapping[str, int],
    today: dt.date | None = None,
) -> dict[str, int]:
    today = today or dt.date.today()
    existing = {
        (r["source_id"], r["source_record_id"]): str(r["place_id"])
        for r in conn.execute(
            """
            SELECT DISTINCT ON (ps.source_id, ps.source_record_id)
                   ps.source_id, ps.source_record_id, ps.place_id
              FROM poi.place_source ps JOIN poi.place p ON p.id = ps.place_id
             WHERE p.country_iso2 = %s
             ORDER BY ps.source_id, ps.source_record_id, ps.dataset_version_id DESC
            """,
            (iso2,),
        )
    }
    ids = assign_place_ids(entities, existing)

    conn.execute(
        """
        CREATE TEMP TABLE tmp_place (
            id uuid, name text, name_norm text, brand text, category_id text,
            source_category text, lon double precision, lat double precision, h3 text,
            address jsonb, website text, phone text, opening_hours jsonb,
            operating_status text, confidence numeric(4,3), source_count smallint,
            content_hash text, changed boolean
        ) ON COMMIT DROP
        """
    )
    conn.execute(
        """
        CREATE TEMP TABLE tmp_member (
            place_id uuid, source_id text, source_record_id text, dataset_version_id bigint,
            match_score numeric(4,3), match_method text, raw jsonb
        ) ON COMMIT DROP
        """
    )
    with conn.cursor().copy(
        "COPY tmp_place FROM STDIN"
    ) as copy:
        for e, (pid, _new) in zip(entities, ids, strict=True):
            c = e.canonical
            copy.write_row((
                pid, c.name, c.name_norm, c.brand, c.category_id, c.source_category, c.lon,
                c.lat, c.h3_r9, json.dumps(c.address, ensure_ascii=False) if c.address else None,
                c.website, c.phone_e164 or c.phone,
                json.dumps(c.opening_hours, ensure_ascii=False) if c.opening_hours else None,
                c.operating_status, e.confidence, e.source_count, content_hash(c, e.confidence),
                None,
            ))
    with conn.cursor().copy("COPY tmp_member FROM STDIN") as copy:
        for e, (pid, _new) in zip(entities, ids, strict=True):
            for m in e.members:
                r = m.record
                copy.write_row((
                    pid, r.source_id, r.source_record_id, dataset_versions[r.source_id],
                    round(m.score, 3), m.method, json.dumps(r.raw, ensure_ascii=False, default=str),
                ))

    # Dirty cells: new or changed places (new and old location), then disappeared ones.
    conn.execute(
        """
        INSERT INTO feat.dirty_cell (h3, reason)
        SELECT DISTINCT cell, reason FROM (
            SELECT t.h3::h3index AS cell, 'place_new_or_changed' AS reason
              FROM tmp_place t LEFT JOIN poi.place p ON p.id = t.id
             WHERE p.id IS NULL OR p.content_hash <> t.content_hash
            UNION ALL
            SELECT p.h3_r9, 'place_moved_or_changed'
              FROM tmp_place t JOIN poi.place p ON p.id = t.id
             WHERE p.content_hash <> t.content_hash
            UNION ALL
            SELECT p.h3_r9, 'place_not_seen'
              FROM poi.place p
             WHERE p.country_iso2 = %(iso2)s
               AND p.last_seen_at = (SELECT max(last_seen_at) FROM poi.place
                                      WHERE country_iso2 = %(iso2)s)
               AND NOT EXISTS (SELECT 1 FROM tmp_place t WHERE t.id = p.id)
        ) s
        ON CONFLICT (h3) DO UPDATE SET reason = EXCLUDED.reason, marked_at = now()
        """,
        {"iso2": iso2, "today": today},
    )
    changed = conn.execute(
        """
        SELECT count(*) FILTER (WHERE p.id IS NULL) AS inserted,
               count(*) FILTER (WHERE p.id IS NOT NULL AND p.content_hash <> t.content_hash) AS updated
          FROM tmp_place t LEFT JOIN poi.place p ON p.id = t.id
        """
    ).fetchone()
    conn.execute(
        """
        UPDATE tmp_place t
           SET changed = NOT EXISTS (SELECT 1 FROM poi.place p
                                      WHERE p.id = t.id AND p.content_hash = t.content_hash)
        """
    )

    conn.execute(
        """
        INSERT INTO poi.place AS p
            (id, name, name_norm, brand, category_id, source_category, country_iso2, geom, h3_r9,
             address, website, phone, opening_hours, operating_status, confidence, source_count,
             first_seen_at, last_seen_at, closed_detected_at, content_hash)
        SELECT t.id, t.name, t.name_norm, t.brand, t.category_id, t.source_category, %(iso2)s,
               ST_SetSRID(ST_MakePoint(t.lon, t.lat), 4326), t.h3::h3index,
               t.address, t.website, t.phone, t.opening_hours, t.operating_status, t.confidence,
               t.source_count, %(today)s, %(today)s,
               CASE WHEN t.operating_status = 'closed' THEN %(today)s::date END, t.content_hash
          FROM tmp_place t
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name, name_norm = EXCLUDED.name_norm, brand = EXCLUDED.brand,
            category_id = EXCLUDED.category_id, source_category = EXCLUDED.source_category,
            geom = EXCLUDED.geom, h3_r9 = EXCLUDED.h3_r9, address = EXCLUDED.address,
            website = EXCLUDED.website, phone = EXCLUDED.phone,
            opening_hours = EXCLUDED.opening_hours, operating_status = EXCLUDED.operating_status,
            confidence = EXCLUDED.confidence, source_count = EXCLUDED.source_count,
            last_seen_at = EXCLUDED.last_seen_at,
            closed_detected_at = COALESCE(p.closed_detected_at, EXCLUDED.closed_detected_at),
            content_hash = EXCLUDED.content_hash,
            updated_at = CASE WHEN p.content_hash <> EXCLUDED.content_hash THEN now()
                              ELSE p.updated_at END
        """,
        {"iso2": iso2, "today": today},
    )
    conn.execute(
        """
        INSERT INTO poi.place_source (source_id, source_record_id, dataset_version_id, place_id,
                                      match_score, match_method, raw)
        SELECT source_id, source_record_id, dataset_version_id, place_id, match_score,
               match_method, raw
          FROM tmp_member
        ON CONFLICT (source_id, source_record_id, dataset_version_id) DO UPDATE SET
            place_id = EXCLUDED.place_id, match_score = EXCLUDED.match_score,
            match_method = EXCLUDED.match_method, raw = EXCLUDED.raw
        """
    )
    # Admin area = the most specific area containing the place (only for touched rows).
    conn.execute(
        """
        UPDATE poi.place p
           SET admin_area_id = (
               SELECT a.id FROM geo.admin_area a
                WHERE a.country_iso2 = p.country_iso2 AND ST_Covers(a.geom, p.geom)
                ORDER BY CASE a.level WHEN 'district' THEN 0 WHEN 'city' THEN 1
                                      WHEN 'region' THEN 2 ELSE 3 END
                LIMIT 1)
          FROM tmp_place t
         WHERE p.id = t.id AND (t.changed OR p.admin_area_id IS NULL)
        """
    )
    not_seen = conn.execute(
        """
        SELECT count(*) AS n FROM poi.place p
         WHERE p.country_iso2 = %s AND p.last_seen_at < %s
        """,
        (iso2, today),
    ).fetchone()["n"]
    return {
        "entities": len(entities),
        "inserted": int(changed["inserted"]),
        "updated": int(changed["updated"]),
        "not_seen_this_run": int(not_seen),
    }


def save_review_pairs(conn, iso2: str,
                      pairs: Iterable[tuple[PlaceRecord, PlaceRecord, PairDecision]]) -> int:
    n = 0
    for a, b, d in pairs:
        if (a.source_id, a.source_record_id) > (b.source_id, b.source_record_id):
            a, b = b, a
        conn.execute(
            """
            INSERT INTO poi.match_review (country_iso2, a_source_id, a_record_id, b_source_id,
                                          b_record_id, distance_m, name_sim)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (iso2, a.source_id, a.source_record_id, b.source_id, b.source_record_id,
             d.distance_m, d.name_sim),
        )
        n += 1
    return n


def mark_country_ingested(conn, country_id: int) -> None:
    conn.execute("UPDATE geo.country SET last_ingested_at = now() WHERE id = %s", (country_id,))
