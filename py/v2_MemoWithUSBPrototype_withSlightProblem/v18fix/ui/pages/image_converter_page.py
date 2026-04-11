from __future__ import annotations

import os
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageTk

from core import ConversionError, PRESETS, build_conversion_result, build_preview_image, load_image
from core.app_config import AppConfig, load_config, save_config
from core.arduino_cli import (
    BoardPortInfo,
    FlashWorkflowResult,
    check_cli_available,
    compile_and_upload,
    guess_arduino_cli_path,
    list_board_ports,
)
from core.export_c import build_gray4_debug_text, default_output_basename
from core.models import ConversionOptions, PartialRegion
from core.sketch_builder import create_temp_sketch
from core.serial_link import send_result_to_device


class ImageConverterPage(ttk.Frame):
    def __init__(self, master: ttk.Frame, app_shell) -> None:
        super().__init__(master, padding=12)
        self.app_shell = app_shell
        self.app_root = Path(__file__).resolve().parents[2]
        self.config = load_config(self.app_root)

        guessed_cli = guess_arduino_cli_path()
        if guessed_cli and not self.config.arduino_cli_path:
            self.config.arduino_cli_path = guessed_cli

        self.source_path: str = ""
        self.source_image: Image.Image | None = None
        self.last_result = None
        self._input_preview_ref = None
        self._output_preview_ref = None
        self.detected_ports: list[BoardPortInfo] = []
        self.is_flashing = False

        self._build_variables()
        self._build_layout()
        self._refresh_preset_info()
        self._refresh_port_choices([])
        self._update_control_states()

    def _build_variables(self) -> None:
        preset_key = next(iter(PRESETS.keys()))
        self.preset_var = tk.StringVar(value=preset_key)
        self.color_mode_var = tk.StringVar(value="bw")
        self.update_mode_var = tk.StringVar(value="full")
        self.fit_mode_var = tk.StringVar(value="contain")
        self.rotation_var = tk.StringVar(value="0")
        self.dither_var = tk.StringVar(value="none")
        self.invert_var = tk.BooleanVar(value=False)
        self.horizontal_flip_var = tk.BooleanVar(value=True)
        self.threshold_var = tk.IntVar(value=128)
        self.variable_name_var = tk.StringVar(value="gImage_custom")
        self.partial_x_var = tk.IntVar(value=0)
        self.partial_y_var = tk.IntVar(value=0)
        self.partial_w_var = tk.IntVar(value=128)
        self.partial_h_var = tk.IntVar(value=64)
        self.status_var = tk.StringVar(value="请先加载图片。")
        self.preset_info_var = tk.StringVar(value="")

        self.arduino_cli_path_var = tk.StringVar(value=self.config.arduino_cli_path)
        self.esp32_project_dir_var = tk.StringVar(value=self.config.esp32_project_dir)
        self.fqbn_var = tk.StringVar(value=self.config.fqbn or "esp32:esp32:esp32")
        self.serial_port_var = tk.StringVar(value=self.config.serial_port)
        self.temp_build_root_var = tk.StringVar(value=self.config.temp_build_root)
        self.serial_baud_var = tk.StringVar(value=self.config.serial_baud or "115200")

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(2, weight=1)
        self.rowconfigure(3, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        ttk.Button(header, text="← 返回主界面", command=lambda: self.app_shell.show_page("home")).grid(
            row=0, column=0, padx=(0, 12)
        )
        ttk.Label(header, text="#1 单张图片转换", font=("Microsoft YaHei UI", 18, "bold")).grid(row=0, column=1, sticky="w")

        tip = ttk.Label(
            self,
            text=(
                "说明：现在默认流程改成两步。\n"
                "第 1 步：先把“接收固件”编译并部署到 ESP32（通常只需要做一次）。\n"
                "第 2 步：以后图片变化时，直接点“发送当前图片到屏幕”，通过串口更新显示内容，不再重复烧录。"
            ),
            justify="left",
        )
        tip.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        preview_frame = ttk.LabelFrame(self, text="预览")
        preview_frame.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.columnconfigure(1, weight=1)
        preview_frame.rowconfigure(0, weight=1)

        input_frame = ttk.LabelFrame(preview_frame, text="输入图片预览")
        input_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        input_frame.rowconfigure(0, weight=1)
        input_frame.columnconfigure(0, weight=1)
        self.input_preview_label = ttk.Label(input_frame, text="未加载图片", anchor="center")
        self.input_preview_label.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        output_frame = ttk.LabelFrame(preview_frame, text="输出图片预览（墨水屏效果）")
        output_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        output_frame.rowconfigure(0, weight=1)
        output_frame.columnconfigure(0, weight=1)
        self.output_preview_label = ttk.Label(output_frame, text="尚未转换", anchor="center")
        self.output_preview_label.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        options = ttk.LabelFrame(self, text="转换设置")
        options.grid(row=2, column=1, sticky="nsew", pady=(0, 8))
        for i in range(4):
            options.columnconfigure(i, weight=1)

        row = 0
        ttk.Button(options, text="加载图片", command=self.load_image_dialog).grid(row=row, column=0, sticky="ew", padx=6, pady=4)
        self.path_entry = ttk.Entry(options)
        self.path_entry.grid(row=row, column=1, columnspan=3, sticky="ew", padx=6, pady=4)

        row += 1
        ttk.Label(options, text="墨水屏预设").grid(row=row, column=0, sticky="w", padx=6, pady=4)
        preset_combo = ttk.Combobox(options, textvariable=self.preset_var, state="readonly", values=list(PRESETS.keys()))
        preset_combo.grid(row=row, column=1, columnspan=3, sticky="ew", padx=6, pady=4)
        preset_combo.bind("<<ComboboxSelected>>", lambda _: self._refresh_preset_info())

        row += 1
        ttk.Label(options, textvariable=self.preset_info_var, wraplength=360, justify="left").grid(
            row=row, column=0, columnspan=4, sticky="ew", padx=6, pady=(0, 8)
        )

        row += 1
        ttk.Label(options, text="颜色模式").grid(row=row, column=0, sticky="w", padx=6, pady=4)
        color_combo = ttk.Combobox(options, textvariable=self.color_mode_var, state="readonly", values=["bw", "gray4"])
        color_combo.grid(row=row, column=1, sticky="ew", padx=6, pady=4)
        color_combo.bind("<<ComboboxSelected>>", lambda _: self._update_control_states())

        ttk.Label(options, text="刷新方式").grid(row=row, column=2, sticky="w", padx=6, pady=4)
        update_combo = ttk.Combobox(options, textvariable=self.update_mode_var, state="readonly", values=["full", "partial"])
        update_combo.grid(row=row, column=3, sticky="ew", padx=6, pady=4)
        update_combo.bind("<<ComboboxSelected>>", lambda _: self._update_control_states())

        row += 1
        ttk.Label(options, text="适配方式").grid(row=row, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(options, textvariable=self.fit_mode_var, state="readonly", values=["contain", "cover", "stretch"]).grid(
            row=row, column=1, sticky="ew", padx=6, pady=4
        )
        ttk.Label(options, text="旋转").grid(row=row, column=2, sticky="w", padx=6, pady=4)
        ttk.Combobox(options, textvariable=self.rotation_var, state="readonly", values=["0", "90", "180", "270"]).grid(
            row=row, column=3, sticky="ew", padx=6, pady=4
        )

        row += 1
        ttk.Label(options, text="阈值（黑白）").grid(row=row, column=0, sticky="w", padx=6, pady=4)
        self.threshold_spin = ttk.Spinbox(options, from_=0, to=255, textvariable=self.threshold_var, increment=1)
        self.threshold_spin.grid(row=row, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(options, text="抖动").grid(row=row, column=2, sticky="w", padx=6, pady=4)
        self.dither_combo = ttk.Combobox(options, textvariable=self.dither_var, state="readonly", values=["none", "floyd"])
        self.dither_combo.grid(row=row, column=3, sticky="ew", padx=6, pady=4)

        row += 1
        ttk.Label(options, text="变量名").grid(row=row, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(options, textvariable=self.variable_name_var).grid(row=row, column=1, sticky="ew", padx=6, pady=4)
        ttk.Checkbutton(options, text="反相", variable=self.invert_var).grid(row=row, column=2, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(options, text="左右翻转", variable=self.horizontal_flip_var).grid(row=row, column=3, sticky="w", padx=6, pady=4)

        row += 1
        self.partial_frame = ttk.LabelFrame(options, text="局部刷新区域（BW 可导出 / 可烧录）")
        self.partial_frame.grid(row=row, column=0, columnspan=4, sticky="ew", padx=6, pady=(8, 6))
        for i in range(4):
            self.partial_frame.columnconfigure(i, weight=1)
        ttk.Label(self.partial_frame, text="x").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Spinbox(self.partial_frame, from_=0, to=10000, textvariable=self.partial_x_var, increment=1).grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(self.partial_frame, text="y").grid(row=0, column=2, sticky="w", padx=6, pady=4)
        ttk.Spinbox(self.partial_frame, from_=0, to=10000, textvariable=self.partial_y_var, increment=1).grid(row=0, column=3, sticky="ew", padx=6, pady=4)
        ttk.Label(self.partial_frame, text="width").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Spinbox(self.partial_frame, from_=1, to=10000, textvariable=self.partial_w_var, increment=1).grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(self.partial_frame, text="height").grid(row=1, column=2, sticky="w", padx=6, pady=4)
        ttk.Spinbox(self.partial_frame, from_=1, to=10000, textvariable=self.partial_h_var, increment=1).grid(row=1, column=3, sticky="ew", padx=6, pady=4)

        row += 1
        action_bar = ttk.Frame(options)
        action_bar.grid(row=row, column=0, columnspan=4, sticky="ew", padx=6, pady=(8, 2))
        for i in range(4):
            action_bar.columnconfigure(i, weight=1)
        ttk.Button(action_bar, text="确认转换", command=self.convert_current_image).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(action_bar, text="复制数组", command=self.copy_array_text).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(action_bar, text="保存数组", command=self.save_array_text).grid(row=0, column=2, sticky="ew", padx=6)
        ttk.Button(action_bar, text="保存调试", command=self.save_debug_text).grid(row=0, column=3, sticky="ew", padx=(6, 0))

        deploy = ttk.LabelFrame(self, text="Arduino / ESP32 联动")
        deploy.grid(row=3, column=0, sticky="nsew", padx=(0, 8))
        deploy.columnconfigure(1, weight=1)
        deploy.columnconfigure(3, weight=1)

        drow = 0
        ttk.Label(deploy, text="arduino-cli 路径").grid(row=drow, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(deploy, textvariable=self.arduino_cli_path_var).grid(row=drow, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(deploy, text="浏览", command=self.pick_arduino_cli_path).grid(row=drow, column=2, padx=6, pady=4)
        ttk.Button(deploy, text="检查环境", command=self.check_arduino_environment).grid(row=drow, column=3, sticky="ew", padx=6, pady=4)

        drow += 1
        ttk.Label(deploy, text="ESP32 工程目录").grid(row=drow, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(deploy, textvariable=self.esp32_project_dir_var).grid(row=drow, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(deploy, text="浏览", command=self.pick_esp32_project_dir).grid(row=drow, column=2, padx=6, pady=4)
        ttk.Label(deploy, text="示例：.../memo_code/esp32").grid(row=drow, column=3, sticky="w", padx=6, pady=4)

        drow += 1
        ttk.Label(deploy, text="FQBN").grid(row=drow, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(deploy, textvariable=self.fqbn_var).grid(row=drow, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(deploy, text="串口").grid(row=drow, column=2, sticky="w", padx=6, pady=4)
        self.port_combo = ttk.Combobox(deploy, textvariable=self.serial_port_var)
        self.port_combo.grid(row=drow, column=3, sticky="ew", padx=6, pady=4)

        drow += 1
        ttk.Label(deploy, text="临时构建目录").grid(row=drow, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(deploy, textvariable=self.temp_build_root_var).grid(row=drow, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(deploy, text="浏览", command=self.pick_temp_build_root).grid(row=drow, column=2, padx=6, pady=4)
        ttk.Button(deploy, text="扫描串口", command=self.refresh_ports).grid(row=drow, column=3, sticky="ew", padx=6, pady=4)

        drow += 1
        ttk.Label(deploy, text="串口波特率").grid(row=drow, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(deploy, textvariable=self.serial_baud_var).grid(row=drow, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(deploy, text="建议保持 115200").grid(row=drow, column=2, columnspan=2, sticky="w", padx=6, pady=4)

        drow += 1
        deploy_actions = ttk.Frame(deploy)
        deploy_actions.grid(row=drow, column=0, columnspan=4, sticky="ew", padx=6, pady=(8, 6))
        for i in range(4):
            deploy_actions.columnconfigure(i, weight=1)
        self.save_config_button = ttk.Button(deploy_actions, text="保存联动配置", command=self.save_app_config)
        self.save_config_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.generate_sketch_button = ttk.Button(deploy_actions, text="生成接收固件工程", command=self.generate_temp_sketch_only)
        self.generate_sketch_button.grid(row=0, column=1, sticky="ew", padx=6)
        self.flash_button = ttk.Button(deploy_actions, text="编译并烧录接收固件", command=self.flash_to_esp32)
        self.flash_button.grid(row=0, column=2, sticky="ew", padx=6)
        self.push_button = ttk.Button(deploy_actions, text="发送当前图片到屏幕", command=self.push_current_image)
        self.push_button.grid(row=0, column=3, sticky="ew", padx=(6, 0))

        output_box = ttk.LabelFrame(self, text="输出模块")
        output_box.grid(row=3, column=1, sticky="nsew")
        output_box.rowconfigure(0, weight=1)
        output_box.columnconfigure(0, weight=1)

        notebook = ttk.Notebook(output_box)
        notebook.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        self.array_text = ScrolledText(notebook, wrap=tk.NONE, height=16)
        self.debug_text = ScrolledText(notebook, wrap=tk.WORD, height=16)
        self.flash_log_text = ScrolledText(notebook, wrap=tk.WORD, height=16)
        notebook.add(self.array_text, text="数组输出")
        notebook.add(self.debug_text, text="调试信息")
        notebook.add(self.flash_log_text, text="烧录日志")

        status = ttk.Label(self, textvariable=self.status_var, anchor="w")
        status.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _refresh_preset_info(self) -> None:
        preset = PRESETS[self.preset_var.get()]
        self.preset_info_var.set(
            f"{preset.name} | {preset.width}x{preset.height} | 黑白={preset.bw_bytes} 字节 | 4灰={preset.gray4_bytes} 字节\n{preset.notes}"
        )

    def _update_control_states(self) -> None:
        color_mode = self.color_mode_var.get()
        update_mode = self.update_mode_var.get()

        bw_mode = color_mode == "bw"
        self.threshold_spin.state(["!disabled"] if bw_mode else ["disabled"])
        self.dither_combo.state(["!disabled"] if bw_mode else ["disabled"])

        partial_state = update_mode == "partial"
        for child in self.partial_frame.winfo_children():
            try:
                child.state(["!disabled"] if partial_state else ["disabled"])
            except tk.TclError:
                pass

        if color_mode == "gray4" and update_mode == "partial":
            self.status_var.set("提示：当前示例驱动没有 4 灰局刷接口，转换和烧录时都会阻止该组合。")

    def load_image_dialog(self) -> None:
        filetypes = [("Image files", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("All files", "*.*")]
        path = filedialog.askopenfilename(title="选择图片", filetypes=filetypes)
        if not path:
            return
        self._load_image_path(path)

    def _load_image_path(self, path: str) -> None:
        self.source_path = path
        self.path_entry.delete(0, tk.END)
        self.path_entry.insert(0, path)
        self.variable_name_var.set(default_output_basename(path, self.variable_name_var.get()))
        try:
            self.source_image = load_image(path)
            self._set_preview(self.input_preview_label, self.source_image, is_input=True)
            self.status_var.set(f"已加载图片：{os.path.basename(path)}，尺寸 {self.source_image.size[0]}x{self.source_image.size[1]}。")
        except Exception as exc:
            self.source_image = None
            messagebox.showerror("加载失败", f"无法加载图片：\n{exc}")
            self.status_var.set("加载图片失败。")

    def convert_current_image(self) -> None:
        if self.source_image is None:
            raw_path = self.path_entry.get().strip()
            if raw_path and Path(raw_path).exists():
                self._load_image_path(raw_path)
            if self.source_image is None:
                messagebox.showwarning("未加载图片", "请先加载一张图片。")
                return

        try:
            options = self._collect_options()
            result = build_conversion_result(self.source_image, options)
            self.last_result = result

            preview = build_preview_image(result)
            self._set_preview(self.output_preview_label, preview, is_input=False)
            self.array_text.delete("1.0", tk.END)
            self.array_text.insert("1.0", result.exported_c_text)
            self._refresh_debug_text(result)

            extra = ""
            if result.partial_region_applied is not None:
                region = result.partial_region_applied
                extra = f" | 局刷区域 {region.x},{region.y},{region.width},{region.height}"

            self.status_var.set(f"转换完成：{result.options.color_mode} | {result.byte_count} 字节{extra}")
        except ConversionError as exc:
            messagebox.showwarning("转换失败", str(exc))
            self.status_var.set(f"转换失败：{exc}")
        except Exception as exc:
            traceback.print_exc()
            messagebox.showerror("程序异常", f"发生未处理异常：\n{exc}")
            self.status_var.set("程序异常，请看终端 traceback。")

    def _collect_options(self) -> ConversionOptions:
        return ConversionOptions(
            preset_key=self.preset_var.get(),
            color_mode=self.color_mode_var.get(),
            update_mode=self.update_mode_var.get(),
            fit_mode=self.fit_mode_var.get(),
            rotation=int(self.rotation_var.get()),
            invert=self.invert_var.get(),
            flip_horizontal=self.horizontal_flip_var.get(),
            threshold=self.threshold_var.get(),
            dither=self.dither_var.get(),
            variable_name=self.variable_name_var.get(),
            partial_region=PartialRegion(
                x=self.partial_x_var.get(),
                y=self.partial_y_var.get(),
                width=self.partial_w_var.get(),
                height=self.partial_h_var.get(),
            ),
        )

    def _set_preview(self, label: ttk.Label, image: Image.Image, is_input: bool) -> None:
        preview = image.copy()
        preview.thumbnail((360, 300), Image.Resampling.NEAREST)
        photo = ImageTk.PhotoImage(preview)
        label.configure(image=photo, text="")
        if is_input:
            self._input_preview_ref = photo
        else:
            self._output_preview_ref = photo

    def _refresh_debug_text(self, result) -> None:
        lines: list[str] = []
        lines.append(f"输入尺寸: {result.input_preview_size[0]}x{result.input_preview_size[1]}")
        lines.append(f"输出尺寸: {result.output_preview_size[0]}x{result.output_preview_size[1]}")
        lines.append(f"颜色模式: {result.options.color_mode}")
        lines.append(f"刷新方式: {result.options.update_mode}")
        lines.append(f"数组长度: {result.byte_count} bytes")
        if result.partial_region_applied is not None:
            region = result.partial_region_applied
            lines.append(f"局刷区域: x={region.x}, y={region.y}, w={region.width}, h={region.height}")
            lines.append(f"调用示例: EPD_Dis_Part({region.x}, {region.y}, {result.options.variable_name}, {region.height}, {region.width});")
        if result.debug.messages:
            lines.append("")
            lines.append("消息:")
            lines.extend(f"- {msg}" for msg in result.debug.messages)
        if result.options.color_mode == "gray4" and result.debug.plane24 and result.debug.plane26:
            lines.append("")
            lines.append("说明：")
            lines.append("- 主数组是 2bit 打包格式，可直接给示例里的 EPD_WhiteScreen_ALL_4G()。")
            lines.append("- plane24 / plane26 是为了对照驱动内部拆分逻辑，便于调试。")
            lines.append("")
            lines.append(build_gray4_debug_text(result.options.variable_name, result.debug.plane24, result.debug.plane26))

        self.debug_text.delete("1.0", tk.END)
        self.debug_text.insert("1.0", "\n".join(lines))

    def copy_array_text(self) -> None:
        text = self.array_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo("没有内容", "当前没有可复制的数组内容。")
            return
        self.winfo_toplevel().clipboard_clear()
        self.winfo_toplevel().clipboard_append(text)
        self.status_var.set("数组内容已复制到剪贴板。")

    def save_array_text(self) -> None:
        text = self.array_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo("没有内容", "请先转换图片，再保存数组。")
            return
        default_name = f"{self.variable_name_var.get()}.h"
        path = filedialog.asksaveasfilename(
            title="保存数组头文件",
            defaultextension=".h",
            initialfile=default_name,
            filetypes=[("Header files", "*.h"), ("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        self.status_var.set(f"数组已保存：{path}")

    def save_debug_text(self) -> None:
        text = self.debug_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo("没有内容", "当前没有可保存的调试信息。")
            return
        default_name = f"{self.variable_name_var.get()}_debug.txt"
        path = filedialog.asksaveasfilename(
            title="保存调试信息",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        self.status_var.set(f"调试信息已保存：{path}")

    def pick_arduino_cli_path(self) -> None:
        path = filedialog.askopenfilename(title="选择 arduino-cli", filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
        if path:
            self.arduino_cli_path_var.set(path)

    def pick_esp32_project_dir(self) -> None:
        path = filedialog.askdirectory(title="选择 ESP32 工程目录")
        if path:
            self.esp32_project_dir_var.set(path)

    def pick_temp_build_root(self) -> None:
        path = filedialog.askdirectory(title="选择临时构建目录")
        if path:
            self.temp_build_root_var.set(path)

    def save_app_config(self) -> None:
        self.config = AppConfig(
            arduino_cli_path=self.arduino_cli_path_var.get().strip(),
            esp32_project_dir=self.esp32_project_dir_var.get().strip(),
            fqbn=self.fqbn_var.get().strip() or "esp32:esp32:esp32",
            serial_port=self.serial_port_var.get().strip(),
            temp_build_root=self.temp_build_root_var.get().strip(),
            serial_baud=self.serial_baud_var.get().strip() or "115200",
        )
        config_path = save_config(self.app_root, self.config)
        self.status_var.set(f"联动配置已保存：{config_path}")

    def check_arduino_environment(self) -> None:
        try:
            self.save_app_config()
            result = check_cli_available(self.config.arduino_cli_path)
            self._append_flash_log("=== arduino-cli version ===\n" + (result.combined_output or "(no output)"))
            if result.ok:
                self.status_var.set("arduino-cli 可用。接下来可以扫描串口或直接生成临时工程。")
            else:
                self.status_var.set("arduino-cli 已找到，但运行失败。请检查日志。")
        except Exception as exc:
            self._append_flash_log(f"环境检查失败：\n{exc}")
            messagebox.showerror("环境检查失败", str(exc))
            self.status_var.set("环境检查失败。")

    def refresh_ports(self) -> None:
        try:
            self.save_app_config()
            ports = list_board_ports(self.config.arduino_cli_path)
            self.detected_ports = ports
            self._refresh_port_choices(ports)
            if ports and not self.serial_port_var.get().strip():
                self.serial_port_var.set(ports[0].port)
            if ports:
                joined = "\n".join(f"- {item.display_text}" for item in ports)
                self._append_flash_log("=== 检测到的串口 ===\n" + joined)
                self.status_var.set(f"已检测到 {len(ports)} 个串口。")
            else:
                self._append_flash_log("=== 检测到的串口 ===\n未找到可识别的设备。")
                self.status_var.set("未检测到可识别的设备；也可以手动填写 COM 口。")
        except Exception as exc:
            self._append_flash_log(f"扫描串口失败：\n{exc}")
            messagebox.showerror("扫描串口失败", str(exc))
            self.status_var.set("扫描串口失败。")

    def _refresh_port_choices(self, ports: list[BoardPortInfo]) -> None:
        values = []
        for item in ports:
            values.append(item.port)
            values.append(item.display_text)
        unique_values = []
        seen = set()
        for value in values:
            if value not in seen:
                unique_values.append(value)
                seen.add(value)
        self.port_combo["values"] = unique_values

    def _normalize_port_value(self, raw: str) -> str:
        raw = raw.split("|")[0].strip()
        if not raw:
            return ""

        import re

        match = re.search(r"\bCOM\d+\b", raw, flags=re.IGNORECASE)
        if match:
            return match.group(0).upper()

        match = re.search(r"(/dev/(?:tty|cu)\S+)", raw)
        if match:
            return match.group(1)

        return raw

    def _ensure_result_ready(self) -> None:
        if self.last_result is None:
            self.convert_current_image()
        if self.last_result is None:
            raise RuntimeError("当前还没有可用的转换结果。")

    def generate_temp_sketch_only(self) -> None:
        try:
            self.save_app_config()
            self._ensure_result_ready()
            sketch_dir = create_temp_sketch(
                esp32_project_dir=self.config.esp32_project_dir,
                temp_build_root=self.config.temp_build_root,
                result=self.last_result,
            )
            self._append_flash_log(f"已生成接收固件工程：\n{sketch_dir}")
            self.status_var.set(f"接收固件工程已生成：{sketch_dir}")
        except Exception as exc:
            self._append_flash_log(f"生成临时工程失败：\n{exc}")
            messagebox.showerror("生成失败", str(exc))
            self.status_var.set("生成临时工程失败。")

    def flash_to_esp32(self) -> None:
        if self.is_flashing:
            messagebox.showinfo("正在执行", "当前已经有一个编译/烧录任务在运行，请稍等。")
            return

        try:
            self.save_app_config()
            self._ensure_result_ready()
            port = self._normalize_port_value(self.serial_port_var.get().strip())
            if not port:
                raise ValueError("请先填写串口，例如 COM5。")
            if not self.config.fqbn.strip():
                raise ValueError("请先填写 FQBN，例如 esp32:esp32:esp32。")

            cli_path = self.config.arduino_cli_path
            esp32_project_dir = self.config.esp32_project_dir
            temp_build_root = self.config.temp_build_root
            fqbn = self.config.fqbn
            result = self.last_result

            self._set_flash_busy(True)
            self.flash_log_text.delete("1.0", tk.END)
            self._append_flash_log("开始后台编译并烧录接收固件，请不要重复点击。")
            self.status_var.set("正在后台编译并烧录接收固件，请稍候……")

            def worker() -> None:
                try:
                    sketch_dir = create_temp_sketch(
                        esp32_project_dir=esp32_project_dir,
                        temp_build_root=temp_build_root,
                        result=result,
                    )
                    workflow = compile_and_upload(
                        cli_path=cli_path,
                        sketch_dir=str(sketch_dir),
                        fqbn=fqbn,
                        port=port,
                    )
                    self.after(0, lambda: self._on_flash_finished(workflow, port))
                except Exception as exc:
                    traceback_text = traceback.format_exc()
                    self.after(0, lambda: self._on_flash_error(exc, traceback_text))

            threading.Thread(target=worker, daemon=True).start()
        except Exception as exc:
            self._append_flash_log(f"编译/烧录失败：\n{exc}\n\n{traceback.format_exc()}")
            messagebox.showerror("烧录失败", str(exc))
            self.status_var.set("烧录失败。")

    def _set_flash_busy(self, busy: bool) -> None:
        self.is_flashing = busy
        state = ["disabled"] if busy else ["!disabled"]
        for widget_name in ("flash_button", "generate_sketch_button", "save_config_button", "push_button"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.state(state)

    def _on_flash_finished(self, workflow: FlashWorkflowResult, port: str) -> None:
        self._set_flash_busy(False)
        self._show_flash_result(workflow)
        if workflow.ok:
            self.status_var.set(f"接收固件部署完成：{port}。以后可直接用串口发送图片刷新屏幕。")
        else:
            self.status_var.set("烧录失败，请查看烧录日志。")

    def _on_flash_error(self, exc: Exception, traceback_text: str) -> None:
        self._set_flash_busy(False)
        self._append_flash_log(f"编译/烧录失败：\n{exc}\n\n{traceback_text}")
        messagebox.showerror("烧录失败", str(exc))
        self.status_var.set("烧录失败。")

    def _show_flash_result(self, workflow: FlashWorkflowResult) -> None:
        self.flash_log_text.delete("1.0", tk.END)
        self.flash_log_text.insert("1.0", workflow.combined_output)
        if workflow.ok:
            messagebox.showinfo("部署完成", f"接收固件编译和上传成功。\n\n临时工程目录：\n{workflow.sketch_dir}\n\n后续可直接点“发送当前图片到屏幕”。")
        else:
            messagebox.showwarning("烧录失败", "编译或烧录失败，请查看“烧录日志”页签。")

    def push_current_image(self) -> None:
        try:
            self.save_app_config()
            self._ensure_result_ready()
            port = self._normalize_port_value(self.serial_port_var.get().strip())
            if not port:
                raise ValueError("请先填写串口，例如 COM4。")
            try:
                baud = int((self.serial_baud_var.get() or "115200").strip())
            except Exception:
                raise ValueError("串口波特率必须是整数。")

            send_result = send_result_to_device(port=port, result=self.last_result, baudrate=baud)
            self.flash_log_text.delete("1.0", tk.END)
            self.flash_log_text.insert("1.0", send_result.log_text)
            if send_result.ok:
                self.status_var.set(f"已通过串口更新屏幕：{port}")
                messagebox.showinfo("发送完成", "图片数据已发送到 ESP32，屏幕应已刷新。")
            else:
                self.status_var.set("串口发送失败，请查看烧录日志。")
                messagebox.showwarning("发送失败", "串口发送失败，请查看“烧录日志”页签。")
        except Exception as exc:
            self._append_flash_log(f"串口发送失败：\n{exc}\n\n{traceback.format_exc()}")
            messagebox.showerror("发送失败", str(exc))
            self.status_var.set("串口发送失败。")

    def _append_flash_log(self, text: str) -> None:
        current = self.flash_log_text.get("1.0", tk.END).strip()
        if current:
            self.flash_log_text.insert(tk.END, "\n\n" + text)
        else:
            self.flash_log_text.insert("1.0", text)
        self.flash_log_text.see(tk.END)
