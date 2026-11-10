"""Full-resolution image processing and layout, independent of the Tk interface.

Preview images must never be passed to these export functions. Coordinates are
fractions of the oriented source image, so a batch may contain different sizes.
"""
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
import tempfile
import os
from types import SimpleNamespace

from PIL import Image, ImageColor, ImageDraw, ImageOps
from settings import CONFIG


EDITOR = CONFIG["editor"]
LAYOUT = CONFIG["layout"]
LIMITS = CONFIG["limits"]
MAX_OUTPUT_PIXELS = LIMITS["max_output_pixels"]


@dataclass
class Shape:
    kind: str
    box: tuple[float, float, float, float]
    color: str = "#FF0000"
    width: int = EDITOR["line_width"]
    arrow_size: int = EDITOR["arrow_size"]
    # Arrow heads use the original tool's preview-pixel units. Stroke width is
    # always measured in reference-image pixels, for rectangles and arrows alike.
    preview_size: tuple[float, float] | None = None

    def validate(self):
        if self.kind not in ("rectangle", "arrow"):
            raise ValueError("未知标注类型")
        if len(self.box) != 4 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in self.box):
            raise ValueError("标注坐标必须位于图像内")
        if self.kind == "rectangle" and (self.box[2] <= self.box[0] or self.box[3] <= self.box[1]):
            raise ValueError("ROI 的宽和高必须大于零")
        ImageColor.getrgb(self.color)
        if not LIMITS["line_width_min"] <= self.width <= LIMITS["line_width_max"] or not LIMITS["arrow_size_min"] <= self.arrow_size <= LIMITS["arrow_size_max"]:
            raise ValueError("线宽或箭头大小超出范围")
        if self.preview_size is not None and (len(self.preview_size) != 2 or any(not math.isfinite(v) or v <= 0 for v in self.preview_size)):
            raise ValueError("无效的箭头参考预览尺寸")


def load_image(path, rotation=0):
    with open(path, "rb") as stream:
        header = stream.read(26)
    if header[:8] == b"\x89PNG\r\n\x1a\n" and len(header) > 24 and header[24] > 8:
        raise ValueError(f"{Path(path).name} 是 16 位 PNG；为避免静默转换成 8 位，暂不处理。")
    with Image.open(path) as source:
        # Pillow's color drawing is 8 bit. Refuse silent conversion of scientific
        # high-bit-depth / floating-point data instead of destroying its range.
        bits = source.tag_v2.get(258, ()) if hasattr(source, "tag_v2") else ()
        bits = (bits,) if isinstance(bits, int) else bits
        if source.mode not in ("1", "L", "LA", "P", "RGB", "RGBA") or any(b > 8 for b in bits):
            raise ValueError(f"{Path(path).name} 为 {source.mode} / 高位深图像。为避免精度损失，暂不转换；请使用已完成窗宽窗位映射的 8 位显示图。")
        image = ImageOps.exif_transpose(source)
        image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
        image.info = {key: value for key, value in source.info.items() if key in ("icc_profile", "dpi")}
        if rotation % 360:
            turns = {90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_270}
            image = image.transpose(turns[rotation % 360])
        return image


def pixel_box(shape, size):
    w, h = size
    x1, y1, x2, y2 = shape.box
    if shape.kind == "rectangle":
        # Shared rounding at both edges gives exact integer-aligned crops.
        left, top = max(0, min(w - 1, round(x1 * w))), max(0, min(h - 1, round(y1 * h)))
        return left, top, max(left + 1, min(w, round(x2 * w))), max(top + 1, min(h, round(y2 * h)))
    return x1 * w, y1 * h, x2 * w, y2 * h


