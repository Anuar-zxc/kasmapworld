"""Country registry (`geo.country`)."""

from __future__ import annotations

from typing import Any

from kasmap.db.connection import Jsonb

from kasmap.ingestion.base import CountryRef
from kasmap.ingestion.sources.osm import GEOFABRIK_PATHS
from kasmap.processing.geo import BBox

_GEOM_SQL = "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromWKB(%(wkb)s, 4326)), 3))"


def upsert_country(conn, c: dict[str, Any]) -> int:
    b: BBox = c["bbox"]
    row = conn.execute(
        f"""
        INSERT INTO geo.country (iso2, name, names, bbox, geom, geofabrik_path)
        VALUES (%(iso2)s, %(name)s, %(names)s,
                ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, 4326),
                {_GEOM_SQL}, %(gf)s)
        ON CONFLICT (iso2) DO UPDATE
           SET name = EXCLUDED.name, names = EXCLUDED.names, bbox = EXCLUDED.bbox,
               geom = EXCLUDED.geom,
               geofabrik_path = COALESCE(geo.country.geofabrik_path, EXCLUDED.geofabrik_path)
        RETURNING id
        """,
        {
            "iso2": c["iso2"], "name": c["name"] or c["iso2"], "names": Jsonb(c.get("names") or {}),
            "xmin": b.xmin, "ymin": b.ymin, "xmax": b.xmax, "ymax": b.ymax,
            "wkb": c["wkb"], "gf": GEOFABRIK_PATHS.get(c["iso2"]),
        },
    ).fetchone()
    country_id = int(row["id"])
    # Mirror as a level='country' admin area so every place can join one hierarchy.
    conn.execute(
        """
        INSERT INTO geo.admin_area (level, country_iso2, name, names, geom, gers_id)
        SELECT 'country', iso2, name, names, geom, 'country:' || iso2 FROM geo.country WHERE id = %s
        ON CONFLICT (gers_id) DO UPDATE SET name = EXCLUDED.name, names = EXCLUDED.names,
                                            geom = EXCLUDED.geom
        """,
        (country_id,),
    )
    conn.execute(
        """
        UPDATE geo.country c SET admin_area_id = a.id
          FROM geo.admin_area a WHERE a.gers_id = 'country:' || c.iso2 AND c.id = %s
        """,
        (country_id,),
    )
    return country_id


def get_country(conn, iso2: str) -> CountryRef | None:
    row = conn.execute(
        """
        SELECT id, iso2, name, geofabrik_path, ST_AsBinary(geom) AS wkb,
               ST_XMin(bbox) AS xmin, ST_YMin(bbox) AS ymin, ST_XMax(bbox) AS xmax,
               ST_YMax(bbox) AS ymax
        FROM geo.country WHERE iso2 = %s
        """,
        (iso2.upper(),),
    ).fetchone()
    if row is None:
        return None
    bbox = BBox(row["xmin"], row["ymin"], row["xmax"], row["ymax"]) if row["xmin"] is not None else None
    return CountryRef(
        iso2=row["iso2"], name=row["name"], bbox=bbox,
        geom_wkb=bytes(row["wkb"]) if row["wkb"] else None,
        geofabrik_path=row["geofabrik_path"], db_id=row["id"],
    )


def get_country_by_id(conn, country_id: int) -> CountryRef:
    iso2 = conn.execute("SELECT iso2 FROM geo.country WHERE id = %s", (country_id,)).fetchone()["iso2"]
    ref = get_country(conn, iso2)
    assert ref is not None
    return ref


def all_iso2(conn) -> list[str]:
    return [r["iso2"] for r in conn.execute("SELECT iso2 FROM geo.country ORDER BY iso2")]
