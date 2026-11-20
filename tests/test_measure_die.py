"""合成刀模单元测试（不含任何客户文件）。运行：python -m unittest discover -s tests -v"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

import pymupdf

spec = importlib.util.spec_from_file_location("measure_die", Path(__file__).parents[1] / "scripts/measure_die.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

MM = 72 / 25.4
RED, GREEN, BLUE = (0.8, 0.1, 0.1), (0.3, 0.7, 0.3), (0.1, 0.2, 0.9)


def tray(page, ox, oy, W, L, H, g, Hi, window=None, bleed=None):
    """在 (ox,oy) 画一个四边双层内折的十字形托盘（单位 mm）。"""
    ext = H + g + Hi
    bx0, by0 = ox + ext, oy + ext
    bx1, by1 = bx0 + W, by0 + L
    pts = [(bx0, oy), (bx1, oy), (bx1, by0), (bx1 + ext, by0), (bx1 + ext, by1), (bx1, by1),
           (bx1, by1 + ext), (bx0, by1 + ext), (bx0, by1), (ox, by1), (ox, by0), (bx0, by0), (bx0, oy)]
    sh = page.new_shape()
    for a, b in zip(pts, pts[1:]):
        sh.draw_line(pymupdf.Point(a[0] * MM, a[1] * MM), pymupdf.Point(b[0] * MM, b[1] * MM))
    sh.finish(color=RED, width=0.5)
    if window:
        wx, wy, ww, wl = window
        sh.draw_rect(pymupdf.Rect((bx0 + wx) * MM, (by0 + wy) * MM, (bx0 + wx + ww) * MM, (by0 + wy + wl) * MM))
        sh.finish(color=RED, width=0.5)
    for d in (0, H, H + g):  # 竖直压痕（左右）
        for x in (bx0 - d, bx1 + d):
            sh.draw_line(pymupdf.Point(x * MM, by0 * MM), pymupdf.Point(x * MM, by1 * MM))
        for y in (by0 - d, by1 + d):
            sh.draw_line(pymupdf.Point(bx0 * MM, y * MM), pymupdf.Point(bx1 * MM, y * MM))
    sh.finish(color=GREEN, width=0.5)
    if bleed:
        sh.draw_rect(pymupdf.Rect((ox - bleed) * MM, (oy - bleed) * MM, (bx1 + ext + bleed) * MM, (by1 + ext + bleed) * MM))
        sh.finish(color=BLUE, width=0.3)
    sh.commit()


def make_pdf(path, bleed=None, window=None):
    doc = pymupdf.open()
    page = doc.new_page(width=700 * MM, height=500 * MM)
    tray(page, 20, 20, 70, 245, 55, 4, 53, window=window, bleed=bleed)
    tray(page, 360, 30, 65, 235, 51, 3.7, 49.4)
    doc.save(str(path))


class MeasureDie(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pdf = Path(self.tmp.name) / "die.pdf"

    def test_lid_and_base(self):
        make_pdf(self.pdf)
        r = m.measure(self.pdf, cut="#cc1a1a", crease=["#4db24d"])
        self.assertEqual(r["piece_count"], 2)
        self.assertEqual(r["colors"]["cut"], "#cc1a1a")
        lid, base = r["pieces"]
        self.assertAlmostEqual(lid["bottom_panel_mm"]["width"], 70, delta=0.05)
        self.assertAlmostEqual(lid["bottom_panel_mm"]["length"], 245, delta=0.05)
        self.assertAlmostEqual(lid["wall_height_mm"], 55, delta=0.05)
        self.assertEqual(lid["double_wall_sides"], 4)
        self.assertAlmostEqual(lid["sides"]["left"]["double_crease_gap_mm"], 4, delta=0.05)
        self.assertAlmostEqual(lid["sides"]["left"]["inner_wall_mm"], 53, delta=0.05)
        self.assertAlmostEqual(base["wall_height_mm"], 51, delta=0.05)
        a = r["assembly"]
        self.assertEqual(a["lid_piece"], 1)
        self.assertAlmostEqual(a["clearance_mm"]["width"], 5, delta=0.05)
        self.assertAlmostEqual(a["clearance_mm"]["length"], 10, delta=0.05)
        self.assertEqual(a["closed_outer_mm_approx"], [245.0, 70.0, 55.0])
        self.assertFalse(r["bleed_line"]["found"])

    def test_claimed_mismatch_and_match(self):
        make_pdf(self.pdf)
        bad = m.measure(self.pdf, cut="#cc1a1a", crease=["#4db24d"], claimed="200x47x235")
        self.assertFalse(bad["claimed_size_check"]["match"])
        good = m.measure(self.pdf, cut="#cc1a1a", crease=["#4db24d"], claimed="245x70x55")
        self.assertTrue(good["claimed_size_check"]["match"])

    def test_bleed_and_window(self):
        make_pdf(self.pdf, bleed=3, window=(10, 30, 50, 120))
        r = m.measure(self.pdf, cut="#cc1a1a", crease=["#4db24d"])
        self.assertTrue(r["bleed_line"]["found"])
        holes = r["pieces"][0]["internal_cut_outlines"]
        self.assertEqual(len(holes), 1)
        self.assertAlmostEqual(holes[0]["size_mm"][0], 50, delta=0.05)
        self.assertAlmostEqual(holes[0]["size_mm"][1], 120, delta=0.05)

    def test_cli_exit_codes(self):
        make_pdf(self.pdf)
        out = Path(self.tmp.name) / "r.json"
        self.assertEqual(m.main([str(self.pdf), "--json", str(out), "--claimed", "245x70x55"]), 1)  # 无出血线→提示
        self.assertTrue(out.exists())
        self.assertEqual(m.main([str(Path(self.tmp.name) / "missing.pdf")]), 2)

    def test_mixed_colors_require_both_roles(self):
        make_pdf(self.pdf, bleed=3)
        for kwargs in ({}, {"cut": "#cc1a1a"}, {"crease": ["#4db24d"]}):
            r = m.measure(self.pdf, claimed="245x70x55", **kwargs)
            self.assertEqual(r["measurement_status"], "provisional")
            self.assertFalse(r["colors"]["roles_explicit"])
            self.assertIsNone(r["assembly"])
            self.assertIsNone(r["claimed_size_check"]["match"])
            self.assertEqual(r["bleed_line"]["status"], "unknown")
            self.assertIn("不能据此判定", m.to_markdown(r))

    def test_bleed_candidate_is_not_confirmation(self):
        make_pdf(self.pdf, bleed=3)
        r = m.measure(self.pdf, cut="#cc1a1a", crease=["#4db24d"])
        self.assertEqual(r["bleed_line"]["status"], "candidate")
        self.assertTrue(r["warnings"])
        self.assertIn("尚不能确认", m.to_markdown(r))

    def test_unmatched_or_conflicting_colors_rejected(self):
        make_pdf(self.pdf)
        for cut, crease in [("#ff0000", ["#4db24d"]),
                            ("#cc1a1a", ["#00ff00"]),
                            ("#cc1a1a", ["#cc1a1a"])]:
            with self.assertRaises(ValueError):
                m.measure(self.pdf, cut=cut, crease=crease)

    def test_no_candidate_does_not_prove_no_bleed(self):
        make_pdf(self.pdf)
        r = m.measure(self.pdf, cut="#cc1a1a", crease=["#4db24d"])
        self.assertEqual(r["bleed_line"]["status"], "unknown")
        self.assertNotIn("刀模里没有出血线", r["warnings"])
        self.assertIn("不能据此判定", r["bleed_line"]["note"])

    @unittest.skipUnless(os.environ.get("DIE_PDF"), "设置 DIE_PDF=真实刀模路径才跑")
    def test_real_file_smoke(self):
        r = m.measure(os.environ["DIE_PDF"])
        self.assertGreaterEqual(r["piece_count"], 1)


if __name__ == "__main__":
    unittest.main()
