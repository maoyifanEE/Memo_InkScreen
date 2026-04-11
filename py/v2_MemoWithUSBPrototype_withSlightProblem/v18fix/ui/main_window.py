from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.pages.home_page import HomePage
from ui.pages.image_converter_page import ImageConverterPage
from ui.pages.desktop_memo_page import DesktopMemoPage
from core.serial_link import close_all_serial_sessions


class MainWindow(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=0)
        self.master = master
        self.pack(fill=tk.BOTH, expand=True)

        self.current_page: ttk.Frame | None = None
        self.page_factories = {
            "home": lambda parent: HomePage(parent, self),
            "image_converter": lambda parent: ImageConverterPage(parent, self),
            "desktop_memo": lambda parent: DesktopMemoPage(parent, self),
        }

        self.show_page("home")

    def show_page(self, page_name: str) -> None:
        if self.current_page is not None:
            self.current_page.destroy()
            self.current_page = None

        factory = self.page_factories[page_name]
        self.current_page = factory(self)
        self.current_page.pack(fill=tk.BOTH, expand=True)


    def destroy(self):
        try:
            close_all_serial_sessions()
        finally:
            super().destroy()
