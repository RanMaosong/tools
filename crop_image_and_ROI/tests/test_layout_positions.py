import unittest

from PIL import Image

from image_core import LayoutOptions, Panel, layout_plan, move_panel_to, render_layout


class LayoutPositionTests(unittest.TestCase):
    def setUp(self):
        self.panels = [Panel("a", "a", "original", "a", Image.new("RGB", (100, 50), "red")),
                       Panel("b", "b", "original", "b", Image.new("RGB", (100, 50), "blue"))]
        self.options = LayoutOptions(mode="free", columns=2, margin=0, gap=10)

    def positions(self, options):
        plan, size = layout_plan(self.panels, options)
        return {p.panel.key: (p.x, p.y) for p in plan}, size

    def test_positive_positions_and_repeated_negative_origin_changes_are_exact(self):
        options = move_panel_to(self.panels, self.options, "b", 20, 70)
        self.assertEqual(self.positions(options), ({"a": (0, 0), "b": (20, 70)}, (120, 120)))
        options = move_panel_to(self.panels, options, "a", -30, -10)
        self.assertEqual(self.positions(options), ({"a": (0, 0), "b": (50, 80)}, (150, 130)))
        # The new origin must be committed, so moving a former negative anchor
        # to a positive coordinate does not reveal a hidden previous offset.
        options = move_panel_to(self.panels, options, "a", 50, 25)
        self.assertEqual(self.positions(options)[0], {"a": (50, 25), "b": (50, 80)})
        options = move_panel_to(self.panels, options, "b", -10, -20)
        self.assertEqual(self.positions(options)[0], {"a": (60, 45), "b": (0, 0)})

    def test_negative_coordinate_rebases_both_axes_to_all_images_top_left(self):
        options = LayoutOptions(mode="free", columns=2, margin=7, gap=10)
        options = move_panel_to(self.panels, options, "b", -20, 70)
        self.assertEqual(self.positions(options)[0], {"a": (27, 0), "b": (0, 63)})

    def test_manual_positions_work_in_every_automatic_layout_and_preserve_pixels(self):
        before = [p.image.tobytes() for p in self.panels]
        for mode in ("relative", "below", "right", "rows", "free"):
            with self.subTest(mode=mode):
                options = LayoutOptions(mode=mode, columns=2, margin=0, gap=10)
                options = move_panel_to(self.panels, options, "b", -15, -25)
                plan, size = layout_plan(self.panels, options)
                positions = {p.panel.key: p for p in plan}
                self.assertEqual((positions["b"].x, positions["b"].y), (0, 0))
                rendered, _, _ = render_layout(self.panels, options)
                blue = positions["b"]
                self.assertEqual(rendered.crop((blue.x, blue.y, blue.x+100, blue.y+50)).convert("RGB").tobytes(), before[1])
                self.assertEqual(size, rendered.size)
        self.assertEqual([p.image.tobytes() for p in self.panels], before)

    def test_original_moves_all_its_rois_and_keeps_individual_roi_adjustments(self):
        panels = [self.panels[0], Panel("a-roi-0", "a", "roi-0", "roi0", Image.new("RGB", (20, 10))),
                  Panel("a-roi-1", "a", "roi-1", "roi1", Image.new("RGB", (10, 15))), self.panels[1]]
        for mode in ("relative", "below", "right", "rows", "free"):
            with self.subTest(mode=mode):
                options = LayoutOptions(mode=mode, columns=2, margin=0, gap=10)
                options = move_panel_to(panels, options, "a-roi-0", 15, 82)
                before = {p.panel.key: (p.x, p.y) for p in layout_plan(panels, options)[0]}
                options = move_panel_to(panels, options, "a", before["a"][0]+35, before["a"][1]+25)
                after = {p.panel.key: (p.x, p.y) for p in layout_plan(panels, options)[0]}
                for key, (x, y) in before.items():
                    self.assertEqual(after[key], (x+35, y+25) if key.startswith("a") else (x, y))
                # Negative linked moves rebase the canvas, preserving every
                # ROI's relative position to its original after the rebase.
                options = move_panel_to(panels, options, "a", -100, -100)
                negative = {p.panel.key: (p.x, p.y) for p in layout_plan(panels, options)[0]}
                self.assertEqual(min(p[0] for p in negative.values()), 0)
                self.assertEqual(min(p[1] for p in negative.values()), 0)
                for key in ("a-roi-0", "a-roi-1"):
                    self.assertEqual((negative[key][0]-negative["a"][0], negative[key][1]-negative["a"][1]),
                                     (after[key][0]-after["a"][0], after[key][1]-after["a"][1]))

    def test_transparent_placeholder_preserves_underlying_rgba_regardless_of_order(self):
        source = Image.new("RGBA", (100, 50), (29, 43, 71, 123))
        original = Panel("source", "source", "original", "source", source)
        blank = Panel("blank", "blank", "extra", "blank", Image.new("RGB", (1, 1)), blank_size=(512, 512))
        for panels in ([original, blank], [blank, original]):
            with self.subTest(blank_last=panels[-1] is blank):
                options = LayoutOptions(mode="free", columns=2, margin=0, gap=10)
                at = next(p for p in layout_plan(panels, options)[0] if p.panel.key == "source")
                options = move_panel_to(panels, options, "blank", at.x, at.y)
                actual, _, hits = render_layout(panels, options)
                self.assertEqual(actual.crop((at.x, at.y, at.x+100, at.y+50)).tobytes(), source.tobytes())
                self.assertIn("blank", [key for key, box in hits])

    def test_position_validation_rejects_noninteger_or_malformed_values(self):
        for offsets in ([], {"a": [1, 2]}, {"a": {"x": 1}}, {"a": {"x": True, "y": 0}},
                        {"a": {"x": 1.2, "y": 0}}):
            with self.subTest(offsets=offsets), self.assertRaises(ValueError):
                LayoutOptions(panel_offsets=offsets).validate()
        with self.assertRaises(ValueError):
            move_panel_to(self.panels, self.options, "missing", 0, 0)


if __name__ == "__main__":
    unittest.main()
