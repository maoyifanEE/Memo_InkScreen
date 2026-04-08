from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.pages.home_page import HomePage
from ui.pages.image_converter_page import ImageConverterPage
from ui.pages.placeholder_page import PlaceholderPage


class MainWindow(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=0)
        self.master = master
        self.pack(fill=tk.BOTH, expand=True)

        self.current_page: ttk.Frame | None = None
        self.page_factories = {
            "home": lambda parent: HomePage(parent, self),
            "image_converter": lambda parent: ImageConverterPage(parent, self),
            "desktop_memo": lambda parent: PlaceholderPage(
                parent,
                self,
                title="桌面备忘录",
                description="这里预留给桌面备忘录主功能页，例如提醒内容编辑、版式布局、同步到墨水屏、定时刷新等。",
            ),
        }

        self.show_page("home")

    def show_page(self, page_name: str) -> None:
        if self.current_page is not None:
            self.current_page.destroy()
            self.current_page = None

        factory = self.page_factories[page_name]
        self.current_page = factory(self)
        self.current_page.pack(fill=tk.BOTH, expand=True)
