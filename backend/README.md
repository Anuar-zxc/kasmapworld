# backend — KasMap web app + API

Deployed to **Vercel** (project Root Directory = `backend`): https://kasmapworld.vercel.app

| Endpoint | |
|---|---|
| `GET /` | web app: pick a business type, see opportunity hexes, top-5 places with reasons |
| `GET /v1/cities/{city}/meta` | categories with counts, data release |
| `GET /v1/cities/{city}/scores?category=` | Opportunity Score v0 for every H3 r9 cell |
| `GET /v1/cities/{city}/best?category=&limit=` | top cells, spread apart, with reasons and a landmark |
| `GET /v1/cities/{city}/places?category=` | places (GeoJSON) |
| `GET /v1/cells/{h3}?category=` | one cell: score, components, competitors and anchors nearby |
| `GET /v1/cities/{city}/source-categories` | raw source categories (mapping coverage) |
| `POST /v1/admin/bootstrap?city=almaty[&force=true]` | migrate + seed + load the city from the latest Overture release (≈15 s; 12 h guard) |
| `GET /v1/status`, `/health`, `/docs` | status, liveness, OpenAPI |

Data today: Overture Maps Places only, Almaty extent (approximate rectangle). Country-scale
ingestion with OSM, dedup and official boundaries is the `kasmap` pipeline (`src/`, GitHub Actions).

Score v0 (`kasmap_api/scoring.py`): percentile within the city of a blend of activity around the
cell, anchors (universities, schools, malls, business centres, transit, hospitals), competition
balance and saturation gap. It is a comparative index, not a probability of success.

`kasmap_api/categories.py` and `normalization.py` are vendored from `src/kasmap/processing/`
and `sql/0001_initial.sql` from `database/schemas/` — keep them in sync.

Tests: `python -m unittest discover -s backend/tests`
