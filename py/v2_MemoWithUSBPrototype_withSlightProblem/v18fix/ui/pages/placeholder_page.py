from __future__ import annotations

from tkinter import ttk


class PlaceholderPage(ttk.Frame):
    def __init__(self, master: ttk.Frame, app_shell, title: str, description: str) -> None:
        super().__init__(master, padding=20)
        self.app_shell = app_shell
        self.title = title
        self.description = description
        self._build_layout()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        top.columnconfigure(1, weight=1)
        ttk.Button(top, text="← 返回主界面", command=lambda: self.app_shell.show_page("home")).grid(
            row=0, column=0, padx=(0, 12)
        )
        ttk.Label(top, text=self.title, font=("Microsoft YaHei UI", 18, "bold")).grid(row=0, column=1, sticky="w")

        body = ttk.LabelFrame(self, text="说明")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        ttk.Label(
            body,
            text=self.description + "\n\n这页现在先占位，等你后面继续这个功能时，再按独立页面扩展。",
            justify="left",
            wraplength=900,
        ).grid(row=0, column=0, sticky="nw", padx=16, pady=16)
