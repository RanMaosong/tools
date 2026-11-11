"""16-bit RGBA pixels kept separate from Pillow's 8-bit display images."""
import zlib

import numpy as np
import png
import tifffile
from PIL import Image, ImageColor


MAX16 = 65535
BYTE_SCALE = 257


class Image16:
    mode = "RGBA;16"

    def __init__(self, pixels, info=None):
        if pixels.dtype != np.uint16 or pixels.ndim != 3 or pixels.shape[2] != 4:
            raise ValueError("16 位图像必须是 uint16 RGBA 数据")
        self.pixels = np.ascontiguousarray(pixels)
        self.info = dict(info or {})

    @property
    def size(self):
        return self.width, self.height

    @property
    def width(self):
        return self.pixels.shape[1]

    @property
    def height(self):
        return self.pixels.shape[0]

    @classmethod
    def new(cls, size, color):
        pixels = np.empty((size[1], size[0], 4), dtype=np.uint16)
        pixels[:] = np.array(ImageColor.getcolor(color, "RGBA"), dtype=np.uint16) * BYTE_SCALE
        return cls(pixels)

    def copy(self):
        return Image16(self.pixels.copy(), self.info)

    def close(self):
        pass

    def tobytes(self):
        return self.pixels.tobytes()

    def getpixel(self, point):
        return tuple(int(value) for value in self.pixels[point[1], point[0]])

    def crop(self, box):
        left, top, right, bottom = box
        pixels = np.zeros((bottom-top, right-left, 4), dtype=np.uint16)
        x1, y1, x2, y2 = max(0, left), max(0, top), min(self.width, right), min(self.height, bottom)
        if x2 > x1 and y2 > y1:
            pixels[y1-top:y2-top, x1-left:x2-left] = self.pixels[y1:y2, x1:x2]
        return Image16(pixels, self.info)

    def transpose(self, method):
        operations = {
            Image.Transpose.FLIP_LEFT_RIGHT: lambda a: a[:, ::-1],
            Image.Transpose.FLIP_TOP_BOTTOM: lambda a: a[::-1],
            Image.Transpose.ROTATE_90: lambda a: np.rot90(a),
            Image.Transpose.ROTATE_180: lambda a: np.rot90(a, 2),
            Image.Transpose.ROTATE_270: lambda a: np.rot90(a, 3),
            Image.Transpose.TRANSPOSE: lambda a: a.swapaxes(0, 1),
            Image.Transpose.TRANSVERSE: lambda a: a.swapaxes(0, 1)[::-1, ::-1],
        }
        return Image16(operations[method](self.pixels).copy(), self.info)

    def resize(self, size, resample=Image.Resampling.NEAREST):
        channels = []
        for channel in range(4):
            band = Image.fromarray(self.pixels[:, :, channel])
            if resample == Image.Resampling.NEAREST:
                values = np.asarray(band.resize(size, resample), dtype=np.uint16)
            else:
                # Float32 exactly represents all uint16 integers. Interpolate
                # before rounding back to uint16, without an 8-bit intermediate.
                values = np.asarray(band.convert("F").resize(size, resample))
                values = np.clip(np.rint(values), 0, MAX16).astype(np.uint16)
            channels.append(values)
        return Image16(np.stack(channels, axis=2), self.info)

    def display(self):
        # A fixed full-range mapping is for screen display only. Never use this
        # result as the source of a crop, annotation or saved layout.
        pixels = np.rint(self.pixels.astype(np.float32) / BYTE_SCALE).astype(np.uint8)
        return Image.fromarray(pixels)

    def paste(self, image, position):
        pixels = image.pixels if isinstance(image, Image16) else np.asarray(image.convert("RGBA"), dtype=np.uint16) * BYTE_SCALE
        x, y = position
        self.pixels[y:y+pixels.shape[0], x:x+pixels.shape[1]] = pixels

    def overlay_annotations(self, layer, position=(0, 0)):
        values = np.asarray(layer)
        mask = values[:, :, 3] != 0
        x, y = position
        target = self.pixels[y:y+layer.height, x:x+layer.width]
        source = values[mask].astype(np.uint64)
        dest = target[mask].astype(np.uint64)
        alpha = source[:, 3:4]
        alpha_numer = alpha*MAX16 + dest[:, 3:4]*(255-alpha)
        color_numer = source[:, :3]*BYTE_SCALE*alpha*MAX16 + dest[:, :3]*dest[:, 3:4]*(255-alpha)
        blended = np.empty_like(dest)
        blended[:, :3] = (color_numer + alpha_numer//2)//alpha_numer
        blended[:, 3:4] = (alpha_numer+127)//255
        target[mask] = blended.astype(np.uint16)


def display_image(image):
    return image.display() if isinstance(image, Image16) else image


def _rgba16(values, greyscale, alpha, transparent=None):
    height, width, _ = values.shape
    pixels = np.empty((height, width, 4), dtype=np.uint16)
    pixels[:, :, :3] = values[:, :, :1] if greyscale else values[:, :, :3]
    pixels[:, :, 3] = values[:, :, -1] if alpha else MAX16
    if transparent is not None and not alpha:
        match = np.all(values == np.asarray(transparent, dtype=np.uint16), axis=2)
        pixels[match, 3] = 0
    return pixels


def read_png16(path, info):
    with open(path, "rb") as stream:
        width, height, rows, metadata = png.Reader(file=stream).read()
        # read(), unlike asDirect(), retains the actual samples even with sBIT.
        values = np.vstack([np.asarray(row, dtype=np.uint16) for row in rows])
    values = values.reshape(height, width, metadata["planes"])
    return Image16(_rgba16(values, metadata["greyscale"], metadata["alpha"], metadata.get("transparent")), info)


def read_tiff16(path, info):
    with tifffile.TiffFile(path) as source:
        page = source.pages[0]
        if page.photometric not in (1, 2):
            raise ValueError("16 位 TIFF 仅支持普通灰度或 RGB/RGBA 图像")
        values = page.asarray()
        if page.planarconfig == 2:
            values = np.moveaxis(values, 0, -1)
        if values.ndim == 2:
            values = values[:, :, None]
        greyscale = page.photometric == 1
        if values.dtype != np.uint16 or values.shape[2] not in ((1, 2) if greyscale else (3, 4)):
            raise ValueError("仅支持无符号 16 位灰度或 RGB/RGBA TIFF")
        if any(value == 1 for value in page.extrasamples):
            raise ValueError("暂不转换预乘透明度的 16 位 TIFF，以免改变原始像素")
        return Image16(_rgba16(values, greyscale, values.shape[2] in (2, 4)), info)


def save_image16(image, path, dpi):
    if str(path).lower().endswith(".png"):
        class Writer(png.Writer):
            def write_preamble(self, stream):
                super().write_preamble(stream)
                if image.info.get("icc_profile"):
                    png.write_chunk(stream, b"iCCP", b"ICC profile\x00\x00" + zlib.compress(image.info["icc_profile"]))

        pixels_per_meter = round(dpi / 0.0254)
        writer = Writer(image.width, image.height, greyscale=False, alpha=True, bitdepth=16,
                        x_pixels_per_unit=pixels_per_meter, y_pixels_per_unit=pixels_per_meter, unit_is_meter=True)
        with open(path, "wb") as stream:
            writer.write(stream, image.pixels.reshape(image.height, image.width*4))
    else:
        # Deflate is lossless and needs no additional native compression codec.
        tifffile.imwrite(path, image.pixels, photometric="rgb", extrasamples="unassalpha", compression="deflate",
                         iccprofile=image.info.get("icc_profile"), resolution=(dpi, dpi), resolutionunit="INCH", metadata=None)
