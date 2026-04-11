from __future__ import annotations

from tkinter import ttk


class HomePage(ttk.Frame):
    def __init__(self, master: ttk.Frame, app_shell) -> None:
        super().__init__(master, padding=24)
        self.app_shell = app_shell
        self._build_layout()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="桌面备忘录 / EPD 工具箱", font=("Microsoft YaHei UI", 22, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="这里作为总入口。先把单张图片转换和后续桌面备忘录分成两个独立界面。",
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        note = ttk.LabelFrame(self, text="当前说明")
        note.grid(row=1, column=0, sticky="ew", pady=(0, 18))
        ttk.Label(
            note,
            text=(
                "#1 单张图片转换：已实现图片预览、数组转换；本次新增 Arduino / ESP32 一键编译上传入口。\n"
                "#2 桌面备忘录：已改成固定布局原型，顶部 / 右侧 / A 区域可直接编辑，并可一键加载到墨水屏。"
            ),
            justify="left",
        ).grid(row=0, column=0, sticky="w", padx=14, pady=12)

        menu = ttk.Frame(self)
        menu.grid(row=2, column=0, sticky="nsew")
        menu.columnconfigure(0, weight=1)
        menu.columnconfigure(1, weight=1)

        self._build_card(
            menu,
            column=0,
            title="#1 单张图片转换",
            desc="进入当前已实现的图片转数组页面，并支持一键生成临时 Arduino 工程、编译、烧录到 ESP32。",
            command=lambda: self.app_shell.show_page("image_converter"),
        )
        self._build_card(
            menu,
            column=1,
            title="#2 桌面备忘录",
            desc="进入桌面备忘录编辑页。当前支持固定布局：顶部文本区、右侧文本区、A 区域 checkbox 表格、放大编辑区、快捷键调节样式，以及通过串口把当前版面直接加载到墨水屏。",
            command=lambda: self.app_shell.show_page("desktop_memo"),
        )

    def _build_card(self, parent: ttk.Frame, column: int, title: str, desc: str, command) -> None:
        card = ttk.LabelFrame(parent, text=title, padding=16)
        card.grid(row=0, column=column, sticky="nsew", padx=10, pady=10)
        card.columnconfigure(0, weight=1)

        ttk.Label(card, text=title, font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(card, text=desc, wraplength=420, justify="left").grid(row=1, column=0, sticky="ew", pady=(10, 18))
        ttk.Button(card, text="进入", command=command).grid(row=2, column=0, sticky="w")