def arrow_coordinates(shape):
    """Original arrow geometry; config defaults retain Lixing Dong's values."""
    point1 = [EDITOR["arrow_tip_offset"], 0]
    point2 = [-shape.arrow_size, shape.arrow_size * EDITOR["arrow_head_half_width_ratio"]]
    point3 = [-shape.arrow_size * EDITOR["arrow_notch_ratio"], 0]
    point4 = [-shape.arrow_size, -shape.arrow_size * EDITOR["arrow_head_half_width_ratio"]]

    angle = get_angle(shape.box[:2], shape.box[2:])
    new_point1 = coordinate_transformation(point1, angle, shape.box[2:])
    new_point2 = coordinate_transformation(point2, angle, shape.box[2:])
    new_point3 = coordinate_transformation(point3, angle, shape.box[2:])
    new_point4 = coordinate_transformation(point4, angle, shape.box[2:])
    return *new_point1, *new_point2, *new_point3, *new_point4


def coordinate_transformation(point, angle, dest_point):
    x, y = point
    dest_x, dest_y = dest_point
    new_x = x * math.cos(angle) - y * math.sin(angle) + dest_x
    new_y = x * math.sin(angle) + y * math.cos(angle) + dest_y
    return new_x, new_y


def get_angle(point1, point2):
    x1, y1 = point1
    x2, y2 = point2
    if y1 == y2 and x1 > x2:
        return math.pi
    elif y1 == y2 and x1 <= x2:
        return 0
    elif x1 == x2 and y1 > y2:
        return math.pi * 3 / 2
    elif x1 == x2 and y1 <= y2:
        return math.pi / 2
    slope = (y1 - y2) / (x1 - x2)
    angle = math.atan(slope)
    if angle < 0:
        angle += math.pi
    if y2 < y1:
        angle += math.pi
    return angle


def arrow_polygon(box, head):
    flat = arrow_coordinates(SimpleNamespace(box=box, arrow_size=head))
    return list(zip(flat[::2], flat[1::2]))


def arrow_geometry(shape, size, reference_size):
    """Map the original preview geometry to a preview or full-resolution source.

    Transform the finished polygon, including its +3 tip offset, so saving an
    image cannot change the arrow's size or position relative to the image.
    """
    basis = shape.preview_size or reference_size
    sx, sy = size[0] / basis[0], size[1] / basis[1]
    polygon = arrow_polygon(pixel_box(shape, basis), shape.arrow_size)
    return [(x*sx, y*sy) for x, y in polygon], stroke_width(shape, size, reference_size)


def stroke_width(shape, size, reference_size):
    """One stroke unit for all annotation types, independent of head geometry."""
    return shape.width * min(size[0] / reference_size[0], size[1] / reference_size[1])


def annotate(image, shapes, reference_size, kind):
    result = image.copy()
    draw = ImageDraw.Draw(result)
    for shape in shapes:
        shape.validate()
        if shape.kind != kind:
            continue
        box = pixel_box(shape, image.size)
        width = max(1, round(stroke_width(shape, image.size, reference_size)))
        if kind == "rectangle":
            # Crop coordinates are exclusive at right/bottom; draw inside them.
            draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), outline=shape.color, width=width)
        else:
            polygon, arrow_width = arrow_geometry(shape, image.size, reference_size)
            width = max(1, round(arrow_width))
            draw.line(box, fill=shape.color, width=width)
            draw.polygon(polygon, fill=shape.color, outline=shape.color, width=width)
    return result


@dataclass
class Panel:
    key: str
    group: str
    kind: str
    title: str
    image: Image.Image


def make_panels(files, shapes, reference_size, rotation=0):
    """Yield one source at a time; ROI pixels always come from the source."""
    rectangles = [shape for shape in shapes if shape.kind == "rectangle"]
    for index, filename in enumerate(files):
        image = load_image(filename, rotation)
        group = str(index)
        title = Path(filename).stem
        yield Panel(f"{group}:original", group, "original", title, annotate(image, shapes, reference_size, "rectangle"))
        arrows = annotate(image, shapes, reference_size, "arrow")
        for roi_index, shape in enumerate(rectangles):
            roi = arrows.crop(pixel_box(shape, image.size))
            roi.info = image.info.copy()
            yield Panel(f"{group}:roi-{roi_index}", group, f"roi-{roi_index}", f"{title} · ROI {roi_index + 1}", roi)


