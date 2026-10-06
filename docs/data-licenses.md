# Data licenses

A source enters production only after its row here is complete and its `meta.source` record
says `storage_ok = true`.  "Publicly available" never means "commercially unrestricted".

Checklist per source: license · commercial use · attribution · redistribution · database
rights · storage/caching · derivative database rules · API limits · update frequency.

| Source | License | Commercial | Storage | Share-alike | Attribution | Status |
|---|---|---|---|---|---|---|
| Overture Places | CDLA-Permissive-2.0 (main); Apache-2.0 (Foursquare part); CC0-1.0 (AllThePlaces) | yes | yes | no | Overture Maps Foundation + listed contributors | **in use** |
| Overture Divisions | ODbL-1.0 (+ CC-BY-4.0 parts: geoBoundaries, Esri) | yes | yes | yes (derived databases) | © OpenStreetMap contributors, Overture | **in use** |
| OpenStreetMap (Geofabrik) | ODbL-1.0 | yes | yes | yes (derived databases) | © OpenStreetMap contributors | **in use** |
| Overture Buildings / Transportation / Base | ODbL-1.0 | yes | yes | yes | © OpenStreetMap contributors | planned (Phase 2 features) |
| Foursquare OS Places | Apache-2.0 | yes | yes | no | Foursquare | candidate (partly inside Overture) |
| Kontur Population (H3, ~400 m) | **not verified** | ? | ? | ? | ? | verify before use |
| WorldPop / GHSL | **not verified** | ? | ? | ? | ? | verify before use |
| 2GIS API | proprietary offer | — | **no** (offer §3.1: extraction, storage, caching forbidden; §6.3 penalty 3 mln RUB) | — | — | **not allowed as stored data**; live display only, or a separate data-licence contract. KZ edition (law.2gis.kz) still to be checked |
| Krisha.kz | not verified | ? | ? | ? | ? | partnership only, no scraping |
| stat.gov.kz, data.egov.kz | not verified | ? | ? | ? | ? | verify terms |

## Obligations in the product

* Map attribution control and every PDF/export: «© OpenStreetMap contributors», «Overture Maps Foundation».
* ODbL: if we publicly distribute a *derived database* (e.g. bulk B2B exports of places), it
  must be offered under ODbL.  Scores and aggregates as a *Produced Work* are believed to be
  outside share-alike — **get a legal opinion before selling bulk exports**.
* Keep `share_alike` sources separable: `poi.place_source` records which source every
  canonical place came from.

Sources: [Overture attribution](https://docs.overturemaps.org/attribution/),
[2GIS API offer](https://law.2gis.ru/webapi-offer),
[OSM copyright](https://www.openstreetmap.org/copyright).
