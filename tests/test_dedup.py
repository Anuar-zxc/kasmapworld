import unittest

from helpers import LAT, LON, M_LAT, rec

from kasmap.processing.dedup import MatchConfig, decide, jaro_winkler, resolve

PRIORITY = {"osm": 10, "overture_places": 20}


def ids(entity):
    return sorted(f"{m.record.source_id}:{m.record.source_record_id}" for m in entity.members)


class SimilarityTests(unittest.TestCase):
    def test_jaro_winkler_reference_values(self):
        self.assertAlmostEqual(jaro_winkler("martha", "marhta"), 0.961, places=3)
        self.assertAlmostEqual(jaro_winkler("dwayne", "duane"), 0.84, places=2)
        self.assertEqual(jaro_winkler("", "abc"), 0.0)


class DecisionTests(unittest.TestCase):
    cfg = MatchConfig()

    def test_cross_script_same_place(self):
        a = rec("osm", "n1", "Starbucks", LAT, LON, category="food.cafe.coffee_shop")
        b = rec("overture_places", "g1", "Старбакс", LAT + 20 * M_LAT, LON,
                category="food.cafe")
        d = decide(a, b, self.cfg)
        self.assertIsNotNone(d)
        self.assertTrue(d.match, d)

    def test_phone_match_even_with_different_names(self):
        a = rec("osm", "n1", "Аптека №5", LAT, LON, category="health.pharmacy", phone="87011234567")
        b = rec("overture_places", "g1", "Europharma", LAT + 90 * M_LAT, LON,
                category="health.pharmacy", phone="+7 701 123 45 67")
        d = decide(a, b, self.cfg)
        self.assertEqual(d.method, "rule:phone")

    def test_different_categories_never_match(self):
        a = rec("osm", "n1", "Tau", LAT, LON, category="food.cafe")
        b = rec("overture_places", "g1", "Tau", LAT, LON, category="health.pharmacy")
        self.assertIsNone(decide(a, b, self.cfg))

    def test_neighbouring_distinct_cafes(self):
        a = rec("osm", "n1", "Coffee Boom", LAT, LON, category="food.cafe.coffee_shop")
        b = rec("overture_places", "g1", "Daily Grind", LAT + 30 * M_LAT, LON,
                category="food.cafe.coffee_shop")
        self.assertIsNone(decide(a, b, self.cfg))

    def test_grey_zone_goes_to_review(self):
        a = rec("osm", "n1", "Bahandi Burger", LAT, LON, category="food.fast_food")
        b = rec("overture_places", "g1", "Bahandy", LAT + 50 * M_LAT, LON,
                category="food.fast_food")
        d = decide(a, b, self.cfg)
        self.assertIsNotNone(d)
        self.assertFalse(d.match)
        self.assertEqual(d.method, "review:grey_zone")


class ResolveTests(unittest.TestCase):
    def test_merge_and_canonical(self):
        a = rec("osm", "n1", "Starbucks", LAT, LON, category="food.cafe.coffee_shop",
                website="https://starbucks.kz", confidence=None)
        b = rec("overture_places", "g1", "Starbucks Coffee", LAT + 10 * M_LAT, LON,
                category="food.cafe", phone="+77272000000", confidence=0.8)
        far = rec("overture_places", "g2", "Starbucks", LAT + 800 * M_LAT, LON,
                  category="food.cafe.coffee_shop")
        result = resolve([a, b, far], PRIORITY)
        self.assertEqual(result.stats["entities"], 2)
        merged = next(e for e in result.entities if len(e.members) == 2)
        self.assertEqual(ids(merged), ["osm:n1", "overture_places:g1"])
        c = merged.canonical
        self.assertEqual(c.name, "Starbucks")                    # OSM has priority
        self.assertEqual(c.category_id, "food.cafe.coffee_shop")  # most specific
        self.assertEqual(c.phone_e164, "+77272000000")            # filled from Overture
        self.assertEqual(c.domain, "starbucks.kz")
        self.assertEqual(merged.source_count, 2)
        self.assertAlmostEqual(merged.confidence, 1 - (1 - 0.5) * (1 - 0.8), places=3)

    def test_source_guard_blocks_bridges(self):
        # Two distinct OSM pharmacies, one Overture record close to both with the same name.
        o1 = rec("osm", "n1", "Europharma", LAT, LON, category="health.pharmacy")
        o2 = rec("osm", "n2", "Europharma", LAT + 60 * M_LAT, LON, category="health.pharmacy")
        g = rec("overture_places", "g1", "Europharma", LAT + 30 * M_LAT, LON,
                category="health.pharmacy")
        result = resolve([o1, o2, g], PRIORITY)
        self.assertEqual(result.stats["entities"], 2)
        self.assertEqual(result.stats["merges_blocked_by_source_guard"], 1)
        for e in result.entities:
            sources = [m.record.source_id for m in e.members]
            self.assertEqual(len(sources), len(set(sources)))

    def test_same_source_duplicates(self):
        a = rec("overture_places", "g1", "Magnum", LAT, LON, category="retail.supermarket")
        b = rec("overture_places", "g2", "Magnum", LAT + 5 * M_LAT, LON,
                category="retail.supermarket")
        result = resolve([a, b], PRIORITY)
        self.assertEqual(result.stats["entities"], 1)
        self.assertEqual(result.entities[0].members[1].method, "rule:same_source_duplicate")

    def test_chain_branches_stay_separate(self):
        records = [
            rec("overture_places", f"g{i}", "Magnum", LAT + i * 400 * M_LAT, LON,
                category="retail.supermarket")
            for i in range(4)
        ]
        self.assertEqual(resolve(records, PRIORITY).stats["entities"], 4)

    def test_blocking_across_grid_cells(self):
        # Points straddle a grid boundary; neighbour lookup must still compare them.
        a = rec("osm", "n1", "Shokoladnitsa", LAT, LON, category="food.cafe")
        b = rec("overture_places", "g1", "Shokoladnitsa", LAT, LON + 0.0008,
                category="food.cafe")  # ~65 m east
        self.assertEqual(resolve([a, b], PRIORITY).stats["entities"], 1)

    def test_empty(self):
        self.assertEqual(resolve([]).entities, [])


if __name__ == "__main__":
    unittest.main()