def save_lossless(image, path, dpi=LAYOUT["dpi"]):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in (".png", ".tif", ".tiff"):
        raise ValueError("仅支持无损 PNG 或 TIFF 导出")
    path.parent.mkdir(parents=True, exist_ok=True)
    options = {"dpi": (dpi, dpi)}
    if image.info.get("icc_profile"):
        options["icc_profile"] = image.info["icc_profile"]
    if suffix != ".png":
        options["compression"] = "tiff_lzw"
    # A failed export must not truncate an existing result.
    handle, temp_path = tempfile.mkstemp(dir=path.parent, suffix=suffix)
    os.close(handle)
    try:
        image.save(temp_path, format="PNG" if suffix == ".png" else "TIFF", **options)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def export_batch(files, shapes, reference_size, directory, rotation=0, extension=".png"):
    directory = Path(directory)
    names, used = [], set()
    for filename in files:
        stem = Path(filename).stem
        # Windows filesystems are case insensitive; avoid duplicate basenames.
        candidate, count = stem, 2
        while candidate.casefold() in used:
            candidate = f"{stem}_{count}"
            count += 1
        names.append(candidate)
        used.add(candidate.casefold())
    records = []
    for panel in make_panels(files, shapes, reference_size, rotation):
        folder = "rectange" if panel.kind == "original" else panel.kind
        path = directory / folder / (names[int(panel.group)] + extension)
        save_lossless(panel.image, path)
        records.append(str(path))
    coordinates = {"version": 1, "reference_size": reference_size, "rotation": rotation,
                   "coordinate_system": "normalized_oriented_image", "files": list(files),
                   "shapes": [asdict(s) for s in shapes]}
    (directory / "coordinates.json").write_text(json.dumps(coordinates, ensure_ascii=False, indent=2), encoding="utf-8")
    return records


def import_panels(files, rotation=0):
    """Recognize the original tool's rectange/roi-N output folders."""
    for index, filename in enumerate(files):
        path = Path(filename)
        folder = path.parent.name.lower()
        roi = folder.startswith("roi-") and folder[4:].isdigit()
        recognized = roi or folder in ("rectange", "rectangle")
        group = f"method:{path.stem.casefold()}" if recognized else f"file:{index}"
        kind = folder if roi else "original"
        title = f"{path.stem} · ROI {int(folder[4:]) + 1}" if roi else path.stem
        yield Panel(f"import:{index}", group, kind, title, load_image(path, rotation))


