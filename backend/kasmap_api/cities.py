"""Cities available in the product.  Adding a city = one entry here + a bootstrap run."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    slug: str
    name_ru: str
    name_en: str
    country_iso2: str
    # Processing extent (lon/lat).  An approximate rectangle around the city until the
    # official boundary from Overture divisions is loaded by the full pipeline.
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    center: tuple[float, float]
    zoom: float = 11.5


CITIES: dict[str, City] = {
    "almaty": City(
        slug="almaty", name_ru="Алматы", name_en="Almaty", country_iso2="KZ",
        xmin=76.74, ymin=43.15, xmax=77.10, ymax=43.40,
        center=(76.9286, 43.2389),
    ),
}

COUNTRIES = {"KZ": ("Kazakhstan", (46.49, 40.57, 87.36, 55.44))}


def get_city(slug: str) -> City:
    try:
        return CITIES[slug]
    except KeyError as exc:
        raise KeyError(f"unknown city '{slug}'") from exc
