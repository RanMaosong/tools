########################
# Author: Lixing Dong
# Note: PIL required
# License: GPL
########################
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except:
    pass
import math
import re

from typing import List
from PIL import Image
from PIL.ExifTags import TAGS
import sys, os
import tkinter as tk
import tkinter.colorchooser
from tkinter import filedialog, font, messagebox
from PIL import ImageTk, ImageDraw
import customtkinter
import cv2
import numpy as np


class ImageCropper:

    def __init__(self):
        self.message = None
        self.rectangle = None
        self.canvas_image = None
        self.canvas_message = None
        self.files = []
        self.box = [0, 0, 0, 0]
        self.ratio = 1.0
        self.min_image_h = 800
        self.min_image_w = 800
        self.font_size = 10
        self.img_h = self.min_image_h
        self.img_w = self.min_image_w
        self.right_frame_width = 500
        self.img = None
        self.shape_list: List[RightFrameItem] = []
        self.arrow_root_xy = [0, 0]
        self.arrow_mini_size = 10
        self.arrow_cur_size = 20
        self.arrow_step = 2
        self.mini_rec_area = 100
        self.cur_file_path = None
        self.cur_file_path_var = None
        self.img_index = 0

        self.files = None
        self.file_in_root_path = None
        self.file_out_root_path = None
        self.shapes = []
        self.cur_shape = None
        self.default_color = '#ff1111'
        self.default_line_width = 3
        self.rectange_mode = True
        self.arrow_mode = False
        self.shape_name = "矩形"
        self.drawed_shapes = []

        self.line_arrow = {}

        self.line_handle = {}

        self.__init_layout()
        self.__bind_event()

        self.right_frame_var = tk.IntVar()

        self.color_path = "./colors.txt"
        with open(self.color_path, encoding="utf-8") as f:
            lines = f.readlines()
        
        self.color_candidates = []
        for line in lines:
            line = line.strip("\n").strip("")
            if line != "":
                self.color_candidates.append(line.strip("\n"))
        


        self.color_index = 0
        
        
    
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


        self.file_btn = tk.Button(self.top_frame, text="文  件", font=self.font_size, command=self.__on_file_button)
        self.file_btn.grid(row=0, column=0, padx=2)
        # self.top_frame.columnconfigure(0, minsize=80)

        self.save_btn = tk.Button(self.top_frame, text="保  存", font=self.font_size, underline=5, command=self.__on_save_button)
        self.save_btn.grid(row=0, column=1, padx=2)
        # self.top_frame.columnconfigure(1, minsize=80)

        tk.Label(self.top_frame, text="  图像大小：", font=self.font_size).grid(row=0, column=2)
        self.vcmd = (self.top_frame.register(self.__only_numeric_input), "%P")
        self.height_str = tk.IntVar(self.top_frame, value=self.img_h)
        self.image_h_entry = tk.Entry(self.top_frame, textvariable=self.height_str, width=5, font=self.font_size, validate="key", validatecommand=self.vcmd)
        self.image_h_entry.grid(row=0, column=3)
        tk.Label(self.top_frame, text="x", font=self.font_size).grid(row=0, column=4)
        self.width_str = tk.IntVar(self.top_frame, value=self.img_w)
        self.image_w_entry = tk.Entry(self.top_frame, textvariable=self.width_str, width=5, font=self.font_size, validate="key", validatecommand=self.vcmd)
        self.image_w_entry.grid(row=0, column=5)

        
        self.v = tk.IntVar(self.top_frame, value=1)
        self.rectange_mode_btn = tk.Radiobutton(self.top_frame, text="矩形框", variable=self.v, value=1, font=self.font_size, command=self.__on_rectange_mode)
        self.rectange_mode_btn.select()
        self.rectange_mode_btn.grid(row=0, column=6)
        tk.Radiobutton(self.top_frame, text="箭头", variable=self.v, value=2, font=self.font_size, command=self.__on_arrow_mode).grid(row=0, column=7)

        self.file_btn = tk.Button(self.top_frame, text="清  除", font=self.font_size, command=self.__on_clear_shapes)
        self.file_btn.grid(row=0, column=8, padx=2)
        # self.top_frame.columnconfigure(0, minsize=80)

        self.file_btn = tk.Button(self.top_frame, text="更  新", font=self.font_size, command=self.on_update_all_shapes)
        self.file_btn.grid(row=0, column=9, padx=2)
        # self.top_frame.columnconfigure(0, minsize=80)

        self.arrow_up_btn = tk.Button(self.top_frame, text="箭头增大", font=self.font_size, command=self.__on_arrow_up)
        self.arrow_up_btn.grid(row=0, column=10, padx=2)

        self.arrow_down_btn = tk.Button(self.top_frame, text="箭头减小", font=self.font_size, command=self.__on_arrow_down)
        self.arrow_down_btn.grid(row=0, column=11, padx=2)
        self.arrow_size_var = tk.IntVar(value=self.arrow_cur_size)
        self.arrow_size_label = tk.Label(self.top_frame, textvariable=self.arrow_size_var, font=self.font_size)
        self.arrow_size_label.grid(row=0, column=12)
        
        self.pre_shown_image = tk.Button(self.top_frame, text="显示上一张图", font=self.font_size, command=self.__on_previous_img)
        self.pre_shown_image.grid(row=0, column=13, padx=2)

        self.next_shown_image = tk.Button(self.top_frame, text="显示下一张图", font=self.font_size, command=self.__on_next_img)
        self.next_shown_image.grid(row=0, column=14, padx=2)

        self.common_colors = tk.Button(self.top_frame, text="常用颜色", font=self.font_size, command=self.__on_common_colors)
        self.common_colors.grid(row=0, column=15, padx=2)

        

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
    
    def __on_common_colors(self):
        def cut(editor, event=None):
            editor.event_generate("<<Cut>>")
        def copy(editor, event=None):
            editor.event_generate("<<Copy>>")
        def paste(editor, event=None):
            editor.event_generate('<<Paste>>')
        def rightKey(event, editor):
            menubar.delete(0,tk.END)
            menubar.add_command(label='剪切',command=lambda:cut(editor))
            menubar.add_command(label='复制',command=lambda:copy(editor))
            menubar.add_command(label='粘贴',command=lambda:paste(editor))
            menubar.post(event.x_root,event.y_root)

        color_panel = tk.Toplevel(master=self.top_frame)
        color_panel.attributes("-toolwindow", 2)
        color_panel.title("Colors")
        menubar = tk.Menu(color_panel,tearoff=False)#创建一个菜单

        
        index = 0
        while index < len(self.color_candidates):
            row_index = index // 2
            column_index = index % 2 * 2

            color = self.color_candidates[index]

            color_label = tk.Label(color_panel, text="    ", bg=color, font=self.font_size, bd=2)

            color_var = tk.StringVar(color_panel, value=color)
            color_entry = tk.Entry(color_panel, textvariable=color_var, width=8, font=self.font_size, state="readonly")

            color_label.grid(row=row_index, column=column_index+0, padx=10, pady=5)
            color_entry.grid(row=row_index, column=column_index+1, padx=10, pady=5)

            color_entry.bind("<Button-3>", lambda x: rightKey(x, color_entry))

            index += 1
        


            
            

    
    def __on_next_img(self, event=None):
        self.img_index += 1
        self.img_index %= len(self.files)
        self.__show_img(self.files)

    def __on_previous_img(self, event=None):
        self.img_index -= 1
        self.img_index %= len(self.files)
        self.__show_img(self.files)
    

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

        self.top_frame.bind_all("<KeyPress-Left>", self.__on_previous_img)
        self.top_frame.bind_all("<KeyPress-Right>", self.__on_next_img)


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
        basename = os.path.basename(filename)
        if self.cur_file_path_var is not None:
            self.cur_file_path_var.set(basename)
            self.right_frame.update()
        else:
            self.cur_file_path_var = tk.StringVar(value=basename)
            self.cur_file_path_label = tk.Label(self.right_frame, textvariable=self.cur_file_path_var)
            self.cur_file_path_label.pack(side="top", fill="both")
        self.img = self.__get_image(filename)

    def __on_clear_shapes(self):
        print("__on_clear_all_shapes")
        index = 0
        while index < len(self.drawed_shapes):
            if self.shape_list[index].is_selected():
                deleted_item = self.drawed_shapes.pop(index)
                # self.canvas.delete(deleted_item)
                self.__canvas_delete_shape(deleted_item)
                 
                deleted_shape = self.shapes.pop(index)
                self.shape_list.pop(index)
                
            else:
                index += 1
        if len(self.drawed_shapes) != len(self.right_frame.winfo_children()):
            self.__recreate_right_frame_item_by_shapes()

        self.cur_shape = None
        # self.__refresh_rectangle()


        
    
    def on_update_all_shapes(self):
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

        if not (isinstance(files, tuple) and len(files) > 0):
            return
        
        print("Selected files: ", files)
        self.__show_img(files)
    
    def __show_img(self, files):
        if isinstance(files, tuple) and len(files) > 0:
            self.files = files
            self.file_in_root_path = os.path.dirname(files[self.img_index])
        

        self.file_out_root_path = self.file_in_root_path
        
        self.shapes = []
        self.set_image(self.files[self.img_index])
        self.__update_img_size()

        self.__recreate_right_frame_item_by_shapes()
        self.__refresh_rectangle()
        
        
    
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
        self.img_h = self.img_h if self.img_h > self.min_image_h else self.min_image_h
        self.img_w = self.img_w if self.img_w > self.min_image_w else self.min_image_w
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
        current_color = self.__get_current_color()
        self.cur_shape = Shape(self.shape_name, box, self.default_line_width, current_color, self.arrow_cur_size)

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
        # self.cur_shape.box[2] = event.x
        # self.cur_shape.box[3] = event.y
        self.cur_shape.update_point2((event.x, event.y))
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
        item = RightFrameItem(self, self.right_frame, len(self.shape_list), shape, height=30, width=self.right_frame_width, font_size=self.font_size)
        self.shape_list.append(item)

    
    def __recreate_right_frame_item_by_shapes(self):
        for widget in self.right_frame.winfo_children()[1:]:
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
        
        # save user's colors to file
        self.color_candidates = []
        for shape in self.shapes:
            self.color_candidates.append(shape.color)
        
        with open(self.color_path, "w", encoding="utf-8") as f:
            for color in self.color_candidates:
                f.write(f"{color}\n")

    
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
                    image_wrapper.draw_arrow_on_resized_image(shape)
                elif shape.shape_name == "矩形":
                    image_wrapper.draw_rectange_on_origin_image(shape)
                    image_wrapper.draw_rectange_on_resized_image(shape)
        
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

        # path = os.path.join(root_path, "rectange")
        # if not os.path.exists(path):
        #     os.makedirs(path)

        # for filename, image_wrapper in zip(self.files, images):
        #     basename = os.path.basename(filename)
        #     path = os.path.join(root_path, "rectange", basename)
        #     image_wrapper.save_origin_image_with_rectange(path)
        

    def run(self):
        # self.roll_image()
        self.root.mainloop()
        # self.root.update()

    
    def __get_current_color(self):
        if len(self.color_candidates) == 0:
            return self.default_color
        
        self.color_index = self.color_index % len(self.color_candidates) + 1
        
        return self.color_candidates[self.color_index - 1]

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
    def __init__(self, root_obj, master, index, shape, height, width, font_size) -> None:
        self.root_obj = root_obj
        self.index = index
        self.shape_name =shape.shape_name
        font_size = font.Font(master, size=font_size)
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

        self.x = tk.Label(item_frame, text="x:",  font=font_size)
        self.x_var = tk.IntVar(master, value=shape.box[0])
        self.x_entry = tk.Entry(item_frame, textvariable=self.x_var, width=5, font=font_size)

        self.w = tk.Label(item_frame, text="w:", font=font_size)
        self.w_var = tk.IntVar(master, value=shape.box[2] - shape.box[0])
        self.w_entry = tk.Entry(item_frame, textvariable=self.w_var, width=5, font=font_size)

        self.y = tk.Label(item_frame, text="y:", font=font_size)
        self.y_var = tk.IntVar(master, value=shape.box[1])
        self.y_entry = tk.Entry(item_frame, textvariable=self.y_var, width=5, font=font_size)

        self.h = tk.Label(item_frame, text="h:", font=font_size)
        self.h_var = tk.IntVar(master, value=shape.box[3] - shape.box[1])
        self.h_entry = tk.Entry(item_frame, textvariable=self.h_var, width=5, font=font_size)

        self.selection_btn.grid(row=0, column=0)
        self.coord_label.grid(row=0, column=1)
        self.x.grid(row=0, column=2)
        self.x_entry.grid(row=0, column=3)
        self.w.grid(row=0, column=4, padx=(5, 0))
        self.w_entry.grid(row=0, column=5)
        self.color_label1.grid(row=0, column=6, padx=(30, 10))
        self.color_entry.grid(row=0, column=7)

        self.y.grid(row=1, column=2)
        self.y_entry.grid(row=1, column=3)
        self.h.grid(row=1, column=4, padx=(5, 0))
        self.h_entry.grid(row=1, column=5)
        self.line_width_label.grid(row=1, column=6, padx=(30, 10))
        self.line_width_entry.grid(row=1, column=7)
        self.arrow_size = shape.arrow_size

        self.add_event()
        self.old_color = shape.color
    def add_event(self):
        self.color_label1.bind("<Button>", self.__color_label_click)
        self.color_entry.bind("<Return>", self.__on_color_change)

    
    def __on_color_change(self, event):
        def validate_color_code(color_code):
            """
            使用正则表达式验证颜色编码格式（#RRGGBB 或 #RGB）。

            Args:
                color_code: 要验证的颜色编码字符串。

            Returns:
                True 如果颜色编码格式正确，False 否则。
            """
            pattern = re.compile(r'^#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})$')
            return bool(pattern.match(color_code))

        if validate_color_code(self.color_var.get()):
            self.old_color = self.color_var.get()
            self.root_obj.on_update_all_shapes()
        else:
            tk.messagebox.showinfo('错误',f'第{self.index}个颜色{self.color_var.get()}错误！')
            self.color_var.set(self.old_color)


        print(self.color_var.get())
        

    
    def __color_label_click(self, event):
        selected_colors = tk.colorchooser.askcolor(title='颜色选择器')
        hex_color = selected_colors[1]
        self.color_label1["bg"] = hex_color
        self.color_var.set(hex_color)
        self.root_obj.on_update_all_shapes()



    def is_selected(self):
        return self.bool_var.get()
    
    def to_shape(self):
        box = (self.x_var.get(), self.y_var.get(), self.x_var.get() + self.w_var.get(), self.y_var.get()+self.h_var.get())
        shape = Shape(self.shape_name, box, self.line_width_var.get(), self.color_var.get(), self.arrow_size)
        print(shape)

        return shape

