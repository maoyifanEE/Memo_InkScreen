from __future__ import annotations

import os
import sys
import tkinter as tk

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from ui.main_window import MainWindow


def main() -> None:
    root = tk.Tk()
    root.title("EPD 图片转数组工具")
    root.geometry("1400x900")
    root.minsize(1100, 760)
    MainWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
