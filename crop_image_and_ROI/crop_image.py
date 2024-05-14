########################
# Author: Lixing Dong
# Note: PIL required
# License: GPL
########################
import math

from typing import List
from PIL import Image
from PIL.ExifTags import TAGS
import sys, os
import tkinter as tk
from tkinter import filedialog, font, messagebox
from PIL import ImageTk, ImageDraw
import customtkinter


class ImageCropper:

    def __init__(self):
        self.message = None
        self.rectangle = None
        self.canvas_image = None
        self.canvas_message = None
        self.files = []
        self.box = [0, 0, 0, 0]
        self.ratio = 1.0
        self.img_h = 400
        self.img_w = 400
        self.min_image_h = 300
        self.min_image_w = 300
        self.right_frame_width = 500
        self.img = None
        self.shape_list: List[RightFrameItem] = []
        self.arrow_root_xy = [0, 0]
        self.arrow_mini_size = 10
        self.arrow_cur_size = 20
        self.arrow_step = 2
        self.mini_rec_area = 100

        self.files = None
        self.file_in_root_path = None
        self.file_out_root_path = None
        self.shapes = []
        self.cur_shape = None
        self.default_color = '#ff1111'
        self.default_line_width = 2
        self.rectange_mode = True
        self.arrow_mode = False
        self.shape_name = "矩形"
        self.drawed_shapes = []

        self.line_arrow = {}

        self.line_handle = {}

        self.__init_layout()
        self.__bind_event()

        self.right_frame_var = tk.IntVar()
    
    def __init_layout(self):
        self.root = root = tk.Tk()
        self.root.resizable(0, 0)

        screen_width = self.root.winfo_screenwidth()
        factor = screen_width / 1920
        self.right_frame_width = int(self.right_frame_width * factor) 

        self.top_frame = tk.Frame(root)

        self.left_frame = tk.Frame(root, width=self.img_w, height=self.img_h)
        self.right_frame =  customtkinter.CTkScrollableFrame(master=self.root, width=self.right_frame_width, height=200)


        self.top_frame.grid(row=0, columnspan=2, sticky='nsew')
        self.left_frame.grid(row=1, column=0, sticky='nsew')

        self.right_frame.grid(row=1, column=1, sticky='nsew')


        self.file_btn = tk.Button(self.top_frame, text="文  件", font=10, command=self.__on_file_button)
        self.file_btn.grid(row=0, column=0, padx=2)
        # self.top_frame.columnconfigure(0, minsize=80)

        self.save_btn = tk.Button(self.top_frame, text="保  存", font=10, underline=5, command=self.__on_save_button)
        self.save_btn.grid(row=0, column=1, padx=2)
        # self.top_frame.columnconfigure(1, minsize=80)

        tk.Label(self.top_frame, text="  图像大小：", font=10).grid(row=0, column=2)
        self.vcmd = (self.top_frame.register(self.__only_numeric_input), "%P")
        self.height_str = tk.IntVar(self.top_frame, value=self.img_h)
        self.image_h_entry = tk.Entry(self.top_frame, textvariable=self.height_str, width=5, font=10, validate="key", validatecommand=self.vcmd)
        self.image_h_entry.grid(row=0, column=3)
        tk.Label(self.top_frame, text="x", font=10).grid(row=0, column=4)
        self.width_str = tk.IntVar(self.top_frame, value=self.img_w)
        self.image_w_entry = tk.Entry(self.top_frame, textvariable=self.width_str, width=5, font=10, validate="key", validatecommand=self.vcmd)
        self.image_w_entry.grid(row=0, column=5)

        
        self.v = tk.IntVar(self.top_frame, value=1)
        self.rectange_mode_btn = tk.Radiobutton(self.top_frame, text="矩形框", variable=self.v, value=1, font=10, command=self.__on_rectange_mode)
        self.rectange_mode_btn.select()
        self.rectange_mode_btn.grid(row=0, column=6)
        tk.Radiobutton(self.top_frame, text="箭头", variable=self.v, value=2, font=10, command=self.__on_arrow_mode).grid(row=0, column=7)

        self.file_btn = tk.Button(self.top_frame, text="清  除", font=10, command=self.__on_clear_shapes)
        self.file_btn.grid(row=0, column=8, padx=2)
        # self.top_frame.columnconfigure(0, minsize=80)

        self.file_btn = tk.Button(self.top_frame, text="更  新", font=10, command=self.__on_update_all_shapes)
        self.file_btn.grid(row=0, column=9, padx=2)
        # self.top_frame.columnconfigure(0, minsize=80)

        self.arrow_up_btn = tk.Button(self.top_frame, text="箭头增大", font=10, command=self.__on_arrow_up)
        self.arrow_up_btn.grid(row=0, column=10, padx=2)

        self.arrow_down_btn = tk.Button(self.top_frame, text="箭头减小", font=10, command=self.__on_arrow_down)
        self.arrow_down_btn.grid(row=0, column=11, padx=2)
        self.arrow_size_var = tk.IntVar(value=self.arrow_cur_size)
        self.arrow_size_label = tk.Label(self.top_frame, textvariable=self.arrow_size_var, font=10)
        self.arrow_size_label.grid(row=0, column=12)

        self.canvas = tk.Canvas(self.left_frame, 
                             highlightthickness = 0)
        # self.__update_window_size()
    
    def update_widget(self):
        self.left_frame.config(width=self.img_w, height=self.img_h)

    def __get_selected_shape_index(self):
        shape_index = []
        for index, item in enumerate(self.shape_list):
            if item.is_selected():
                shape_index.append(index)
        
        return shape_index
    

    def __on_arrow_up(self):
        shape_index = self.__get_selected_shape_index()
        if len(shape_index) == 0:
            self.arrow_cur_size += self.arrow_step
            self.arrow_size_var.set(self.arrow_cur_size)
        else:
            for index in shape_index:
                shape = self.shapes[index]
                shape.arrow_size += self.arrow_step
            self.__recreate_right_frame_item_by_shapes()
            self.__refresh_rectangle()

            for index in shape_index:
                self.shape_list[index].bool_var.set(True)


    def __on_arrow_down(self):
        shape_index = self.__get_selected_shape_index()
        if len(shape_index) == 0:
            self.arrow_cur_size = self.arrow_cur_size - self.arrow_step if self.arrow_cur_size >= self.arrow_mini_size + self.arrow_step else self.arrow_mini_size
            self.arrow_size_var.set(self.arrow_cur_size)
        else:
            for index in shape_index:
                shape = self.shapes[index]
                shape.arrow_size -= self.arrow_step
                if shape.arrow_size < self.arrow_mini_size:
                    shape.arrow_size = self.arrow_mini_size

            self.__recreate_right_frame_item_by_shapes()
            self.__refresh_rectangle()

            for index in shape_index:
                self.shape_list[index].bool_var.set(True)
        

    def __only_numeric_input(self, P):
        if P.isdigit():
            return True
        return False
    

    def __bind_event(self):
        self.image_h_entry.bind("<KeyRelease>", self.__on_update_image_size)
        self.image_w_entry.bind("<KeyRelease>", self.__on_update_image_size)

        self.canvas.bind("<Button-1>", self.__on_mouse_down)
        self.canvas.bind("<ButtonRelease-1>", self.__on_mouse_release)
        self.canvas.bind("<B1-Motion>", self.__on_mouse_move)


    def get_image_exif(self, image):
        if image is None:
            img_exif = None
        info = image._getexif()
        if info is not None:
            img_exif = {}
            for tag, value in info.items():
                decoded = TAGS.get(tag, tag)
                img_exif[decoded] = value
            return img_exif
        else:
            return None

    def rotate(self, image, exif):
        if exif is None:
            return image
        if exif['Orientation'] == 6:
            return image.rotate(-90)

    def set_ratio(self, ratio):
        self.ratio = float(ratio)

    
    def __get_image(self, filename):
        if filename == None:
            return None

        self.filename = filename
        self.outputname = filename[:filename.rfind('.')] + '_cropped'
        try:
            self.img = Image.open(filename)
        except IOError:
            print('Ignore: ' + filename + ' cannot be opened as an image')
            return None

        exif = self.get_image_exif(self.img)
        img = self.rotate(self.img, exif)
        self.img = img

        return img
    
    def set_image(self, filename):
        self.img = self.__get_image(filename)

    def __on_clear_shapes(self):
        print("__on_clear_all_shapes")
        index = 0
        while index < len(self.drawed_shapes):
            if self.shape_list[index].is_selected():
                deleted_item = self.drawed_shapes.pop(index)
                # self.canvas.delete(deleted_item)
                self.__canvas_delete_shape(deleted_item)
                self.shapes.pop(index)
                self.shape_list.pop(index)
                
            else:
                index += 1
        if len(self.drawed_shapes) != len(self.right_frame.winfo_children()):
            self.__recreate_right_frame_item_by_shapes()

        self.cur_shape = None
        self.__refresh_rectangle()

        
    
    def __on_update_all_shapes(self):
        self.shapes = []
        discarded_shape = []
        for index, item in enumerate(self.shape_list):
            shape = item.to_shape()
            if (abs(shape.box[2] - shape.box[0]) * abs(shape.box[3] - shape.box[1])) >= self.mini_rec_area:
                self.shapes.append(shape)
            elif shape.shape_name == "箭头":
                self.shapes.append(shape)
            else:
                discarded_shape.append(str(index))

        if discarded_shape:
            msg = "形状  " + ", ".join(discarded_shape) + f" 面积<{self.mini_rec_area}, 被丢弃！"
            messagebox.showinfo(message=msg)

        self.__recreate_right_frame_item_by_shapes()
        self.__refresh_rectangle()
    
    def __on_file_button(self):
        print("__on_file_button")
        if self.file_in_root_path is None:
            self.file_in_root_path = "./"
        files = filedialog.askopenfilenames(initialdir=self.file_in_root_path)
        if isinstance(files, tuple) and len(files) > 0:
            self.files = files
            self.file_in_root_path = os.path.dirname(files[0])
        
        if not self.file_out_root_path:
            self.file_out_root_path = self.file_in_root_path
        
        self.shapes = []
        self.__recreate_right_frame_item_by_shapes()
        self.__refresh_rectangle()
        
        self.set_image(self.files[0])
        self.__update_img_size()
    
    def __on_rectange_mode(self):
        print("rectange mode")
        self.rectange_mode = True
        self.arrow_mode = False
        self.shape_name = "矩形"
    
    def __on_arrow_mode(self):
        print("arrow mode")
        self.rectange_mode=False
        self.arrow_mode = True
        self.shape_name = "箭头"
    
    def __on_update_image_size(self, event):
        self.__update_window_size()
        self.__update_img_size()
    
    def __update_window_size(self):
        img_h = int(self.image_h_entry.get())
        img_w = int(self.image_w_entry.get())
        if img_h != self.img_h or img_w != self.img_w:
            self.__on_clear_shapes()
        self.img_h = int(img_h)
        self.img_w = int(img_w)
        # self.update_widget()
        self.img_h = img_h if img_h >= self.min_image_h else self.min_image_h
        self.img_w = img_w if img_w >= self.min_image_w else self.min_image_w
        self.height_str.set(self.img_h)
        self.width_str.set(self.img_w)

        self.__set_window_size()

        self.left_frame.config(width=self.img_w, height=self.img_h)
        self.right_frame.config(width=self.right_frame_width, height=self.img_h)
        self.root.update()
    
    def __set_window_size(self):
        win_h = self.img_h + 35
        win_w = self.img_w + self.right_frame_width
        self.root.geometry(f"{win_w}x{win_h}")
        
    
    def __update_img_size(self):
        self.scale = (self.img.size[0]/self.img_h, self.img.size[1]/self.img_w)
        self.resized_img = self.img.resize((self.img_w, self.img_h), Image.ANTIALIAS)

        self.photo = ImageTk.PhotoImage(self.resized_img)
        if self.canvas_image is not None:
            self.canvas.delete(self.canvas_image)
        self.canvas.config(width = self.resized_img.size[0], height = self.resized_img.size[1])
        self.canvas_image = self.canvas.create_image(0, 0, anchor = tk.NW, image = self.photo)
        # self.canvas.pack(fill = tk.BOTH, expand = tk.YES)
        self.canvas.pack(padx=10, pady=10)

       

    def __on_mouse_down(self, event):
        self.box[0], self.box[1] = event.x, event.y
        self.box[2], self.box[3] = event.x, event.y
        box = [event.x, event.y, event.x, event.y]
        self.cur_shape = Shape(self.shape_name, box, self.default_line_width, self.default_color, self.arrow_cur_size)

    def __on_mouse_release(self, event):
        print(abs(self.cur_shape.box[2] - self.cur_shape.box[0]) * abs(self.cur_shape.box[3] - self.cur_shape.box[1]))
        if (abs(self.cur_shape.box[2] - self.cur_shape.box[0]) * abs(self.cur_shape.box[3] - self.cur_shape.box[1]) >= self.mini_rec_area) or self.cur_shape.shape_name == "箭头":
            if self.cur_shape is not None:
                self.shapes.append(self.cur_shape)
            self.__add_right_frame_item(self.cur_shape)
            self.cur_shape = None
            self.__refresh_rectangle()
        else:
            self.cur_shape = None
            self.__refresh_rectangle()

    def __crop_image(self):
        box = (self.box[0] * self.scale,
               self.box[1] * self.scale,
               self.box[2] * self.scale, 
               self.box[3] * self.scale)
        try:
            cropped = self.img.crop(box)
            if cropped.size[0] == 0 and cropped.size[1] == 0:
                raise SystemError('no size')
            cropped.save(self.outputname + '.jpg', 'jpeg')
            self.message = 'Saved: ' + self.outputname + '.jpg'
        except SystemError as e:
            pass

    def __fix_ratio_point(self, px, py):
        dx = px - self.box[0]
        dy = py - self.box[1]
        if min((dy / self.ratio), dx) == dx:
            dy = int(dx * self.ratio)
        else:
            dx = int(dy / self.ratio)
        return self.box[0] + dx, self.box[1] + dy


    def __on_mouse_move(self, event):
        self.cur_shape.box[2] = event.x
        self.cur_shape.box[3] = event.y
        # if self.shape_name == "矩形":
        self.__refresh_rectangle()
    
    def __canvas_delete_shape(self, handle):
        if handle in self.line_arrow:
            self.canvas.delete(self.line_arrow[handle])
        
        self.canvas.delete(handle)

    def __refresh_rectangle(self):
        for item in self.drawed_shapes:
            # self.canvas.delete(item)
            self.__canvas_delete_shape(item)
        self.drawed_shapes = []

        for shape in self.shapes:
            # rectange = self.canvas.create_rectangle(*shape.box, outline=shape.color, width=shape.line_width)
            shape_handle = self.__draw_shape(shape)
            self.drawed_shapes.append(shape_handle)
        
        if self.cur_shape is not None:
            # rectange = self.canvas.create_rectangle(*self.cur_shape.box, outline=self.cur_shape.color, width=self.cur_shape.line_width)
            # self.drawed_shapes.append(rectange)

            shape_handle = self.__draw_shape(self.cur_shape)
            self.drawed_shapes.append(shape_handle)
    
    def __draw_shape(self, shape):
        if shape.shape_name == "矩形":
            res = self.canvas.create_rectangle(*shape.box, outline=shape.color, width=shape.line_width)
        else:
            res = self.__draw_line_with_arrow(shape)

        return res
    
    def __draw_line_with_arrow(self, shape):
        res = self.canvas.create_line(*shape.box, fill=shape.color, width=shape.line_width, smooth=True)
        arrow_coord = arrow_coordinates(shape)

        poly = self.canvas.create_polygon(*arrow_coord, fill=shape.color, width=shape.line_width)
        self.line_arrow[res] = poly
        return res
    
   
    
    def __add_right_frame_item(self, shape):
        item = RightFrameItem(self.right_frame, len(self.shape_list), shape, height=30, width=self.right_frame_width)
        self.shape_list.append(item)

    
    def __recreate_right_frame_item_by_shapes(self):
        for widget in self.right_frame.winfo_children():
            widget.destroy()
        self.shape_list = []
        for shape in self.shapes:
            self.__add_right_frame_item(shape)
    
    def __on_save_button(self):
        print("__on_save_button")

        if self.file_out_root_path is None:
            self.file_out_root_path = "./"
        
        path_root = filedialog.askdirectory(initialdir=self.file_out_root_path)
        self.save_images_with_rectange(path_root)
        self.save_images_with_arrow(path_root)

    
    def save_images_with_rectange(self, root_path):
        images: List[ImageWrapper] = []
        for filename in self.files:
            image = self.__get_image(filename)
            images.append(ImageWrapper(image, self.scale))
        
        for image_wrapper in images:
            for shape in self.shapes:
                if shape.shape_name == "矩形":
                    image_wrapper.draw_rectange_on_origin_image(shape)
        
        path = os.path.join(root_path, "rectange")
        if not os.path.exists(path):
            os.makedirs(path)

        for filename, image_wrapper in zip(self.files, images):
            basename = os.path.basename(filename)
            path = os.path.join(root_path, "rectange", basename)
            image_wrapper.save_origin_image_with_rectange(path)
    
    def save_images_with_arrow(self, root_path):
        images: List[ImageWrapper] = []
        for filename in self.files:
            image = self.__get_image(filename)
            images.append(ImageWrapper(image, self.scale))
        
        for image_wrapper in images:
            for shape in self.shapes:
                if shape.shape_name == "箭头":
                    image_wrapper.draw_arrow_on_origin_image(shape)
        
        index = 0
        for shape in self.shapes:
            if shape.shape_name == "箭头":
                continue
            path = os.path.join(root_path, f"roi-{index}")
            if not os.path.exists(path):
                os.makedirs(path)

            for filename, image_wrapper in zip(self.files, images):
                basename = os.path.basename(filename)
                path = os.path.join(root_path, f"roi-{index}", basename)
                image_wrapper.save_roi(shape, path)

            index += 1

        path = os.path.join(root_path, "rectange")
        if not os.path.exists(path):
            os.makedirs(path)

        for filename, image_wrapper in zip(self.files, images):
            basename = os.path.basename(filename)
            path = os.path.join(root_path, "rectange", basename)
            image_wrapper.save_origin_image_with_rectange(path)
        

    def run(self):
        # self.roll_image()
        self.root.mainloop()
        # self.root.update()

