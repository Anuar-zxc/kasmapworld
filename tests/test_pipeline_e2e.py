"""Offline end-to-end: adapter.process() → processed file → resolve (no DB, no network)."""

import tempfile
import unittest
from pathlib import Path

from helpers import LAT, LON, M_LAT

from kasmap.config import Settings
from kasmap.ingestion import storage
from kasmap.ingestion.base import CountryRef, GeoDataSource, JobContext
from kasmap.ingestion.orchestrator import quality_passes
from kasmap.ingestion.sources.osm import OSM_META, tags_to_record
from kasmap.ingestion.sources.overture import PLACES_META, row_to_record
from kasmap.processing.dedup import resolve
from kasmap.processing.geo import BBox
from kasmap.processing.pipeline import enrich_all

KZ = CountryRef(iso2="KZ", name="Kazakhstan", bbox=BBox(46.49, 40.57, 87.36, 55.44))


class FakeOverture(GeoDataSource):
    meta = PLACES_META

    def resolve_version(self, settings, country):
        return "test"

    def download(self, ctx):
        return {}

    def normalize(self, ctx):
        rows = [
            {"id": "g1", "name": "Coffee Boom", "lat": LAT, "lon": LON,
             "basic_category": "coffee_shop", "confidence": 0.9, "phone": "+77001112233"},
            {"id": "g2", "name": "Europharma", "lat": LAT + 300 * M_LAT, "lon": LON,
             "basic_category": "pharmacy", "confidence": 0.8},
            {"id": "g3", "name": "Somewhere in Paris", "lat": 48.85, "lon": 2.35,
             "basic_category": "cafe", "confidence": 0.9},
        ]
        return (row_to_record(r, ctx.iso2) for r in rows)

    def transform(self, ctx, records):
        return enrich_all(records, with_h3=False)


class FakeOSM(FakeOverture):
    meta = OSM_META

    def normalize(self, ctx):
        yield tags_to_record("n1", {"amenity": "cafe", "cuisine": "coffee_shop",
                                    "name": "Кофе Бум", "phone": "8 700 111 22 33"},
                             LAT + 15 * M_LAT, LON, ctx.iso2)
        yield tags_to_record("n2", {"amenity": "pharmacy", "name": "Европа"},
                             LAT + 900 * M_LAT, LON, ctx.iso2)


class EndToEndTests(unittest.TestCase):
    def test_two_sources_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(data_root=Path(tmp))
            lake = storage.DataLake(Path(tmp))
            outputs = []
            for src in (FakeOverture(), FakeOSM()):
                ctx = JobContext(country=KZ, version="test", settings=settings, lake=lake)
                res = src.process(ctx)
                manifest = storage.read_manifest(res.path.parent, "process")
                self.assertTrue(quality_passes(manifest["report"]) or src.source_id ==
                                "overture_places")
                outputs.append(res)
            overture_report = outputs[0].report
            self.assertEqual(overture_report.dropped, 1)          # the Paris record
            records = [r for o in outputs for r in storage.read_records(o.path)]
            result = resolve(records, {"osm": 10, "overture_places": 20})
            self.assertEqual(result.stats["records"], 4)
            self.assertEqual(result.stats["entities"], 3)          # coffee merged by phone
            coffee = next(e for e in result.entities if e.source_count == 2)
            self.assertEqual(coffee.canonical.category_id, "food.cafe.coffee_shop")
            self.assertEqual(coffee.canonical.name, "Кофе Бум")    # OSM has priority


if __name__ == "__main__":
    unittest.main()
