from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from high_depth import Image16
from image_core import LayoutOptions, Shape, annotate, make_panels, render_layout
from settings import CONFIG


class ArrowRenderTests(unittest.TestCase):
    def test_diagonal_arrow_has_partial_edge_coverage_without_touching_other_pixels(self):
        shape = Shape("arrow", (.2, .2, .8, .8), width=2, arrow_size=7)
        blank = Image.new("RGBA", (64, 64))
        layer = annotate(blank, [shape], blank.size, "arrow")
        alpha = np.asarray(layer)[:, :, 3]
        self.assertTrue(np.any((alpha > 0) & (alpha < 255)))
        self.assertTrue(np.any(alpha == 255))
        source = Image.fromarray(np.arange(64*64*3, dtype=np.uint8).reshape(64, 64, 3))
        before = np.array(source)
        result = annotate(source, [shape], source.size, "arrow")
        np.testing.assert_array_equal(np.asarray(result)[alpha == 0], before[alpha == 0])
        np.testing.assert_array_equal(np.asarray(source), before)

    def test_large_roi_redraws_arrow_at_output_resolution_instead_of_scaling_stairs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.png"
            source = Image.new("RGB", (32, 32), (77, 88, 99))
            source.save(path)
            shapes = [Shape("rectangle", (0, 0, 1, 1)),
                      Shape("arrow", (.2, .2, .8, .8), width=1, arrow_size=18, preview_size=(256, 256))]
            roi = list(make_panels([str(path)], shapes, source.size))[1]
            options = LayoutOptions(mode="free", columns=1, gap=0, margin=0,
                                    roi_settings={"roi-0": dict(width=256, height=256, x_edge="left", y_edge="top", x_distance=0, y_distance=0)})
            rendered, size, _ = render_layout([roi], options)
            self.assertEqual(size, (256, 256))
            expected = annotate(source.resize(size, Image.Resampling.NEAREST), shapes, source.size, "arrow")
            np.testing.assert_array_equal(np.asarray(rendered)[:, :, :3], np.asarray(expected))
            self.assertNotEqual(rendered.convert("RGB").tobytes(), roi.image.resize(size, Image.Resampling.NEAREST).tobytes())
            # Rendering a smaller preview must not modify either native source.
            before = roi.image.tobytes(), roi.arrow_source.image.tobytes()
            render_layout([roi], options, (100, 100))
            self.assertEqual((roi.image.tobytes(), roi.arrow_source.image.tobytes()), before)

    def test_antialias_tiles_do_not_introduce_seams(self):
        shape = Shape("arrow", (.073, .121, .913, .817), width=3, arrow_size=9)
        source = Image.new("RGBA", (97, 83), (61, 87, 93, 180))
        with patch.dict(CONFIG["editor"], annotation_tile_size=256):
            expected = annotate(source, [shape], source.size, "arrow")
        with patch.dict(CONFIG["editor"], annotation_tile_size=7):
            tiled = annotate(source, [shape], source.size, "arrow")
        self.assertEqual(tiled.tobytes(), expected.tobytes())

    def test_arrow_crossing_roi_boundary_keeps_16bit_background_and_clips_to_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.png"
            pixels = (np.arange(64*64, dtype=np.uint32)*13+1).astype(np.uint16).reshape(64, 64)
            Image.fromarray(pixels).save(path)
            shapes = [Shape("rectangle", (.25, .25, .75, .75)),
                      Shape("arrow", (.1, .1, .9, .9), width=1, arrow_size=5)]
            roi = list(make_panels([str(path)], shapes, (64, 64)))[1]
            actual = roi.arrow_source.render((128, 128), Image.Resampling.NEAREST)
            self.assertIsInstance(actual, Image16)
            self.assertEqual(actual.size, (128, 128))
            expected = np.repeat(np.repeat(pixels[16:48, 16:48], 4, axis=0), 4, axis=1)
            # Upper-right pixels are away from the diagonal and remain exact.
            np.testing.assert_array_equal(actual.pixels[:20, -20:, 0], expected[:20, -20:])
            self.assertTrue(np.any(actual.pixels[:, :, 0] != expected))
            self.assertTrue(np.all(actual.pixels[:, :, 3] == 65535))


if __name__ == "__main__":
    unittest.main()
