from __future__ import annotations

from kasmap.processing.pipeline import enrich
from kasmap.processing.records import PlaceRecord


def rec(source: str, rid: str, name: str, lat: float, lon: float, *, category: str | None = None,
        phone: str | None = None, website: str | None = None, confidence: float | None = None,
        iso2: str = "KZ", brand: str | None = None) -> PlaceRecord:
    r = PlaceRecord(
        source_id=source, source_record_id=rid, name=name, lat=lat, lon=lon, country_iso2=iso2,
        category_id=category, phone=phone, website=website, source_confidence=confidence,
        brand=brand,
    )
    return enrich(r, with_h3=False)


# Al-Farabi avenue near KazNU, Almaty (approximate, for tests only)
LAT, LON = 43.2220, 76.9210
M_LAT = 1 / 111_320  # one metre of latitude in degrees
