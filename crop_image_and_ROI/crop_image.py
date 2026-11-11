"""Reconstruction image annotation and lossless layout. Original: Lixing Dong, GPL."""
from dataclasses import asdict
from pathlib import Path
import ctypes
import json
import math
import queue
import threading
import uuid
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk
from settings import CONFIG
from high_depth import display_image
from export_naming import layout_filename
from image_core import (LayoutOptions, Panel, Shape, arrow_geometry, export_batch,
                        import_panels, layout_plan, linked_panel_keys, load_image, make_panels, move_panel_to, render_layout, save_lossless, stroke_width)

IMAGE_TYPES = [("图像", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"), ("所有文件", "*.*")]
MODES = {"按 ROI 相对位置": "relative", "原图上 / ROI 下": "below", "原图左 / ROI 右": "right", "原图一行 / ROI 分行": "rows", "自由混排（逐张排序）": "free"}
HORIZONTAL_EDGES = {"左": "left", "右": "right"}
VERTICAL_EDGES = {"上": "top", "下": "bottom"}
WINDOW, EDITOR, LAYOUT = CONFIG["window"], CONFIG["editor"], CONFIG["layout"]
UI, LIMITS, TIMING = CONFIG["ui"], CONFIG["limits"], CONFIG["timing"]


class ImageCropper:
    def __init__(self, root=None):
        self.root = root or tk.Tk()
        self.root.title("重建图像 · ROI 与结果排版")
        scale = self.root.winfo_fpixels("1i") / WINDOW["reference_dpi"]
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width, height = max(1, min(round(WINDOW["width"]*scale), screen_w-WINDOW["screen_margin_x"])), max(1, min(round(WINDOW["height"]*scale), screen_h-WINDOW["screen_margin_y"]))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(round(WINDOW["min_width"]*scale), width), min(round(WINDOW["min_height"]*scale), height))
        self.files, self.shapes, self.panels, self.import_files = [], [], [], []
        self.extra_images = []
        self.method_names = {}
        self.layout_offsets = {}
        self.layout_position_key = None
        self.layout_placements = {}
        self.roi_layout_vars, self.roi_setting_shapes = {}, []
        self.index, self.rotation = 0, 0
        self.import_rotation = 0
        self.reference_size, self.image = (1, 1), None
        self.generated = self.dirty_panels = self.busy = False
        self.drawing = self.start = self.drag_index = None
        self.file_drag_index = None
        self.order_drag_selection = ()
        self.order_native_selection = False
        self.order_drop_index = None
        self.preview_hits = []
        self.messages = queue.Queue()
        self.preview_after = self.draw_after = None
        self.status = tk.StringVar(value="打开同一组重建结果，在任意一张上画框和箭头；所有方法共用相对坐标。")
        self._build()
        self.root.after(TIMING["poll_ms"], self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _button(self, parent, text, command):
        widget = ttk.Button(parent, text=text, command=command)
        widget.pack(side="left", padx=UI["button_pack_padding"][0], pady=UI["button_pack_padding"][1])
        return widget

    def _build(self):
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TButton", padding=tuple(UI["button_padding"]))
        top = ttk.Frame(self.root, padding=tuple(UI["top_padding"]))
        top.pack(fill="x")
        for text, command in (("打开图像组", self.open_files), ("打开项目", self.open_project), ("保存项目", self.save_project),
                              ("导出原图和 ROI", self.export_images), ("生成 / 刷新排版素材", self.generate_panels)):
            self._button(top, text, command)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=UI["notebook_padding"][0], pady=UI["notebook_padding"][1])
        self.edit_tab, self.layout_tab = ttk.Frame(self.notebook), ttk.Frame(self.notebook)
        self.notebook.add(self.edit_tab, text="  1  画框与箭头  ")
        self.notebook.add(self.layout_tab, text="  2  排列结果  ")
        self._build_editor()
        self._build_layout()
        ttk.Label(self.root, textvariable=self.status, anchor="w", padding=UI["space_lg"]).pack(fill="x")
        self.root.bind("<Control-o>", lambda e: self.open_files())
        self.root.bind("<Control-s>", lambda e: self.save_project())
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Escape>", lambda e: self.clear_shape_selection())
        self.root.bind("<Button-1>", self._blank_area_click, add="+")
        self.root.bind("<Left>", lambda event: self._navigate_key(event, -1))
        self.root.bind("<Right>", lambda event: self._navigate_key(event, 1))
        self._bind_editor_navigation()
        self.notebook.bind("<<NotebookTabChanged>>", self._focus_editor_canvas)

    def _bind_editor_navigation(self):
        # A toplevel binding runs after widget class bindings. In particular,
        # TNotebook consumes arrows before that binding can turn the image.
        tag = f"ImageNavigation:{id(self)}"
        self.root.bind_class(tag, "<Left>", lambda event: self._navigate_key(event, -1))
        self.root.bind_class(tag, "<Right>", lambda event: self._navigate_key(event, 1))

        def install(widget):
            tags = widget.bindtags()
            widget.bindtags((tags[0], tag, *tags[1:]))
            for child in widget.winfo_children():
                install(child)

        install(self.root)

    def _focus_editor_canvas(self, event=None):
        if self.notebook.select() == str(self.edit_tab):
            self.canvas.focus_set()

    def _build_editor(self):
        bar = ttk.Frame(self.edit_tab, padding=UI["space"])
        bar.pack(fill="x")
        self.mode = tk.StringVar(value="rectangle")
        ttk.Radiobutton(bar, text="矩形 ROI", variable=self.mode, value="rectangle").pack(side="left", padx=UI["space"])
        ttk.Radiobutton(bar, text="箭头", variable=self.mode, value="arrow").pack(side="left", padx=UI["space"])
        self.color = tk.StringVar(value="#FF0000")
        self._button(bar, "颜色", self.choose_color)
        self.color_chip = tk.Label(bar, width=UI["color_chip_width"], background=self.color.get())
        self.color_chip.pack(side="left", padx=UI["space_md"])
        palette = ttk.Menubutton(bar, text="常用颜色")
        palette.pack(side="left", padx=UI["space_sm"])
        menu = tk.Menu(palette, tearoff=False)
        palette.configure(menu=menu)
        colors_path = Path(__file__).with_name("colors.txt")
        colors = colors_path.read_text(encoding="utf-8").splitlines() if colors_path.exists() else ["#FF0000", "#FFFF00", "#00FFFF", "#00FF00"]
        for color in colors:
            try:
                self.root.winfo_rgb(color.strip())
            except tk.TclError:
                continue
            menu.add_command(label=color.strip(), foreground=color.strip(), command=lambda value=color.strip(): self._set_color(value))
        self.line_width, self.arrow_size = tk.StringVar(value=str(EDITOR["line_width"])), tk.StringVar(value=str(EDITOR["arrow_size"]))
        for text, variable, name in (("线宽(原图px)", self.line_width, "line_width"), ("箭头头部(预览px)", self.arrow_size, "arrow_size")):
            ttk.Label(bar, text=text).pack(side="left", padx=(UI["space_outer"], UI["space_xs"]))
            spinbox = ttk.Spinbox(bar, from_=LIMITS[f"{name}_min"], to=LIMITS[f"{name}_max"], textvariable=variable, width=UI["number_input_width"])
            spinbox.pack(side="left")
            self._bind_shape_apply(spinbox)
        self._button(bar, "撤销", self.undo)
        self._button(bar, "箭头增大", lambda: self.resize_arrow(EDITOR["arrow_step"]))
        self._button(bar, "箭头减小", lambda: self.resize_arrow(-EDITOR["arrow_step"]))
        self._button(bar, "左转 90°", lambda: self.rotate(90))
        self._button(bar, "右转 90°", lambda: self.rotate(-90))
        body = ttk.Panedwindow(self.edit_tab, orient="horizontal")
        body.pack(fill="both", expand=True)
        image_frame, sidebar = ttk.Frame(body), ttk.Frame(body, width=UI["sidebar_width"], padding=UI["space_lg"])
        body.add(image_frame, weight=UI["editor_image_weight"])
        body.add(sidebar, weight=UI["editor_sidebar_weight"])
        self.canvas = tk.Canvas(image_frame, background="#20252B", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        for event, command in (("<Configure>", self._schedule_draw), ("<Button-1>", self._mouse_down),
                               ("<B1-Motion>", self._mouse_move), ("<ButtonRelease-1>", self._mouse_up)):
            self.canvas.bind(event, command)
        nav = ttk.Frame(image_frame)
        nav.pack(fill="x")
        self._button(nav, "上一张 (←)", lambda: self.navigate(-1))
        self._button(nav, "下一张 (→)", lambda: self.navigate(1))
        self.file_label = ttk.Label(nav, text="未打开图像")
        self.file_label.pack(side="left", padx=UI["space_outer"])
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(1, weight=1)
        sidebar.rowconfigure(4, weight=1)
        ttk.Label(sidebar, text="标注列表（点击后可修改）").grid(row=0, column=0, sticky="w")
        self.shape_tree = ttk.Treeview(sidebar, columns=("kind", "box"), show="headings", height=UI["shape_list_rows"], selectmode="extended")
        self.shape_tree.heading("kind", text="类型")
        self.shape_tree.heading("box", text="起点 x, y, 宽, 高")
        self.shape_tree.column("kind", width=UI["shape_type_width"], stretch=False)
        self.shape_tree.column("box", width=UI["shape_coordinate_width"])
        self.shape_tree.grid(row=1, column=0, sticky="nsew", pady=UI["space"])
        self.shape_tree.bind("<<TreeviewSelect>>", self._select_shape)
        self.shape_tree.bind("<Button-1>", self._shape_list_click)
        actions = ttk.Frame(sidebar)
        actions.grid(row=2, column=0, sticky="ew")
        self._button(actions, "删除选中", self.delete_shapes)
        self._button(actions, "清空标注", self.clear_shapes)
        fields = ttk.LabelFrame(sidebar, text="修改选中标注（原图像素）", padding=UI["space_lg"])
        fields.grid(row=3, column=0, sticky="ew", pady=UI["space_outer"])
        self.coord_vars = [tk.StringVar(value="0") for _ in range(4)]
        self.coordinate_entries = []
        for i, (name, var) in enumerate(zip(("起点 x", "起点 y", "宽", "高"), self.coord_vars)):
            ttk.Label(fields, text=name).grid(row=i, column=0, sticky="w")
            entry = ttk.Entry(fields, textvariable=var, width=UI["coordinate_input_width"])
            entry.grid(row=i, column=1, padx=UI["space"], pady=UI["space_xs"])
            self.coordinate_entries.append(entry)
            self._bind_shape_apply(entry)
        ttk.Button(fields, text="应用坐标 / 当前颜色 / 线宽", command=self.update_shape).grid(row=4, column=0, columnspan=2, pady=UI["space_section"])
        file_frame = ttk.LabelFrame(sidebar, text="当前文件列表（拖动排序 / 点击显示）", padding=UI["space"])
        file_frame.grid(row=4, column=0, sticky="nsew", pady=(0, UI["space"]))
        file_actions = ttk.Frame(file_frame)
        file_actions.pack(fill="x")
        self._button(file_actions, "添加文件", self.add_files)
        self._button(file_actions, "上移", lambda: self.move_file(-1))
        self._button(file_actions, "下移", lambda: self.move_file(1))
        file_body = ttk.Frame(file_frame)
        file_body.pack(fill="both", expand=True)
        self.file_list = tk.Listbox(file_body, height=UI["file_list_rows"], width=UI["list_width"], exportselection=False,
                                   activestyle="none", selectbackground="#2463A6")
        file_scroll = ttk.Scrollbar(file_body, command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=file_scroll.set)
        file_scroll.pack(side="right", fill="y")
        self.file_list.pack(fill="both", expand=True)
        self.file_list.bind("<Button-1>", self._file_drag_start)
        self.file_list.bind("<B1-Motion>", self._file_drag_motion)
        self.file_list.bind("<ButtonRelease-1>", self._file_drag_end)
        self.file_list.bind("<Up>", lambda e: self._file_key(-1))
        self.file_list.bind("<Down>", lambda e: self._file_key(1))

    def _build_layout(self):
        controls = ttk.Frame(self.layout_tab, padding=UI["space_section"])
        controls.pack(fill="x")
        self.layout_mode = tk.StringVar(value=next(iter(MODES)))
        ttk.Label(controls, text="排列方式").grid(row=0, column=0, padx=UI["space_md"])
        combo = ttk.Combobox(controls, state="readonly", textvariable=self.layout_mode, values=list(MODES), width=UI["layout_mode_width"])
        combo.grid(row=0, column=1, columnspan=2, sticky="w")
        combo.bind("<<ComboboxSelected>>", lambda e: self._layout_mode_changed())
        defaults = {key: str(LAYOUT[key]) for key in ("columns", "gap", "margin", "panel_width", "dpi")}
        self.layout_vars = {key: tk.StringVar(value=value) for key, value in defaults.items()}
        fields = [("每行张数/组数", "columns"), ("间距(px)", "gap"), ("边距(px)", "margin"),
                  ("原图宽度(0=原尺寸)", "panel_width"), ("导出 DPI", "dpi")]
        for i, (name, key) in enumerate(fields):
            row, col = 1 + i // UI["layout_fields_per_row"], (i % UI["layout_fields_per_row"]) * 2
            ttk.Label(controls, text=name).grid(row=row, column=col, sticky="e", padx=UI["space_sm"], pady=UI["space_md"])
            ttk.Entry(controls, textvariable=self.layout_vars[key], width=UI["layout_input_width"]).grid(row=row, column=col+1, sticky="w", padx=UI["space_sm"])
            self.layout_vars[key].trace_add("write", self._schedule_preview)
        self.labels = tk.BooleanVar(value=False)
        self.interpolation = tk.StringVar(value="像素复制（保留像素）")
        interpolation = ttk.Combobox(controls, textvariable=self.interpolation, values=["像素复制（保留像素）", "Lanczos（平滑放大）"], state="readonly", width=UI["interpolation_width"])
        interpolation.grid(row=0, column=3, columnspan=2)
        interpolation.bind("<<ComboboxSelected>>", lambda e: self.update_preview())
        self.background = "#FFFFFF"
        ttk.Button(controls, text="背景颜色", command=self.choose_background).grid(row=0, column=5, padx=UI["space_md"])
        self.method_button = ttk.Button(controls, text="设置前 N 项方法名", command=self.configure_method_names)
        self.method_button.grid(row=2, column=2, columnspan=4, sticky="w", padx=UI["space_md"])
        self.original_size_text = tk.StringVar(value="原图尺寸：未加载")
        ttk.Label(self.layout_tab, textvariable=self.original_size_text, padding=UI["space"]).pack(fill="x", padx=UI["space_section"])
        roi_box = ttk.LabelFrame(self.layout_tab, text="每个 ROI 的输出尺寸与相对位置", padding=UI["space"])
        roi_box.pack(fill="x", padx=UI["space_section"])
        self.roi_canvas = tk.Canvas(roi_box, height=UI["roi_settings_height"], highlightthickness=0,
                                    background=self.root.cget("background"))
        roi_scroll = ttk.Scrollbar(roi_box, command=self.roi_canvas.yview)
        self.roi_canvas.configure(yscrollcommand=roi_scroll.set)
        roi_scroll.pack(side="right", fill="y")
        self.roi_canvas.pack(fill="x", expand=True)
        self.roi_settings_frame = ttk.Frame(self.roi_canvas)
        roi_window = self.roi_canvas.create_window(0, 0, window=self.roi_settings_frame, anchor="nw")
        self.roi_canvas.bind("<Configure>", lambda e: self.roi_canvas.itemconfigure(roi_window, width=e.width))
        self.roi_settings_frame.bind("<Configure>", self._roi_frame_configure)
        self._refresh_roi_controls(reset=True)
        tools = ttk.Frame(self.layout_tab, padding=(UI["space"], 0))
        tools.pack(fill="x")
        self._button(tools, "导入已有原图 / ROI", self.import_panels)
        self._button(tools, "导入结果文件夹", self.import_folder)
        self._button(tools, "追加图片（无 ROI）", self.append_images)
        self._button(tools, "刷新预览", self.update_preview)
        self._button(tools, "导出排版 PNG / TIFF", self.export_layout)
        self.layout_info = ttk.Label(tools, text="预览会缩小；保存按实际像素尺寸输出。")
        self.layout_info.pack(side="left", padx=UI["space_lg"])
        body = ttk.Panedwindow(self.layout_tab, orient="horizontal")
        body.pack(fill="both", expand=True, pady=UI["space"])
        left, right = ttk.Frame(body, padding=UI["space_section"], width=UI["layout_sidebar_width"]), ttk.Frame(body)
        body.add(left, weight=UI["layout_sidebar_weight"])
        body.add(right, weight=UI["layout_preview_weight"])
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        self.order_label = ttk.Label(left, text="拖动调整顺序；列表 Ctrl / Shift 多选")
        self.order_label.grid(row=0, column=0, sticky="w")
        list_frame = ttk.Frame(left)
        list_frame.grid(row=1, column=0, sticky="nsew", pady=UI["space"])
        self.order_list = tk.Listbox(list_frame, exportselection=False, selectmode="extended", height=UI["order_list_rows"], width=UI["list_width"], activestyle="none", selectbackground="#2463A6", font=("Microsoft YaHei UI", UI["list_font_size"]))
        scroll = ttk.Scrollbar(list_frame, command=self.order_list.yview)
        self.order_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.order_list.pack(fill="both", expand=True)
        for event, command in (("<Button-1>", self._order_start), ("<B1-Motion>", self._order_motion), ("<ButtonRelease-1>", self._order_end)):
            self.order_list.bind(event, command)
        self.order_list.bind("<Double-Button-1>", self.rename_panel)
        self.order_list.bind("<Control-a>", lambda e: self.select_order("all"))
        self.order_list.bind("<Control-A>", lambda e: self.select_order("all"))
        self.order_list.bind("<Escape>", lambda e: self.select_order("none"))
        self.order_list.bind("<<ListboxSelect>>", self._position_from_order)
        self.blank_button = ttk.Button(left, text="添加空白图", command=self.add_blank)
        self.blank_button.grid(row=2, column=0, sticky="ew", pady=UI["space_xs"])
        selection_actions = ttk.Frame(left)
        selection_actions.grid(row=3, column=0, sticky="ew")
        self._button(selection_actions, "全选", lambda: self.select_order("all"))
        self._button(selection_actions, "仅选附加图", lambda: self.select_order("extra"))
        self._button(selection_actions, "取消选择", lambda: self.select_order("none"))
        move = ttk.Frame(left)
        move.grid(row=4, column=0, sticky="ew")
        self._button(move, "上移", lambda: self.move_order(-1))
        self._button(move, "下移", lambda: self.move_order(1))
        rotation_actions = ttk.Frame(left)
        rotation_actions.grid(row=5, column=0, sticky="ew")
        self._button(rotation_actions, "批量左转 90°", lambda: self.rotate_extra(90))
        self._button(rotation_actions, "批量右转 90°", lambda: self.rotate_extra(-90))
        self._button(move, "移除所选素材", self.remove_panel)
        ttk.Label(left, text="Ctrl / Shift 多选，Ctrl+A 全选。\n批量旋转仅处理附加图。\n选中条目可批量移动、移除。\nROI 边距：正数向内，负数向外。", wraplength=UI["layout_help_wrap_width"], justify="left").grid(row=6, column=0, sticky="w", pady=UI["space_lg"])
        position_box = ttk.LabelFrame(right, text="选中图片位置（最终导出像素）", padding=UI["space"])
        position_box.pack(fill="x", padx=UI["space"], pady=UI["space"])
        self.layout_position_text = tk.StringVar(value="点击预览中的图片，查看和修改它的位置。")
        ttk.Label(position_box, textvariable=self.layout_position_text, width=UI["list_width"]).grid(row=0, column=0, columnspan=7, sticky="ew")
        self.layout_position_vars = {axis: tk.StringVar() for axis in ("x", "y")}
        self.layout_position_entries = {}
        for column, (axis, text) in enumerate((("x", "距左(px)"), ("y", "距上(px)"))):
            ttk.Label(position_box, text=text).grid(row=1, column=column*2, padx=UI["space_xs"])
            entry = ttk.Entry(position_box, textvariable=self.layout_position_vars[axis], width=UI["layout_input_width"])
            entry.grid(row=1, column=column*2+1, padx=UI["space_sm"], pady=UI["space_sm"])
            entry.bind("<Return>", self.apply_layout_position)
            entry.bind("<KP_Enter>", self.apply_layout_position)
            self.layout_position_entries[axis] = entry
        self.position_apply_button = ttk.Button(position_box, text="应用位置", command=self.apply_layout_position, state="disabled")
        self.position_apply_button.grid(row=1, column=4, padx=UI["space_sm"])
        self.position_reset_button = ttk.Button(position_box, text="恢复所选位置", command=self.reset_layout_position, state="disabled")
        self.position_reset_button.grid(row=1, column=5, padx=UI["space_sm"])
        self.position_reset_all_button = ttk.Button(position_box, text="全部恢复自动", command=lambda: self.reset_layout_position(all_images=True), state="disabled")
        self.position_reset_all_button.grid(row=1, column=6, padx=UI["space_sm"])
        self.preview = tk.Canvas(right, background="#E4E8ED", highlightthickness=0)
        self.preview.pack(fill="both", expand=True)
        for event, command in (("<Configure>", self._schedule_preview), ("<Button-1>", self._preview_start),
                               ("<B1-Motion>", self._preview_motion), ("<ButtonRelease-1>", self._preview_end)):
            self.preview.bind(event, command)

    def _fail(self, error):
        self.status.set(str(error))
        messagebox.showerror("操作未完成", str(error), parent=self.root)

    def _bind_shape_apply(self, widget):
        widget.bind("<Return>", self._apply_shape_key)
        widget.bind("<KP_Enter>", self._apply_shape_key)

    def _apply_shape_key(self, event=None):
        self.update_shape()
        return "break"

    @staticmethod
    def _integer_or(value, fallback):
        try:
            return int(value)
        except (ValueError, TypeError):
            return fallback

    def _update_original_size_info(self):
        originals = [panel for panel in self.panels if panel.kind == "original"]
        if self.generated:
            current = next((panel for panel in originals if panel.group == str(self.index)), None)
        else:
            current = originals[0] if originals else None
        image = current.image if current is not None else self.image
        if image is None:
            self.original_size_text.set("原图尺寸：未加载")
            return
        width = self._integer_or(self.layout_vars["panel_width"].get(), 0) or image.width
        height = round(image.height*width/image.width)
        self.original_size_text.set(f"当前原图尺寸：宽 {image.width} × 高 {image.height} px；排版原图尺寸：{width} × {height} px。边距基准：ROI 与原图选中的同侧边缘。")

    @staticmethod
    def _anchor_label(kind):
        return "原图" if kind == "original" else f"ROI {int(kind[4:])+1}"

    def _roi_frame_configure(self, event=None):
        self.roi_canvas.configure(scrollregion=self.roi_canvas.bbox("all"),
                                  height=min(UI["roi_settings_height"], self.roi_settings_frame.winfo_reqheight()))

    def _roi_setting_values(self):
        return {kind: {"width": fields["width"].get(), "height": fields["height"].get(),
                       "x_edge": HORIZONTAL_EDGES[fields["x_edge"].get()], "x_distance": fields["x_distance"].get(),
                       "y_edge": VERTICAL_EDGES[fields["y_edge"].get()], "y_distance": fields["y_distance"].get()}
                for kind, fields in self.roi_layout_vars.items()}

    def _refresh_roi_controls(self, saved=None, reset=False):
        old = {} if reset else self._roi_setting_values()
        kinds = sorted({p.kind for p in self.panels if p.kind.startswith("roi-")}, key=lambda kind: int(kind[4:]))
        current_shapes = [s for s in self.shapes if s.kind == "rectangle"] if self.generated else []
        # Track the actual rectangles so deleting ROI 1 does not transfer its
        # settings to the old ROI 2 when the displayed ROI numbers shift.
        remap = {f"roi-{i}": f"roi-{j}" for i, previous in enumerate(self.roi_setting_shapes)
                 for j, current in enumerate(current_shapes) if previous is current}
        by_new_kind = {new: old[previous] for previous, new in remap.items() if previous in old}
        effective = {}
        default_x = 0
        gap = self._integer_or(self.layout_vars["gap"].get(), LAYOUT["gap"])
        for kind in kinds:
            images = [p.image for p in self.panels if p.kind == kind]
            width = LAYOUT["roi_width"] or max(image.width for image in images)
            height = LAYOUT["roi_height"] or max(image.height for image in images)
            setting = (saved or {}).get(kind)
            if setting is None:
                setting = by_new_kind.get(kind) if self.generated and self.roi_setting_shapes else old.get(kind)
                if setting is not None:
                    setting = dict(setting)
                    if "x_direction" not in setting and "x_edge" not in setting:
                        anchor = setting.get("relative_to", "original")
                        if self.generated and self.roi_setting_shapes:
                            anchor = remap.get(anchor, "original")
                        setting["relative_to"] = anchor
            setting = setting or {"width": width, "height": height, "x_edge": "left", "x_distance": default_x,
                                  "y_edge": "top", "y_distance": 0}
            if "x_direction" not in setting and "x_edge" not in setting and (setting["relative_to"] not in ["original", *kinds] or setting["relative_to"] == kind):
                setting = dict(setting, relative_to="original")
            effective[kind] = setting
            default_x += self._integer_or(setting["width"], width) + gap
        # Convert legacy coordinates to equivalent left/top edge distances.
        # Current edge settings are kept exactly, including partial input.
        offsets = {}
        if any("x_edge" not in item for item in effective.values()) and self.panels:
            original = next((p for p in self.panels if p.kind == "original"), None)
            if original:
                group = [p for p in self.panels if p.group == original.group]
                numeric = {kind: dict(item) for kind, item in effective.items()}
                for kind, item in numeric.items():
                    native = next(p.image for p in self.panels if p.kind == kind)
                    for key, fallback in (("width", native.width), ("height", native.height), ("x_distance", 0), ("y_distance", 0)):
                        if key in item:
                            item[key] = self._integer_or(item[key], fallback)
                try:
                    plan, _ = layout_plan(group, LayoutOptions(mode="relative", labels=False, roi_settings=numeric,
                                          gap=self._integer_or(self.layout_vars["gap"].get(), LAYOUT["gap"]),
                                          panel_width=self._integer_or(self.layout_vars["panel_width"].get(), 0)))
                    origin = next(p for p in plan if p.panel.kind == "original")
                    offsets = {p.panel.kind: (p.x-origin.x, p.y-origin.y) for p in plan}
                except ValueError:
                    pass
        self.roi_layout_vars = {}
        for widget in self.roi_settings_frame.winfo_children():
            widget.destroy()
        if not kinds:
            ttk.Label(self.roi_settings_frame, text="画好 ROI 后点击“生成 / 刷新排版素材”，这里会显示每个 ROI 的设置。",
                      padding=UI["space"]).grid(row=0, column=0, sticky="w")
        default_y = (self.image.height if self.image is not None else 0) + LAYOUT["gap"]
        for row, kind in enumerate(kinds):
            setting = effective[kind]
            if "x_edge" not in setting:
                if "x_direction" in setting:
                    x = self._integer_or(setting["x_distance"], 0) * (-1 if setting["x_direction"] == "left" else 1)
                    y = self._integer_or(setting["y_distance"], 0) * (-1 if setting["y_direction"] == "up" else 1)
                else:
                    x, y = offsets.get(kind, (0, default_y))
                setting = dict(setting, x_edge="left", x_distance=x, y_edge="top", y_distance=y)
            default_y += self._integer_or(setting["height"], 0) + LAYOUT["gap"]
            fields = {"width": tk.StringVar(value=str(setting["width"])), "height": tk.StringVar(value=str(setting["height"])),
                      "x_edge": tk.StringVar(value=next(label for label, value in HORIZONTAL_EDGES.items() if value == setting["x_edge"])),
                      "x_distance": tk.StringVar(value=str(setting["x_distance"])),
                      "y_edge": tk.StringVar(value=next(label for label, value in VERTICAL_EDGES.items() if value == setting["y_edge"])),
                      "y_distance": tk.StringVar(value=str(setting["y_distance"]))}
            self.roi_layout_vars[kind] = fields
            ttk.Label(self.roi_settings_frame, text=self._anchor_label(kind)).grid(row=row, column=0, padx=UI["space"], pady=UI["space_sm"])
            for column, text, name in ((1, "宽(px)", "width"), (3, "高(px)", "height")):
                ttk.Label(self.roi_settings_frame, text=text).grid(row=row, column=column, padx=UI["space_xs"])
                ttk.Entry(self.roi_settings_frame, textvariable=fields[name], width=UI["layout_input_width"]).grid(row=row, column=column+1, padx=UI["space_sm"])
            for column, text, axis, edges in ((5, "左右基准", "x", HORIZONTAL_EDGES), (9, "上下基准", "y", VERTICAL_EDGES)):
                ttk.Label(self.roi_settings_frame, text=text).grid(row=row, column=column, padx=UI["space_sm"])
                ttk.Combobox(self.roi_settings_frame, textvariable=fields[f"{axis}_edge"], state="readonly",
                             values=list(edges), width=UI["number_input_width"]).grid(row=row, column=column+1, padx=UI["space_sm"])
                ttk.Entry(self.roi_settings_frame, textvariable=fields[f"{axis}_distance"], width=UI["layout_input_width"]).grid(row=row, column=column+2, padx=UI["space_sm"])
                ttk.Label(self.roi_settings_frame, text="px").grid(row=row, column=column+3, padx=UI["space_xs"])
            for variable in fields.values():
                variable.trace_add("write", self._schedule_preview)
        self.roi_setting_shapes = current_shapes

    def _work(self, task, callback, text):
        if self.busy:
            self.status.set("正在处理上一个任务，请稍候。")
            return
        self.busy = True
        self.status.set(text)
        def run():
            try:
                self.messages.put((callback, task(), None))
            except Exception as error:
                self.messages.put((None, None, error))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        try:
            callback, result, error = self.messages.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if error:
                self._fail(error)
            elif callback:
                callback(result)
        self.root.after(TIMING["poll_ms"], self._poll)

    def open_files(self):
        if self.busy:
            return
        files = filedialog.askopenfilenames(title="选择多个重建方法的结果图", filetypes=IMAGE_TYPES)
        if not files:
            return
        self._replace_files(files)

    def _replace_files(self, files):
        try:
            image = load_image(files[0])
        except Exception as error:
            self._fail(error)
            return
        self.files, self.index, self.rotation = list(files), 0, 0
        self.import_rotation = 0
        self.image, self.reference_size = image, image.size
        self.shapes, self.panels, self.import_files = [], [], []
        self.extra_images = []
        self.method_names = {}
        self.layout_offsets = {}
        self.layout_position_key = None
        self.generated = self.dirty_panels = False
        self._refresh_roi_controls(reset=True)
        self._refresh_shapes()
        self._refresh_order()
        self._show_image()
        self.update_preview()
        self.notebook.select(self.edit_tab)
        self._focus_editor_canvas()

    def add_files(self):
        if self.busy:
            return
        files = filedialog.askopenfilenames(title="添加图像（保留已有文件和标注）", filetypes=IMAGE_TYPES)
        if not files:
            return
        if not self.files:
            self._replace_files(files)
            return
        seen = {str(Path(path).resolve()).casefold() for path in self.files}
        added = []
        for path in files:
            identity = str(Path(path).resolve()).casefold()
            if identity not in seen:
                seen.add(identity)
                added.append(path)
        if not added:
            self.status.set("所选文件已在当前列表中，没有重复添加。")
            return
        rotation = self.rotation

        def validate():
            for path in added:
                image = load_image(path, rotation)
                image.close()
            return added

        def done(paths):
            self.files.extend(paths)
            if self.generated:
                self.dirty_panels = True
            self._refresh_file_list()
            self.file_label.configure(text=f"{self.index+1}/{len(self.files)}  {Path(self.files[self.index]).name}  ·  {self.image.width} × {self.image.height}px")
            self.status.set(f"已添加 {len(paths)} 个文件，共 {len(self.files)} 个；已有标注已保留。" + (" 请刷新排版素材。" if self.generated else ""))

        self._work(validate, done, "正在检查要添加的图像…")

    def _refresh_file_list(self):
        self.file_list.delete(0, "end")
        for index, path in enumerate(self.files):
            self.file_list.insert("end", f"{index+1:0{UI['file_index_digits']}d}  {Path(path).name}")
        if self.files:
            self.file_list.selection_set(self.index)
            self.file_list.activate(self.index)
            self.file_list.see(self.index)

    def _select_file(self, index):
        if not self.busy and 0 <= index < len(self.files):
            self.index = index
            self._show_image()

    def _file_key(self, step):
        self._select_file(max(0, min(self.index + step, len(self.files)-1)))
        return "break"

    def _reorder_files(self, source, target):
        if self.busy or source is None or source == target or not (0 <= source < len(self.files) and 0 <= target < len(self.files)):
            self._refresh_file_list()
            return
        previous = list(self.files)
        current = self.files[self.index]
        self.files.insert(target, self.files.pop(source))
        self.index = self.files.index(current)
        if self.generated:
            # Panel keys use file indices; remap them with the filenames so a
            # title or ROI can never become attached to a different method.
            positions = {path: str(i) for i, path in enumerate(self.files)}
            key_map = {p.key: p.key for p in self.panels}
            for panel in self.panels:
                if panel.kind == "extra":
                    continue
                old_key = panel.key
                panel.group = positions[previous[int(panel.group)]]
                panel.key = f"{panel.group}:{panel.kind}"
                key_map[old_key] = panel.key
            self._remap_layout_offsets(key_map, {p.key for p in self.panels})
            source_panels = iter(sorted((p for p in self.panels if p.kind != "extra"), key=lambda p: int(p.group)))
            self.panels = [p if p.kind == "extra" else next(source_panels) for p in self.panels]
            self._refresh_order()
            self.update_preview()
        self._show_image()
        self.status.set("文件顺序已更新，上一张 / 下一张和生成的排版素材均使用新顺序。")

    def move_file(self, step):
        self._reorder_files(self.index, self.index + step)

    def _file_drag_start(self, event):
        self.file_drag_index = None
        if self.files and not self.busy:
            index = self.file_list.nearest(event.y)
            bounds = self.file_list.bbox(index)
            if bounds and bounds[1] <= event.y < bounds[1] + bounds[3]:
                self.file_drag_index = index
                self.file_list.focus_set()
                self._select_file(index)
        return "break"

    def _file_drag_motion(self, event):
        if self.file_drag_index is not None:
            target = self.file_list.nearest(event.y)
            self.file_list.selection_clear(0, "end")
            self.file_list.selection_set(target)
            if event.y < UI["drag_scroll_edge"]:
                self.file_list.yview_scroll(-UI["drag_scroll_units"], "units")
            elif event.y > self.file_list.winfo_height()-UI["drag_scroll_edge"]:
                self.file_list.yview_scroll(UI["drag_scroll_units"], "units")
        return "break"

    def _file_drag_end(self, event):
        if self.file_drag_index is not None:
            self._reorder_files(self.file_drag_index, self.file_list.nearest(event.y))
        self.file_drag_index = None
        return "break"

    def _show_image(self):
        self._refresh_file_list()
        if not self.files:
            self.draw_image()
            self.file_label.configure(text="未打开图像")
            self._update_original_size_info()
            return
        try:
            self.image = load_image(self.files[self.index], self.rotation)
            self.file_label.configure(text=f"{self.index+1}/{len(self.files)}  {Path(self.files[self.index]).name}  ·  {self.image.width} × {self.image.height}px")
            self.draw_image()
            self._update_original_size_info()
        except Exception as error:
            self._fail(error)

    def navigate(self, step):
        if self.files:
            self.index = (self.index + step) % len(self.files)
            self._show_image()

    def _navigate_key(self, event, step):
        if self.notebook.select() != str(self.edit_tab):
            return
        if event.widget.winfo_class() in ("Entry", "TEntry", "Spinbox", "TSpinbox", "Text", "TCombobox"):
            return
        # Tk uses 0x0008 for Num Lock on Windows, while Alt is 0x20000.
        # Treating the X11 Alt bit as universal disables ordinary arrow keys
        # whenever Num Lock is enabled (including native extended-key events).
        alt_mask = 0x20000 if self.root.tk.call("tk", "windowingsystem") == "win32" else 0x0008
        if getattr(event, "state", 0) & (0x0004 | alt_mask):
            return
        if self.busy or self.drawing is not None:
            return "break"
        self.navigate(step)
        return "break"

    def rotate(self, angle):
        if not self.files or self.busy:
            return
        def point(x, y):
            return (y, 1-x) if angle > 0 else (1-y, x)
        for shape in self.shapes:
            x1, y1 = point(*shape.box[:2])
            x2, y2 = point(*shape.box[2:])
            shape.box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)) if shape.kind == "rectangle" else (x1, y1, x2, y2)
            if shape.preview_size:
                shape.preview_size = shape.preview_size[::-1]
        self.rotation = (self.rotation + angle) % 360
        self.reference_size = self.reference_size[::-1]
        self._changed()
        self._show_image()

    def _schedule_draw(self, event=None):
        if self.draw_after:
            self.root.after_cancel(self.draw_after)
        self.draw_after = self.root.after(TIMING["image_redraw_ms"], self.draw_image)

    def draw_image(self):
        self.draw_after = None
        self.canvas.delete("all")
        if self.image is None:
            self.canvas.create_text(max(UI["editor_hint_min_position"], self.canvas.winfo_width()//2), max(UI["editor_hint_min_position"], self.canvas.winfo_height()//2), text="打开一组图像，开始画框与箭头", fill="#CBD4DE", font=("Microsoft YaHei UI", UI["editor_hint_font_size"]))
            return
        cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        scale = max(EDITOR["minimum_view_scale"], min((cw-EDITOR["canvas_padding"])/self.image.width, (ch-EDITOR["canvas_padding"])/self.image.height))
        w, h = max(1, round(self.image.width*scale)), max(1, round(self.image.height*scale))
        self.offset, self.display_size = ((cw-w)//2, (ch-h)//2), (w, h)
        self.photo = ImageTk.PhotoImage(display_image(self.image).resize((w, h), Image.Resampling.LANCZOS))
        self.canvas.create_image(*self.offset, anchor="nw", image=self.photo)
        self._draw_shapes()

    def _draw_shapes(self):
        self.canvas.delete("shape")
        if self.image is None or not hasattr(self, "display_size"):
            return
        w, h = self.display_size
        ox, oy = self.offset
        selected = set(self.shape_tree.selection())
        for i, shape in enumerate(self.shapes + ([self.drawing] if self.drawing else [])):
            dash = tuple(EDITOR["selection_dash"]) if str(i) in selected else ()
            tags = ("shape", f"shape-{i}")
            x1, y1, x2, y2 = shape.box
            box = (ox+x1*w, oy+y1*h, ox+x2*w, oy+y2*h)
            width = max(1, stroke_width(shape, (w, h), self.reference_size))
            if shape.kind == "rectangle":
                self.canvas.create_rectangle(*box, outline=shape.color, width=width, tags=tags, dash=dash)
            else:
                polygon, width = arrow_geometry(shape, (w, h), self.reference_size)
                width = max(1, width)
                self.canvas.create_line(*box, fill=shape.color, width=width, tags=(*tags, "arrow-shaft"), dash=dash)
                coords = [value for x, y in polygon for value in (ox+x, oy+y)]
                if dash:
                    self.canvas.create_polygon(*coords, fill="", outline=shape.color, width=width,
                                               dash=dash, tags=(*tags, "arrow-head"))
                else:
                    self.canvas.create_polygon(*coords, fill=shape.color, tags=(*tags, "arrow-head"))

    def clear_shape_selection(self):
        self.shape_tree.selection_remove(*self.shape_tree.selection())
        self.shape_tree.focus("")
        for variable in self.coord_vars:
            variable.set("")
        self._draw_shapes()

    def _shape_list_click(self, event):
        if not self.shape_tree.identify_row(event.y) and self.shape_tree.identify_region(event.x, event.y) != "heading":
            self.clear_shape_selection()
            return "break"

    def _blank_area_click(self, event):
        # Frames are blank layout space; entries, buttons and list rows must keep
        # their selection so editing/applying an annotation still works.
        if event.widget.winfo_class() in ("Tk", "TFrame", "Frame", "TLabelframe", "Labelframe"):
            self.clear_shape_selection()
            self._focus_editor_canvas()

    def _point(self, event, clamp=True):
        w, h = self.display_size
        x, y = (event.x-self.offset[0])/w, (event.y-self.offset[1])/h
        if not clamp and not (0 <= x <= 1 and 0 <= y <= 1):
            return None
        return max(0, min(1, x)), max(0, min(1, y))

    def _mouse_down(self, event):
        self.clear_shape_selection()
        if self.image is None or self.busy:
            return
        self.canvas.focus_set()
        point = self._point(event, False)
        if point is None:
            return
        try:
            width, head = int(self.line_width.get()), int(self.arrow_size.get())
            if not LIMITS["line_width_min"] <= width <= LIMITS["line_width_max"] or not LIMITS["arrow_size_min"] <= head <= LIMITS["arrow_size_max"]:
                raise ValueError("线宽或箭头头部超出 config.json 中 limits 配置范围")
            self.start = point
            self.drawing = Shape(self.mode.get(), (*point, *point), self.color.get(), width, head,
                                 self.display_size if self.mode.get() == "arrow" else None)
        except ValueError as error:
            self._fail(error)

    def _mouse_move(self, event):
        if self.drawing is None:
            return
        x1, y1 = self.start
        x2, y2 = self._point(event)
        self.drawing.box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)) if self.drawing.kind == "rectangle" else (x1, y1, x2, y2)
        self._draw_shapes()

    def _mouse_up(self, event):
        if self.drawing is None:
            return
        self._mouse_move(event)
        box = self.drawing.box
        dx, dy = abs(box[2]-box[0])*self.reference_size[0], abs(box[3]-box[1])*self.reference_size[1]
        if (self.drawing.kind == "rectangle" and dx >= EDITOR["minimum_rectangle_side"] and dy >= EDITOR["minimum_rectangle_side"]) or (self.drawing.kind == "arrow" and math.hypot(dx, dy) >= EDITOR["minimum_arrow_length"]):
            self.shapes.append(self.drawing)
        self.drawing = None
        self._changed()

    @staticmethod
    def _coordinate_text(value):
        decimals = UI["coordinate_decimals"]
        return str(round(value)) if decimals == 0 else f"{value:.{decimals}f}"

    def _refresh_shapes(self):
        selection = self.shape_tree.selection()
        self.shape_tree.delete(*self.shape_tree.get_children())
        w, h = self.reference_size
        roi = 0
        for i, shape in enumerate(self.shapes):
            x1, y1, x2, y2 = shape.box
            if shape.kind == "rectangle":
                roi += 1
                name, coords = f"ROI {roi}", (x1*w, y1*h, (x2-x1)*w, (y2-y1)*h)
            else:
                name, coords = "箭头", (x1*w, y1*h, (x2-x1)*w, (y2-y1)*h)
            self.shape_tree.insert("", "end", iid=str(i), values=(name, ", ".join(self._coordinate_text(v) for v in coords)))
        valid = [i for i in selection if self.shape_tree.exists(i)]
        if valid:
            self.shape_tree.selection_set(valid)

    def _select_shape(self, event=None):
        selected = self.shape_tree.selection()
        if selected:
            shape = self.shapes[int(selected[0])]
            x1, y1, x2, y2 = shape.box
            w, h = self.reference_size
            values = (x1*w, y1*h, (x2-x1)*w, (y2-y1)*h)
            for var, value in zip(self.coord_vars, values):
                var.set(self._coordinate_text(value))
            self.color.set(shape.color)
            self.color_chip.configure(background=shape.color)
            self.line_width.set(str(shape.width))
            self.arrow_size.set(str(shape.arrow_size))
        else:
            for variable in self.coord_vars:
                variable.set("")
        self._draw_shapes()

    def _changed(self):
        self._refresh_shapes()
        self._draw_shapes()
        if self.generated:
            self.dirty_panels = True
        self.status.set(f"共 {len(self.shapes)} 个标注；坐标以原图为准。" + (" 排版素材待刷新。" if self.dirty_panels else ""))

    def update_shape(self):
        selected = self.shape_tree.selection()
        if not selected or self.busy:
            return
        try:
            index = int(selected[0])
            shape = self.shapes[index]
            x, y, a, b = (float(v.get()) for v in self.coord_vars)
            w, h = self.reference_size
            box = (x/w, y/h, (x+a)/w, (y+b)/h)
            new = Shape(shape.kind, box, self.color.get(), int(self.line_width.get()), int(self.arrow_size.get()),
                        (shape.preview_size or self.display_size) if shape.kind == "arrow" else None)
            new.validate()
            self.shapes[index] = new
            self.roi_setting_shapes = [new if item is shape else item for item in self.roi_setting_shapes]
            self._changed()
        except (ValueError, tk.TclError) as error:
            self._fail(error)

    def choose_color(self):
        color = colorchooser.askcolor(color=self.color.get(), parent=self.root)[1]
        if color:
            self._set_color(color)

    def resize_arrow(self, step):
        if self.busy:
            return
        selected = [self.shapes[int(index)] for index in self.shape_tree.selection()
                    if self.shapes[int(index)].kind == "arrow"]
        try:
            if selected:
                for shape in selected:
                    shape.arrow_size = min(LIMITS["arrow_size_max"], max(EDITOR["arrow_min_size"], shape.arrow_size + step))
                    if shape.preview_size is None and self.image is not None:
                        shape.preview_size = self.display_size
                self.arrow_size.set(str(selected[0].arrow_size))
                self._changed()
            else:
                self.arrow_size.set(str(min(LIMITS["arrow_size_max"], max(EDITOR["arrow_min_size"], int(self.arrow_size.get()) + step))))
        except ValueError as error:
            self._fail(error)

    def _set_color(self, color):
        self.color.set(color)
        self.color_chip.configure(background=color)

    def choose_background(self):
        color = colorchooser.askcolor(color=self.background, parent=self.root)[1]
        if color:
            self.background = color
            self.update_preview()

    def undo(self):
        if self.shapes and not self.busy:
            self.shapes.pop()
            self._changed()

    def delete_shapes(self):
        if not self.busy:
            for index in sorted(map(int, self.shape_tree.selection()), reverse=True):
                del self.shapes[index]
            self._changed()

    def clear_shapes(self):
        if not self.busy:
            self.shapes.clear()
            self._changed()

    def generate_panels(self):
        if not self.files or self.busy:
            return
        files, shapes, size, rotation = list(self.files), list(self.shapes), self.reference_size, self.rotation
        def done(panels):
            order = {p.key: i for i, p in enumerate(self.panels)}
            titles = {p.key: p.title for p in self.panels}
            rectangles = [shape for shape in shapes if shape.kind == "rectangle"]
            kind_map = {f"roi-{i}": f"roi-{j}" for i, old in enumerate(self.roi_setting_shapes)
                        for j, current in enumerate(rectangles) if old is current}
            key_map = {p.key: p.key for p in self.panels if p.kind in ("original", "extra")}
            if self.generated:
                key_map.update({p.key: f"{p.group}:{kind_map[p.kind]}" for p in self.panels if p.kind in kind_map})
            panels.extend(p for p in self.panels if p.kind == "extra")
            panels.sort(key=lambda p: order.get(p.key, len(order)))
            for panel in panels:
                panel.title = titles.get(panel.key, panel.title)
            self._remap_layout_offsets(key_map, {p.key for p in panels})
            self.panels = panels
            self.generated, self.dirty_panels = True, False
            self.labels.set(False)
            self._refresh_roi_controls()
            self._refresh_order()
            self.notebook.select(self.layout_tab)
            self.update_preview()
            self.status.set("素材已从原始图像生成。拖动列表或预览图片改变顺序。")
        self._work(lambda: list(make_panels(files, shapes, size, rotation)), done, "正在读取原始像素并生成排版素材…")

    def import_panels(self):
        if self.busy:
            return
        files = filedialog.askopenfilenames(title="导入已有的画框图和 ROI（进入自由混排）", filetypes=IMAGE_TYPES)
        if not files:
            return
        self._import_files(list(files))

    @staticmethod
    def _load_extra_panels(records):
        if not isinstance(records, list):
            raise ValueError("附加图片记录必须为列表")
        seen, panels = set(), []
        for item in records:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item["key"].startswith("extra:"):
                raise ValueError("附加图片记录无效")
            key = item["key"]
            if key in seen:
                raise ValueError("附加图片标识重复")
            rotation = item.get("rotation", 0)
            if type(rotation) is not int or rotation not in (0, 90, 180, 270):
                raise ValueError("附加图片旋转角度必须为 0、90、180 或 270")
            seen.add(key)
            item_type = item.get("type", "image")
            if item_type == "blank":
                size = item.get("size")
                if not isinstance(size, (list, tuple)) or len(size) != 2 or any(type(value) is not int or not 1 <= value <= LIMITS["panel_width_max"] for value in size) or rotation != 0:
                    raise ValueError("空白图尺寸或旋转参数无效")
                # A one-pixel backing avoids allocating a full image per
                # placeholder; the renderer fills the cell at output size.
                panels.append(Panel(key, key, "extra", "空白图", Image.new("RGB", (1, 1)), blank_size=tuple(size)))
                continue
            if item_type != "image" or not isinstance(item.get("path"), str):
                raise ValueError("附加图片路径或类型无效")
            path = item["path"]
            # Use each image's saved rotation; never infer annotations or ROI
            # membership from its filename or apply the editor rotation twice.
            panels.append(Panel(key, key, "extra", Path(path).stem, load_image(path, rotation)))
        return panels

    def add_blank(self):
        if self.busy:
            return
        size = self.image.size if self.image is not None else (LAYOUT["blank_width"], LAYOUT["blank_height"])
        record = {"key": f"extra:{uuid.uuid4().hex}", "type": "blank", "size": list(size), "rotation": 0}
        panel = self._load_extra_panels([record])[0]
        units = self._order_units()
        selected = self.order_list.curselection()
        index = max(selected)+1 if selected else len(units)
        units.insert(index, [panel])
        self.panels = [item for unit in units for item in unit]
        self.extra_images.append(record)
        self._refresh_order(index)
        self.update_preview()
        self.status.set("已插入透明空白占位；可拖动排序或移除，重叠时不遮挡图片，尺寸随排版设置更新。")

    def append_images(self):
        if self.busy:
            return
        files = filedialog.askopenfilenames(title="追加残差图等独立图片（不生成 ROI）", filetypes=IMAGE_TYPES)
        if not files:
            return
        existing = {str(Path(item["path"]).resolve()).casefold() for item in self.extra_images if item.get("type") != "blank"}
        records = []
        for filename in files:
            path = str(Path(filename).resolve())
            if path.casefold() not in existing:
                existing.add(path.casefold())
                records.append({"key": f"extra:{uuid.uuid4().hex}", "path": path, "rotation": self.rotation})
        if not records:
            self.status.set("所选图片已在附加图片中，没有重复添加。")
            return

        def done(panels):
            self.panels.extend(panels)
            self.extra_images.extend(records)
            self._refresh_order()
            self.notebook.select(self.layout_tab)
            self.update_preview()
            self.status.set(f"已追加 {len(panels)} 张独立图片，不生成 ROI；尺寸按排版第一张图统一 resize，刷新素材后仍会保留。")
        self._work(lambda: self._load_extra_panels(records), done, "正在读取附加图片的原始像素…")

    def import_folder(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(title="选择含 rectange / roi-0 等子目录的结果文件夹")
        if folder:
            extensions = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
            files = sorted(str(p.resolve()) for p in Path(folder).rglob("*") if p.suffix.lower() in extensions and p.is_file())
            if files:
                self._import_files(files)
            else:
                self.status.set("所选文件夹中没有可导入的图像。")

    def _import_files(self, files):
        rotation = self.rotation
        def done(panels):
            self.panels, self.import_files = panels, list(files)
            self.import_rotation = rotation
            self.extra_images = []
            self.method_names = {}
            self.layout_offsets = {}
            self.layout_position_key = None
            self.generated = self.dirty_panels = False
            self.labels.set(False)
            self._refresh_roi_controls(reset=True)
            mode = "relative" if any(p.kind.startswith("roi-") for p in panels) else "free"
            self.layout_mode.set(next(name for name, value in MODES.items() if value == mode))
            self._layout_mode_changed()
            self.notebook.select(self.layout_tab)
            self.status.set("已导入已有图片，可以逐张拖动排序。")
        self._work(lambda: list(import_panels(files, rotation)), done, "正在导入原始图片…")

    def options(self):
        values = {key: float(var.get()) if key == "roi_scale" else int(var.get()) for key, var in self.layout_vars.items()}
        settings = self._roi_setting_values()
        for kind, setting in settings.items():
            try:
                for key in ("width", "height", "x_distance", "y_distance"):
                    setting[key] = int(setting[key])
            except ValueError as error:
                raise ValueError(f"{self._anchor_label(kind)} 的宽、高和边距必须输入整数像素") from error
        result = LayoutOptions(mode=MODES[self.layout_mode.get()], **values, labels=False, roi_settings=settings,
                               panel_offsets={key: dict(value) for key, value in self.layout_offsets.items()},
                               interpolation="nearest" if self.interpolation.get().startswith("像素") else "lanczos", background=self.background)
        result.validate()
        return result

    def _layout_mode_changed(self):
        self.order_label.configure(text="逐张排序；列表 Ctrl / Shift 多选" if MODES[self.layout_mode.get()] == "free" else "整组排序；列表 Ctrl / Shift 多选")
        self._refresh_order()
        self.update_preview()

    def _order_units(self):
        if MODES[self.layout_mode.get()] == "free":
            return [[p] for p in self.panels]
        groups = {}
        for panel in self.panels:
            groups.setdefault(panel.group, []).append(panel)
        return [sorted(group, key=lambda p: (p.kind != "original", int(p.kind[4:]) if p.kind.startswith("roi-") else 0)) for group in groups.values()]

    def _method_key(self, unit):
        panel = unit[0]
        if panel.kind == "extra":
            return panel.key
        if self.generated:
            return "source:" + str(Path(self.files[int(panel.group)]).resolve()).casefold()
        return "import:" + panel.group

    def _method_name(self, unit, names=None):
        name = (self.method_names if names is None else names).get(self._method_key(unit), "").strip()
        if name:
            return name
        original = next((p for p in self.panels if p.group == unit[0].group and p.kind == "original"), unit[0])
        return original.title

    def _method_units(self):
        count = int(self.layout_vars["columns"].get())
        if not LIMITS["columns_min"] <= count <= LIMITS["columns_max"]:
            raise ValueError("每行张数超出配置范围")
        return self._order_units()[:count]

    def suggested_layout_filename(self, names=None):
        return layout_filename([self._method_name(unit, names) for unit in self._method_units()])

    def _update_method_button(self):
        try:
            count = len(self._method_units())
            self.method_button.configure(text=f"设置前 {count} 项方法名", state="normal" if count else "disabled")
        except (ValueError, tk.TclError):
            self.method_button.configure(text="设置前 N 项方法名", state="disabled")

    def set_method_names(self, names):
        if not isinstance(names, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in names.items()):
            raise ValueError("方法名必须是文本")
        updated = dict(self.method_names)
        updated.update({key: value.strip() for key, value in names.items()})
        self.suggested_layout_filename(updated)
        self.method_names = updated
        self._refresh_order(self.order_list.curselection())

    def configure_method_names(self):
        if self.busy:
            return
        try:
            units = self._method_units()
        except (ValueError, tk.TclError) as error:
            self._fail(error)
            return
        if not units:
            self.status.set("请先生成或导入排版素材。")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("前 N 项实际方法名 → 导出文件名")
        dialog.transient(self.root)
        content = ttk.Frame(dialog, padding=UI["space_outer"])
        content.pack(fill="both", expand=True)
        ttk.Label(content, text=f"按排版列表前 {len(units)} 项顺序命名；方法名随图片移动，不显示在图中。\n同一方法的原图和 ROI 共用名称；未填写时沿用素材名称。").pack(anchor="w")
        canvas = tk.Canvas(content, width=UI["method_dialog_width"], height=UI["method_dialog_height"], highlightthickness=0)
        scroll = ttk.Scrollbar(content, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True, pady=UI["space_lg"])
        rows = ttk.Frame(canvas)
        window = canvas.create_window(0, 0, window=rows, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        rows.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        rows.columnconfigure(1, weight=1)
        rows.columnconfigure(2, weight=1)
        for column, title in enumerate(("列", "当前素材", "实际方法名")):
            ttk.Label(rows, text=title).grid(row=0, column=column, sticky="w", padx=UI["space"])
        variables, entries = {}, []
        for row, unit in enumerate(units, start=1):
            key = self._method_key(unit)
            if key not in variables:
                variables[key] = tk.StringVar(value=self._method_name(unit))
            ttk.Label(rows, text=f"C{row:0{UI['file_index_digits']}d}").grid(row=row, column=0, padx=UI["space"])
            ttk.Label(rows, text=unit[0].title, wraplength=UI["method_dialog_width"]//2).grid(row=row, column=1, sticky="w", padx=UI["space"])
            entry = ttk.Entry(rows, textvariable=variables[key])
            entry.grid(row=row, column=2, sticky="ew", padx=UI["space"], pady=UI["space_md"])
            entries.append(entry)
        filename = tk.StringVar()
        ttk.Label(content, textvariable=filename, wraplength=UI["method_dialog_width"], justify="left").pack(fill="x")

        def current_names():
            return {**self.method_names, **{key: var.get() for key, var in variables.items()}}

        def preview(*args):
            try:
                filename.set("导出默认文件名：" + self.suggested_layout_filename(current_names()))
            except ValueError as error:
                filename.set(str(error))

        def save(event=None):
            try:
                self.set_method_names(current_names())
            except ValueError as error:
                filename.set(str(error))
                return "break"
            dialog.destroy()
            self.status.set("方法名已保存；导出文件名按当前排版列表前 N 项顺序生成。")
            return "break"

        for variable in variables.values():
            variable.trace_add("write", preview)
        actions = ttk.Frame(content)
        actions.pack(fill="x", pady=UI["space"])
        self._button(actions, "保存方法名", save)
        self._button(actions, "取消", dialog.destroy)
        dialog.bind("<Return>", save)
        dialog.bind("<KP_Enter>", save)
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        preview()
        dialog.grab_set()
        entries[0].focus_set()
        return dialog

    def rename_panel(self, event=None):
        selected = self.order_list.curselection()
        if not selected or self.busy:
            return
        if len(selected) != 1:
            self.status.set("修改名称时请只选择一个条目。")
            return
        unit = self._order_units()[selected[0]]
        name = simpledialog.askstring("图片名称", "输入排版中显示的名称：", initialvalue=unit[0].title, parent=self.root)
        if name:
            for panel in unit:
                panel.title = name if panel.kind == "original" or len(unit) == 1 else f"{name} · ROI {int(panel.kind[4:]) + 1}"
            self._refresh_order(selected[0])
            self.update_preview()

    def _refresh_order(self, selected=None):
        self.order_list.delete(0, "end")
        self.order_drop_index = None
        rotations = {item["key"]: item.get("rotation", 0) for item in self.extra_images}
        for i, unit in enumerate(self._order_units()):
            title = unit[0].title + (f"（{len(unit)} 张）" if len(unit) > 1 else "")
            alias = self.method_names.get(self._method_key(unit), "").strip()
            if alias and alias != unit[0].title:
                title = alias + " | " + title
            if unit[0].kind == "extra":
                title += "（空白占位）" if unit[0].blank_size is not None else "（附加图，无 ROI）"
                rotation = rotations.get(unit[0].key, 0)
                if rotation:
                    title += {90: " 左转90°", 180: " 旋转180°", 270: " 右转90°"}[rotation]
            self.order_list.insert("end", f"{i+1:0{UI['file_index_digits']}d}  {title}")
        if selected is not None and self.order_list.size():
            indices = [selected] if isinstance(selected, int) else selected
            valid = sorted({index for index in indices if 0 <= index < self.order_list.size()})
            for index in valid:
                self.order_list.selection_set(index)
            if valid:
                self.order_list.selection_anchor(valid[0])
                self.order_list.activate(valid[0])
                self.order_list.see(valid[0])
        self._update_method_button()

    def select_order(self, scope="all"):
        self.order_list.selection_clear(0, "end")
        if scope == "all" and self.order_list.size():
            self.order_list.selection_set(0, "end")
        elif scope == "extra":
            for i, unit in enumerate(self._order_units()):
                if any(p.kind == "extra" and p.blank_size is None for p in unit):
                    self.order_list.selection_set(i)
        selected = self.order_list.curselection()
        if selected:
            self.order_list.selection_anchor(selected[0])
            self.order_list.activate(selected[0])
        self.order_list.focus_set()
        self._position_from_order()
        return "break"

    def rotate_extra(self, angle):
        if self.busy:
            return
        selection = self.order_list.curselection()
        if not selection:
            self.status.set("请先多选附加图片，或点击“仅选附加图”。")
            return
        units = self._order_units()
        selected_keys = {p.key for index in selection for p in units[index]}
        targets = {p.key: p for index in selection for p in units[index] if p.kind == "extra" and p.blank_size is None}
        if not targets:
            self.status.set("批量旋转用于残差图等附加图片，请选中附加图。")
            return
        if angle not in (90, -90):
            raise ValueError("附加图片每次旋转 90°")
        records = {item["key"]: item for item in self.extra_images}
        transpose = Image.Transpose.ROTATE_90 if angle > 0 else Image.Transpose.ROTATE_270

        def task():
            return {key: panel.image.transpose(transpose) for key, panel in targets.items()}

        def done(images):
            for key, image in images.items():
                targets[key].image = image
                records[key]["rotation"] = (records[key].get("rotation", 0) + angle) % 360
            selected = [i for i, unit in enumerate(self._order_units()) if any(p.key in selected_keys for p in unit)]
            self._refresh_order(selected)
            self.update_preview()
            self.status.set(f"已批量旋转 {len(images)} 张附加图；先旋转再统一尺寸，重建原图和 ROI 保持原样。")
        self._work(task, done, f"正在批量旋转 {len(targets)} 张附加图片…")

    def _reorder(self, source, target):
        if source is not None:
            self._move_order_selection((source,), target, source)

    def _move_order_selection(self, selection, target, source=None):
        if self.busy:
            return
        units = self._order_units()
        selected = sorted({index for index in selection if 0 <= index < len(units)})
        if not selected or not 0 <= target < len(units) or target in selected:
            return
        source = selected[0] if source is None else source
        selected_set = set(selected)
        picked = [units[index] for index in selected]
        remaining = [unit for i, unit in enumerate(units) if i not in selected_set]
        boundary = target+1 if target > source else target
        insertion = sum(i not in selected_set for i in range(boundary))
        ordered = remaining[:insertion] + picked + remaining[insertion:]
        self.panels = [p for unit in ordered for p in unit]
        self._refresh_order(range(insertion, insertion+len(picked)))
        self.update_preview()

    def _clear_order_drop(self):
        if self.order_drop_index is not None and self.order_drop_index < self.order_list.size():
            self.order_list.itemconfigure(self.order_drop_index, background=self.order_list.cget("background"))
        self.order_drop_index = None

    def _order_start(self, event):
        self._clear_order_drop()
        self.drag_index = None
        self.order_drag_selection = ()
        self.order_native_selection = False
        if not self.panels or self.busy:
            return "break"
        self.order_list.focus_set()
        if getattr(event, "state", 0) & (0x0001 | 0x0004):
            # Let Tk's extended-selection bindings handle Shift ranges and Ctrl
            # toggles; modified clicks must never start a reorder operation.
            self.order_native_selection = True
            return
        index = self.order_list.nearest(event.y)
        bounds = self.order_list.bbox(index)
        if not bounds or not bounds[1] <= event.y < bounds[1]+bounds[3]:
            self.select_order("none")
            return "break"
        selected = self.order_list.curselection()
        if index not in selected:
            self.order_list.selection_clear(0, "end")
            self.order_list.selection_set(index)
            self.order_list.selection_anchor(index)
            selected = (index,)
        self.order_list.activate(index)
        self.drag_index, self.order_drag_selection = index, selected
        # Ordinary single clicks still initialize Tk's selection anchor/state.
        # Only suppress the class binding when preserving a multi-item drag.
        return "break" if len(selected) > 1 else None

    def _order_motion(self, event):
        if self.order_native_selection:
            return
        if self.drag_index is not None:
            self._clear_order_drop()
            target = self.order_list.nearest(event.y)
            self.order_drop_index = target
            self.order_list.itemconfigure(target, background="#DCE9F8")
            if event.y < UI["drag_scroll_edge"]:
                self.order_list.yview_scroll(-UI["drag_scroll_units"], "units")
            elif event.y > self.order_list.winfo_height()-UI["drag_scroll_edge"]:
                self.order_list.yview_scroll(UI["drag_scroll_units"], "units")
        return "break"

    def _order_end(self, event):
        if self.order_native_selection:
            self.order_native_selection = False
            return
        self._clear_order_drop()
        if self.drag_index is not None:
            self._move_order_selection(self.order_drag_selection, self.order_list.nearest(event.y), self.drag_index)
        self.drag_index = None
        self.order_drag_selection = ()
        return "break"

    def move_order(self, direction):
        selection = self.order_list.curselection()
        if not selection or self.busy:
            return
        tagged = [(unit, i in selection) for i, unit in enumerate(self._order_units())]
        if direction < 0:
            for i in range(1, len(tagged)):
                if tagged[i][1] and not tagged[i-1][1]:
                    tagged[i-1], tagged[i] = tagged[i], tagged[i-1]
        else:
            for i in range(len(tagged)-2, -1, -1):
                if tagged[i][1] and not tagged[i+1][1]:
                    tagged[i], tagged[i+1] = tagged[i+1], tagged[i]
        self.panels = [p for unit, _ in tagged for p in unit]
        self._refresh_order([i for i, (_, selected) in enumerate(tagged) if selected])
        self.update_preview()

    def remove_panel(self):
        selection = self.order_list.curselection()
        if selection and not self.busy:
            units = self._order_units()
            removed = {p.key for i in selection for p in units[i]}
            units = [unit for i, unit in enumerate(units) if i not in selection]
            self.panels = [p for unit in units for p in unit]
            self.extra_images = [item for item in self.extra_images if item["key"] not in removed]
            self.layout_offsets = {key: value for key, value in self.layout_offsets.items() if key not in removed}
            self._refresh_roi_controls()
            self._refresh_order()
            self.update_preview()

    def _remap_layout_offsets(self, key_map, valid_keys):
        self.layout_offsets = {key_map[key]: value for key, value in self.layout_offsets.items()
                               if key in key_map and key_map[key] in valid_keys}
        self.layout_position_key = key_map.get(self.layout_position_key)

    def _sync_layout_position(self):
        self.preview.delete("position-selection")
        item = self.layout_placements.get(self.layout_position_key)
        if item is None:
            if not any(p.key == self.layout_position_key for p in self.panels):
                self.layout_position_key = None
            self.layout_position_text.set("点击预览中的图片，查看和修改它的位置。")
            for variable in self.layout_position_vars.values():
                variable.set("")
        else:
            linked = linked_panel_keys(self.panels, self.layout_position_key)
            suffix = "  ·  同组 ROI 随原图移动" if len(linked) > 1 else ""
            self.layout_position_text.set(f"{item.panel.title}  ·  {item.width} × {item.height} px{suffix}")
            self.layout_position_vars["x"].set(str(item.x))
            self.layout_position_vars["y"].set(str(item.y))
            for key, box in self.preview_hits:
                if key in linked:
                    self.preview.create_rectangle(*box, outline="#2475D0", width=LAYOUT["drop_outline_width"], tags="position-selection")
        self.position_apply_button.configure(state="normal" if item is not None else "disabled")
        self.position_reset_button.configure(state="normal" if item is not None else "disabled")
        self.position_reset_all_button.configure(state="normal" if self.layout_offsets else "disabled")

    def _position_from_order(self, event=None):
        units = self._order_units()
        selected = [i for i in self.order_list.curselection() if i < len(units)]
        keys = {p.key for i in selected for p in units[i]}
        if self.layout_position_key not in keys:
            self.layout_position_key = units[selected[0]][0].key if selected else None
        self._sync_layout_position()

    def apply_layout_position(self, event=None):
        if self.busy or self.layout_position_key is None:
            return "break"
        try:
            try:
                x, y = (int(self.layout_position_vars[axis].get()) for axis in ("x", "y"))
            except ValueError as error:
                raise ValueError("距左、距上请输入整数像素，可以输入负数。") from error
            updated = move_panel_to(self.panels, self.options(), self.layout_position_key, x, y)
            self.layout_offsets = updated.panel_offsets
            self.update_preview()
            self.status.set("图片位置已更新；若出现负坐标，已按所有图片最左、最上的边缘重新计算原点。")
        except (ValueError, tk.TclError) as error:
            self._fail(error)
        return "break"

    def reset_layout_position(self, all_images=False):
        if self.busy:
            return
        if all_images:
            self.layout_offsets = {}
        else:
            for key in linked_panel_keys(self.panels, self.layout_position_key):
                self.layout_offsets.pop(key, None)
        self.update_preview()

    def _schedule_preview(self, *args):
        if self.preview_after:
            self.root.after_cancel(self.preview_after)
        self.preview_after = self.root.after(TIMING["layout_preview_ms"], self.update_preview)

    def update_preview(self):
        self._update_original_size_info()
        self._update_method_button()
        if self.preview_after:
            self.root.after_cancel(self.preview_after)
            self.preview_after = None
        self.preview.delete("all")
        self.preview_hits = []
        self.layout_placements = {}
        if not self.panels:
            self.preview.create_text(max(UI["layout_hint_min_x"], self.preview.winfo_width()//2), max(UI["layout_hint_min_y"], self.preview.winfo_height()//2), text="生成排版素材，或导入已有的画框图与 ROI", fill="#566577", font=("Microsoft YaHei UI", UI["layout_hint_font_size"]))
            self._sync_layout_position()
            return
        try:
            options = self.options()
            plan, _ = layout_plan(self.panels, options)
            image, size, hits = render_layout(self.panels, options, (max(LAYOUT["preview_min_size"], self.preview.winfo_width()-LAYOUT["preview_padding"]), max(LAYOUT["preview_min_size"], self.preview.winfo_height()-LAYOUT["preview_padding"])))
            self.layout_placements = {item.panel.key: item for item in plan}
            ox, oy = (self.preview.winfo_width()-image.width)//2, (self.preview.winfo_height()-image.height)//2
            self.layout_photo = ImageTk.PhotoImage(image)
            self.preview.create_image(ox, oy, anchor="nw", image=self.layout_photo)
            self.preview_hits = [(key, (x1+ox, y1+oy, x2+ox, y2+oy)) for key, (x1, y1, x2, y2) in hits]
            self.layout_info.configure(text=f"导出 {size[0]} × {size[1]} px  |  {len(self.panels)} 张素材  |  预览已缩放")
        except (ValueError, OSError, MemoryError) as error:
            self.layout_info.configure(text=f"无法排版：{error}")
        self._sync_layout_position()

    def _hit_panel(self, event):
        # Later images are painted on top, including ROIs over an original.
        blank_keys = {panel.key for panel in self.panels if panel.blank_size is not None}
        blank_hit = None
        for key, (x1, y1, x2, y2) in reversed(self.preview_hits):
            if x1 <= event.x < x2 and y1 <= event.y < y2:
                if key in blank_keys:
                    if blank_hit is None:
                        blank_hit = key
                    continue
                return key
        return blank_hit

    def _hit(self, event):
        key = self._hit_panel(event)
        for i, unit in enumerate(self._order_units()):
            if any(p.key == key for p in unit):
                return i
        return None

    def _preview_start(self, event):
        if self.busy:
            return
        self.layout_position_key = self._hit_panel(event)
        self.drag_index = self._hit(event)
        if self.drag_index is None:
            self.clear_shape_selection()
            self.select_order("none")
        if self.drag_index is not None:
            selection = self.order_list.curselection()
            self.order_drag_selection = selection if self.drag_index in selection else (self.drag_index,)
            self._refresh_order(self.order_drag_selection)
            self.preview.configure(cursor="fleur")
        self._sync_layout_position()

    def _preview_motion(self, event):
        self.preview.delete("drop")
        if self.drag_index is not None:
            target = self._hit(event)
            if target is not None:
                keys = {p.key for p in self._order_units()[target]}
                for key, box in self.preview_hits:
                    if key in keys:
                        self.preview.create_rectangle(*box, outline="#2475D0", width=LAYOUT["drop_outline_width"], tags="drop")

    def _preview_end(self, event):
        target = self._hit(event)
        if target is not None:
            self._move_order_selection(self.order_drag_selection, target, self.drag_index)
        self.preview.delete("drop")
        self.preview.configure(cursor="")
        self.drag_index = None
        self.order_drag_selection = ()

    def export_images(self):
        if not self.files or self.busy:
            return
        directory = filedialog.askdirectory(title="选择输出目录（自动新建结果文件夹）")
        if not directory:
            return
        from datetime import datetime
        output = Path(directory) / ("ROI_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        files, shapes, size, rotation = list(self.files), list(self.shapes), self.reference_size, self.rotation
        self._work(lambda: export_batch(files, shapes, size, output, rotation),
                   lambda paths: self.status.set(f"已无损保存 {len(paths)} 张 PNG：{output}"), "正在按原始分辨率导出画框图和 ROI…")

    def export_layout(self):
        if not self.panels or self.busy:
            return
        if self.dirty_panels:
            self.status.set("标注已修改，请点击“生成 / 刷新排版素材”后再导出。")
            return
        try:
            options = self.options()
            initialfile = self.suggested_layout_filename()
        except (ValueError, tk.TclError) as error:
            self._fail(error)
            return
        path = filedialog.asksaveasfilename(title="无损保存排版图", defaultextension=".png", filetypes=[("无损 PNG", "*.png"), ("无损 TIFF", "*.tif")], initialfile=initialfile)
        if not path:
            return
        panels = list(self.panels)
        def task():
            image, size, _ = render_layout(panels, options)
            save_lossless(image, path, options.dpi)
            return size
        self._work(task, lambda size: self.status.set(f"已无损保存 {size[0]} × {size[1]} px：{path}"), "正在从原始素材渲染并无损保存排版图…")

    def save_project(self):
        if self.busy:
            return
        path = filedialog.asksaveasfilename(title="保存可继续编辑的项目", defaultextension=".json", filetypes=[("ROI 项目", "*.json")])
        if not path:
            return
        try:
            data = {"version": 1, "files": self.files, "rotation": self.rotation, "reference_size": self.reference_size,
                    "shapes": [asdict(s) for s in self.shapes], "layout": asdict(self.options()),
                    "generated": self.generated, "panel_order": [p.key for p in self.panels],
                    "panels_dirty": self.dirty_panels,
                    "panel_titles": {p.key: p.title for p in self.panels},
                    "method_names": self.method_names,
                    "extra_images": [item for item in self.extra_images if any(p.key == item["key"] for p in self.panels)],
                    "import_rotation": self.import_rotation,
                    "imports": self.import_files if not self.generated else []}
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status.set(f"项目已保存：{path}（请保留源图文件）")
        except (ValueError, OSError) as error:
            self._fail(error)

    def open_project(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title="打开 ROI 项目", filetypes=[("ROI 项目", "*.json")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if data.get("version") != 1:
                raise ValueError("不支持的项目版本")
            files, rotation = data["files"], data["rotation"]
            if rotation not in (0, 90, 180, 270):
                raise ValueError("无效旋转角度")
            reference_size = tuple(data["reference_size"])
            if len(reference_size) != 2 or min(reference_size) <= 0:
                raise ValueError("无效参考尺寸")
            shapes = [Shape(**item) for item in data["shapes"]]
            for shape in shapes:
                shape.validate()
            options = LayoutOptions(**data["layout"])
            options.validate()
            options.labels = False
            method_names = data.get("method_names", {})
            if not isinstance(method_names, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in method_names.items()):
                raise ValueError("项目中的方法名必须是文本")
            image = load_image(files[0], rotation) if files else None
            imports = data.get("imports", [])
            import_rotation = data.get("import_rotation", 0)
            if type(import_rotation) is not int or import_rotation not in (0, 90, 180, 270):
                raise ValueError("无效导入图片旋转角度")
            panels = list(make_panels(files, shapes, reference_size, rotation)) if data.get("generated") else list(import_panels(imports, import_rotation))
            extras = data.get("extra_images", [])
            panels.extend(self._load_extra_panels(extras))
            lookup = {p.key: p for p in panels}
            panels = [lookup[key] for key in data.get("panel_order", []) if key in lookup]
            for panel in panels:
                panel.title = data.get("panel_titles", {}).get(panel.key, panel.title)
            self.files, self.shapes, self.image = files, shapes, image
            self.index, self.rotation, self.reference_size = 0, rotation, reference_size
            self.panels, self.generated = panels, bool(data.get("generated"))
            self.dirty_panels = bool(data.get("panels_dirty", False))
            self.import_files = imports
            self.import_rotation = import_rotation
            self.extra_images = [item for item in extras if any(p.key == item["key"] for p in panels)]
            self.method_names = method_names
            valid_keys = {p.key for p in panels}
            self.layout_offsets = {key: value for key, value in options.panel_offsets.items() if key in valid_keys}
            self.layout_position_key = None
            for key, var in self.layout_vars.items():
                var.set(str(getattr(options, key)))
            self.layout_mode.set(next(name for name, mode in MODES.items() if mode == options.mode))
            self.labels.set(options.labels)
            self.interpolation.set("像素复制（保留像素）" if options.interpolation == "nearest" else "Lanczos（平滑放大）")
            self.background = options.background
            self._refresh_roi_controls(saved=options.roi_settings, reset=True)
            self._refresh_shapes()
            self._show_image()
            self._layout_mode_changed()
            self.status.set(f"项目已恢复：{path}")
        except (ValueError, OSError, KeyError, TypeError) as error:
            self._fail(error)

    def _close(self):
        if self.busy:
            self.status.set("正在导出，请等待完成后再关闭。")
            return
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    ImageCropper().run()


if __name__ == "__main__":
    main()
