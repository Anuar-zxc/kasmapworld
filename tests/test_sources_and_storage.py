import datetime as dt
import tempfile
import unittest
from pathlib import Path

from helpers import LAT, LON, rec

from kasmap.db.jobs import job_key
from kasmap.db.loader import assign_place_ids, content_hash
from kasmap.ingestion import storage
from kasmap.ingestion.sources import get_source
from kasmap.ingestion.sources.osm import is_poi, tags_to_record, version_from_last_modified
from kasmap.ingestion.sources.overture import bbox_predicate, place_select, row_to_record
from kasmap.processing.dedup import resolve
from kasmap.processing.geo import BBox


class OvertureSqlTests(unittest.TestCase):
    def test_bbox_predicate_is_intersection(self):
        sql = bbox_predicate(BBox(46.5, 40.5, 87.4, 55.5))
        self.assertIn("bbox.xmin <= 87.4", sql)
        self.assertIn("bbox.xmax >= 46.5", sql)
        self.assertIn("bbox.ymin <= 55.5", sql)
        self.assertIn("bbox.ymax >= 40.5", sql)

    def test_select_adapts_to_schema(self):
        old = place_select({"id", "names", "geometry", "categories", "confidence"})
        self.assertIn("categories.primary AS category_primary", old)
        self.assertNotIn("basic_category", old)
        new = place_select({"id", "names", "geometry", "basic_category", "taxonomy", "phones"})
        self.assertIn("basic_category", new)
        self.assertIn("phones[1] AS phone", new)
        self.assertFalse(any("categories.primary" in c for c in new))

    def test_row_to_record(self):
        r = row_to_record({
            "id": "08f2...", "name": "Coffee Boom", "lon": LON, "lat": LAT,
            "basic_category": "coffee_shop", "confidence": 0.77, "phone": "+77001112233",
            "address_json": '{"freeform": "Al-Farabi 71", "locality": "Almaty"}',
            "operating_status": "permanently_closed",
        }, "KZ")
        self.assertEqual(r.category_id, "food.cafe.coffee_shop")
        self.assertEqual(r.address["locality"], "Almaty")
        self.assertEqual(r.operating_status, "closed")
        self.assertEqual(r.source_confidence, 0.77)


class OsmTests(unittest.TestCase):
    def test_is_poi(self):
        self.assertTrue(is_poi({"amenity": "pharmacy", "name": "Europharma"}))
        self.assertFalse(is_poi({"amenity": "pharmacy"}))
        self.assertTrue(is_poi({"highway": "bus_stop"}))
        self.assertTrue(is_poi({"craft": "shoemaker", "name": "Etik"}))
        self.assertFalse(is_poi({"craft": "shoemaker"}))
        self.assertFalse(is_poi({"building": "yes", "name": "Dom"}))

    def test_tags_to_record(self):
        r = tags_to_record("n42", {"amenity": "cafe", "name:ru": "Кафе Тау", "addr:street": "Абая",
                                   "contact:phone": "87272000000"}, LAT, LON, "KZ")
        self.assertEqual(r.name, "Кафе Тау")
        self.assertEqual(r.address, {"street": "Абая"})
        self.assertEqual(r.phone, "87272000000")
        self.assertEqual(r.source_category, "amenity=cafe")

    def test_version_from_header(self):
        self.assertEqual(version_from_last_modified("Mon, 05 Oct 2026 21:10:03 GMT"), "2026-10-05")


class RegistryTests(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(get_source("overture").source_id, "overture_places")
        self.assertEqual(get_source("osm").source_id, "osm")
        with self.assertRaises(ValueError):
            get_source("2gis")

    def test_job_key(self):
        self.assertEqual(job_key("kz", "source", "osm", "2026-10-05", "0.1.0"),
                         "KZ:source:osm:2026-10-05:0.1.0")


class StorageTests(unittest.TestCase):
    def test_roundtrip_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake = storage.DataLake(Path(tmp))
            d = lake.processed("osm", "2026-10-05", "kz")
            self.assertTrue(str(d).endswith("processed/osm/2026-10-05/KZ"))
            path = storage.records_path(d, "places")
            r = rec("osm", "n1", "Кафе", LAT, LON, category="food.cafe")
            r.address = {"street": "Абая"}
            self.assertEqual(storage.write_records(path, [r]), 1)
            back = list(storage.read_records(path))
            self.assertEqual(back[0].name, "Кафе")
            self.assertEqual(back[0].address, {"street": "Абая"})
            self.assertFalse(storage.is_done(d, "process"))
            storage.mark_done(d, "process", {"rows": 1})
            self.assertTrue(storage.is_done(d, "process"))
            self.assertEqual(storage.read_manifest(d, "process"), {"rows": 1})


class LoaderLogicTests(unittest.TestCase):
    def test_stable_ids_and_split(self):
        a = rec("osm", "n1", "Magnum", LAT, LON, category="retail.supermarket")
        b = rec("overture_places", "g1", "Magnum", LAT, LON, category="retail.supermarket")
        c = rec("osm", "n9", "Small", LAT + 0.01, LON, category="retail.convenience")
        entities = resolve([a, b, c]).entities
        existing = {("osm", "n1"): "p-old", ("overture_places", "g1"): "p-old",
                    ("osm", "n9"): "p-old"}  # n9 was wrongly merged before → split now
        ids = assign_place_ids(entities, existing)
        reused = [pid for pid, new in ids if not new]
        self.assertEqual(reused, ["p-old"])
        self.assertEqual(sum(new for _, new in ids), 1)
        big = next(i for i, e in enumerate(entities) if e.source_count == 2)
        self.assertEqual(ids[big], ("p-old", False))

    def test_content_hash_changes_with_content_only(self):
        r = rec("osm", "n1", "Magnum", LAT, LON, category="retail.supermarket")
        h1 = content_hash(r, 0.9)
        self.assertEqual(h1, content_hash(r, 0.9001))
        r.phone_e164 = "+77001112233"
        self.assertNotEqual(h1, content_hash(r, 0.9))
        self.assertIsInstance(dt.date.today(), dt.date)


if __name__ == "__main__":
    unittest.main()
