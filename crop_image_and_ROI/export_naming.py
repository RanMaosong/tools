"""Readable, Windows-safe layout names in first-row method order."""
import re

from settings import CONFIG


def layout_filename(method_names, suffix=".png"):
    if suffix.lower() not in (".png", ".tif", ".tiff"):
        raise ValueError("排版文件仅支持 PNG / TIFF")
    parts = []
    for name in method_names:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name.strip())
        name = re.sub(r"\s+", "_", name)
        name = re.sub(r"_+", "_", name).strip(" ._") or "method"
        parts.append(name)
    filename = "comparison" + ("__" + "__".join(parts) if parts else "") + suffix.lower()
    if len(filename.encode("utf-16-le")) // 2 > CONFIG["limits"]["filename_max_chars"]:
        raise ValueError("建议文件名过长，请在“方法名”中填写更简短的方法简称。")
    return filename