@dataclass
class LayoutOptions:
    mode: str = "relative"
    columns: int = LAYOUT["columns"]
    gap: int = LAYOUT["gap"]
    margin: int = LAYOUT["margin"]
    panel_width: int = LAYOUT["panel_width"]
    roi_scale: float = LAYOUT["roi_scale"]
    interpolation: str = "nearest"
    background: str = "#FFFFFF"
    labels: bool = False
    label_size: int = LAYOUT["label_size"]
    dpi: int = LAYOUT["dpi"]
    roi_settings: dict = field(default_factory=dict)

    def validate(self):
        if self.mode not in ("relative", "below", "right", "rows", "free"):
            raise ValueError("未知排版模式")
        if not LIMITS["columns_min"] <= self.columns <= LIMITS["columns_max"] or not 0 <= self.gap <= LIMITS["spacing_max"] or not 0 <= self.margin <= LIMITS["spacing_max"]:
            raise ValueError("每行数量、间距或边距超出 config.json 中 limits 配置范围")
        if not 0 <= self.panel_width <= LIMITS["panel_width_max"] or not LIMITS["roi_scale_min"] <= self.roi_scale <= LIMITS["roi_scale_max"]:
            raise ValueError("宽度或 ROI 放大倍数超出 config.json 中 limits 配置范围")
        if not LIMITS["label_size_min"] <= self.label_size <= LIMITS["label_size_max"] or not LIMITS["dpi_min"] <= self.dpi <= LIMITS["dpi_max"]:
            raise ValueError("标题大小或 DPI 超出 config.json 中 limits 配置范围")
        if self.interpolation not in ("nearest", "lanczos"):
            raise ValueError("未知放大算法")
        ImageColor.getrgb(self.background)
        if not isinstance(self.roi_settings, dict):
            raise ValueError("ROI 设置必须是对象")
        for kind, setting in self.roi_settings.items():
            if not isinstance(kind, str) or not kind.startswith("roi-") or not kind[4:].isdigit() or not isinstance(setting, dict):
                raise ValueError("无效的 ROI 设置")
            for dimension in ("width", "height"):
                value = setting.get(dimension)
                if type(value) is not int or not 1 <= value <= LIMITS["panel_width_max"]:
                    raise ValueError(f"{kind} 的宽、高必须为 1–{LIMITS['panel_width_max']} 的整数像素")
            if "x_edge" in setting:
                if setting.get("x_edge") not in ("left", "right") or setting.get("y_edge") not in ("top", "bottom"):
                    raise ValueError(f"{kind} 的左右或上下边缘基准无效")
                for name in ("x_distance", "y_distance"):
                    if type(setting.get(name)) is not int or not -LIMITS["panel_width_max"] <= setting[name] <= LIMITS["panel_width_max"]:
                        raise ValueError(f"{kind} 的边距必须为 ±{LIMITS['panel_width_max']} 范围内的整数像素")
            elif "x_direction" in setting:
                if setting.get("x_direction") not in ("left", "right") or setting.get("y_direction") not in ("up", "down"):
                    raise ValueError(f"{kind} 的水平或垂直方向无效")
                for name in ("x_distance", "y_distance"):
                    if type(setting.get(name)) is not int or not 0 <= setting[name] <= LIMITS["panel_width_max"]:
                        raise ValueError(f"{kind} 的水平、垂直距离必须为 0–{LIMITS['panel_width_max']} 的整数像素")
            else:
                # Compatibility with projects made before manual X/Y offsets.
                if setting.get("position") not in ("top", "bottom", "left", "right"):
                    raise ValueError(f"{kind} 的相对位置无效")
                if not isinstance(setting.get("relative_to"), str) or setting["relative_to"] not in {"original", *self.roi_settings}:
                    raise ValueError(f"{kind} 的相对对象不存在")
        visited, active = set(), set()

        def visit(kind):
            if kind == "original" or kind in visited:
                return
            if kind in active:
                raise ValueError("ROI 相对位置存在循环引用，请调整相对对象")
            active.add(kind)
            if "x_direction" not in self.roi_settings[kind] and "x_edge" not in self.roi_settings[kind]:
                visit(self.roi_settings[kind]["relative_to"])
            active.remove(kind)
            visited.add(kind)
        for kind in self.roi_settings:
            visit(kind)


@dataclass
class Placement:
    panel: Panel
    x: int
    y: int
    width: int
    height: int


