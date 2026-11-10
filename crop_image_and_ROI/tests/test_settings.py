import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from settings import CONFIG, load_config


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_default_config_roundtrip(self):
        self.write(CONFIG)
        self.assertEqual(load_config(self.path), CONFIG)

    def test_invalid_numeric_settings_fail_with_their_key(self):
        cases = [("editor", "arrow_step", -1), ("timing", "poll_ms", 0),
                 ("editor", "selection_dash", [0, 3]), ("editor", "selection_dash", [256, 3]),
                 ("layout", "columns", True), ("layout", "roi_scale", float("nan")),
                 ("ui", "list_font_size", 10.5), ("limits", "filename_max_chars", 256)]
        for section, name, value in cases:
            data = copy.deepcopy(CONFIG)
            data[section][name] = value
            self.write(data)
            with self.subTest(key=f"{section}.{name}"), self.assertRaisesRegex(ValueError, f"{section}.{name}"):
                load_config(self.path)

    def test_missing_unknown_and_out_of_range_settings_are_rejected(self):
        data = copy.deepcopy(CONFIG)
        del data["editor"]["arrow_size"]
        self.write(data)
        with self.assertRaisesRegex(ValueError, "editor.arrow_size"):
            load_config(self.path)
        data = copy.deepcopy(CONFIG)
        data["layout"]["colums"] = 3
        self.write(data)
        with self.assertRaisesRegex(ValueError, "layout.colums"):
            load_config(self.path)
        data = copy.deepcopy(CONFIG)
        data["layout"]["columns"] = data["limits"]["columns_max"]+1
        self.write(data)
        with self.assertRaisesRegex(ValueError, "columns"):
            load_config(self.path)

    def test_custom_file_controls_gui_and_export_defaults_on_startup(self):
        data = copy.deepcopy(CONFIG)
        data["editor"].update(line_width=7, arrow_size=28, arrow_tip_offset=5)
        data["layout"].update(columns=2, gap=13, dpi=600)
        data["ui"]["coordinate_decimals"] = 2
        self.write(data)
        env = dict(os.environ, CROP_IMAGE_CONFIG=str(self.path))
        code = """
import json
import tkinter as tk
from crop_image import ImageCropper
from image_core import Shape, LayoutOptions, arrow_polygon, save_lossless
from PIL import Image
from pathlib import Path
from settings import CONFIG_PATH
root = tk.Tk(); root.withdraw(); app = ImageCropper(root)
path = CONFIG_PATH.with_suffix('.png')
save_lossless(Image.new('RGB', (10, 10), 'red'), path)
with Image.open(path) as image: dpi = image.info['dpi'][0]
print(json.dumps([app.line_width.get(), app.arrow_size.get(), app.options().columns,
                  app.options().gap, app.options().dpi, Shape('arrow', (0,0,1,1)).width,
                  LayoutOptions().columns, arrow_polygon((0,0,40,0),20)[0][0],
                  app._coordinate_text(1.2345), round(dpi)]))
for timer in root.tk.call('after','info'): root.after_cancel(timer)
root.destroy()
"""
        result = subprocess.run([sys.executable, "-c", code], env=env, cwd=Path(__file__).resolve().parents[1],
                                check=True, capture_output=True, text=True, timeout=20)
        self.assertEqual(json.loads(result.stdout), ["7", "28", 2, 13, 600, 7, 2, 45, "1.23", 600])


if __name__ == "__main__":
    unittest.main()