class Shape:
    def __init__(self, shape_name, box, line_width, color, arrow_size):
        self.shape_name = shape_name
        self.line_width = line_width
        self.color = color
        self.arrow_size = arrow_size

        if shape_name == "矩形":
            x1 = min(box[0], box[2])
            y1 = min(box[1], box[3])

            x2 = max(box[0], box[2])
            y2 = max(box[1], box[3])
            self.point1 = box[:2]

            self.box = [x1, y1, x2, y2]
        else:
            self.box = box
    
    def update_point2(self, point):
        if self.shape_name == "矩形":
            x1 = min(self.point1[0], point[0])
            y1 = min(self.point1[1], point[1])

            x2 = max(self.point1[0], point[0])
            y2 = max(self.point1[1], point[1])

            self.box = [x1, y1, x2, y2]
        else:
            self.box[2] = point[0]
            self.box[3] = point[1]
        
    
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
    
    def draw_arrow_on_resized_image(self, shape: Shape):
        arrow_coord = arrow_coordinates(shape)
        box = [(shape.box[0], shape.box[1]),
               (shape.box[2], shape.box[3])]
        
        xys = []
        for i in range(0, len(arrow_coord), 2):
            xys.append((arrow_coord[i], arrow_coord[i+1]))
        
        draw = ImageDraw.Draw(self.resized_img)
        # draw.line(box, fill=shape.color, width=shape.line_width*2)
        draw.line(box, fill=shape.color, width=shape.line_width)
        draw.polygon(xys,  fill=shape.color, outline=shape.color, width=shape.line_width)

        # img = np.array(self.resized_img)
        # arrow_img = cv2.arrowedLine(img, shape.box[:2], shape.box[2:], color=hex_to_rgb(shape.color), thickness=shape.line_width, tipLength=0.2, line_type=cv2.LINE_AA)
        # self.resized_img = Image.fromarray(arrow_img)

    def draw_rectange_on_resized_image(self, shape: Shape):
        box = [(shape.box[0], shape.box[1]),
               (shape.box[2], shape.box[3])]
        
        draw = ImageDraw.Draw(self.resized_img)
        draw.rectangle(box, outline=shape.color, width=shape.line_width)
    
    def draw_rectange_on_origin_image(self, shape: Shape):
        box = [(shape.box[0] * self.scale[0],
               shape.box[1] * self.scale[1]),
               (shape.box[2] * self.scale[0], 
               shape.box[3] * self.scale[1])]
        
        draw = ImageDraw.Draw(self.origin_img_with_rectange)
        draw.rectangle(box, outline=shape.color, width=shape.line_width)
    
    def get_image_with_rectange(self):
        return self.origin_img_with_rectange
    
    def save_origin_image_with_rectange(self, path):
        self.origin_img_with_rectange.save(path)
    
    def save_roi(self, shape, path):
        box = [shape.box[0], shape.box[1],
               shape.box[2], shape.box[3]]
        cropped = self.resized_img.crop(box)
        cropped.save(path)

def hex_to_rgb(hex_code):
    r = int(hex_code[1:3], 16)
    g = int(hex_code[3:5], 16)
    b = int(hex_code[5:7], 16)
    return (r, g, b)    
    
    
    

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