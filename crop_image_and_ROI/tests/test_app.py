from pathlib import Path
import json
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
import numpy as np
from high_depth import Image16
from crop_image import ImageCropper, MODES, HORIZONTAL_EDGES, VERTICAL_EDGES
from image_core import Shape, arrow_geometry, layout_plan, make_panels, render_layout


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.files = []
        for i in range(3):
            path = self.path / f"method-{i}.png"
            Image.new("RGB", (200, 100), (i*60, 70, 90)).save(path)
            self.files.append(str(path))
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = ImageCropper(self.root)
        self.errors = []
        self.app._fail = self.errors.append
        with patch("crop_image.filedialog.askopenfilenames", return_value=self.files):
            self.app.open_files()
        self.root.update()

    def tearDown(self):
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.temp.cleanup()

    def finish(self):
        deadline = time.monotonic()+10
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy)
        self.assertFalse(self.errors, self.errors)

    def generate(self):
        self.app.shapes = [Shape("rectangle", (.1, .2, .6, .8)), Shape("arrow", (.2, .3, .4, .5))]
        self.app.generate_panels()
        self.finish()

    def append_test_extras(self, count=3):
        paths = []
        for i in range(count):
            path = self.path / f"batch-extra-{i}.png"
            Image.new("RGB", (160, 80), (10+i, 30+i, 70)).save(path)
            paths.append(str(path))
        with patch("crop_image.filedialog.askopenfilenames", return_value=paths):
            self.app.append_images()
        self.finish()
        return [p for p in self.app.panels if p.kind == "extra"]

    def test_16bit_editor_preview_rotation_layout_export_and_project_restore(self):
        app = self.app
        pixels = ((np.arange(40*30, dtype=np.uint32)*53+1) % 65536).astype(np.uint16).reshape(30, 40)
        path = self.path / "gt.png"
        Image.fromarray(pixels).save(path)
        original_bytes = path.read_bytes()
        app._replace_files([str(path)])
        self.assertIsInstance(app.image, Image16)
        np.testing.assert_array_equal(app.image.pixels[:, :, 0], pixels)
        app.draw_image()
        self.assertIsNotNone(app.photo)
        app.rotate(90)
        np.testing.assert_array_equal(app.image.pixels[:, :, 0], np.rot90(pixels))
        self.generate()
        self.assertTrue(all(isinstance(panel.image, Image16) for panel in app.panels))
        app.update_preview()
        expected, size, _ = render_layout(app.panels, app.options())
        output = self.path / "16bit-layout.png"
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(output)):
            app.export_layout()
        self.finish()
        self.assertEqual(output.read_bytes()[24], 16)
        from image_core import load_image
        saved = load_image(output)
        self.assertEqual(saved.size, size)
        np.testing.assert_array_equal(saved.pixels, expected.pixels)
        project = self.path / "16bit-project.json"
        before = [p.image.tobytes() for p in app.panels]
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(project)):
            app.save_project()
        with patch("crop_image.filedialog.askopenfilename", return_value=str(project)):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual([p.image.tobytes() for p in app.panels], before)
        self.assertEqual(path.read_bytes(), original_bytes)

    def test_reverse_drawing_and_resize_keep_normalized_coordinates(self):
        app = self.app
        app.display_size, app.offset = (800, 400), (10, 20)
        app._mouse_down(SimpleNamespace(x=650, y=340))
        app._mouse_up(SimpleNamespace(x=170, y=100))
        self.assertEqual(len(app.shapes), 1)
        self.assertEqual(app.shapes[0].box, (.2, .2, .8, .8))
        app.draw_image()
        self.assertEqual(app.shapes[0].box, (.2, .2, .8, .8))

    def test_rotation_transforms_boxes_and_arrow_directions_and_roundtrips(self):
        self.generate()
        before = [s.box for s in self.app.shapes]
        self.app.rotate(90)
        self.assertEqual(self.app.reference_size, (100, 200))
        self.assertTrue(self.app.dirty_panels)
        self.app.rotate(-90)
        for shape, expected in zip(self.app.shapes, before):
            for actual, value in zip(shape.box, expected):
                self.assertAlmostEqual(actual, value)

    def test_drawing_arrow_keeps_original_preview_size_and_step_controls(self):
        app = self.app
        # This is a geometry regression test; user-configurable defaults are
        # verified separately with an alternate config file.
        app.line_width.set("3")
        app.arrow_size.set("20")
        app.display_size, app.offset = (800, 400), (10, 20)
        app.mode.set("arrow")
        app._mouse_down(SimpleNamespace(x=90, y=220))
        app._mouse_up(SimpleNamespace(x=410, y=220))
        shape = app.shapes[0]
        self.assertEqual(shape.preview_size, (800, 400))
        polygon, width = arrow_geometry(shape, app.display_size, app.reference_size)
        self.assertEqual(polygon, [(403, 200), (380, 210), (390, 200), (380, 190)])
        self.assertEqual(width, 12)
        app.shape_tree.selection_set("0")
        app.resize_arrow(2)
        self.assertEqual(shape.arrow_size, 22)
        app.resize_arrow(-2)
        self.assertEqual(shape.arrow_size, 20)
        for _ in range(10):
            app.resize_arrow(-2)
        self.assertEqual(shape.arrow_size, 10)

    def test_selected_rectangle_and_arrow_are_dashed_only_in_preview(self):
        app = self.app
        app.shapes = [Shape("rectangle", (.1, .1, .9, .9)),
                      Shape("arrow", (.2, .3, .7, .5), preview_size=(800, 400))]
        app._refresh_shapes()
        before = [p.image.tobytes() for p in make_panels(self.files, app.shapes, app.reference_size)]
        app.shape_tree.selection_set("0", "1")
        app._select_shape()
        for item in app.canvas.find_withtag("shape"):
            self.assertEqual(app.canvas.itemcget(item, "dash"), "5 3")
        head = app.canvas.find_withtag("arrow-head")[0]
        self.assertEqual(app.canvas.itemcget(head, "fill"), "")
        after = [p.image.tobytes() for p in make_panels(self.files, app.shapes, app.reference_size)]
        self.assertEqual(before, after)
        app.clear_shape_selection()
        for item in app.canvas.find_withtag("shape"):
            self.assertEqual(app.canvas.itemcget(item, "dash"), "")
        head = app.canvas.find_withtag("arrow-head")[0]
        self.assertEqual(app.canvas.itemcget(head, "fill"), "#FF0000")

    def test_rectangle_and_arrow_preview_use_the_same_stroke_units(self):
        app = self.app
        app.shapes = [Shape("rectangle", (.1, .1, .9, .9), width=3),
                      Shape("arrow", (.2, .3, .7, .5), width=3, preview_size=(800, 400))]
        app._refresh_shapes()
        for size, expected in (((800, 400), 12), ((100, 50), 1.5)):
            app.display_size, app.offset = size, (0, 0)
            app._draw_shapes()
            rectangle = app.canvas.find_withtag("shape-0")[0]
            shaft = app.canvas.find_withtag("arrow-shaft")[0]
            self.assertEqual(float(app.canvas.itemcget(rectangle, "width")), expected)
            self.assertEqual(float(app.canvas.itemcget(shaft, "width")), expected)

    def test_blank_canvas_click_clears_all_annotation_selection_without_adding_shape(self):
        app = self.app
        app.shapes = [Shape("rectangle", (.1, .2, .6, .8)), Shape("arrow", (.2, .3, .4, .5))]
        app._refresh_shapes()
        app.shape_tree.selection_set("0", "1")
        app._select_shape()
        app.offset, app.display_size = (10, 20), (800, 400)
        event = SimpleNamespace(x=0, y=0)
        app._mouse_down(event)
        app._mouse_up(event)
        self.assertEqual(app.shape_tree.selection(), ())
        self.assertEqual([v.get() for v in app.coord_vars], [""]*4)
        self.assertEqual(len(app.shapes), 2)
        # A click on the image without dragging also only clears selection.
        app.shape_tree.selection_set("0")
        event = SimpleNamespace(x=500, y=250)
        app._mouse_down(event)
        app._mouse_up(event)
        self.assertEqual(app.shape_tree.selection(), ())
        self.assertEqual(len(app.shapes), 2)

    def test_blank_list_and_frames_deselect_but_edit_controls_keep_selection(self):
        app = self.app
        app.shapes = [Shape("rectangle", (.1, .2, .6, .8))]
        app._refresh_shapes()
        app.shape_tree.selection_set("0")
        sidebar = app.shape_tree.master
        controls = [child for frame in sidebar.winfo_children() for child in frame.winfo_children()
                    if child.winfo_class() in ("TEntry", "TButton")]
        self.assertTrue(controls)
        for control in controls:
            app._blank_area_click(SimpleNamespace(widget=control))
            self.assertEqual(app.shape_tree.selection(), ("0",))
        app._blank_area_click(SimpleNamespace(widget=sidebar))
        self.assertEqual(app.shape_tree.selection(), ())
        app.shape_tree.selection_set("0")
        self.assertEqual(app._shape_list_click(SimpleNamespace(x=20, y=10000)), "break")
        self.assertEqual(app.shape_tree.selection(), ())

    def test_drag_reorders_groups_and_free_mode_reorders_single_panels(self):
        self.generate()
        app = self.app
        self.root.deiconify()
        self.root.update()
        first, last = app.order_list.bbox(0), app.order_list.bbox(2)
        app._order_start(SimpleNamespace(y=first[1]+first[3]//2))
        app._order_end(SimpleNamespace(y=last[1]+last[3]//2))
        self.assertEqual(app.panels[-1].group, "0")
        app.layout_mode.set(next(k for k, v in MODES.items() if v == "free"))
        app._layout_mode_changed()
        key = app.panels[0].key
        app._reorder(0, 3)
        self.assertEqual(app.panels[3].key, key)
        self.assertEqual(len(app.panels), 6)

    def test_source_file_reordering_keeps_panels_and_titles_with_their_files(self):
        self.generate()
        app = self.app
        app._select_file(1)
        for panel in app.panels:
            panel.title = f"custom-{panel.group}-{panel.kind}"
        reference = app.reference_size
        app._reorder_files(0, 2)
        self.assertEqual(app.files, self.files[1:] + self.files[:1])
        self.assertEqual(app.files[app.index], self.files[1])
        self.assertEqual(app.reference_size, reference)
        app.generate_panels()
        self.finish()
        for panel in app.panels:
            source_index = self.files.index(app.files[int(panel.group)])
            self.assertEqual(panel.title, f"custom-{source_index}-{panel.kind}")
            if panel.kind == "original":
                self.assertEqual(panel.image.getpixel((0, 0)), (source_index*60, 70, 90))
        path = str(self.path / "ordered.json")
        with patch("crop_image.filedialog.asksaveasfilename", return_value=path):
            app.save_project()
        with patch("crop_image.filedialog.askopenfilename", return_value=path):
            app.open_project()
        self.assertEqual(app.files, self.files[1:] + self.files[:1])
        self.assertEqual(app.file_list.size(), 3)

    def test_adding_files_preserves_annotations_rotation_and_current_image(self):
        self.generate()
        app = self.app
        app.rotate(90)
        app._select_file(1)
        boxes = [s.box for s in app.shapes]
        pixels, reference = app.image.tobytes(), app.reference_size
        new_path = self.path / "added.png"
        Image.new("RGB", (500, 300), "green").save(new_path)
        with patch("crop_image.filedialog.askopenfilenames", return_value=[self.files[0], str(new_path), str(new_path)]):
            app.add_files()
        self.finish()
        self.assertEqual(app.files, self.files + [str(new_path)])
        self.assertEqual(app.index, 1)
        self.assertEqual(app.reference_size, reference)
        self.assertEqual(app.rotation, 90)
        self.assertEqual([s.box for s in app.shapes], boxes)
        self.assertEqual(app.image.tobytes(), pixels)
        self.assertEqual(app.file_list.size(), 4)
        self.assertTrue(app.dirty_panels)
        app.generate_panels()
        self.finish()
        self.assertEqual(len(app.panels), 8)
        added = next(p for p in app.panels if p.key == "3:original")
        self.assertEqual(added.image.size, (300, 500))

    def test_invalid_addition_is_atomic_and_cancel_leaves_files_unchanged(self):
        app = self.app
        invalid = self.path / "floating-point.tif"
        Image.new("F", (20, 10), -1.5).save(invalid)
        good = self.path / "good.png"
        Image.new("RGB", (20, 10)).save(good)
        with patch.object(app, "_fail") as fail:
            with patch("crop_image.filedialog.askopenfilenames", return_value=[str(good), str(invalid)]):
                app.add_files()
            self.finish()
            fail.assert_called_once()
        self.assertEqual(app.files, self.files)
        with patch("crop_image.filedialog.askopenfilenames", return_value=[]):
            app.add_files()
        self.assertEqual(app.files, self.files)

    def test_file_list_drag_keeps_moved_file_visible_and_navigation_uses_new_order(self):
        app = self.app
        self.root.deiconify()
        self.root.update()
        first, last = app.file_list.bbox(0), app.file_list.bbox(2)
        app._file_drag_start(SimpleNamespace(y=first[1] + first[3]//2))
        event = SimpleNamespace(y=last[1] + last[3]//2)
        app._file_drag_motion(event)
        app._file_drag_end(event)
        self.assertEqual(app.files, self.files[1:] + self.files[:1])
        self.assertEqual(app.index, 2)
        self.assertEqual(app.file_list.curselection(), (2,))
        app.navigate(-1)
        self.assertEqual(app.files[app.index], self.files[2])
        self.assertEqual(app.file_list.curselection(), (1,))

    def test_project_restores_coordinates_order_titles_and_layout(self):
        self.generate()
        app = self.app
        app.shapes[1].preview_size = (800, 400)
        app._reorder(0, 2)
        app.panels[0].title = "中文方法名"
        app.layout_vars["columns"].set("2")
        order = [p.key for p in app.panels]
        path = str(self.path / "project.json")
        with patch("crop_image.filedialog.asksaveasfilename", return_value=path):
            app.save_project()
        app.shapes, app.panels = [], []
        with patch("crop_image.filedialog.askopenfilename", return_value=path):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual([p.key for p in app.panels], order)
        self.assertEqual(app.panels[0].title, "中文方法名")
        self.assertEqual(len(app.shapes), 2)
        self.assertEqual(tuple(app.shapes[1].preview_size), (800, 400))
        self.assertEqual(app.options().columns, 2)

    def test_imported_project_restores_removed_items_without_baking_preview(self):
        app = self.app
        app._import_files(self.files)
        self.finish()
        app.order_list.selection_set(0)
        app.remove_panel()
        path = str(self.path / "imported.json")
        with patch("crop_image.filedialog.asksaveasfilename", return_value=path):
            app.save_project()
        with patch("crop_image.filedialog.askopenfilename", return_value=path):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(len(app.panels), 2)
        self.assertEqual(app.panels[0].image.size, (200, 100))

    def test_preview_click_edits_specific_roi_and_rebases_all_coordinates_on_return(self):
        self.generate()
        app = self.app
        self.root.deiconify()
        self.root.update()
        app.update_preview()
        key = "0:roi-0"
        box = next(box for panel_key, box in app.preview_hits if panel_key == key)
        x, y = round((box[0]+box[2])/2), round((box[1]+box[3])/2)
        app.preview.event_generate("<ButtonPress-1>", x=x, y=y)
        app.preview.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.root.update()
        self.assertEqual(app.layout_position_key, key)
        item = app.layout_placements[key]
        self.assertEqual((app.layout_position_vars["x"].get(), app.layout_position_vars["y"].get()), (str(item.x), str(item.y)))
        self.assertTrue(app.preview.find_withtag("position-selection"))
        before = {key: (p.x, p.y) for key, p in app.layout_placements.items()}
        pixels = {p.key: p.image.tobytes() for p in app.panels}
        app.layout_position_vars["x"].set("-25")
        app.layout_position_vars["y"].set("-10")
        entry = app.layout_position_entries["x"]
        entry.focus_force()
        self.root.update()
        entry.event_generate("<Return>")
        self.root.update()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual((app.layout_position_vars["x"].get(), app.layout_position_vars["y"].get()), ("0", "0"))
        for other, position in app.layout_placements.items():
            wanted = (0, 0) if other == key else (before[other][0]+25, before[other][1]+10)
            self.assertEqual((position.x, position.y), wanted)
        self.assertEqual({p.key: p.image.tobytes() for p in app.panels}, pixels)
        app.layout_position_vars["x"].set("50")
        app.layout_position_vars["y"].set("40")
        app.position_apply_button.invoke()
        self.assertEqual((app.layout_placements[key].x, app.layout_placements[key].y), (50, 40))
        app.position_reset_all_button.invoke()
        self.assertEqual(app.layout_offsets, {})
        self.assertEqual((app.layout_placements[key].x, app.layout_placements[key].y), before[key])

    def test_layout_positions_follow_source_reorder_roi_renumber_and_project_restore(self):
        app = self.app
        app.shapes = [Shape("rectangle", (.1, .2, .6, .8)), Shape("rectangle", (.3, .3, .7, .7))]
        app.generate_panels()
        self.finish()
        app.layout_position_key = "0:roi-1"
        app.layout_position_vars["x"].set("140")
        app.layout_position_vars["y"].set("90")
        app.apply_layout_position()
        offset = dict(app.layout_offsets["0:roi-1"])
        app._reorder_files(0, 2)
        self.assertNotIn("0:roi-1", app.layout_offsets)
        self.assertEqual(app.layout_offsets["2:roi-1"], offset)
        app._refresh_shapes()
        app.shape_tree.selection_set("0")
        app.delete_shapes()
        app.generate_panels()
        self.finish()
        self.assertNotIn("2:roi-1", app.layout_offsets)
        self.assertEqual(app.layout_offsets["2:roi-0"], offset)
        before = dict(app.layout_offsets)
        project = self.path / "positions.json"
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(project)):
            app.save_project()
        app.layout_offsets = {}
        with patch("crop_image.filedialog.askopenfilename", return_value=str(project)):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(app.layout_offsets, before)
        app.select_order("all")
        app.remove_panel()
        self.assertEqual(app.layout_offsets, {})

    def test_invalid_layout_position_keeps_previous_position_and_empty_click_deselects(self):
        self.generate()
        app = self.app
        app.layout_position_key = "0:original"
        app._sync_layout_position()
        app.layout_position_vars["x"].set("invalid")
        with patch.object(app, "_fail") as fail:
            app.apply_layout_position()
            fail.assert_called_once()
        self.assertEqual(app.layout_offsets, {})
        app._preview_start(SimpleNamespace(x=-10000, y=-10000))
        self.assertIsNone(app.layout_position_key)
        self.assertEqual(app.layout_position_vars["x"].get(), "")

    def test_original_position_moves_its_roi_and_transparent_blank_does_not_block_selection(self):
        self.generate()
        app = self.app
        app.layout_position_key = "0:original"
        app._sync_layout_position()
        before = {key: (item.x, item.y) for key, item in app.layout_placements.items()}
        app.layout_position_vars["x"].set("25")
        app.layout_position_vars["y"].set("35")
        app.apply_layout_position()
        for key, item in app.layout_placements.items():
            expected = (before[key][0]+25, before[key][1]+35) if key.startswith("0:") else before[key]
            self.assertEqual((item.x, item.y), expected)
        self.assertEqual(len(app.preview.find_withtag("position-selection")), 2)
        app.position_reset_button.invoke()
        self.assertEqual(app.layout_offsets, {})
        app.add_blank()
        blank = next(p for p in app.panels if p.blank_size is not None)
        app.layout_position_key = blank.key
        app.layout_position_vars["x"].set("0")
        app.layout_position_vars["y"].set("0")
        app.apply_layout_position()
        rendered, _, _ = render_layout(app.panels, app.options())
        without_blank, _, _ = render_layout([p for p in app.panels if p.blank_size is None], app.options())
        self.assertEqual(rendered.tobytes(), without_blank.tobytes())
        box = next(box for key, box in app.preview_hits if key == "0:original")
        event = SimpleNamespace(x=box[0]+(box[2]-box[0])*.9, y=box[1]+(box[3]-box[1])*.9)
        self.assertEqual(app._hit_panel(event), "0:original")
        # A transparent overlapping placeholder remains editable via the list.
        unit = next(i for i, unit in enumerate(app._order_units()) if unit[0].key == blank.key)
        app.order_list.selection_clear(0, "end")
        app.order_list.selection_set(unit)
        app._position_from_order()
        self.assertEqual(app.layout_position_key, blank.key)

    def test_blank_button_inserts_after_selection_and_follows_layout_size_and_background(self):
        self.generate()
        app = self.app
        before = {p.key: p.image.tobytes() for p in app.panels}
        app.order_list.selection_set(0)
        app.blank_button.invoke()
        units = app._order_units()
        self.assertEqual(len(units), 4)
        blank = units[1][0]
        self.assertIsNotNone(blank.blank_size)
        self.assertEqual(app.order_list.curselection(), (1,))
        self.assertIn("空白占位", app.order_list.get(1))
        app.background = "#123456"
        app.layout_vars["panel_width"].set("400")
        plan, _ = layout_plan(app.panels, app.options())
        cell = next(item for item in plan if item.panel.key == blank.key)
        self.assertEqual((cell.width, cell.height), (400, 200))
        rendered, _, _ = render_layout(app.panels, app.options())
        region = rendered.crop((cell.x, cell.y, cell.x+cell.width, cell.y+cell.height))
        self.assertEqual(region.convert("RGB").getextrema(), ((18, 18), (52, 52), (86, 86)))
        for panel in app.panels:
            if panel.key in before:
                self.assertEqual(panel.image.tobytes(), before[panel.key])

    def test_blank_survives_append_refresh_reorder_project_restore_and_removal(self):
        self.generate()
        app = self.app
        app.add_blank()
        blank = next(p for p in app.panels if p.blank_size is not None)
        self.append_test_extras(1)
        app._reorder(3, 0)
        app.select_order("all")
        app.rotate_extra(90)
        self.finish()
        self.assertEqual(next(r for r in app.extra_images if r["key"] == blank.key)["rotation"], 0)
        app.generate_panels()
        self.finish()
        self.assertEqual(app.panels[0].key, blank.key)
        keys = [p.key for p in app.panels]
        project = self.path / "with-blank.json"
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(project)):
            app.save_project()
        record = next(r for r in json.loads(project.read_text(encoding="utf-8"))["extra_images"] if r["type"] == "blank")
        self.assertNotIn("path", record)
        with patch("crop_image.filedialog.askopenfilename", return_value=str(project)):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual([p.key for p in app.panels], keys)
        self.assertIsNotNone(app.panels[0].blank_size)
        app.select_order("extra")
        self.assertEqual(len(app.order_list.curselection()), 1)
        self.assertNotIn(0, app.order_list.curselection())
        app.order_list.selection_clear(0, "end")
        app.order_list.selection_set(0)
        app.remove_panel()
        self.assertFalse(any(p.blank_size is not None for p in app.panels))
        self.assertFalse(any(r.get("type") == "blank" for r in app.extra_images))

    def test_blank_only_layout_exports_and_restores_without_source_images(self):
        app = self.app
        app.files, app.image = [], None
        app.add_blank()
        app.add_blank()
        self.assertEqual(len(app.panels), 2)
        self.assertNotEqual(app.panels[0].key, app.panels[1].key)
        expected_size = (app.panels[0].blank_size[0]*2+app.options().gap+app.options().margin*2,
                         app.panels[0].blank_size[1]+app.options().margin*2)
        project = self.path / "blanks-only.json"
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(project)):
            app.save_project()
        with patch("crop_image.filedialog.askopenfilename", return_value=str(project)):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        output = self.path / "blanks-only.png"
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(output)):
            app.export_layout()
        self.finish()
        with Image.open(output) as saved:
            self.assertEqual(saved.size, expected_size)
            self.assertEqual(saved.convert("RGB").getextrema(), ((255, 255),)*3)

    def test_append_plain_images_does_not_create_roi_and_resizes_to_first_image(self):
        app = self.app
        app.rotate(90)
        self.generate()
        settings = app.options().roi_settings
        path = self.path / "roi-0" / "residual.png"
        path.parent.mkdir()
        source = Image.frombytes("RGB", (80, 40), bytes(i % 251 for i in range(80*40*3)))
        source.save(path)
        with patch("crop_image.filedialog.askopenfilenames", return_value=[str(path), str(path)]):
            app.append_images()
        self.finish()
        self.assertTrue(app.generated)
        self.assertEqual(app.files, self.files)
        self.assertEqual(app.options().roi_settings, settings)
        self.assertEqual(len(app.panels), 7)
        self.assertEqual(len(app.extra_images), 1)
        extra = next(p for p in app.panels if p.kind == "extra")
        expected = source.transpose(Image.Transpose.ROTATE_90)
        self.assertEqual(extra.image.size, (40, 80))
        self.assertEqual(extra.image.tobytes(), expected.tobytes())
        self.assertEqual(app.extra_images[0]["rotation"], 90)
        self.assertEqual(sum(p.group == extra.group for p in app.panels), 1)
        plan, _ = layout_plan(app.panels, app.options())
        placed = next(p for p in plan if p.panel.key == extra.key)
        self.assertEqual((placed.width, placed.height), (100, 200))
        rendered, _, _ = render_layout(app.panels, app.options())
        crop = rendered.crop((placed.x, placed.y, placed.x+placed.width, placed.y+placed.height))
        self.assertEqual(crop.convert("RGB").tobytes(), expected.resize((100, 200), Image.Resampling.LANCZOS).tobytes())
        app._reorder(3, 0)
        app.generate_panels()
        self.finish()
        self.assertEqual(app.panels[0].key, extra.key)
        self.assertEqual(app.panels[0].image.tobytes(), expected.tobytes())
        self.assertEqual(app.options().roi_settings, settings)

    def test_layout_imports_and_appends_inherit_all_editor_rotation_angles(self):
        app = self.app
        path = self.path / "asymmetric.png"
        source = Image.frombytes("RGB", (12, 8), bytes(i % 251 for i in range(12*8*3)))
        source.save(path)
        file_before = path.read_bytes()
        for angle, transpose in ((0, None), (90, Image.Transpose.ROTATE_90),
                                 (180, Image.Transpose.ROTATE_180), (270, Image.Transpose.ROTATE_270)):
            with self.subTest(angle=angle):
                if angle:
                    app.rotate(90)
                app._import_files([str(path)])
                self.finish()
                expected = source if transpose is None else source.transpose(transpose)
                self.assertEqual(app.import_rotation, angle)
                self.assertEqual(app.panels[0].image.size, expected.size)
                self.assertEqual(app.panels[0].image.tobytes(), expected.tobytes())
                with patch("crop_image.filedialog.askopenfilenames", return_value=[str(path)]):
                    app.append_images()
                self.finish()
                self.assertEqual(app.extra_images[0]["rotation"], angle)
                self.assertEqual(app.panels[-1].image.tobytes(), expected.tobytes())
                self.assertEqual(app.panels[-1].image.size, expected.size)
        self.assertEqual(path.read_bytes(), file_before)

    def test_import_rotation_is_saved_independently_and_legacy_projects_keep_orientation(self):
        app = self.app
        path = self.path / "import-orientation.png"
        source = Image.frombytes("RGB", (12, 8), bytes(i % 251 for i in range(12*8*3)))
        source.save(path)
        app.rotate(-90)
        app._import_files([str(path)])
        self.finish()
        # Subsequent editor rotation must not change existing imported images,
        # including when the project is restored from original source files.
        app.rotate(90)
        project = self.path / "import-orientation.json"
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(project)):
            app.save_project()
        with patch("crop_image.filedialog.askopenfilename", return_value=str(project)):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(app.rotation, 0)
        self.assertEqual(app.import_rotation, 270)
        self.assertEqual(app.panels[0].image.tobytes(), source.transpose(Image.Transpose.ROTATE_270).tobytes())
        data = json.loads(project.read_text(encoding="utf-8"))
        data.pop("import_rotation")
        data["rotation"] = 90
        project.write_text(json.dumps(data), encoding="utf-8")
        with patch("crop_image.filedialog.askopenfilename", return_value=str(project)):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(app.import_rotation, 0)
        self.assertEqual(app.panels[0].image.size, source.size)
        self.assertEqual(app.panels[0].image.tobytes(), source.tobytes())

    def test_mixed_project_preserves_extras_order_titles_and_removed_images(self):
        self.generate()
        app = self.app
        paths = []
        for i in range(2):
            path = self.path / f"extra-{i}.png"
            Image.new("RGB", (320+i*20, 140), (20, 40+i, 60)).save(path)
            paths.append(str(path))
        with patch("crop_image.filedialog.askopenfilenames", return_value=paths):
            app.append_images()
        self.finish()
        extras = [p for p in app.panels if p.kind == "extra"]
        extras[0].title = "Residual custom title"
        app._reorder(3, 0)
        target = next(i for i, unit in enumerate(app._order_units()) if unit[0].key == extras[1].key)
        app.order_list.selection_clear(0, "end")
        app.order_list.selection_set(target)
        app.remove_panel()
        self.assertEqual(len(app.extra_images), 1)
        app._reorder_files(0, 2)
        self.assertEqual(app.panels[0].key, extras[0].key)
        order = [p.key for p in app.panels]
        path = self.path / "mixed.json"
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(path)):
            app.save_project()
        self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["extra_images"]), 1)
        with patch("crop_image.filedialog.askopenfilename", return_value=str(path)):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual([p.key for p in app.panels], order)
        self.assertEqual(app.panels[0].title, "Residual custom title")
        self.assertEqual(app.panels[0].image.size, (320, 140))
        app.generate_panels()
        self.finish()
        self.assertEqual(sum(p.kind == "extra" for p in app.panels), 1)
        self.assertEqual(app.panels[0].key, extras[0].key)

    def test_append_to_imported_layout_preserves_base_images_and_restores(self):
        app = self.app
        app._import_files(self.files)
        self.finish()
        path = self.path / "residual.png"
        Image.new("RGB", (500, 300), "green").save(path)
        with patch("crop_image.filedialog.askopenfilenames", return_value=[str(path)]):
            app.append_images()
        self.finish()
        self.assertFalse(app.generated)
        self.assertEqual(app.import_files, self.files)
        self.assertEqual(len(app.panels), 4)
        project = self.path / "import-with-extra.json"
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(project)):
            app.save_project()
        with patch("crop_image.filedialog.askopenfilename", return_value=str(project)):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(len(app.panels), 4)
        self.assertEqual(sum(p.kind == "extra" for p in app.panels), 1)
        app.layout_vars["panel_width"].set("400")
        plan, _ = layout_plan(app.panels, app.options())
        extra = next(p for p in plan if p.panel.kind == "extra")
        self.assertEqual((extra.width, extra.height), (400, 200))

    def test_invalid_or_cancelled_append_keeps_existing_materials(self):
        self.generate()
        app = self.app
        good, bad = self.path / "good.png", self.path / "bad.tif"
        Image.new("RGB", (30, 20)).save(good)
        Image.new("F", (30, 20)).save(bad)
        keys = [p.key for p in app.panels]
        with patch.object(app, "_fail") as fail:
            with patch("crop_image.filedialog.askopenfilenames", return_value=[str(good), str(bad)]):
                app.append_images()
            self.finish()
            fail.assert_called_once()
        self.assertEqual([p.key for p in app.panels], keys)
        self.assertEqual(app.extra_images, [])
        with patch("crop_image.filedialog.askopenfilenames", return_value=[]):
            app.append_images()
        self.assertFalse(app.busy)
        self.assertEqual([p.key for p in app.panels], keys)

    def test_extra_rotation_is_lossless_and_happens_before_resize(self):
        self.generate()
        app = self.app
        settings = app.options().roi_settings
        native_before = {p.key: p.image.tobytes() for p in app.panels}
        path = self.path / "portrait-residual.png"
        source = Image.frombytes("RGB", (100, 200), bytes((i*7+i//11) % 256 for i in range(100*200*3)))
        source.save(path)
        file_before = path.read_bytes()
        with patch("crop_image.filedialog.askopenfilenames", return_value=[str(path)]):
            app.append_images()
        self.finish()
        index = next(i for i, unit in enumerate(app._order_units()) if unit[0].kind == "extra")
        app.order_list.selection_set(index)
        app.rotate_extra(90)
        self.finish()
        extra = next(p for p in app.panels if p.kind == "extra")
        expected = source.transpose(Image.Transpose.ROTATE_90)
        self.assertEqual(extra.image.size, (200, 100))
        self.assertEqual(extra.image.tobytes(), expected.tobytes())
        self.assertEqual(app.extra_images[0]["rotation"], 90)
        self.assertEqual(app.options().roi_settings, settings)
        for p in app.panels:
            if p.kind != "extra":
                self.assertEqual(p.image.tobytes(), native_before[p.key])
        plan, _ = layout_plan(app.panels, app.options())
        placed = next(p for p in plan if p.panel.kind == "extra")
        rendered, _, _ = render_layout(app.panels, app.options())
        crop = rendered.crop((placed.x, placed.y, placed.x+placed.width, placed.y+placed.height))
        self.assertEqual(crop.convert("RGB").tobytes(), expected.tobytes())
        app.rotate_extra(-90)
        self.finish()
        self.assertEqual(extra.image.tobytes(), source.tobytes())
        self.assertEqual(app.extra_images[0]["rotation"], 0)
        self.assertEqual(path.read_bytes(), file_before)

    def test_extra_rotation_survives_refresh_and_project_restore(self):
        self.generate()
        app = self.app
        path = self.path / "residual-rotation.png"
        source = Image.frombytes("RGBA", (30, 60), bytes(i % 256 for i in range(30*60*4)))
        source.save(path)
        with patch("crop_image.filedialog.askopenfilenames", return_value=[str(path)]):
            app.append_images()
        self.finish()
        index = next(i for i, unit in enumerate(app._order_units()) if unit[0].kind == "extra")
        app.order_list.selection_set(index)
        app.rotate_extra(-90)
        self.finish()
        expected = source.transpose(Image.Transpose.ROTATE_270)
        app.generate_panels()
        self.finish()
        self.assertEqual(next(p for p in app.panels if p.kind == "extra").image.tobytes(), expected.tobytes())
        project = self.path / "rotated-extra.json"
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(project)):
            app.save_project()
        with patch("crop_image.filedialog.askopenfilename", return_value=str(project)):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        extra = next(p for p in app.panels if p.kind == "extra")
        self.assertEqual(app.extra_images[0]["rotation"], 270)
        self.assertEqual(extra.image.size, (60, 30))
        self.assertEqual(extra.image.tobytes(), expected.tobytes())

    def test_extra_rotation_requires_extra_selection_and_valid_saved_angle(self):
        self.generate()
        app = self.app
        pixels = [p.image.tobytes() for p in app.panels]
        app.rotate_extra(90)
        app.order_list.selection_set(0)
        app.rotate_extra(90)
        self.assertEqual([p.image.tobytes() for p in app.panels], pixels)
        with self.assertRaisesRegex(ValueError, "旋转角度"):
            app._load_extra_panels([dict(key="extra:test", path=self.files[0], rotation=45)])
        legacy = app._load_extra_panels([dict(key="extra:test", path=self.files[0])])
        self.assertEqual(legacy[0].image.size, (200, 100))

    def test_select_all_and_extra_only_batch_rotation_preserve_selection(self):
        self.generate()
        app = self.app
        extras = self.append_test_extras()
        original_pixels = {p.key: p.image.tobytes() for p in app.panels if p.kind != "extra"}
        app.select_order("all")
        selected = app.order_list.curselection()
        self.assertEqual(len(selected), 6)
        app.rotate_extra(90)
        self.finish()
        self.assertEqual(app.order_list.curselection(), selected)
        self.assertTrue(all(p.image.size == (80, 160) for p in extras))
        self.assertTrue(all(record["rotation"] == 90 for record in app.extra_images))
        for p in app.panels:
            if p.kind != "extra":
                self.assertEqual(p.image.tobytes(), original_pixels[p.key])
        project = self.path / "batch-rotations.json"
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(project)):
            app.save_project()
        with patch("crop_image.filedialog.askopenfilename", return_value=str(project)):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertTrue(all(record["rotation"] == 90 for record in app.extra_images))
        app.select_order("extra")
        selected = app.order_list.curselection()
        self.assertEqual(selected, (3, 4, 5))
        self.assertEqual(app.order_list.index("anchor"), 3)
        app.rotate_extra(-90)
        self.finish()
        self.assertEqual(app.order_list.curselection(), selected)
        self.assertTrue(all(p.image.size == (160, 80) for p in app.panels if p.kind == "extra"))
        self.assertTrue(all(record["rotation"] == 0 for record in app.extra_images))

    def test_batch_move_drag_and_remove_keep_all_selected_items_in_order(self):
        self.generate()
        app = self.app
        extras = self.append_test_extras()
        keys = [p.key for p in extras]
        app.select_order("extra")
        app.move_order(-1)
        self.assertEqual(app.order_list.curselection(), (2, 3, 4))
        self.assertEqual([unit[0].key for unit in app._order_units()[2:5]], keys)
        app.move_order(1)
        self.assertEqual(app.order_list.curselection(), (3, 4, 5))
        self.root.deiconify()
        self.root.update()
        start, end = app.order_list.bbox(3), app.order_list.bbox(1)
        app._order_start(SimpleNamespace(y=start[1]+start[3]//2))
        app._order_motion(SimpleNamespace(y=end[1]+end[3]//2))
        self.assertEqual(app.order_list.curselection(), (3, 4, 5))
        app._order_end(SimpleNamespace(y=end[1]+end[3]//2))
        self.assertEqual(app.order_list.curselection(), (1, 2, 3))
        self.assertEqual([unit[0].key for unit in app._order_units()[1:4]], keys)
        app.remove_panel()
        self.assertEqual(app.extra_images, [])
        self.assertEqual(len(app.panels), 6)
        app.select_order("all")
        app.remove_panel()
        self.assertEqual(app.panels, [])
        self.assertEqual(app.roi_layout_vars, {})

    def test_ctrl_shift_click_and_ctrl_a_really_select_multiple_list_items(self):
        self.generate()
        app = self.app
        self.append_test_extras()
        self.root.deiconify()
        self.root.update()
        widget = app.order_list
        widget.focus_force()
        self.root.update()
        keys = [p.key for p in app.panels]

        def click(index, state=0):
            x, y, w, h = widget.bbox(index)
            widget.event_generate("<ButtonPress-1>", x=x+w//2, y=y+h//2, state=state)
            widget.event_generate("<ButtonRelease-1>", x=x+w//2, y=y+h//2, state=state)
            self.root.update()

        click(0)
        click(2, 0x0004)
        self.assertEqual(widget.curselection(), (0, 2))
        click(1)
        click(4, 0x0001)
        self.assertEqual(widget.curselection(), (1, 2, 3, 4))
        self.assertEqual([p.key for p in app.panels], keys)
        widget.event_generate("<Control-a>")
        self.root.update()
        self.assertEqual(widget.curselection(), tuple(range(6)))
        widget.event_generate("<Escape>")
        self.root.update()
        self.assertEqual(widget.curselection(), ())

    def test_failed_batch_rotation_does_not_partially_update_images(self):
        self.generate()
        app = self.app
        extras = self.append_test_extras()
        before = [p.image.tobytes() for p in extras]
        app.select_order("extra")
        with patch.object(extras[1].image, "transpose", side_effect=ValueError("test failure")):
            with patch.object(app, "_fail") as fail:
                app.rotate_extra(90)
                self.finish()
                fail.assert_called_once()
        self.assertEqual([p.image.tobytes() for p in extras], before)
        self.assertTrue(all(record["rotation"] == 0 for record in app.extra_images))

    def test_dirty_project_requires_refresh_before_export(self):
        self.generate()
        app = self.app
        app.shapes.append(Shape("rectangle", (.3, .3, .7, .7)))
        app._changed()
        path = str(self.path / "dirty.json")
        with patch("crop_image.filedialog.asksaveasfilename", return_value=path):
            app.save_project()
        with patch("crop_image.filedialog.askopenfilename", return_value=path):
            app.open_project()
        self.assertTrue(app.dirty_panels)
        with patch("crop_image.filedialog.asksaveasfilename") as dialog:
            app.export_layout()
            dialog.assert_not_called()
        app.generate_panels()
        self.finish()
        self.assertFalse(app.dirty_panels)
        self.assertEqual(len(app.panels), 9)

    def test_refresh_keeps_custom_titles_and_order(self):
        self.generate()
        app = self.app
        app._reorder(0, 2)
        keys = [p.key for p in app.panels]
        app.panels[0].title = "Renamed"
        app.generate_panels()
        self.finish()
        self.assertEqual([p.key for p in app.panels], keys)
        self.assertEqual(app.panels[0].title, "Renamed")

    def test_generate_shows_one_manual_size_row_per_roi_and_hides_names(self):
        app = self.app
        app.shapes = [Shape("rectangle", (.1, .2, .6, .8)), Shape("arrow", (.2, .3, .4, .5)),
                      Shape("rectangle", (.3, .3, .7, .7))]
        app.generate_panels()
        self.finish()
        self.assertEqual(set(app.roi_layout_vars), {"roi-0", "roi-1"})
        self.assertEqual(app.roi_layout_vars["roi-0"]["width"].get(), "100")
        self.assertEqual(app.roi_layout_vars["roi-0"]["height"].get(), "60")
        self.assertNotIn("roi_scale", app.layout_vars)
        self.assertFalse(app.options().labels)
        self.assertEqual(app.options().mode, "relative")

    def test_new_roi_defaults_start_at_top_left_and_expand_right_by_width_and_gap(self):
        app = self.app
        app.layout_vars["gap"].set("7")
        app.shapes = [Shape("rectangle", (.1, .2, .6, .8)), Shape("arrow", (.2, .3, .4, .5)),
                      Shape("rectangle", (.3, .3, .7, .7)), Shape("rectangle", (.1, .1, .3, .4))]
        app.generate_panels()
        self.finish()
        settings = app.options().roi_settings
        plan, _ = layout_plan(app.panels, app.options())
        original = next(p for p in plan if p.panel.group == "0" and p.panel.kind == "original")
        x = 0
        for kind, setting in settings.items():
            self.assertEqual((setting["x_edge"], setting["y_edge"]), ("left", "top"))
            self.assertEqual((setting["x_distance"], setting["y_distance"]), (x, 0))
            placed = next(p for p in plan if p.panel.group == "0" and p.panel.kind == kind)
            self.assertEqual((placed.x-original.x, placed.y-original.y), (x, 0))
            x += setting["width"] + 7
        app.roi_layout_vars["roi-0"]["x_distance"].set("13")
        app.roi_layout_vars["roi-0"]["y_distance"].set("-5")
        before = app.options().roi_settings
        app.generate_panels()
        self.finish()
        self.assertEqual(app.options().roi_settings, before)

    def test_per_roi_sizes_and_relative_positions_survive_refresh_and_project_restore(self):
        app = self.app
        app.shapes = [Shape("rectangle", (.1, .2, .6, .8)), Shape("rectangle", (.3, .3, .7, .7))]
        app.generate_panels()
        self.finish()
        for kind, width, height in (("roi-0", "300", "200"), ("roi-1", "200", "100")):
            app.roi_layout_vars[kind]["width"].set(width)
            app.roi_layout_vars[kind]["height"].set(height)
        app.roi_layout_vars["roi-1"]["x_edge"].set(next(label for label, edge in HORIZONTAL_EDGES.items() if edge == "right"))
        app.roi_layout_vars["roi-1"]["x_distance"].set("20")
        app.roi_layout_vars["roi-1"]["y_edge"].set(next(label for label, edge in VERTICAL_EDGES.items() if edge == "bottom"))
        app.roi_layout_vars["roi-1"]["y_distance"].set("10")
        settings = app.options().roi_settings
        app.generate_panels()
        self.finish()
        self.assertEqual(app.options().roi_settings, settings)
        path = str(self.path / "relative.json")
        with patch("crop_image.filedialog.asksaveasfilename", return_value=path):
            app.save_project()
        app.roi_layout_vars["roi-0"]["width"].set("999")
        with patch("crop_image.filedialog.askopenfilename", return_value=path):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(app.options().roi_settings, settings)
        self.assertFalse(app.options().labels)

    def test_deleting_roi_keeps_remaining_rois_own_settings(self):
        app = self.app
        app.shapes = [Shape("rectangle", (.1, .2, .6, .8)), Shape("rectangle", (.3, .3, .7, .7))]
        app.generate_panels()
        self.finish()
        app.roi_layout_vars["roi-1"]["width"].set("220")
        app._refresh_shapes()
        app.shape_tree.selection_set("0")
        app.delete_shapes()
        app.generate_panels()
        self.finish()
        self.assertEqual(set(app.roi_layout_vars), {"roi-0"})
        self.assertEqual(app.roi_layout_vars["roi-0"]["width"].get(), "220")

    def test_annotation_return_and_keypad_enter_apply_coordinates(self):
        app = self.app
        app.shapes = [Shape("rectangle", (.1, .2, .6, .8))]
        app._refresh_shapes()
        app.shape_tree.selection_set("0")
        app._select_shape()
        self.root.deiconify()
        self.root.update()
        for variable, value in zip(app.coord_vars, ("40", "20", "80", "50")):
            variable.set(value)
        entry = app.coordinate_entries[2]
        entry.focus_force()
        self.root.update()
        entry.event_generate("<Return>")
        self.root.update()
        for actual, expected in zip(app.shapes[0].box, (.2, .2, .6, .7)):
            self.assertAlmostEqual(actual, expected)
        app.coord_vars[2].set("100")
        # Windows Tk does not synthesize KP_Enter through event_generate.
        # Check its binding and exercise the same callback directly.
        self.assertTrue(entry.bind("<KP_Enter>"))
        app._apply_shape_key(SimpleNamespace(widget=entry, keysym="KP_Enter"))
        self.root.update()
        self.assertAlmostEqual(app.shapes[0].box[2], .7)

    def test_left_right_keys_navigate_but_keep_text_editing_in_entries(self):
        app = self.app
        self.root.deiconify()
        app.notebook.select(app.edit_tab)
        self.root.update()
        app.canvas.focus_force()
        self.root.update()
        app.canvas.event_generate("<Right>")
        self.root.update()
        self.assertEqual(app.index, 1)
        app.canvas.event_generate("<Left>")
        self.root.update()
        self.assertEqual(app.index, 0)
        entry = app.coordinate_entries[0]
        entry.delete(0, "end")
        entry.insert(0, "123")
        entry.icursor(1)
        entry.focus_force()
        self.root.update()
        entry.event_generate("<Right>")
        self.root.update()
        self.assertEqual(app.index, 0)
        self.assertEqual(entry.index("insert"), 2)
        self.assertTrue(app.root.bind("<Left>"))
        self.assertTrue(app.root.bind("<Right>"))

    def test_editor_arrow_keys_work_when_file_list_or_annotation_list_has_focus(self):
        app = self.app
        self.root.deiconify()
        app.notebook.select(app.edit_tab)
        self.root.update()
        for widget in (app.file_list, app.shape_tree, app.notebook):
            with self.subTest(widget=widget.winfo_class()):
                app._select_file(0)
                widget.focus_force()
                self.root.update()
                widget.event_generate("<Right>")
                self.root.update()
                self.assertEqual(app.index, 1)
                self.assertEqual(app.file_list.curselection(), (1,))
                widget.event_generate("<Left>")
                self.root.update()
                self.assertEqual(app.index, 0)
                self.assertEqual(app.notebook.select(), str(app.edit_tab))

    def test_clicking_editor_canvas_releases_entry_focus_for_arrow_navigation(self):
        app = self.app
        self.root.deiconify()
        app.notebook.select(app.edit_tab)
        self.root.update()
        entry = app.coordinate_entries[0]
        entry.focus_force()
        self.root.update()
        app.canvas.event_generate("<ButtonPress-1>", x=0, y=0)
        app.canvas.event_generate("<ButtonRelease-1>", x=0, y=0)
        self.root.update()
        self.assertEqual(self.root.focus_get(), app.canvas)
        app.canvas.event_generate("<Right>")
        self.root.update()
        self.assertEqual(app.index, 1)

    def test_arrow_navigation_does_not_switch_images_on_layout_tab_or_mid_drawing(self):
        app = self.app
        self.root.deiconify()
        app.notebook.select(app.edit_tab)
        self.root.update()
        app.canvas.focus_force()
        app.drawing = Shape("rectangle", (.1, .1, .2, .2))
        app.canvas.event_generate("<Right>")
        self.root.update()
        self.assertEqual(app.index, 0)
        app.drawing = None
        app.notebook.select(app.layout_tab)
        app.order_list.focus_force()
        self.root.update()
        app.order_list.event_generate("<Right>")
        self.root.update()
        self.assertEqual(app.index, 0)
        app.notebook.select(app.edit_tab)
        self.root.update()
        self.assertEqual(self.root.focus_get(), app.canvas)
        app.canvas.event_generate("<Right>")
        self.root.update()
        self.assertEqual(app.index, 1)

    def test_windows_num_lock_and_extended_arrow_flags_do_not_block_navigation(self):
        if self.root.tk.call("tk", "windowingsystem") != "win32":
            self.skipTest("Windows modifier bits")
        app = self.app
        self.root.deiconify()
        app.notebook.select(app.edit_tab)
        self.root.update()
        app.canvas.focus_force()
        self.root.update()
        for state in (0x0008, 0x40008):
            with self.subTest(state=hex(state)):
                app.canvas.event_generate("<Right>", state=state)
                self.root.update()
                self.assertEqual(app.index, 1)
                app.canvas.event_generate("<Left>", state=state)
                self.root.update()
                self.assertEqual(app.index, 0)
        for state in (0x0004, 0x20000):
            with self.subTest(modifier=hex(state)):
                app._navigate_key(SimpleNamespace(widget=app.canvas, state=state), 1)
                self.assertEqual(app.index, 0)

    def test_original_dimensions_show_native_and_layout_output_sizes(self):
        app = self.app
        self.assertIn("200", app.original_size_text.get())
        self.assertIn("100", app.original_size_text.get())
        app.layout_vars["panel_width"].set("400")
        app.update_preview()
        self.assertIn("400 × 200", app.original_size_text.get())
        Image.new("RGB", (320, 160)).save(self.files[1])
        app._select_file(1)
        self.assertIn("320", app.original_size_text.get())
        self.assertIn("160", app.original_size_text.get())

    def test_legacy_edge_placement_projects_convert_to_manual_offsets(self):
        self.generate()
        app = self.app
        path = self.path / "legacy-position.json"
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(path)):
            app.save_project()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["layout"]["roi_settings"] = {"roi-0": dict(width=100, height=60, relative_to="original", position="right")}
        data["layout"]["gap"] = 10
        path.write_text(json.dumps(data), encoding="utf-8")
        with patch("crop_image.filedialog.askopenfilename", return_value=str(path)):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        setting = app.options().roi_settings["roi-0"]
        self.assertEqual((setting["x_edge"], setting["x_distance"]), ("left", 210))
        self.assertEqual((setting["y_edge"], setting["y_distance"]), ("top", 20))

    def test_legacy_direction_offsets_convert_without_changing_coordinates(self):
        self.generate()
        app = self.app
        path = self.path / "legacy-direction.json"
        with patch("crop_image.filedialog.asksaveasfilename", return_value=str(path)):
            app.save_project()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["layout"]["roi_settings"] = {"roi-0": dict(width=100, height=60, x_direction="left", x_distance=30,
                                                       y_direction="down", y_distance=120)}
        path.write_text(json.dumps(data), encoding="utf-8")
        with patch("crop_image.filedialog.askopenfilename", return_value=str(path)):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        setting = app.options().roi_settings["roi-0"]
        self.assertEqual((setting["x_edge"], setting["x_distance"]), ("left", -30))
        self.assertEqual((setting["y_edge"], setting["y_distance"]), ("top", 120))

    def test_method_names_follow_current_first_row_and_source_files_through_refresh(self):
        self.generate()
        app = self.app
        app.layout_vars["columns"].set("2")
        units = app._order_units()
        names = {app._method_key(unit): name for unit, name in zip(units, ("FBP", "SART", "Ours"))}
        app.set_method_names(names)
        self.assertEqual(app.suggested_layout_filename(), "comparison__FBP__SART.png")
        app._reorder(0, 2)
        self.assertEqual(app.suggested_layout_filename(), "comparison__SART__Ours.png")
        app.layout_vars["columns"].set("3")
        self.assertEqual(app.suggested_layout_filename(), "comparison__SART__Ours__FBP.png")
        app._reorder_files(2, 0)
        self.assertEqual(app.suggested_layout_filename(), "comparison__Ours__FBP__SART.png")
        app.generate_panels()
        self.finish()
        expected = app.suggested_layout_filename()
        self.assertEqual(expected, "comparison__Ours__FBP__SART.png")
        path = str(self.path / "methods.json")
        with patch("crop_image.filedialog.asksaveasfilename", return_value=path):
            app.save_project()
        app.method_names = {}
        with patch("crop_image.filedialog.askopenfilename", return_value=path):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(app.suggested_layout_filename(), expected)
        self.assertEqual(app.method_names, names)

    def test_method_dialog_edits_only_first_n_and_cancel_keeps_names(self):
        self.generate()
        app = self.app
        app.layout_vars["columns"].set("2")

        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)

        dialog = app.configure_method_names()
        self.root.update()
        entries = [w for w in children(dialog) if w.winfo_class() == "TEntry"]
        self.assertEqual(len(entries), 2)
        for entry, name in zip(entries, ("FBP", "Ours")):
            entry.delete(0, "end")
            entry.insert(0, name)
        self.assertTrue(any("comparison__FBP__Ours.png" in str(w.cget("text"))
                            for w in children(dialog) if w.winfo_class() == "TLabel"))
        next(w for w in children(dialog) if w.winfo_class() == "TButton" and w.cget("text") == "保存方法名").invoke()
        self.assertEqual(app.suggested_layout_filename(), "comparison__FBP__Ours.png")
        self.assertEqual(len(app.method_names), 2)
        dialog = app.configure_method_names()
        entry = next(w for w in children(dialog) if w.winfo_class() == "TEntry")
        entry.delete(0, "end")
        entry.insert(0, "cancelled")
        next(w for w in children(dialog) if w.winfo_class() == "TButton" and w.cget("text") == "取消").invoke()
        self.assertEqual(app.suggested_layout_filename(), "comparison__FBP__Ours.png")

    def test_export_uses_method_filename_without_changing_pixels(self):
        self.generate()
        app = self.app
        app.layout_vars["columns"].set("2")
        expected, size, _ = render_layout(app.panels, app.options())
        units = app._method_units()
        app.order_list.selection_set(0, 1)
        app.set_method_names({app._method_key(unit): name for unit, name in zip(units, ("FBP", "Ours"))})
        self.assertEqual(app.order_list.curselection(), (0, 1))
        path = str(self.path / app.suggested_layout_filename())
        with patch("crop_image.filedialog.asksaveasfilename", return_value=path) as dialog:
            app.export_layout()
        self.assertEqual(dialog.call_args.kwargs["initialfile"], "comparison__FBP__Ours.png")
        self.finish()
        with Image.open(path) as saved:
            self.assertEqual(saved.size, size)
            self.assertEqual(saved.tobytes(), expected.tobytes())

    def test_import_and_extra_method_names_follow_images_and_reset_on_replacement(self):
        app = self.app
        app._import_files(self.files)
        self.finish()
        app.layout_vars["columns"].set("2")
        first = app._order_units()[0]
        app.set_method_names({app._method_key(first): "FBP"})
        extras = self.append_test_extras(1)
        app.set_method_names({app._method_key([extras[0]]): "Residual"})
        app._reorder(3, 0)
        self.assertEqual(app.suggested_layout_filename(), "comparison__Residual__FBP.png")
        path = str(self.path / "import-methods.json")
        with patch("crop_image.filedialog.asksaveasfilename", return_value=path):
            app.save_project()
        with patch("crop_image.filedialog.askopenfilename", return_value=path):
            app.open_project()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(app.suggested_layout_filename(), "comparison__Residual__FBP.png")
        app._import_files(self.files)
        self.finish()
        self.assertEqual(app.method_names, {})
        app.set_method_names({app._method_key(app._order_units()[0]): "Old"})
        app._replace_files(self.files)
        self.assertEqual(app.method_names, {})

    def test_export_and_cancel_do_not_change_current_image(self):
        self.generate()
        app = self.app
        pixels = app.image.tobytes()
        with patch("crop_image.filedialog.askdirectory", return_value=""):
            app.export_images()
        self.assertFalse(app.busy)
        with patch("crop_image.filedialog.askdirectory", return_value=str(self.path)):
            app.export_images()
        self.finish()
        self.assertEqual(len(list(self.path.glob("ROI_*/roi-0/*.png"))), 3)
        path = str(self.path / "figure.tif")
        with patch("crop_image.filedialog.asksaveasfilename", return_value=path):
            app.export_layout()
        self.finish()
        with Image.open(path) as image:
            self.assertGreater(image.width, 200)
        self.assertEqual(app.image.tobytes(), pixels)


if __name__ == "__main__":
    unittest.main()
