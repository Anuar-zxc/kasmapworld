"""Scoring v0 on a synthetic axial hex grid (no h3 dependency)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kasmap_api import scoring  # noqa: E402

DIRS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]


def ring(cell, k):
    q, r = map(int, cell.split(","))
    if k == 0:
        return [cell]
    out = []
    q, r = q + DIRS[4][0] * k, r + DIRS[4][1] * k
    for d in range(6):
        for _ in range(k):
            out.append(f"{q},{r}")
            q, r = q + DIRS[d][0], r + DIRS[d][1]
    return out


def places():
    p = []
    # busy centre 0,0 with a university and 2 coffee shops
    p += [("0,0", "food.restaurant")] * 30 + [("0,0", "education.university")]
    p += [("0,0", "food.cafe.coffee_shop")] * 2
    # crowded street 6,0: lots of activity and 9 coffee shops
    p += [("6,0", "food.restaurant")] * 30 + [("6,0", "food.cafe.coffee_shop")] * 9
    # quiet suburb 12,0
    p += [("12,0", "retail.convenience")] * 2
    return p


class ScoringTests(unittest.TestCase):
    def test_ring_sizes(self):
        self.assertEqual(len(ring("0,0", 1)), 6)
        self.assertEqual(len(ring("0,0", 2)), 12)

    def test_ranking_and_reasons(self):
        s = scoring.score_city(places(), "food.cafe.coffee_shop", ring)
        by = {c.h3: c for c in s.cells}
        self.assertGreater(by["0,0"].score, by["6,0"].score)     # crowded loses
        self.assertGreater(by["0,0"].score, by["12,0"].score)    # quiet loses
        self.assertTrue(all(0 <= c.score <= 100 for c in s.cells))
        signs = [w["sign"] for w in by["6,0"].why]
        self.assertIn("-", signs)
        self.assertEqual(by["0,0"].competitors, 2.0)

    def test_parent_category_counts_half(self):
        self.assertEqual(scoring.competitor_weight("food.cafe.coffee_shop", "food.cafe"), 0.5)
        self.assertEqual(scoring.competitor_weight("health.pharmacy", "health.clinic"), 0.0)
        self.assertEqual(scoring.competitor_weight("health.pharmacy", "health.pharmacy"), 1.0)

    def test_top_spreads_out(self):
        s = scoring.score_city(places(), "food.cafe.coffee_shop", ring)
        top = s.top(3, ring=ring)
        cells = [c.h3 for c in top]
        for i, a in enumerate(cells):
            for b in cells[i + 1:]:
                near = set(ring(a, 0)) | set(ring(a, 1)) | set(ring(a, 2))
                self.assertNotIn(b, near)

    def test_empty(self):
        self.assertEqual(scoring.score_city([], "food.bar", ring).cells, [])


if __name__ == "__main__":
    unittest.main()