def layout_plan(panels, options):
    options.validate()
    if not panels:
        raise ValueError("请先生成排版素材或导入图片")
    gap, label = options.gap, options.label_size + LAYOUT["label_padding"] if options.labels else 0
    # Resolve a stable reference without letting an extra image reference its
    # own unscaled source when dragged to the front. In grouped modes the first
    # method's original is its first image; free mode follows individual order.
    reference = next((p for p in panels if p.kind != "extra"), panels[0])
    if options.mode != "free":
        reference = next((p for p in panels if p.group == reference.group and p.kind == "original"), reference)

    def size(panel):
        image = panel.image
        if panel.kind == "extra":
            return reference.image.size if reference.kind == "extra" else size(reference)
        setting = options.roi_settings.get(panel.kind)
        if setting:
            width, height = setting["width"], setting["height"]
            if width < image.width or height < image.height:
                raise ValueError(f"{panel.title} 的原始 ROI 为 {image.width} × {image.height}px；输入尺寸 {width} × {height}px 不能小于原始尺寸，以免降低画质。")
            return width, height
        width = options.panel_width or round(image.width * (options.roi_scale if panel.kind.startswith("roi-") else 1))
        if width < image.width:
            raise ValueError(f"{panel.title} 的原始宽度为 {image.width}px；统一宽度不能小于此值。设为 0 可保留各自原始像素。")
        return width, max(1, round(image.height * width / image.width))

    def block(items, horizontal=False):
        placements, x, y, w, h = [], 0, 0, 0, 0
        for panel in items:
            pw, ph = size(panel)
            placements.append(Placement(panel, x, y, pw, ph))
            w, h = max(w, x + pw), max(h, y + ph + label)
            if horizontal:
                x += pw + gap
            else:
                y += ph + label + gap
        return placements, w, h

    def relative_block(items):
        lookup = {panel.kind: panel for panel in items}
        if "original" not in lookup:
            return block(items)
        placed = {}

        def place(kind):
            if kind in placed:
                return placed[kind]
            if kind not in lookup:
                raise ValueError(f"相对对象 {kind} 在当前方法组中缺失")
            panel = lookup[kind]
            width, height = size(panel)
            if kind == "original":
                item = Placement(panel, 0, 0, width, height)
            elif "x_edge" in options.roi_settings.get(kind, {}):
                setting = options.roi_settings[kind]
                anchor = place("original")
                # Insets between corresponding edges. Zero aligns the edges;
                # positive distances point inward and negative ones outward.
                x = anchor.x + (setting["x_distance"] if setting["x_edge"] == "left"
                                else anchor.width-width-setting["x_distance"])
                y = anchor.y + (setting["y_distance"] if setting["y_edge"] == "top"
                                else anchor.height-height-setting["y_distance"])
                item = Placement(panel, x, y, width, height)
            elif "x_direction" in options.roi_settings.get(kind, {}):
                # Exact offsets from the original's top-left corner. Deliberate
                # overlaps are allowed; never move a user-entered coordinate.
                setting = options.roi_settings[kind]
                anchor = place("original")
                x = anchor.x + setting["x_distance"] * (-1 if setting["x_direction"] == "left" else 1)
                y = anchor.y + setting["y_distance"] * (-1 if setting["y_direction"] == "up" else 1)
                item = Placement(panel, x, y, width, height)
            else:
                setting = options.roi_settings.get(kind, {"relative_to": "original", "position": "bottom"})
                anchor = place(setting["relative_to"])
                direction = setting["position"]
                x = anchor.x + (anchor.width-width)//2
                y = anchor.y + (anchor.height-height)//2
                if direction == "top":
                    y = anchor.y-height-label-gap
                elif direction == "bottom":
                    y = anchor.y+anchor.height+label+gap
                elif direction == "left":
                    x = anchor.x-width-gap
                else:
                    x = anchor.x+anchor.width+gap
                # Keep the requested direction and move outward when siblings
                # would overlap. This also handles oversized top/left ROIs.
                while True:
                    collisions = [other for other in placed.values()
                                  if not (x+width+gap <= other.x or other.x+other.width+gap <= x or
                                          y+height+label+gap <= other.y or other.y+other.height+label+gap <= y)]
                    if not collisions:
                        break
                    if direction == "top":
                        y = min(other.y for other in collisions)-height-label-gap
                    elif direction == "bottom":
                        y = max(other.y+other.height+label for other in collisions)+gap
                    elif direction == "left":
                        x = min(other.x for other in collisions)-width-gap
                    else:
                        x = max(other.x+other.width for other in collisions)+gap
                item = Placement(panel, x, y, width, height)
            placed[kind] = item
            return item

        for panel in items:
            place(panel.kind)
        min_x, min_y = min(p.x for p in placed.values()), min(p.y for p in placed.values())
        result = [Placement(p.panel, p.x-min_x, p.y-min_y, p.width, p.height) for p in placed.values()]
        return result, max(p.x+p.width for p in result), max(p.y+p.height+label for p in result)

    groups = {}
    for panel in panels:
        groups.setdefault(panel.group, []).append(panel)
    for group in groups.values():
        group.sort(key=lambda p: (p.kind != "original", int(p.kind[4:]) if p.kind.startswith("roi-") else 0))
    rows = []
    if options.mode == "rows":
        grouped = list(groups.values())
        # Align method columns across original and all ROI rows in each block.
        for start in range(0, len(grouped), options.columns):
            chunk = grouped[start:start + options.columns]
            kinds = list(dict.fromkeys("original" if p.kind == "extra" else p.kind for group in chunk for p in group))
            widths = [max(size(p)[0] for p in group) for group in chunk]
            for kind in kinds:
                cells = []
                for group, width in zip(chunk, widths):
                    items = [p for p in group if ("original" if p.kind == "extra" else p.kind) == kind]
                    cell, _, height = block(items)
                    cells.append((cell, width, height))
                rows.append(cells)
    else:
        blocks = ([block([p]) for p in panels] if options.mode == "free" else
                  [relative_block(group) if options.mode == "relative" else block(group, options.mode == "right") for group in groups.values()])
        rows = [blocks[i:i + options.columns] for i in range(0, len(blocks), options.columns)]
    result, y, total_width = [], options.margin, 0
    for row in rows:
        x = options.margin
        row_height = max(cell[2] for cell in row)
        for placements, width, _ in row:
            for item in placements:
                result.append(Placement(item.panel, x + item.x, y + item.y, item.width, item.height))
            x += width + gap
        total_width = max(total_width, x - gap + options.margin)
        y += row_height + gap
    return result, (total_width, y - gap + options.margin)


