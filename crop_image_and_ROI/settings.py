"""Load tunable numeric settings from one user-editable JSON file."""
import json
import math
import os
from pathlib import Path


CONFIG_PATH = Path(os.environ.get("CROP_IMAGE_CONFIG", Path(__file__).with_name("config.json")))

# Structural integers (coordinate indices, format versions, rotations and file
# header offsets) are algorithm/file-format constants, not adjustable settings.
REQUIRED = {
    "window": "width height min_width min_height screen_margin_x screen_margin_y reference_dpi",
    "editor": "line_width arrow_size arrow_antialias_scale annotation_tile_size arrow_step arrow_min_size arrow_tip_offset arrow_head_half_width_ratio arrow_notch_ratio minimum_rectangle_side minimum_arrow_length selection_dash canvas_padding minimum_view_scale",
    "layout": "columns gap margin panel_width roi_scale roi_width roi_height blank_width blank_height dpi label_size label_padding label_text_offset_x preview_min_font_size preview_padding preview_min_size drop_outline_width",
    "limits": "line_width_min line_width_max arrow_size_min arrow_size_max columns_min columns_max spacing_max panel_width_max roi_scale_min roi_scale_max label_size_min label_size_max dpi_min dpi_max max_output_pixels filename_max_chars",
    "timing": "poll_ms image_redraw_ms layout_preview_ms",
    "ui": "space_xs space_sm space_md space space_section space_lg space_outer button_padding button_pack_padding top_padding notebook_padding sidebar_width editor_image_weight editor_sidebar_weight shape_list_rows shape_type_width shape_coordinate_width color_chip_width number_input_width coordinate_input_width list_width order_list_rows file_list_rows layout_sidebar_width roi_settings_height method_dialog_width method_dialog_height layout_sidebar_weight layout_preview_weight layout_mode_width layout_input_width layout_fields_per_row interpolation_width list_font_size editor_hint_font_size layout_hint_font_size editor_hint_min_position layout_hint_min_x layout_hint_min_y layout_help_wrap_width drag_scroll_edge drag_scroll_units file_index_digits coordinate_decimals",
}
ARRAYS = {"editor.selection_dash", "ui.button_padding", "ui.button_pack_padding", "ui.top_padding", "ui.notebook_padding"}
FRACTIONAL = {"editor.arrow_head_half_width_ratio", "editor.arrow_notch_ratio", "editor.minimum_view_scale",
              "editor.minimum_rectangle_side", "editor.minimum_arrow_length", "layout.roi_scale", "limits.roi_scale_min", "limits.roi_scale_max"}
ZERO_ALLOWED = {"window.screen_margin_x", "window.screen_margin_y", "editor.arrow_tip_offset", "editor.canvas_padding",
                "layout.gap", "layout.margin", "layout.panel_width", "layout.label_padding", "layout.label_text_offset_x",
                "layout.preview_padding", "layout.roi_width", "layout.roi_height", "ui.coordinate_decimals"}
ZERO_ALLOWED.update(f"ui.{name}" for name in REQUIRED["ui"].split() if name.startswith("space"))


def load_config(path=CONFIG_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ValueError(f"无法读取配置文件 {path}：{error}") from error
    if not isinstance(data, dict):
        raise ValueError("配置文件最外层必须是对象")
    for section, names in REQUIRED.items():
        values = data.get(section)
        if not isinstance(values, dict):
            raise ValueError(f"配置缺少分组 {section}")
        for name in names.split():
            key = f"{section}.{name}"
            if name not in values:
                raise ValueError(f"配置缺少 {key}")
            value = values[name]
            if key in ARRAYS:
                if not isinstance(value, list) or len(value) != 2 or any(type(v) is not int or v < (1 if key == "editor.selection_dash" else 0) for v in value):
                    raise ValueError(f"配置 {key} 必须是两个有效整数")
                if key == "editor.selection_dash" and any(v > 255 for v in value):
                    raise ValueError("editor.selection_dash 的长度必须在 1–255 之间")
            elif type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (value == 0 and key not in ZERO_ALLOWED):
                raise ValueError(f"配置 {key} 必须是有效的{'非负' if key in ZERO_ALLOWED else '正'}数")
            elif key not in FRACTIONAL and type(value) is not int:
                raise ValueError(f"配置 {key} 必须是整数")
        unknown = set(values) - set(names.split()) - {key for key in values if key.startswith("_")}
        if unknown:
            raise ValueError(f"未知配置项 {section}.{sorted(unknown)[0]}")
    unknown = set(data) - set(REQUIRED) - {key for key in data if key.startswith("_")}
    if unknown:
        raise ValueError(f"未知配置分组 {sorted(unknown)[0]}")
    limits = data["limits"]
    if limits["filename_max_chars"] > 255:
        raise ValueError("limits.filename_max_chars 不能超过 255")
    for name in ("line_width", "arrow_size", "columns", "roi_scale", "label_size", "dpi"):
        low, high = limits[f"{name}_min"], limits[f"{name}_max"]
        default = data["editor" if name in ("line_width", "arrow_size") else "layout"][name]
        if not low <= default <= high:
            raise ValueError(f"配置 {name} 的默认值必须位于其 min/max 范围内")
    if not limits["arrow_size_min"] <= data["editor"]["arrow_min_size"] <= limits["arrow_size_max"]:
        raise ValueError("editor.arrow_min_size 超出箭头大小范围")
    if data["window"]["min_width"] > data["window"]["width"] or data["window"]["min_height"] > data["window"]["height"]:
        raise ValueError("窗口最小尺寸不能大于默认尺寸")
    if any(data["layout"][name] > limits["spacing_max"] for name in ("gap", "margin")) or any(data["layout"][name] > limits["panel_width_max"] for name in ("panel_width", "roi_width", "roi_height", "blank_width", "blank_height")):
        raise ValueError("默认排版间距、边距或宽度超出 limits 范围")
    return data


CONFIG = load_config()
