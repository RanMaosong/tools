import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from image_core import (LayoutOptions, Panel, Shape, annotate, arrow_geometry, arrow_polygon, export_batch,
                        import_panels, layout_plan, load_image, make_panels,
                        render_layout, save_lossless)


class ImageCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, name="source.png", size=(1200, 600)):
        image = Image.frombytes("RGB", size, bytes((i * 17 + i // 7) % 256 for i in range(size[0] * size[1] * 3)))
        path = self.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        return path, image

    def test_roi_uses_source_pixels_and_scales_for_each_image(self):
        path, source = self.source()
        path2, source2 = self.source("second.png", (600, 300))
        rectangle = Shape("rectangle", (.1, .2, .6, .8))
        panels = list(make_panels([path, path2], [rectangle], source.size))
        self.assertEqual(panels[1].image.size, (600, 360))
        self.assertEqual(panels[1].image.tobytes(), source.crop((120, 120, 720, 480)).tobytes())
        self.assertEqual(panels[3].image.tobytes(), source2.crop((60, 60, 360, 240)).tobytes())
        self.assertEqual(panels[0].image.getpixel((0, 0)), source.getpixel((0, 0)))

    def test_extra_images_align_with_original_row_and_resize_exactly_once(self):
        _, source = self.source(size=(50, 30))
        panels = [Panel("o", "0", "original", "original", Image.new("RGB", (80, 40))),
                  Panel("r", "0", "roi-0", "ROI", Image.new("RGB", (20, 10))),
                  Panel("e", "extra:1", "extra", "residual", source)]
        options = LayoutOptions(mode="rows", columns=2, panel_width=160)
        plan, _ = layout_plan(panels, options)
        original = next(p for p in plan if p.panel.kind == "original")
        extra = next(p for p in plan if p.panel.kind == "extra")
        self.assertEqual(extra.y, original.y)
        self.assertEqual((extra.width, extra.height), (160, 80))
        before = source.tobytes()
        render_layout(panels, options, (100, 100))
        rendered, _, _ = render_layout(panels, options)
        crop = rendered.crop((extra.x, extra.y, extra.x+160, extra.y+80))
        self.assertEqual(crop.convert("RGB").tobytes(), source.resize((160, 80), Image.Resampling.LANCZOS).tobytes())
        self.assertEqual(source.tobytes(), before)
        extras_only = [panels[-1], Panel("e2", "extra:2", "extra", "other", Image.new("RGB", (200, 70)))]
        plan, _ = layout_plan(extras_only, LayoutOptions())
        self.assertTrue(all((p.width, p.height) == (50, 30) for p in plan))

    def test_arrows_only_in_roi_and_drawing_never_mutates_source(self):
        path, source = self.source(size=(100, 80))
        before = source.tobytes()
        shapes = [Shape("rectangle", (.1, .1, .9, .9)), Shape("arrow", (.3, .3, .7, .7), "#FF0000", 3, 10)]
        panels = list(make_panels([path], shapes, source.size))
        self.assertEqual(source.tobytes(), before)
        self.assertEqual(panels[0].image.getpixel((50, 40)), source.getpixel((50, 40)))
        self.assertEqual(panels[1].image.getpixel((40, 32)), (255, 0, 0))
        self.assertEqual(panels[1].image.getpixel((0, 0)), source.getpixel((10, 8)))
        annotate(source, shapes, source.size, "rectangle")
        self.assertEqual(source.tobytes(), before)

    def test_original_arrow_tip_head_and_short_arrow_geometry(self):
        # Golden coordinates from the original arrow_coordinates implementation.
        cases = [((0, 0, 40, 0), [(43, 0), (20, 10), (30, 0), (20, -10)]),
                 ((0, 0, 4, 0), [(7, 0), (-16, 10), (-6, 0), (-16, -10)]),
                 ((0, 0, 0, 40), [(0, 43), (-10, 20), (0, 30), (10, 20)]),
                 ((40, 0, 0, 0), [(-3, 0), (20, -10), (10, 0), (20, 10)]),
                 ((0, 40, 0, 0), [(0, -3), (10, 20), (0, 10), (-10, 20)])]
        for box, expected in cases:
            for point, original in zip(arrow_polygon(box, 20), expected):
                for actual, value in zip(point, original):
                    self.assertAlmostEqual(actual, value)

    def test_preview_arrow_geometry_is_scaled_to_source_without_shifting(self):
        shape = Shape("arrow", (.1, .5, .5, .5), "#FF0000", 3, 20, (800, 400))
        preview, width = arrow_geometry(shape, (800, 400), (1600, 800))
        self.assertEqual(preview, [(403, 200), (380, 210), (390, 200), (380, 190)])
        self.assertEqual(width, 1.5)
        source, width = arrow_geometry(shape, (1600, 800), (1600, 800))
        self.assertEqual(source, [(x*2, y*2) for x, y in preview])
        self.assertEqual(width, 3)
        resized, _ = arrow_geometry(shape, (400, 200), (1600, 800))
        self.assertEqual(resized, [(x/2, y/2) for x, y in preview])

    def test_rectangle_and_arrow_export_strokes_have_equal_pixel_thickness(self):
        reference = (200, 100)
        rectangle = Shape("rectangle", (.1, .1, .7, .9), "#FF0000", 3)
        arrow = Shape("arrow", (.1, .5, .8, .5), "#FF0000", 3, 20, (800, 400))
        for scale in (.5, 1, 2):
            size = (round(reference[0]*scale), round(reference[1]*scale))
            source = Image.new("RGB", size, "white")
            framed = annotate(source, [rectangle], reference, "rectangle")
            pointed = annotate(source, [arrow], reference, "arrow")
            expected = max(1, round(3*scale))
            left = round(size[0]*.1)
            rectangle_pixels = sum(framed.getpixel((x, round(size[1]*.3))) == (255, 0, 0)
                                   for x in range(left, left+expected*2))
            arrow_pixels = sum(pointed.getpixel((round(size[0]*.3), y)) == (255, 0, 0)
                               for y in range(size[1]))
            with self.subTest(scale=scale):
                self.assertEqual(rectangle_pixels, expected)
                self.assertEqual(arrow_pixels, expected)

    def test_png_and_tiff_roundtrip_preserve_pixels_and_alpha(self):
        image = Image.frombytes("RGBA", (10, 10), bytes(range(200)) * 2)
        image.info["icc_profile"] = b"test-profile"
        for suffix in (".png", ".tif"):
            path = self.path / ("out" + suffix)
            save_lossless(image, path, 600)
            with Image.open(path) as loaded:
                self.assertEqual(loaded.size, image.size)
                self.assertEqual(loaded.tobytes(), image.tobytes())
                self.assertEqual(loaded.info["icc_profile"], b"test-profile")
                self.assertAlmostEqual(loaded.info["dpi"][0], 600, delta=.1)

    def test_exports_avoid_collisions_and_coordinates_do_not_accumulate(self):
        one, _ = self.source("a/Result.png", (20, 10))
        two, _ = self.source("b/result.png", (20, 10))
        shapes = [Shape("rectangle", (.1, .1, .9, .9))]
        output = self.path / "exports"
        files = export_batch([str(one), str(two)], shapes, (20, 10), output)
        self.assertEqual(len(files), 4)
        self.assertEqual(len(set(files)), 4)
        self.assertTrue((output / "rectange/Result.png").exists())
        export_batch([str(one), str(two)], shapes, (20, 10), output)
        coordinates = json.loads((output / "coordinates.json").read_text(encoding="utf-8"))
        self.assertEqual(len(coordinates["shapes"]), 1)

    def panels(self):
        return [Panel(f"{i}:{kind}", str(i), kind, f"方法{i}", Image.new("RGB", size, color))
                for i in range(5) for kind, size, color in (("original", (80+i*2, 40), "blue"), ("roi-0", (20, 15), "red"), ("roi-1", (30, 10), "green"))]

    def test_all_layout_modes_place_every_image_without_overlap(self):
        for mode in ("relative", "below", "right", "rows", "free"):
            with self.subTest(mode=mode):
                panels = self.panels()
                options = LayoutOptions(mode=mode, columns=3, labels=True)
                plan, (width, height) = layout_plan(panels, options)
                self.assertEqual(len(plan), len(panels))
                for item in plan:
                    self.assertGreaterEqual(item.x, 0)
                    self.assertLessEqual(item.x+item.width, width)
                    self.assertLessEqual(item.y+item.height+options.label_size+12, height)
                for i, a in enumerate(plan):
                    for b in plan[i+1:]:
                        self.assertTrue(a.x+a.width <= b.x or b.x+b.width <= a.x or
                                        a.y+a.height+36 <= b.y or b.y+b.height+36 <= a.y)

    def test_each_roi_can_be_placed_above_below_left_or_right(self):
        panels = [Panel("o", "0", "original", "original", Image.new("RGB", (80, 40)))]
        settings = {}
        for i, direction in enumerate(("top", "bottom", "left", "right")):
            kind = f"roi-{i}"
            panels.append(Panel(kind, "0", kind, kind, Image.new("RGB", (20, 10))))
            settings[kind] = dict(width=40, height=20, relative_to="original", position=direction)
        plan, size = layout_plan(panels, LayoutOptions(roi_settings=settings, gap=5))
        placements = {p.panel.kind: p for p in plan}
        original = placements["original"]
        self.assertLessEqual(placements["roi-0"].y+20+5, original.y)
        self.assertGreaterEqual(placements["roi-1"].y, original.y+40+5)
        self.assertLessEqual(placements["roi-2"].x+40+5, original.x)
        self.assertGreaterEqual(placements["roi-3"].x, original.x+80+5)
        for p in plan:
            self.assertGreaterEqual(p.x, 0)
            self.assertGreaterEqual(p.y, 0)
            self.assertLessEqual(p.x+p.width, size[0])
            self.assertLessEqual(p.y+p.height, size[1])

    def test_roi_chain_and_same_side_siblings_do_not_overlap(self):
        panels = self.panels()[:3]
        settings = {"roi-0": dict(width=40, height=30, relative_to="original", position="bottom"),
                    "roi-1": dict(width=60, height=20, relative_to="roi-0", position="right")}
        plan, _ = layout_plan(panels, LayoutOptions(roi_settings=settings, gap=5))
        lookup = {p.panel.kind: p for p in plan}
        self.assertGreaterEqual(lookup["roi-1"].x, lookup["roi-0"].x+40+5)
        settings["roi-1"]["relative_to"] = "original"
        settings["roi-1"]["position"] = "bottom"
        plan, _ = layout_plan(panels, LayoutOptions(roi_settings=settings, gap=5))
        lookup = {p.panel.kind: p for p in plan}
        self.assertGreaterEqual(lookup["roi-1"].y, lookup["roi-0"].y+30+5)

    def test_manual_roi_cell_preserves_aspect_and_pixels_without_captions(self):
        _, source = self.source(size=(20, 10))
        panels = [Panel("o", "0", "original", "hidden name", Image.new("RGB", (80, 40))),
                  Panel("roi", "0", "roi-0", "hidden ROI name", source)]
        options = LayoutOptions(gap=0, margin=0, roi_settings={"roi-0":
                                dict(width=40, height=40, relative_to="original", position="bottom")})
        plan, size = layout_plan(panels, options)
        roi = next(p for p in plan if p.panel.kind == "roi-0")
        rendered, _, _ = render_layout(panels, options)
        self.assertFalse(options.labels)
        self.assertEqual(size, (80, 80))
        self.assertEqual(rendered.crop((roi.x, roi.y+10, roi.x+40, roi.y+30)).convert("RGB").tobytes(),
                         source.resize((40, 20), Image.Resampling.NEAREST).tobytes())
        self.assertEqual(rendered.getpixel((roi.x, roi.y)), (255, 255, 255, 255))

    def test_roi_relative_cycles_and_shrinking_are_rejected(self):
        settings = {"roi-0": dict(width=40, height=30, relative_to="roi-1", position="bottom"),
                    "roi-1": dict(width=60, height=20, relative_to="roi-0", position="right")}
        with self.assertRaisesRegex(ValueError, "循环"):
            LayoutOptions(roi_settings=settings).validate()
        settings["roi-0"]["relative_to"] = "original"
        settings["roi-0"]["width"] = 19
        with self.assertRaisesRegex(ValueError, "不能小于"):
            render_layout(self.panels()[:3], LayoutOptions(roi_settings=settings))

    def test_manual_horizontal_and_vertical_distances_are_exact_in_all_directions(self):
        panels = self.panels()[:2]
        for x_direction, x_sign in (("left", -1), ("right", 1)):
            for y_direction, y_sign in (("up", -1), ("down", 1)):
                settings = {"roi-0": dict(width=40, height=30, x_direction=x_direction, x_distance=20,
                                           y_direction=y_direction, y_distance=15)}
                plan, size = layout_plan(panels, LayoutOptions(roi_settings=settings, gap=99))
                origin = next(p for p in plan if p.panel.kind == "original")
                roi = next(p for p in plan if p.panel.kind == "roi-0")
                self.assertEqual((roi.x-origin.x, roi.y-origin.y), (x_sign*20, y_sign*15))
                for p in plan:
                    self.assertGreaterEqual(p.x, 0)
                    self.assertGreaterEqual(p.y, 0)
                    self.assertLessEqual(p.x+p.width, size[0])
                    self.assertLessEqual(p.y+p.height, size[1])

    def test_explicit_overlapping_rois_are_not_automatically_moved(self):
        settings = {kind: dict(width=40, height=30, x_direction="right", x_distance=5,
                               y_direction="down", y_distance=6) for kind in ("roi-0", "roi-1")}
        plan, _ = layout_plan(self.panels()[:3], LayoutOptions(roi_settings=settings))
        origin = next(p for p in plan if p.panel.kind == "original")
        for roi in (p for p in plan if p.panel.kind != "original"):
            self.assertEqual((roi.x-origin.x, roi.y-origin.y), (5, 6))
        settings["roi-0"]["x_distance"] = -1
        with self.assertRaisesRegex(ValueError, "距离"):
            LayoutOptions(roi_settings=settings).validate()

    def test_roi_edge_distances_reference_corresponding_original_edges(self):
        panels = [Panel("o", "0", "original", "original", Image.new("RGB", (400, 300))),
                  Panel("r", "0", "roi-0", "roi", Image.new("RGB", (100, 80)))]
        for x_edge, expected_x in (("left", 20), ("right", 280)):
            for y_edge, expected_y in (("top", 10), ("bottom", 210)):
                setting = dict(width=100, height=80, x_edge=x_edge, x_distance=20,
                               y_edge=y_edge, y_distance=10)
                plan, _ = layout_plan(panels, LayoutOptions(roi_settings={"roi-0": setting}, gap=99))
                original = next(p for p in plan if p.panel.kind == "original")
                roi = next(p for p in plan if p.panel.kind == "roi-0")
                self.assertEqual((roi.x-original.x, roi.y-original.y), (expected_x, expected_y))
                x_gap = roi.x-original.x if x_edge == "left" else original.x+original.width-roi.x-roi.width
                y_gap = roi.y-original.y if y_edge == "top" else original.y+original.height-roi.y-roi.height
                self.assertEqual((x_gap, y_gap), (20, 10))

    def test_edge_alignment_tracks_resized_original_and_accepts_negative_margins(self):
        panels = [Panel("o", "0", "original", "original", Image.new("RGB", (400, 300))),
                  Panel("r", "0", "roi-0", "roi", Image.new("RGB", (100, 80)))]
        setting = dict(width=100, height=80, x_edge="right", x_distance=0,
                       y_edge="bottom", y_distance=0)
        plan, _ = layout_plan(panels, LayoutOptions(panel_width=800, roi_settings={"roi-0": setting}))
        original, roi = plan
        self.assertEqual(original.x+800, roi.x+100)
        self.assertEqual(original.y+600, roi.y+80)
        setting.update(x_distance=-10, y_distance=-15)
        plan, size = layout_plan(panels, LayoutOptions(roi_settings={"roi-0": setting}))
        original, roi = plan
        self.assertEqual(roi.x+100, original.x+400+10)
        self.assertEqual(roi.y+80, original.y+300+15)
        self.assertLessEqual(roi.x+roi.width, size[0])
        self.assertLessEqual(roi.y+roi.height, size[1])

    def test_native_layout_pixels_and_preview_do_not_change_export(self):
        path, source = self.source(size=(101, 57))
        panel = Panel("p", "p", "original", "图", load_image(path))
        options = LayoutOptions(mode="free", labels=False, margin=3, roi_scale=1)
        exported, size, _ = render_layout([panel], options)
        self.assertEqual(exported.crop((3, 3, 104, 60)).convert("RGB").tobytes(), source.tobytes())
        preview, _, _ = render_layout([panel], options, (20, 20))
        self.assertLessEqual(preview.width, 20)
        after, _, _ = render_layout([panel], options)
        self.assertEqual(exported.tobytes(), after.tobytes())
        self.assertEqual(size, after.size)

    def test_nearest_roi_enlargement_is_exact_pixel_replication(self):
        _, source = self.source(size=(9, 7))
        panel = Panel("roi", "0", "roi-0", "ROI", source)
        enlarged, _, _ = render_layout([panel], LayoutOptions(labels=False, margin=0, roi_scale=3))
        for y in range(7):
            for x in range(9):
                for dy in range(3):
                    for dx in range(3):
                        self.assertEqual(enlarged.getpixel((x*3+dx, y*3+dy))[:3], source.getpixel((x, y)))

    def test_shrinking_and_excessive_canvas_are_rejected(self):
        panels = self.panels()
        with self.assertRaisesRegex(ValueError, "不能小于"):
            render_layout(panels, LayoutOptions(panel_width=10))
        with self.assertRaisesRegex(ValueError, "超过"):
            render_layout(panels, LayoutOptions(panel_width=10000))
        preview, _, _ = render_layout(panels, LayoutOptions(panel_width=10000), (500, 500))
        self.assertLessEqual(preview.width, 500)

    def test_high_bit_depth_is_rejected_without_modifying_source(self):
        for extension in (".png", ".tif"):
            path = self.path / ("16bit"+extension)
            Image.new("I;16", (20, 10), 32000).save(path)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                load_image(path)
            self.assertEqual(path.read_bytes(), before)

    def test_exif_orientation_is_applied_before_coordinates(self):
        image = Image.new("RGB", (12, 8), "red")
        exif = Image.Exif()
        exif[274] = 6
        path = self.path / "orientation.png"
        image.save(path, exif=exif)
        self.assertEqual(load_image(path).size, (8, 12))
        self.assertEqual(load_image(path, 90).size, (12, 8))

    def test_import_recognizes_existing_output_folders(self):
        files = [self.source(name, (40, 30))[0] for name in ("rectange/A.png", "roi-0/A.png", "rectange/B.png", "roi-0/B.png")]
        panels = list(import_panels(files))
        self.assertEqual(panels[0].group, panels[1].group)
        self.assertNotEqual(panels[0].group, panels[2].group)
        self.assertEqual(panels[1].kind, "roi-0")

    def test_mixed_color_profiles_do_not_silently_change_colors(self):
        panels = self.panels()[:2]
        panels[0].image.info["icc_profile"] = b"profile"
        with self.assertRaisesRegex(ValueError, "ICC"):
            render_layout(panels, LayoutOptions())


if __name__ == "__main__":
    unittest.main()