def label_font(size):
    from PIL import ImageFont
    for name in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render_layout(panels, options, preview_size=None):
    placements, (width, height) = layout_plan(panels, options)
    if preview_size:
        factor = min(preview_size[0] / width, preview_size[1] / height, 1)
    else:
        factor = 1
        if width * height > MAX_OUTPUT_PIXELS:
            raise ValueError(f"排版为 {width} × {height}，超过配置上限 {MAX_OUTPUT_PIXELS:,} 像素。请减少每批图片或调整 ROI 输出尺寸；不会自动降低分辨率。")
    canvas = Image.new("RGBA", (max(1, math.ceil(width * factor)), max(1, math.ceil(height * factor))), options.background)
    draw = ImageDraw.Draw(canvas)
    font = label_font(max(LAYOUT["preview_min_font_size"], round(options.label_size * factor))) if options.labels else None
    resample = Image.Resampling.NEAREST if options.interpolation == "nearest" else Image.Resampling.LANCZOS
    hitboxes = []
    for item in placements:
        x, y = round(item.x * factor), round(item.y * factor)
        w, h = max(1, round(item.width * factor)), max(1, round(item.height * factor))
        image = item.panel.image
        # A user-entered ROI size is a bounding cell. Preserve aspect ratio and
        # center the image, padding with the background instead of stretching it.
        if item.panel.kind == "extra":
            # User-requested exact resize for residual/other standalone images.
            # Always resample from the retained source, never a previous preview.
            image_w, image_h = w, h
        else:
            fit = min(item.width/item.panel.image.width, item.height/item.panel.image.height)
            image_w = min(w, max(1, round(item.panel.image.width*fit*factor)))
            image_h = min(h, max(1, round(item.panel.image.height*fit*factor)))
        if image.size != (image_w, image_h):
            image = image.resize((image_w, image_h), Image.Resampling.LANCZOS if preview_size or item.panel.kind == "extra" else resample)
        # Paste without mask keeps RGBA pixel values, including source alpha.
        canvas.paste(image, (x+(w-image_w)//2, y+(h-image_h)//2))
        if font:
            # Clip labels to their own cells; never overwrite neighboring images.
            label_h = max(1, round((options.label_size + LAYOUT["label_padding"]) * factor))
            strip = Image.new("RGBA", (w, label_h), options.background)
            ImageDraw.Draw(strip).text((LAYOUT["label_text_offset_x"], 0), item.panel.title, fill="#222222", font=font)
            canvas.paste(strip, (x, y + h))
        hitboxes.append((item.panel.key, (x, y, x + w, y + h)))
    profiles = {p.image.info.get("icc_profile") for p in panels}
    if len(profiles) > 1:
        raise ValueError("素材的 ICC 色彩配置不同，无法在不改变像素颜色的前提下合成。请先统一素材的色彩空间。")
    if profiles != {None}:
        canvas.info["icc_profile"] = next(iter(profiles))
    return canvas, (width, height), hitboxes
