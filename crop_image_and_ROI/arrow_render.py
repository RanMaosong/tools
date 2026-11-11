"""Antialias arrow overlays without resampling the underlying image."""
import math

from PIL import Image, ImageDraw

from high_depth import Image16
from settings import CONFIG


def draw_arrows(image, arrows):
    result = image.copy()
    scale = CONFIG["editor"]["arrow_antialias_scale"]
    tile = CONFIG["editor"]["annotation_tile_size"]
    for box, polygon, width, color in arrows:
        width = max(1, round(width))
        points = [*zip(box[::2], box[1::2]), *polygon]
        left = max(0, math.floor(min(x for x, y in points)-width))
        top = max(0, math.floor(min(y for x, y in points)-width))
        right = min(image.width, math.ceil(max(x for x, y in points)+width+1))
        bottom = min(image.height, math.ceil(max(y for x, y in points)+width+1))
        # Tiling keeps supersampling memory bounded for very large images.
        for y in range(top, bottom, tile):
            for x in range(left, right, tile):
                w, h = min(tile, right-x), min(tile, bottom-y)
                layer = Image.new("RGBA", (w*scale, h*scale))
                draw = ImageDraw.Draw(layer)
                phase = (scale-1)/2

                def point(px, py):
                    # Quantize before subtracting the tile origin, so clipping
                    # a negative local coordinate cannot create tile seams.
                    return math.floor(px*scale+phase)-x*scale, math.floor(py*scale+phase)-y*scale

                line = [*point(*box[:2]), *point(*box[2:])]
                axis_aligned = box[0] == box[2] or box[1] == box[3]
                if not axis_aligned:
                    draw.line(line, fill=color, width=width*scale)
                draw.polygon([point(px, py) for px, py in polygon], fill=color, outline=color, width=width*scale)
                # Area coverage softens only the edge, with no blur/ringing
                # spreading into neighboring source pixels.
                if scale != 1:
                    layer = layer.resize((w, h), Image.Resampling.BOX)
                if axis_aligned:
                    # Preserve the exact original integer-pixel stroke width
                    # on horizontal/vertical shafts; only the head needs AA.
                    ImageDraw.Draw(layer).line((box[0]-x, box[1]-y, box[2]-x, box[3]-y), fill=color, width=width)
                if layer.getbbox() is None:
                    continue
                if isinstance(result, Image16):
                    result.overlay_annotations(layer, (x, y))
                else:
                    patch = result.crop((x, y, x+w, y+h)).convert("RGBA")
                    patch = Image.alpha_composite(patch, layer)
                    result.paste(patch.convert(result.mode), (x, y))
    return result
