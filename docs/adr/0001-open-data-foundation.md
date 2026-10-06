# ADR 0001 — Open data foundation instead of stored 2GIS data

Status: accepted (pending confirmation of the KZ edition of the 2GIS offer) · 2026-10-06

## Context
The hackathon version used the 2GIS API as its POI source.  The 2GIS reference-API offer
(§3.1) forbids extracting and storing received data and states that caching is not
provided for; §6.3 sets a 3 mln RUB penalty.  KasMap's core is to store places, aggregate
them into H3 cells and compute features — exactly what is forbidden.

## Decision
Overture Maps Places + OpenStreetMap are the stored POI foundation.  2GIS may only be used
for live display in the UI, or as stored data under a separate written data licence.

## Consequences
* Unknown: Overture/OSM completeness in Almaty vs 2GIS.  Phase 1 includes a recall audit
  (field check of sample H3 cells); below ~70 % recall per target category we need a
  licensed local source.
* Every place keeps source lineage (`poi.place_source`), so a future licensed source can be
  added and removed cleanly.