def arrow_coordinates(shape):
    point1 = [3, 0]
    point2 = [-shape.arrow_size, shape.arrow_size/2]
    point3 = [-shape.arrow_size/2, 0]
    point4 = [-shape.arrow_size, -shape.arrow_size/2]

    angle = get_angle(shape.box[:2], shape.box[2:])
    # print("angle:", angle * 180 / math.pi)
    # angle = 0

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



class RightFrameItem:
    def __init__(self, master, index, shape, height, width) -> None:
        self.shape_name =shape.shape_name
        font_size = font.Font(master, size=11)
        item_frame = tk.Frame(master,  height=height, width=width, bd=2)
        # item_frame2 = tk.Frame(master,  bg="#dbdbdb", height=3, width=width, bd=2)
        item_frame.pack(side="top", fill="both", pady=2)
        # item_frame2.pack(side="top", fill="both")

        self.bool_var = tk.BooleanVar(False)
        self.selection_btn = tk.Checkbutton(item_frame, text=f"{index}", variable=self.bool_var, font=font_size, width=2)

        self.color_label1 = tk.Label(item_frame, text="    ", bg=shape.color, font=font_size, bd=2)
        self.color_var = tk.StringVar(master, value=shape.color)
        self.color_entry = tk.Entry(item_frame, textvariable=self.color_var, width=8, font=font_size)

        self.line_width_label = tk.Label(item_frame, text="线宽：", width=5, height=1, bd=2,  font=font_size)
        self.line_width_var = tk.IntVar(master, value=shape.line_width)
        self.line_width_entry = tk.Entry(item_frame, textvariable=self.line_width_var, width=8, font=font_size)

        self.coord_label = tk.Label(item_frame,  text="坐标", font=font_size)

        self.x1 = tk.Label(item_frame, text="x1:",  font=font_size)
        self.x1_var = tk.IntVar(master, value=shape.box[0])
        self.x1_entry = tk.Entry(item_frame, textvariable=self.x1_var, width=5, font=font_size)

        self.y1 = tk.Label(item_frame, text="y1:", font=font_size)
        self.y1_var = tk.IntVar(master, value=shape.box[1])
        self.y1_entry = tk.Entry(item_frame, textvariable=self.y1_var, width=5, font=font_size)

        self.x2 = tk.Label(item_frame, text="x2:", font=font_size)
        self.x2_var = tk.IntVar(master, value=shape.box[2])
        self.x2_entry = tk.Entry(item_frame, textvariable=self.x2_var, width=5, font=font_size)

        self.y2 = tk.Label(item_frame, text="y2:", font=font_size)
        self.y2_var = tk.IntVar(master, value=shape.box[3])
        self.y2_entry = tk.Entry(item_frame, textvariable=self.y2_var, width=5, font=font_size)

        self.selection_btn.grid(row=0, column=0)
        self.coord_label.grid(row=0, column=1)
        self.x1.grid(row=0, column=2)
        self.x1_entry.grid(row=0, column=3)
        self.y1.grid(row=0, column=4, padx=(5, 0))
        self.y1_entry.grid(row=0, column=5)
        self.color_label1.grid(row=0, column=6, padx=(30, 10))
        self.color_entry.grid(row=0, column=7)

        self.x2.grid(row=1, column=2)
        self.x2_entry.grid(row=1, column=3)
        self.y2.grid(row=1, column=4, padx=(5, 0))
        self.y2_entry.grid(row=1, column=5)
        self.line_width_label.grid(row=1, column=6, padx=(30, 10))
        self.line_width_entry.grid(row=1, column=7)
        self.arrow_size = shape.arrow_size
    
    def is_selected(self):
        return self.bool_var.get()
    
    def to_shape(self):
        box = (self.x1_var.get(), self.y1_var.get(), self.x2_var.get(), self.y2_var.get())
        shape = Shape(self.shape_name, box, self.line_width_var.get(), self.color_var.get(), self.arrow_size)
        print(shape)

        return shape

