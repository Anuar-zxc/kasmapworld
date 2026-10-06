import unittest

from helpers import LAT, LON, rec

from kasmap.processing.geo import BBox
from kasmap.processing.validation import Validator, validate_records

KZ_BBOX = BBox(46.49, 40.57, 87.36, 55.44)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.v = Validator(country_bbox=KZ_BBOX)

    def codes(self, r):
        return {i.code for i in self.v.check(r)}

    def test_valid_record(self):
        self.assertEqual(self.codes(rec("osm", "n1", "Кофейня", LAT, LON, category="food.cafe")),
                         set())

    def test_errors(self):
        self.assertIn("impossible_coordinates", self.codes(rec("osm", "n1", "x", 95, LON)))
        self.assertIn("null_island", self.codes(rec("osm", "n1", "x", 0.0, 0.0)))
        self.assertIn("outside_country_bbox", self.codes(rec("osm", "n1", "Cafe", 48.85, 2.35,
                                                             category="food.cafe")))
        self.assertIn("empty_name", self.codes(rec("osm", "n1", " ", LAT, LON, category="food.cafe")))

    def test_unnamed_bus_stop_is_fine(self):
        self.assertEqual(self.codes(rec("osm", "n1", "", LAT, LON, category="transport.bus_stop")),
                         set())

    def test_polygon_predicate_gives_warning(self):
        v = Validator(country_bbox=KZ_BBOX, in_country=lambda lon, lat: False)
        codes = {i.code for i in v.check(rec("osm", "n1", "Cafe", LAT, LON, category="food.cafe"))}
        self.assertEqual(codes, {"outside_country_boundary"})

    def test_report_and_gate(self):
        records = [
            rec("osm", "n1", "A", LAT, LON, category="food.cafe"),
            rec("osm", "n2", "B", LAT, LON),                         # unknown category: info
            rec("osm", "n3", "C", LAT, LON, category="food.cafe", confidence=0.1),  # warning
            rec("osm", "n4", "", LAT, LON, category="food.cafe"),    # error
        ]
        kept, report = validate_records(records, self.v)
        self.assertEqual([r.source_record_id for r in kept], ["n1", "n2", "n3"])
        self.assertEqual(report.dropped, 1)
        self.assertEqual(report.warnings, 1)
        self.assertAlmostEqual(report.score, (2 + 0.5) / 4)
        self.assertEqual(report.issue_counts["unknown_category"], 1)
        self.assertLess(kept[2].source_confidence, 0.1)
        self.assertFalse(report.passes(min_score=0.8))

    def test_delta_gate(self):
        records = [rec("osm", f"n{i}", "A", LAT, LON, category="food.cafe") for i in range(10)]
        _, report = validate_records(records, self.v, previous_count=20)
        self.assertTrue(report.delta_exceeded)
        self.assertFalse(report.passes())
        _, ok = validate_records(records, self.v, previous_count=11)
        self.assertTrue(ok.passes())


if __name__ == "__main__":
    unittest.main()
