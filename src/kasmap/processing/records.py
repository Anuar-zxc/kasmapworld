"""The normalized place record that every source adapter produces (L1 'staged' layer)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PlaceRecord:
    source_id: str
    source_record_id: str
    name: str
    lat: float
    lon: float
    country_iso2: str
    category_id: str | None = None
    source_category: str | None = None
    brand: str | None = None
    address: dict[str, Any] | None = None
    phone: str | None = None
    website: str | None = None
    opening_hours: Any = None
    operating_status: str = "unknown"
    source_confidence: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    # derived during normalization
    name_norm: str = ""
    core_name: str = ""
    phone_e164: str | None = None
    domain: str | None = None
    h3_r9: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlaceRecord:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)
