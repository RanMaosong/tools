from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import png
import tifffile
from PIL import Image

from high_depth import Image16, display_image
from image_core import (LayoutOptions, Panel, Shape, annotate, export_batch, layout_plan, move_panel_to,
                        load_image, make_panels, render_layout, save_lossless)


class HighDepthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, channels=1, interlace=False, transparent=None):
        values = ((np.arange(20*12*channels, dtype=np.uint32)*109 + 1) % 65536).astype(np.uint16)
        values = values.reshape(12, 20, channels)
        values[0, 0, 0] = 65535
        path = self.path / f"source-{channels}.png"
        with path.open("wb") as stream:
            png.Writer(20, 12, bitdepth=16, greyscale=channels in (1, 2), alpha=channels in (2, 4),
                       interlace=interlace, transparent=transparent).write(stream, values.reshape(12, -1))
        expected = np.empty((12, 20, 4), dtype=np.uint16)
        expected[:, :, :3] = values[:, :, :1] if channels in (1, 2) else values[:, :, :3]
        expected[:, :, 3] = values[:, :, -1] if channels in (2, 4) else 65535
        if transparent is not None:
            expected[np.all(values == transparent, axis=2), 3] = 0
        return path, expected

    def decode_png(self, path):
        with open(path, "rb") as stream:
            width, height, rows, info = png.Reader(file=stream).read()
            self.assertEqual(info["bitdepth"], 16)
            self.assertEqual(info["planes"], 4)
            return np.vstack(list(rows)).reshape(height, width, 4)

    def test_gray_grayalpha_rgb_rgba_and_interlaced_png_keep_all_samples(self):
        for channels in (1, 2, 3, 4):
            for interlace in (False, True):
                with self.subTest(channels=channels, interlace=interlace):
                    path, expected = self.source(channels, interlace)
                    before = path.read_bytes()
                    image = load_image(path)
                    self.assertIsInstance(image, Image16)
                    np.testing.assert_array_equal(image.pixels, expected)
                    np.testing.assert_array_equal(load_image(path, 90).pixels, np.rot90(expected))
                    self.assertEqual(path.read_bytes(), before)

    def test_png_transparent_key_keeps_hidden_rgb_and_alpha(self):
        path, expected = self.source(3, transparent=(65535, 110, 219))
        image = load_image(path)
        np.testing.assert_array_equal(image.pixels, expected)
        self.assertEqual(image.getpixel((0, 0)), (65535, 110, 219, 0))
        save_lossless(image, self.path / "transparent.png")
        np.testing.assert_array_equal(self.decode_png(self.path / "transparent.png"), expected)

    def test_png_significant_bits_and_exif_do_not_truncate_or_misorient_samples(self):
        path, expected = self.source(3)
        with path.open("wb") as stream:
            writer = png.Writer(20, 12, greyscale=False, bitdepth=16)
            writer.write_preamble(stream)
            png.write_chunk(stream, b"sBIT", bytes((12, 12, 12)))
            # Write the IDAT/IEND chunks after the preamble and sBIT chunk.
            import io
            encoded = io.BytesIO()
            writer.write(encoded, expected[:, :, :3].reshape(12, -1))
            stream.write(encoded.getvalue()[33:])
        np.testing.assert_array_equal(load_image(path).pixels, expected)
        gray = expected[:, :, 0]
        exif = Image.Exif()
        exif[274] = 6
        path = self.path / "orientation.png"
        Image.fromarray(gray).save(path, exif=exif)
        np.testing.assert_array_equal(load_image(path).pixels[:, :, 0], np.rot90(gray, 3))

    def test_png_and_tiff_roundtrip_preserve_low_bits_alpha_icc_and_dpi(self):
        path, expected = self.source(4)
        image = load_image(path)
        image.info["icc_profile"] = b"test-icc-profile"
        for suffix in (".png", ".tif"):
            with self.subTest(suffix=suffix):
                output = self.path / ("saved"+suffix)
                save_lossless(image, output, 600)
                actual = self.decode_png(output) if suffix == ".png" else tifffile.imread(output)
                self.assertEqual(actual.dtype, np.uint16)
                np.testing.assert_array_equal(actual, expected)
                reread = load_image(output)
                np.testing.assert_array_equal(reread.pixels, expected)
                self.assertEqual(reread.info["icc_profile"], b"test-icc-profile")
                with Image.open(output) as metadata:
                    self.assertAlmostEqual(metadata.info["dpi"][0], 600, delta=.1)

    def test_16bit_gray_and_planar_rgb_tiff_load_without_truncation(self):
        _, expected = self.source(4)
        for kind in ("gray", "rgb", "planar"):
            path = self.path / (kind+".tif")
            if kind == "gray":
                tifffile.imwrite(path, expected[:, :, 0], photometric="minisblack")
                result = np.repeat(expected[:, :, :1], 4, axis=2)
                result[:, :, 3] = 65535
            else:
                rgb = expected[:, :, :3]
                tifffile.imwrite(path, rgb if kind == "rgb" else np.moveaxis(rgb, -1, 0),
                                 photometric="rgb", planarconfig="contig" if kind == "rgb" else "separate")
                result = expected.copy()
                result[:, :, 3] = 65535
            np.testing.assert_array_equal(load_image(path).pixels, result)

    def test_annotations_change_only_marked_pixels_and_keep_original_arrow_geometry(self):
        path, expected = self.source(4)
        image = load_image(path)
        shapes = [Shape("rectangle", (.1, .1, .9, .9), width=1),
                  Shape("arrow", (.2, .5, .7, .5), width=1, arrow_size=2)]
        for kind in ("rectangle", "arrow"):
            layer = annotate(Image.new("RGBA", image.size), shapes, image.size, kind)
            overlay = np.asarray(layer, dtype=np.uint16)
            mask = overlay[:, :, 3] != 0
            self.assertTrue(np.any(mask))
            wanted = expected.copy()
            coverage = overlay[:, :, 3:4].astype(np.float64)/255
            original_alpha = expected[:, :, 3:4].astype(np.float64)/65535
            alpha = coverage + original_alpha*(1-coverage)
            rgb = (overlay[:, :, :3]*257*coverage + expected[:, :, :3]*original_alpha*(1-coverage))/alpha
            wanted[:, :, :3] = np.floor(rgb+.5).astype(np.uint16)
            wanted[:, :, 3:4] = np.floor(alpha*65535+.5).astype(np.uint16)
            actual = annotate(image, shapes, image.size, kind)
            np.testing.assert_allclose(actual.pixels, wanted, atol=1)
            np.testing.assert_array_equal(actual.pixels[~mask], expected[~mask])
        np.testing.assert_array_equal(image.pixels, expected)
        paths = export_batch([str(path)], shapes, image.size, self.path / "batch")
        panels = list(make_panels([str(path)], shapes, image.size))
        for filename, panel in zip(paths, panels):
            np.testing.assert_array_equal(self.decode_png(filename), panel.image.pixels)
        arrows = annotate(image, shapes, image.size, "arrow")
        np.testing.assert_array_equal(panels[1].image.pixels, arrows.pixels[1:11, 2:18])

    def test_layout_promotes_8bit_and_keeps_16bit_samples_after_preview(self):
        path, expected = self.source(4)
        high = load_image(path)
        low = Image.new("RGBA", (20, 12), (3, 17, 254, 0))
        panels = [Panel("16", "16", "original", "high", high), Panel("8", "8", "original", "low", low)]
        options = LayoutOptions(mode="free", columns=2, gap=3, margin=2, labels=False)
        preview, _, _ = render_layout(panels, options, (30, 20))
        self.assertIsInstance(preview, Image.Image)
        rendered, size, _ = render_layout(panels, options)
        self.assertIsInstance(rendered, Image16)
        plan, _ = layout_plan(panels, options)
        a, b = plan
        np.testing.assert_array_equal(rendered.pixels[a.y:a.y+12, a.x:a.x+20], expected)
        np.testing.assert_array_equal(rendered.pixels[b.y:b.y+12, b.x:b.x+20],
                                      np.asarray(low, dtype=np.uint16)*257)
        save_lossless(rendered, self.path / "layout.png")
        decoded = self.decode_png(self.path / "layout.png")
        self.assertEqual((decoded.shape[1], decoded.shape[0]), size)
        np.testing.assert_array_equal(decoded, rendered.pixels)
        np.testing.assert_array_equal(high.pixels, expected)

    def test_nearest_resize_copies_16bit_pixels_and_lanczos_never_uses_8bit(self):
        path, expected = self.source(4)
        high = load_image(path)
        nearest = high.resize((40, 24), Image.Resampling.NEAREST)
        np.testing.assert_array_equal(nearest.pixels, np.repeat(np.repeat(expected, 2, axis=0), 2, axis=1))
        smooth = high.resize((27, 19), Image.Resampling.LANCZOS)
        reference = np.asarray(Image.fromarray(expected[:, :, 0]).convert("F").resize((27, 19), Image.Resampling.LANCZOS))
        reference = np.clip(np.rint(reference), 0, 65535).astype(np.uint16)
        np.testing.assert_array_equal(smooth.pixels[:, :, 0], reference)
        self.assertTrue(np.any(smooth.pixels % 257 != 0))
        shown = display_image(high)
        self.assertIsInstance(shown, Image.Image)
        self.assertEqual(shown.getpixel((0, 0))[0], 255)
        np.testing.assert_array_equal(high.pixels, expected)

    def test_manual_negative_position_rebases_layout_without_changing_16bit_samples(self):
        path, expected = self.source(4)
        high = load_image(path)
        low = Image.new("RGB", (20, 12), "blue")
        panels = [Panel("high", "high", "original", "high", high), Panel("low", "low", "original", "low", low)]
        options = LayoutOptions(mode="free", columns=2, gap=0, margin=0)
        options = move_panel_to(panels, options, "high", -5, -3)
        plan, size = layout_plan(panels, options)
        self.assertEqual([(p.x, p.y) for p in plan], [(0, 0), (25, 3)])
        rendered, _, _ = render_layout(panels, options)
        self.assertIsInstance(rendered, Image16)
        np.testing.assert_array_equal(rendered.pixels[:12, :20], expected)
        destination = self.path / "positioned-16bit.png"
        save_lossless(rendered, destination)
        saved = self.decode_png(destination)
        self.assertEqual((saved.shape[1], saved.shape[0]), size)
        np.testing.assert_array_equal(saved[:12, :20], expected)

    def test_blank_placeholder_keeps_16bit_samples_profile_and_matching_cell_size(self):
        path, expected = self.source(4)
        high = load_image(path)
        high.info["icc_profile"] = b"same-profile"
        blank = Panel("blank", "blank", "extra", "blank", Image.new("RGB", (1, 1)), blank_size=(512, 512))
        panels = [blank, Panel("source", "source", "original", "source", high)]
        options = LayoutOptions(mode="rows", columns=2, gap=0, margin=0, background="#123456")
        plan, _ = layout_plan(panels, options)
        self.assertEqual([(p.width, p.height) for p in plan], [(20, 12), (20, 12)])
        rendered, _, _ = render_layout(panels, options)
        self.assertIsInstance(rendered, Image16)
        self.assertEqual(rendered.info["icc_profile"], b"same-profile")
        self.assertEqual(rendered.getpixel((0, 0)), (18*257, 52*257, 86*257, 65535))
        np.testing.assert_array_equal(rendered.pixels[:, 20:], expected)

    def test_overlapping_transparent_blank_preserves_all_16bit_channels(self):
        path, expected = self.source(4)
        high = load_image(path)
        panels = [Panel("source", "source", "original", "source", high),
                  Panel("blank", "blank", "extra", "blank", Image.new("RGB", (1, 1)), blank_size=(512, 512))]
        options = move_panel_to(panels, LayoutOptions(mode="free", columns=2, margin=0, gap=0), "blank", 0, 0)
        rendered, _, _ = render_layout(panels, options)
        np.testing.assert_array_equal(rendered.pixels, expected)
        destination = self.path / "overlapping-blank.png"
        save_lossless(rendered, destination)
        np.testing.assert_array_equal(self.decode_png(destination), expected)

    def test_failed_high_depth_save_keeps_existing_file(self):
        path, _ = self.source()
        destination = self.path / "keep.png"
        destination.write_bytes(b"existing-file")
        with patch("high_depth.png.Writer.write", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                save_lossless(load_image(path), destination)
        self.assertEqual(destination.read_bytes(), b"existing-file")
        self.assertEqual(sorted(p.name for p in self.path.iterdir()), ["keep.png", path.name])


if __name__ == "__main__":
    unittest.main()
