# Data sources

| id | Adapter | Access | Version | What we take |
|---|---|---|---|---|
| `overture_places` | `ingestion/sources/overture.py` | `s3://overturemaps-us-west-2/release/<release>/theme=places/type=place/*` via DuckDB, bbox pushdown + country polygon | pinned release, e.g. `2026-08-19.0` | id (GERS), name, basic_category / categories.primary, confidence, first phone/website, brand, first address, operating status, point |
| `overture_divisions` | same | `theme=divisions/type=division_area` | same | country polygon (registry), regions, localities (cities), macrohoods (districts) |
| `osm` | `ingestion/sources/osm.py` | Geofabrik `<path>-latest.osm.pbf` + `.md5` | Last-Modified date | nodes and ways with amenity/shop/leisure/tourism/office/healthcare/craft tags (mapped or named), way centroid |

Schema drift: Overture deprecated `categories` (removal announced for September 2026) in
favour of `basic_category` + `taxonomy`; the adapter discovers columns with `DESCRIBE`.
Category mappings live in `processing/categories.py`; extend them from
`kasmap categories unmapped <ISO2>`.
