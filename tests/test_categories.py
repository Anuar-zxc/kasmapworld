import unittest

from kasmap.processing import categories as c


class TaxonomyTests(unittest.TestCase):
    def test_parents_precede_children_and_exist(self):
        seen: set[str] = set()
        for cat in c.TAXONOMY:
            if cat.parent_id is not None:
                self.assertIn(cat.parent_id, seen, cat.id)
                self.assertTrue(cat.id.startswith(cat.parent_id + "."), cat.id)
            seen.add(cat.id)

    def test_all_mappings_point_into_taxonomy(self):
        for mapping in (c.OVERTURE_MAP, c.OSM_MAP):
            for target in mapping.values():
                self.assertIn(target, c.CATEGORY_IDS)


class MappingTests(unittest.TestCase):
    def test_overture_prefers_most_specific(self):
        self.assertEqual(c.map_overture("restaurant", "coffee_shop"), "food.cafe.coffee_shop")
        self.assertEqual(
            c.map_overture("personal_or_beauty_service", "nail_studio",
                           ["services", "personal_or_beauty_service", "beauty_salon", "nail_studio"]),
            "beauty.salon")
        self.assertEqual(c.map_overture(None, "pharmacy"), "health.pharmacy")
        self.assertIsNone(c.map_overture("space_agency", None))

    def test_osm_rules(self):
        self.assertEqual(c.map_osm({"amenity": "cafe", "cuisine": "coffee_shop;cake"}),
                         "food.cafe.coffee_shop")
        self.assertEqual(c.map_osm({"amenity": "cafe"}), "food.cafe")
        self.assertEqual(c.map_osm({"shop": "hairdresser", "male": "yes"}), "beauty.barber")
        self.assertEqual(c.map_osm({"shop": "hairdresser"}), "beauty.salon")
        self.assertEqual(c.map_osm({"amenity": "pharmacy", "shop": "chemist"}), "health.pharmacy")
        self.assertIsNone(c.map_osm({"building": "yes"}))

    def test_compatibility(self):
        self.assertTrue(c.compatible("food.cafe", "food.cafe.coffee_shop"))
        self.assertTrue(c.compatible(None, "health.pharmacy"))
        self.assertFalse(c.compatible("food.cafe", "health.pharmacy"))


if __name__ == "__main__":
    unittest.main()
