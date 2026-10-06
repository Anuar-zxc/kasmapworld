"""H3 helpers.  Storage resolution is 9; coarser levels are derived (Blueprint §5)."""

from __future__ import annotations

BASE_RESOLUTION = 9
DERIVED_RESOLUTIONS = (8, 7, 6)


def _h3():
    try:
        import h3  # noqa: PLC0415  (optional at import time so pure modules stay testable)
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Package 'h3>=4' is required: pip install h3") from exc
    return h3


def cell(lat: float, lon: float, resolution: int = BASE_RESOLUTION) -> str:
    return _h3().latlng_to_cell(lat, lon, resolution)


def parent(h: str, resolution: int) -> str:
    return _h3().cell_to_parent(h, resolution)


def is_valid(h: str) -> bool:
    try:
        return bool(_h3().is_valid_cell(h))
    except Exception:  # noqa: BLE001
        return False


def disk(h: str, k: int) -> list[str]:
    return list(_h3().grid_disk(h, k))