class Shape:
    def __init__(self, shape_name, box, line_width, color, arrow_size):
        self.shape_name = shape_name
        self.box = box
        self.line_width = line_width
        self.color = color
        self.arrow_size = arrow_size
    
    def __str__(self):
        return f"{self.shape_name}: [{self.box}]  {self.line_width}  {self.color}"


class ImageWrapper:
    def __init__(self, origin_img, scale):
        self.origin_img = origin_img
        self.scale = scale
        self.resized_img = origin_img.resize((int(origin_img.size[0] / scale[0]), int(origin_img.size[1] / scale[1])), Image.ANTIALIAS)
        self.origin_img_with_rectange = origin_img
    
    def crop_img(self, shapes: Shape):
        img_with_arrow = self.origin_img
        img_with_rectange = self.origin_img
        for shape in shapes:
            if shape.shape_name == "箭头":
                img_with_arrow = self.__draw_arrow(img_with_arrow, shape.box, shape.color, shape.line_width)
            elif shape.shape_name == "矩形":
                img_with_rectange = self.__draw_rectange(img_with_rectange, shape.box, shape.color, shape.line_width)

        box = (box[0] * self.scale,
               box[1] * self.scale,
               box[2] * self.scale, 
               box[3] * self.scale)
        try:
            cropped = self.origin_img.crop(box)
            if cropped.size[0] == 0 and cropped.size[1] == 0:
                raise SystemError('no size')
            cropped.save(self.outputname + '.jpg', 'jpeg')
            self.message = 'Saved: ' + self.outputname + '.jpg'
        except SystemError as e:
            pass
    
    def draw_arrow_on_origin_image(self, shape: Shape):
        arrow_coord = arrow_coordinates(shape)
        box = [(shape.box[0], shape.box[1]),
               (shape.box[2], shape.box[3])]
        
        xys = []
        for i in range(0, len(arrow_coord), 2):
            xys.append((arrow_coord[i], arrow_coord[i+1]))
        
        draw = ImageDraw.Draw(self.resized_img)
        draw.line(box, fill=shape.color, width=shape.line_width*2)
        draw.polygon(xys,  fill=shape.color, outline=shape.color, width=shape.line_width*2)
    
    def draw_rectange_on_origin_image(self, shape: Shape):
        box = [(shape.box[0] * self.scale[0],
               shape.box[1] * self.scale[1]),
               (shape.box[2] * self.scale[0], 
               shape.box[3] * self.scale[1])]
        
        draw = ImageDraw.Draw(self.origin_img_with_rectange)
        draw.rectangle(box, outline=shape.color, width=shape.line_width*2)
    
    def get_image_with_rectange(self):
        return self.origin_img_with_rectange
    
    def save_origin_image_with_rectange(self, path):
        self.origin_img_with_rectange.save(path)
    
    def save_roi(self, shape, path):
        box = [shape.box[0], shape.box[1],
               shape.box[2], shape.box[3]]
        cropped = self.resized_img.crop(box)
        cropped.save(path)

        
    
    
    

cropper = ImageCropper()
# if os.path.isdir(sys.argv[1]):
#     cropper.set_directory(sys.argv[1])
# elif os.path.isfile(sys.argv[1]):
#     cropper.set_file(sys.argv[1])
# else:
#     print(sys.argv[1] + ' is not a file or directory')
#     sys.exit()
# if len(sys.argv) > 2:
#     cropper.set_ratio(float(sys.argv[2]))
cropper.run()