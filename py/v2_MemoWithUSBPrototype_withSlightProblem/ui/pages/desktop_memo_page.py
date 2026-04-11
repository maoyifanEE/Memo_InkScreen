from __future__ import annotations

import copy
import math
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

from core.app_config import load_config, save_config
from core.arduino_cli import FlashWorkflowResult, compile_and_upload
from core.sketch_builder import create_temp_sketch
from core.fixed_memo_renderer import (
    DISPLAY_HEIGHT,
    DISPLAY_WIDTH,
    LIST_RECT,
    TOP_RECT,
    BELL_COL_W,
    TIME_COL_W,
    CellStyle,
    ChecklistRow,
    FixedMemoState,
    StyledCell,
    TIME_FONT_SIZE,
    DEFAULT_FONT_SIZE,
    PLACEHOLDER_PLUS,
    default_fixed_memo_state,
    due_rows_for_blink,
    format_due_display,
    parse_due,
    render_fixed_memo_image,
    row_boxes,
)
from core.memo_storage import load_state as load_memo_state, save_state as save_memo_state
from core.models import ConversionOptions, PartialRegion
from core import build_conversion_result
from core.serial_link import DEFAULT_BAUDRATE, send_result_to_device

DEFAULT_PAGE_SIZE = 5
MAX_REAL_ITEMS = 20
MAX_TOTAL_PAGES = 4


class DateTimePickerPopup(tk.Toplevel):
    def __init__(self, master, initial_value: str, on_apply):
        super().__init__(master)
        self.title("选择时间")
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.on_apply = on_apply

        try:
            dt = datetime.strptime(initial_value.strip(), "%Y/%m/%d %H:%M") if initial_value.strip() else datetime.now()
        except Exception:
            dt = datetime.now()

        self.year_var = tk.StringVar(value=f"{dt.year:04d}")
        self.month_var = tk.StringVar(value=f"{dt.month:02d}")
        self.day_var = tk.StringVar(value=f"{dt.day:02d}")
        self.hour_var = tk.StringVar(value=f"{dt.hour:02d}")
        self.minute_var = tk.StringVar(value=f"{dt.minute:02d}")

        outer = ttk.Frame(self, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        date_row = ttk.Frame(outer)
        date_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(date_row, text="日期").pack(side=tk.LEFT)
        ttk.Spinbox(date_row, from_=2024, to=2099, wrap=True, width=6, textvariable=self.year_var, command=self._sync_calendar_from_vars).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Label(date_row, text="/").pack(side=tk.LEFT)
        ttk.Spinbox(date_row, from_=1, to=12, wrap=True, width=4, textvariable=self.month_var, format="%02.0f", command=self._sync_calendar_from_vars).pack(side=tk.LEFT, padx=4)
        ttk.Label(date_row, text="/").pack(side=tk.LEFT)
        ttk.Spinbox(date_row, from_=1, to=31, wrap=True, width=4, textvariable=self.day_var, format="%02.0f").pack(side=tk.LEFT, padx=4)

        head = ttk.Frame(outer)
        head.pack(fill=tk.X)
        ttk.Button(head, text="◀", width=3, command=self.prev_month).pack(side=tk.LEFT)
        self.month_label = ttk.Label(head, anchor="center", font=("Microsoft YaHei UI", 10, "bold"))
        self.month_label.pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(head, text="▶", width=3, command=self.next_month).pack(side=tk.RIGHT)

        self.grid_frame = ttk.Frame(outer)
        self.grid_frame.pack(fill=tk.X, pady=(8, 8))

        time_row = ttk.Frame(outer)
        time_row.pack(fill=tk.X)
        ttk.Label(time_row, text="时间").pack(side=tk.LEFT)
        ttk.Spinbox(time_row, from_=0, to=23, wrap=True, width=4, textvariable=self.hour_var, format="%02.0f").pack(side=tk.LEFT, padx=(8, 4))
        ttk.Label(time_row, text=":").pack(side=tk.LEFT)
        ttk.Spinbox(time_row, from_=0, to=59, wrap=True, width=4, textvariable=self.minute_var, format="%02.0f").pack(side=tk.LEFT, padx=(4, 0))

        foot = ttk.Frame(outer)
        foot.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(foot, text="现在", command=self.use_now).pack(side=tk.LEFT)
        ttk.Button(foot, text="取消", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(foot, text="确定", command=self.apply).pack(side=tk.RIGHT, padx=(0, 8))

        self._sync_calendar_from_vars()
        self._build_calendar()
        self.grab_set()
        self.focus_force()

    def _int_var(self, var: tk.StringVar, default: int) -> int:
        try:
            return int(var.get())
        except Exception:
            return default

    def _sync_calendar_from_vars(self) -> None:
        year = max(2024, min(2099, self._int_var(self.year_var, datetime.now().year)))
        month = max(1, min(12, self._int_var(self.month_var, datetime.now().month)))
        day = max(1, min(31, self._int_var(self.day_var, datetime.now().day)))
        self.year_var.set(f"{year:04d}")
        self.month_var.set(f"{month:02d}")
        self.day_var.set(f"{day:02d}")
        self.view_year = year
        self.view_month = month

    def _build_calendar(self) -> None:
        import calendar

        for child in list(self.grid_frame.winfo_children()):
            child.destroy()
        self.month_label.configure(text=f"{self.view_year}/{self.view_month:02d}")
        week_names = ["一", "二", "三", "四", "五", "六", "日"]
        for i, name in enumerate(week_names):
            ttk.Label(self.grid_frame, text=name, anchor="center", width=4).grid(row=0, column=i, padx=1, pady=1)
        cal = calendar.Calendar(firstweekday=0)
        selected_day = self._int_var(self.day_var, 1)
        for r, week in enumerate(cal.monthdayscalendar(self.view_year, self.view_month), start=1):
            for c, day in enumerate(week):
                if day == 0:
                    ttk.Label(self.grid_frame, text="", width=4).grid(row=r, column=c, padx=1, pady=1)
                    continue
                btn = ttk.Button(self.grid_frame, text=f"{day:02d}", width=4, command=lambda d=day: self.select_day(d))
                btn.grid(row=r, column=c, padx=1, pady=1)
                if day == selected_day:
                    try:
                        btn.state(["pressed"])
                    except Exception:
                        pass

    def select_day(self, day: int) -> None:
        self.day_var.set(f"{day:02d}")
        self._build_calendar()

    def prev_month(self) -> None:
        self._sync_calendar_from_vars()
        if self.view_month == 1:
            self.view_year -= 1
            self.view_month = 12
        else:
            self.view_month -= 1
        self.year_var.set(f"{self.view_year:04d}")
        self.month_var.set(f"{self.view_month:02d}")
        self._build_calendar()

    def next_month(self) -> None:
        self._sync_calendar_from_vars()
        if self.view_month == 12:
            self.view_year += 1
            self.view_month = 1
        else:
            self.view_month += 1
        self.year_var.set(f"{self.view_year:04d}")
        self.month_var.set(f"{self.view_month:02d}")
        self._build_calendar()

    def use_now(self) -> None:
        now = datetime.now()
        self.year_var.set(f"{now.year:04d}")
        self.month_var.set(f"{now.month:02d}")
        self.day_var.set(f"{now.day:02d}")
        self.hour_var.set(f"{now.hour:02d}")
        self.minute_var.set(f"{now.minute:02d}")
        self._sync_calendar_from_vars()
        self._build_calendar()

    def apply(self) -> None:
        try:
            year = max(2024, min(2099, int(self.year_var.get())))
            month = max(1, min(12, int(self.month_var.get())))
            day = max(1, min(31, int(self.day_var.get())))
            hour = max(0, min(23, int(self.hour_var.get())))
            minute = max(0, min(59, int(self.minute_var.get())))
            dt = datetime(year, month, day, hour, minute)
        except Exception:
            dt = datetime.now()
        self.on_apply(dt.strftime("%Y/%m/%d %H:%M"))
        self.destroy()


class FixedEditorSurface(ttk.Frame):
    def __init__(self, master: ttk.Frame, page: "DesktopMemoPage", scale: float = 1.9) -> None:
        super().__init__(master)
        self.page = page
        self.scale = scale
        self.row_widgets: list[dict[str, object]] = []
        self.canvas_w = round(DISPLAY_WIDTH * scale)
        self.canvas_h = round(DISPLAY_HEIGHT * scale)
        self.container = tk.Frame(self, bg="#ffffff", width=self.canvas_w, height=self.canvas_h, highlightthickness=1, highlightbackground="#9a9a9a")
        self.container.pack(fill=tk.BOTH, expand=True)
        self.container.pack_propagate(False)

        self.top_frame = tk.Frame(self.container, bg="#ffffff")
        self.list_frame = tk.Frame(self.container, bg="#ffffff")
        self.top_frame.place(x=round(TOP_RECT[0] * scale), y=round(TOP_RECT[1] * scale), width=round((TOP_RECT[2] - TOP_RECT[0]) * scale), height=round((TOP_RECT[3] - TOP_RECT[1]) * scale))
        self.list_frame.place(x=round(LIST_RECT[0] * scale), y=round(LIST_RECT[1] * scale), width=round((LIST_RECT[2] - LIST_RECT[0]) * scale), height=round((LIST_RECT[3] - LIST_RECT[1]) * scale))

        self.top_canvas = tk.Canvas(self.top_frame, bg="#ffffff", highlightthickness=0)
        self.top_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.time_label = tk.Label(self.top_frame, text="00:00", bg="#ffffff", anchor="center")
        self.date_label = tk.Label(self.top_frame, text="04/09", bg="#ffffff", anchor="e")
        self.weekday_label = tk.Label(self.top_frame, text="星期一", bg="#ffffff", anchor="e")
        self.page_label = tk.Label(self.container, text="", bg="#ffffff", anchor="e")
        self.time_label.place(x=round(62 * scale), y=round(0 * scale), width=round(180 * scale), height=round(42 * scale))
        self.date_label.place(x=round(274 * scale), y=round(36 * scale), width=round(58 * scale), height=round(14 * scale))
        self.weekday_label.place(x=round(332 * scale), y=round(36 * scale), width=round(62 * scale), height=round(14 * scale))
        self.page_label.place(x=round((DISPLAY_WIDTH - 44) * scale), y=round((DISPLAY_HEIGHT - 18) * scale), width=round(38 * scale), height=round(14 * scale))

        self.rebuild_rows()
        self.refresh_static_header()
        self.refresh_time()

    def refresh_static_header(self) -> None:
        self.top_canvas.delete("all")
        s = self.scale
        w = (TOP_RECT[2] - TOP_RECT[0]) * s
        h = (TOP_RECT[3] - TOP_RECT[1]) * s
        self.top_canvas.create_line(0, h - 1, w, h - 1, fill="#444444")
        self._draw_wifi(round(302 * s), round(8 * s), ok=self.page.state.wifi_ok)
        self._draw_bt(round(325 * s), round(7 * s), ok=self.page.state.bluetooth_ok)
        self._draw_battery(round(370 * s), round(8 * s), ok=self.page.state.battery_ok)

    def _draw_wifi(self, x: int, y: int, ok: bool = True):
        c = self.top_canvas
        c.create_arc(x, y + 3, x + 18, y + 18, start=35, extent=110, style="arc", width=2)
        c.create_arc(x + 3, y + 6, x + 15, y + 15, start=40, extent=100, style="arc", width=2)
        c.create_arc(x + 6, y + 9, x + 12, y + 12, start=50, extent=80, style="arc", width=2)
        c.create_oval(x + 8, y + 13, x + 10, y + 15, fill="black", outline="black")
        if not ok:
            c.create_line(x + 1, y + 15, x + 17, y + 1, width=2)

    def _draw_bt(self, x: int, y: int, ok: bool = True):
        c = self.top_canvas
        c.create_line(x + 7, y, x + 7, y + 16, width=2)
        c.create_line(x + 7, y, x + 14, y + 5, width=2)
        c.create_line(x + 7, y + 7, x + 14, y + 2, width=2)
        c.create_line(x + 7, y + 7, x + 14, y + 14, width=2)
        c.create_line(x + 7, y + 16, x + 14, y + 9, width=2)
        if not ok:
            c.create_line(x, y + 15, x + 14, y + 1, width=2)

    def _draw_battery(self, x: int, y: int, ok: bool = True):
        c = self.top_canvas
        c.create_rectangle(x, y + 1, x + 17, y + 12, width=2)
        c.create_rectangle(x + 17, y + 4, x + 20, y + 9, width=2)
        if ok:
            c.create_rectangle(x + 3, y + 4, x + 13, y + 9, fill="black", outline="black")
        else:
            c.create_line(x + 3, y + 10, x + 13, y + 3, width=2)

    def rebuild_rows(self) -> None:
        for child in list(self.list_frame.winfo_children()):
            child.destroy()
        self.row_widgets.clear()
        total_rows = self.page.page_size
        for r in range(total_rows):
            self.list_frame.rowconfigure(r, weight=1, uniform="memo_rows")
        self.list_frame.columnconfigure(0, weight=34, uniform="memo_cols")
        self.list_frame.columnconfigure(1, weight=236, uniform="memo_cols")
        self.list_frame.columnconfigure(2, weight=130, uniform="memo_cols")

        for row_idx in range(total_rows):
            row_map: dict[str, object] = {}
            bell_frame = tk.Frame(self.list_frame, bg="#ffffff")
            bell_frame.grid(row=row_idx, column=0, sticky="nsew")
            bell_var = tk.StringVar(value="🔔")
            bell_btn = tk.Button(bell_frame, textvariable=bell_var, relief="flat", borderwidth=0, bg="#ffffff", activebackground="#f3f3f3", command=lambda idx=row_idx: self._toggle_reminder(idx))
            bell_btn._memo_row_index = row_idx
            bell_btn.place(relx=0.5, rely=0.5, anchor="center")
            bell_btn.bind("<FocusIn>", lambda _e, idx=row_idx: self.page.set_active_target(("reminder", idx)))
            row_map["bell_var"] = bell_var
            row_map["bell_btn"] = bell_btn

            task_frame = tk.Frame(self.list_frame, bg="#ffffff")
            task_frame.grid(row=row_idx, column=1, sticky="nsew")
            task_var = tk.StringVar(value="")
            task_entry = tk.Entry(task_frame, textvariable=task_var, relief="flat", bd=0, highlightthickness=0, bg="#ffffff")
            task_entry.place(relx=0, rely=0, relwidth=1, relheight=1)
            task_entry.bind("<FocusIn>", lambda _e, idx=row_idx: self._on_task_focus_in(idx))
            task_entry.bind("<Return>", lambda _e, idx=row_idx: self._commit_task_text(idx, reason="return") or "break")
            task_entry.bind("<FocusOut>", lambda _e, idx=row_idx: self._on_task_focus_out(idx))
            row_map["task_var"] = task_var
            row_map["task_entry"] = task_entry

            due_frame = tk.Frame(self.list_frame, bg="#ffffff")
            due_frame.grid(row=row_idx, column=2, sticky="nsew")
            due_var = tk.StringVar(value="")
            due_btn = tk.Button(due_frame, textvariable=due_var, relief="flat", borderwidth=0, bg="#ffffff", activebackground="#f3f3f3", command=lambda idx=row_idx: self._open_time_picker(idx))
            due_btn.place(relx=0, rely=0, relwidth=1, relheight=1)
            due_btn.bind("<FocusIn>", lambda _e, idx=row_idx: self.page.set_active_target(("cell", idx, "due_at")))
            row_map["due_var"] = due_var
            row_map["due_btn"] = due_btn
            self.row_widgets.append(row_map)

        self.apply_styles()
        self.pull_state_to_widgets()

    def refresh_time(self) -> None:
        now = self.page.current_now
        self.time_label.configure(text=now.strftime("%H:%M"))
        self.date_label.configure(text=now.strftime("%m/%d"))
        self.weekday_label.configure(text=["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()])
        total = max(1, self.page.total_pages)
        self.page_label.configure(text=f"{self.page.current_page + 1}-{total}" if total > 1 else "")
        for row_idx, row in enumerate(self.page.state.rows):
            due_var = self.row_widgets[row_idx]["due_var"]
            assert isinstance(due_var, tk.StringVar)
            due_var.set(format_due_display(row.due_at, self.page.current_now))

    def pull_state_to_widgets(self) -> None:
        self.refresh_static_header()
        self.refresh_time()
        for row_idx, row in enumerate(self.page.state.rows):
            kind = self.page.get_local_row_kind(row_idx)
            row_map = self.row_widgets[row_idx]
            bell_var = row_map["bell_var"]
            assert isinstance(bell_var, tk.StringVar)
            bell_var.set("🔔" if row.reminder_enabled else "🔕")
            task_var = row_map["task_var"]
            assert isinstance(task_var, tk.StringVar)
            wanted_task = PLACEHOLDER_PLUS if kind == "placeholder" else row.task.text
            if task_var.get() != wanted_task:
                task_var.set(wanted_task)
            due_var = row_map["due_var"]
            assert isinstance(due_var, tk.StringVar)
            if kind != "actual":
                due_var.set("")
            bell_btn = row_map["bell_btn"]
            task_entry = row_map["task_entry"]
            due_btn = row_map["due_btn"]
            assert isinstance(bell_btn, tk.Button)
            assert isinstance(task_entry, tk.Entry)
            assert isinstance(due_btn, tk.Button)
            state = tk.NORMAL if kind in ("actual", "placeholder") else tk.DISABLED
            bell_btn.configure(state=state)
            task_entry.configure(state=state)
            due_btn.configure(state=state)
        self.apply_styles()
        self.apply_blink_state()

    def apply_blink_state(self) -> None:
        blinking = set(due_rows_for_blink(self.page.state, self.page.current_now)) if self.page.blink_phase else set()
        for row_idx in range(self.page.page_size):
            row_map = self.row_widgets[row_idx]
            task_entry = row_map["task_entry"]
            due_btn = row_map["due_btn"]
            bell_btn = row_map["bell_btn"]
            assert isinstance(task_entry, tk.Entry)
            assert isinstance(due_btn, tk.Button)
            assert isinstance(bell_btn, tk.Button)
            if row_idx in blinking:
                task_entry.configure(bg="#000000", fg="#ffffff", insertbackground="#ffffff")
                due_btn.configure(bg="#000000", fg="#ffffff", activebackground="#000000", activeforeground="#ffffff")
                bell_btn.configure(bg="#000000", fg="#ffffff", activebackground="#000000", activeforeground="#ffffff")
            else:
                task_entry.configure(bg="#ffffff", fg="#000000", insertbackground="#000000")
                due_btn.configure(bg="#ffffff", fg="#000000", activebackground="#f3f3f3", activeforeground="#000000")
                bell_btn.configure(bg="#ffffff", fg="#000000", activebackground="#f3f3f3", activeforeground="#000000")

    def apply_styles(self) -> None:
        time_font = tkfont.Font(family="Microsoft YaHei UI", size=24, weight="normal")
        date_font = tkfont.Font(family="Microsoft YaHei UI", size=10, weight="normal")
        self.time_label.configure(font=time_font)
        self.date_label.configure(font=date_font)
        self.weekday_label.configure(font=date_font)
        self.page_label.configure(font=("Microsoft YaHei UI", 9))
        for row_idx, row in enumerate(self.page.state.rows):
            kind = self.page.get_local_row_kind(row_idx)
            task_entry = self.row_widgets[row_idx]["task_entry"]
            due_btn = self.row_widgets[row_idx]["due_btn"]
            bell_btn = self.row_widgets[row_idx]["bell_btn"]
            assert isinstance(task_entry, tk.Entry)
            assert isinstance(due_btn, tk.Button)
            assert isinstance(bell_btn, tk.Button)
            task_entry.configure(font=self._font_from_style(row.task.style), justify="center" if kind == "placeholder" else "left")
            due_btn.configure(font=self._font_from_style(row.time_style), anchor="e", justify="right")
            bell_btn.configure(font=("Segoe UI Emoji", max(12, row.task.style.size)))

    def _font_from_style(self, style: CellStyle):
        slant = "italic" if style.italic else "roman"
        weight = "bold" if style.bold else "normal"
        return tkfont.Font(family="Microsoft YaHei UI", size=max(8, int(style.size)), weight=weight, slant=slant)

    def _toggle_reminder(self, row_idx: int) -> None:
        row = self.page.get_or_create_row(row_idx, create=self.page.get_local_row_kind(row_idx) == "placeholder")
        if row is None:
            return
        row.reminder_enabled = not row.reminder_enabled
        self.pull_state_to_widgets()
        self.page.on_rows_changed([row_idx], force_full=False, reason="toggle_reminder")

    def _on_task_focus_in(self, row_idx: int) -> None:
        self.page.set_active_target(("cell", row_idx, "task"))
        if self.page.get_local_row_kind(row_idx) == "placeholder":
            var = self.row_widgets[row_idx]["task_var"]
            assert isinstance(var, tk.StringVar)
            if var.get().strip() == PLACEHOLDER_PLUS:
                var.set("")

    def _cancel_task_commit(self, row_idx: int) -> None:
        row_map = self.row_widgets[row_idx]
        job = row_map.get("task_commit_job")
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            row_map["task_commit_job"] = None

    def _schedule_task_commit(self, row_idx: int) -> None:
        # 编辑事项时不再自动 debounce 提交，避免输入过程中被排序/重建 UI 打断。
        self._cancel_task_commit(row_idx)
        return

    def _on_task_focus_out(self, row_idx: int) -> None:
        self._cancel_task_commit(row_idx)
        self._commit_task_text(row_idx, reason="focusout")
        if self.page.get_local_row_kind(row_idx) == "placeholder":
            var = self.row_widgets[row_idx]["task_var"]
            assert isinstance(var, tk.StringVar)
            if not var.get().strip():
                var.set(PLACEHOLDER_PLUS)

    def _commit_task_text(self, row_idx: int, reason: str = "manual") -> None:
        self.row_widgets[row_idx]["task_commit_job"] = None
        var = self.row_widgets[row_idx]["task_var"]
        assert isinstance(var, tk.StringVar)
        raw = var.get().strip()
        kind = self.page.get_local_row_kind(row_idx)
        if kind == "blank":
            return
        if kind == "placeholder" and raw in ("", PLACEHOLDER_PLUS):
            var.set(PLACEHOLDER_PLUS)
            return
        row = self.page.get_or_create_row(row_idx, create=bool(raw) and raw != PLACEHOLDER_PLUS)
        if row is None:
            if kind == "placeholder":
                var.set(PLACEHOLDER_PLUS)
            return
        row.task.text = "" if raw == PLACEHOLDER_PLUS else raw
        self.page._append_log(f"提交事项文本 row={row_idx} reason={reason} value={row.task.text!r}")
        self.page.on_rows_changed([row_idx], force_full=False, reason="task_text")

    def _open_time_picker(self, row_idx: int):
        if self.page.get_local_row_kind(row_idx) == "blank":
            return
        self.page.set_active_target(("cell", row_idx, "due_at"))
        existing = self.page.get_display_row(row_idx).due_at
        DateTimePickerPopup(self, existing, lambda value, idx=row_idx: self._set_time_value(idx, value))

    def _set_time_value(self, row_idx: int, value: str) -> None:
        row = self.page.get_or_create_row(row_idx, create=True)
        if row is None:
            return
        row.due_at = value
        self.page._append_log(f"设置时间 row={row_idx} value={value}")
        self.pull_state_to_widgets()
        self.page.on_rows_changed([row_idx], force_full=False, reason="set_time")


class DesktopMemoPage(ttk.Frame):
    def __init__(self, master: ttk.Frame, app_shell) -> None:
        super().__init__(master, padding=12)
        self.app_shell = app_shell
        self.app_root = Path(__file__).resolve().parents[2]

        self.config = load_config(self.app_root)
        stored_items, meta = load_memo_state(self.app_root)
        self.page_size = DEFAULT_PAGE_SIZE
        self.current_page = max(0, min(MAX_TOTAL_PAGES - 1, int(meta.get("current_page", 0))))
        initial_items = stored_items or default_fixed_memo_state(self.page_size).rows
        self.all_items: list[ChecklistRow] = [copy.deepcopy(r) for r in initial_items if (r.task.text.strip() or r.due_at.strip())][:MAX_REAL_ITEMS]
        self.state: FixedMemoState = FixedMemoState(rows=[])
        self.state.wifi_ok = bool(meta.get("wifi_ok", True))
        self.state.bluetooth_ok = bool(meta.get("bluetooth_ok", True))
        self.state.battery_ok = bool(meta.get("battery_ok", True))
        self.total_pages = 1
        self.active_target: tuple | None = None
        self._render_source = None
        self._sending = False
        self.is_flashing = False
        self._pending_regions: list[PartialRegion] = []
        self._screen_initialized = False
        self._screen_page = -1
        self.current_now = datetime.now()
        self.blink_phase = False
        self.placeholder_local_idx: int | None = None
        self.popup = None
        self.popup_editor = None

        self.serial_port_var = tk.StringVar(value=self.config.serial_port)
        self.serial_baud_var = tk.StringVar(value=self.config.serial_baud or str(DEFAULT_BAUDRATE))
        self.page_var = tk.StringVar(value="1/1")
        self.status_var = tk.StringVar(value="首次请先“加载当前页到墨水屏”；之后软件会自动局刷顶部时间。")
        self.wifi_ok_var = tk.BooleanVar(value=self.state.wifi_ok)
        self.bt_ok_var = tk.BooleanVar(value=self.state.bluetooth_ok)
        self.battery_ok_var = tk.BooleanVar(value=self.state.battery_ok)

        self.editor_surfaces: list[FixedEditorSurface] = []
        self._build_layout()
        self._bind_shortcuts()
        self._apply_page_state()
        self._schedule_clock_tick()
        self._blink_job = None
        self.scan_serial_ports()

    def destroy(self) -> None:
        for seq in ("<Control-b>", "<Control-i>", "<Control-equal>", "<Control-plus>", "<Control-minus>", "<Control-0>", "<Delete>"):
            try:
                self.unbind_all(seq)
            except Exception:
                pass
        for job in (getattr(self, "_clock_job", None), getattr(self, "_blink_job", None), getattr(self, "_sync_job", None)):
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        popup = getattr(self, "popup", None)
        if popup is not None:
            try:
                popup.destroy()
            except Exception:
                pass
        super().destroy()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        ttk.Button(header, text="← 返回主界面", command=lambda: self.app_shell.show_page("home")).grid(row=0, column=0, padx=(0, 12))
        ttk.Label(header, text="#2 桌面备忘录", font=("Microsoft YaHei UI", 18, "bold")).grid(row=0, column=1, sticky="w")

        tip = ttk.Label(
            self,
            text=(
                "固定布局：顶部为时间 / 日期 / 星期 + WiFi / 蓝牙 / 电池；下方为提醒清单。\n"
                "Delete 可删除当前选中事项；删除后后面的事项会自动补上。首次全刷后，后续编辑、每分钟时间更新和到点闪烁都会优先局刷。"
            ),
            justify="left",
        )
        tip.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        editor_frame = ttk.LabelFrame(self, text="编辑区（即最终展示内容）")
        editor_frame.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        editor_frame.columnconfigure(0, weight=1)
        editor_frame.rowconfigure(0, weight=1)
        self.main_editor = FixedEditorSurface(editor_frame, self, scale=2.0)
        self.main_editor.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.editor_surfaces.append(self.main_editor)

        side = ttk.Frame(self)
        side.grid(row=2, column=1, sticky="nsew", pady=(0, 8))
        side.columnconfigure(0, weight=1)
        side.rowconfigure(1, weight=1)

        options = ttk.LabelFrame(side, text="页面 / 状态 / 发送")
        options.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        for i in range(4):
            options.columnconfigure(i, weight=1)

        ttk.Button(options, text="生成测试用例", command=self.generate_sample_cases).grid(row=0, column=0, sticky="ew", padx=6, pady=4)
        ttk.Button(options, text="上一页", command=self.prev_page).grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(options, text="下一页 / 翻页", command=self.next_page).grid(row=0, column=2, sticky="ew", padx=6, pady=4)
        ttk.Label(options, textvariable=self.page_var, anchor="center").grid(row=0, column=3, sticky="ew", padx=6, pady=4)

        ttk.Button(options, text="删除当前事项", command=self.delete_selected_row).grid(row=1, column=0, sticky="ew", padx=6, pady=4)
        ttk.Button(options, text="放大编辑区", command=self.open_popout_editor).grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(options, text="编译并烧录接收固件", command=self.flash_receiver_firmware).grid(row=1, column=2, columnspan=2, sticky="ew", padx=6, pady=4)
        ttk.Button(options, text="仅更新时间到墨水屏", command=self.push_top_time_partial).grid(row=2, column=0, columnspan=4, sticky="ew", padx=6, pady=4)

        ttk.Checkbutton(options, text="WiFi 正常", variable=self.wifi_ok_var, command=self.on_header_status_changed).grid(row=3, column=0, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(options, text="蓝牙正常", variable=self.bt_ok_var, command=self.on_header_status_changed).grid(row=3, column=1, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(options, text="电池正常", variable=self.battery_ok_var, command=self.on_header_status_changed).grid(row=3, column=2, sticky="w", padx=6, pady=4)
        ttk.Label(options, text="首次请先全刷一遍。", anchor="w").grid(row=3, column=3, sticky="ew", padx=6, pady=4)

        ttk.Label(options, text="串口").grid(row=4, column=0, sticky="w", padx=6, pady=4)
        self.port_combo = ttk.Combobox(options, textvariable=self.serial_port_var)
        self.port_combo.grid(row=4, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(options, text="波特率").grid(row=4, column=2, sticky="w", padx=6, pady=4)
        ttk.Entry(options, textvariable=self.serial_baud_var).grid(row=4, column=3, sticky="ew", padx=6, pady=4)

        ttk.Button(options, text="扫描串口", command=self.scan_serial_ports).grid(row=5, column=0, sticky="ew", padx=6, pady=4)
        ttk.Button(options, text="加载当前页到墨水屏", command=self.push_to_screen).grid(row=5, column=1, columnspan=3, sticky="ew", padx=6, pady=4)

        helper = ttk.Label(options, text="当前格子获得焦点后，可用 Ctrl+B / Ctrl+I / Ctrl++ / Ctrl+- / Ctrl+0 调样式；Delete 删除整行。", justify="left")
        helper.grid(row=6, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 6))

        log_frame = ttk.LabelFrame(side, text="日志")
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = ScrolledText(log_frame, height=18)
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.log_text.configure(state="disabled")

        ttk.Label(self, textvariable=self.status_var).grid(row=3, column=0, columnspan=2, sticky="w")

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-b>", self.on_toggle_bold, add="+")
        self.bind_all("<Control-i>", self.on_toggle_italic, add="+")
        self.bind_all("<Control-equal>", self.on_increase_font, add="+")
        self.bind_all("<Control-plus>", self.on_increase_font, add="+")
        self.bind_all("<Control-minus>", self.on_decrease_font, add="+")
        self.bind_all("<Control-0>", self.on_reset_font, add="+")
        self.bind_all("<Delete>", self.on_delete_key, add="+")

    def _row_has_content(self, row: ChecklistRow) -> bool:
        return bool(row.task.text.strip() and row.task.text.strip() != PLACEHOLDER_PLUS) or bool(row.due_at.strip())

    def _sort_key(self, row: ChecklistRow):
        dt = parse_due(row.due_at)
        return (0 if dt else 1, dt or datetime.max, row.task.text.strip())

    def _normalize_items(self) -> None:
        actual = [copy.deepcopy(r) for r in self.all_items if self._row_has_content(r)]
        actual.sort(key=self._sort_key)
        self.all_items = actual[:MAX_REAL_ITEMS]

    def _placeholder_row(self) -> ChecklistRow:
        return ChecklistRow(
            reminder_enabled=True,
            task=StyledCell(PLACEHOLDER_PLUS, CellStyle(size=DEFAULT_FONT_SIZE, bold=True)),
            due_at="",
            time_style=CellStyle(size=TIME_FONT_SIZE),
        )

    def _global_slot_for_local(self, local_idx: int) -> int:
        return self.current_page * self.page_size + local_idx

    def get_local_row_kind(self, local_idx: int) -> str:
        global_idx = self._global_slot_for_local(local_idx)
        if global_idx < len(self.all_items):
            return "actual"
        if len(self.all_items) < MAX_REAL_ITEMS and global_idx == len(self.all_items):
            return "placeholder"
        return "blank"

    def _items_on_page(self) -> list[ChecklistRow]:
        start = self.current_page * self.page_size
        return self.all_items[start:start + self.page_size]

    def _blank_row(self) -> ChecklistRow:
        return ChecklistRow(reminder_enabled=True, task=StyledCell(""), due_at="", time_style=CellStyle(size=TIME_FONT_SIZE))

    def get_display_row(self, local_idx: int) -> ChecklistRow:
        if 0 <= local_idx < len(self.state.rows):
            return self.state.rows[local_idx]
        return self._blank_row()

    def get_or_create_row(self, local_idx: int, create: bool = True) -> ChecklistRow | None:
        global_idx = self._global_slot_for_local(local_idx)
        if global_idx < len(self.all_items):
            return self.all_items[global_idx]
        if not create or len(self.all_items) >= MAX_REAL_ITEMS or global_idx != len(self.all_items):
            return None
        row = ChecklistRow(reminder_enabled=True, task=StyledCell(""), due_at="", time_style=CellStyle(size=TIME_FONT_SIZE))
        self.all_items.append(row)
        return row

    def _recompute_pages(self) -> None:
        slots = max(1, len(self.all_items) + (0 if len(self.all_items) >= MAX_REAL_ITEMS else 1))
        self.total_pages = max(1, min(MAX_TOTAL_PAGES, (slots + self.page_size - 1) // self.page_size))
        self.current_page = max(0, min(self.current_page, self.total_pages - 1))

    def _apply_page_state(self) -> None:
        self._normalize_items()
        self._recompute_pages()
        start = self.current_page * self.page_size
        page_rows = [copy.deepcopy(r) for r in self._items_on_page()]
        self.placeholder_local_idx = None
        global_placeholder = len(self.all_items) if len(self.all_items) < MAX_REAL_ITEMS else None
        if global_placeholder is not None and start <= global_placeholder < start + self.page_size:
            self.placeholder_local_idx = global_placeholder - start
        while len(page_rows) < self.page_size:
            local_idx = len(page_rows)
            global_idx = start + local_idx
            if self.placeholder_local_idx is not None and global_idx == global_placeholder:
                page_rows.append(self._placeholder_row())
            else:
                page_rows.append(self._blank_row())
        self.state.rows = page_rows[:self.page_size]
        self.state.page_index = self.current_page
        self.state.total_pages = self.total_pages
        self.state.wifi_ok = self.wifi_ok_var.get()
        self.state.bluetooth_ok = self.bt_ok_var.get()
        self.state.battery_ok = self.battery_ok_var.get()
        self.page_var.set(f"{self.current_page + 1}/{self.total_pages}")
        for surface in self.editor_surfaces:
            surface.rebuild_rows()
        self.sync_render_state()
        self._save_db()

    def _row_signature(self, row: ChecklistRow) -> tuple:
        return (
            bool(row.reminder_enabled),
            row.task.text,
            bool(row.task.style.bold),
            bool(row.task.style.italic),
            int(row.task.style.size),
            row.due_at,
            bool(row.time_style.bold),
            bool(row.time_style.italic),
            int(row.time_style.size),
        )

    def _state_row_signatures(self, rows: list[ChecklistRow]) -> list[tuple]:
        return [self._row_signature(r) for r in rows]

    def _make_region_from_box(self, box: tuple[int, int, int, int]) -> PartialRegion:
        x0, y0, x1, y1 = box
        return PartialRegion(x=x0, y=y0, width=x1 - x0, height=y1 - y0)

    def _region_for_reason(self, reason: str, local_rows: list[int], changed_rows: list[int]) -> tuple[PartialRegion, str]:
        boxes = row_boxes(self.state)
        target_rows = changed_rows or local_rows
        if not target_rows:
            return PartialRegion(x=LIST_RECT[0], y=LIST_RECT[1], width=LIST_RECT[2] - LIST_RECT[0], height=LIST_RECT[3] - LIST_RECT[1]), "fallback:list"
        # Exact-slot partials now land in the correct physical Y position, but this panel/driver stack
        # still shows row-adjacent visual coupling: updating one row can visually regress the neighbour
        # even though the payload/crop checksum is correct. To stabilize what the user sees, refresh a
        # small full-width band that includes the changed row and its immediate neighbours.
        if reason in {"toggle_reminder", "task_text", "set_time"}:
            band_rows: list[int] = []
            for idx in sorted(set(target_rows)):
                if idx - 1 >= 0:
                    band_rows.append(idx - 1)
                band_rows.append(idx)
                if idx + 1 < len(boxes):
                    band_rows.append(idx + 1)
            band_rows = sorted(set(band_rows))
            if band_rows:
                return self._region_for_rows(band_rows), f"row-band:{reason}:{band_rows}"
        return self._region_for_rows(target_rows), f"merged:rows={target_rows}"

    def _checksum_bytes(self, values: list[int]) -> int:
        return sum(int(v) & 0xFF for v in values) & 0xFFFFFFFF

    def _crop_preview_bw_bytes(self, preview_pixels: list[int], width: int, region: PartialRegion) -> list[int]:
        out: list[int] = []
        for y in range(region.y, region.y + region.height):
            row = preview_pixels[y * width : (y + 1) * width]
            cropped = row[region.x : region.x + region.width]
            for x in range(0, region.width, 8):
                byte = 0
                chunk = cropped[x : x + 8]
                if len(chunk) < 8:
                    chunk = chunk + [255] * (8 - len(chunk))
                for pixel in chunk:
                    bit = 1 if int(pixel) >= 128 else 0
                    byte = (byte << 1) | bit
                out.append(byte)
        return out

    def on_rows_changed(self, local_rows: list[int], force_full: bool = False, reason: str = "edit") -> None:
        before_rows = [copy.deepcopy(r) for r in self.state.rows]
        before_page = self.current_page
        before_total = self.total_pages
        before_signatures = self._state_row_signatures(before_rows)

        self._normalize_items()
        self._apply_page_state()

        after_rows = [copy.deepcopy(r) for r in self.state.rows]
        after_signatures = self._state_row_signatures(after_rows)
        changed_rows = sorted({idx for idx in range(min(len(before_signatures), len(after_signatures))) if before_signatures[idx] != after_signatures[idx]} | set(local_rows))
        if len(after_signatures) > len(before_signatures):
            changed_rows.extend(range(len(before_signatures), len(after_signatures)))
            changed_rows = sorted(set(changed_rows))

        self._append_log(
            "局刷行变更分析: "
            f"requested={local_rows}, changed={changed_rows}, "
            f"before_page={before_page + 1}/{before_total}, after_page={self.current_page + 1}/{self.total_pages}, reason={reason}, force_full={force_full}"
        )
        self._append_log(f"局刷前签名: {before_signatures}")
        self._append_log(f"局刷后签名: {after_signatures}")

        if force_full:
            region = PartialRegion(x=LIST_RECT[0], y=LIST_RECT[1], width=LIST_RECT[2] - LIST_RECT[0], height=LIST_RECT[3] - LIST_RECT[1])
            strategy = "forced:list"
            self._append_log("强制局刷整块列表区域。")
        elif before_page != self.current_page or before_total != self.total_pages:
            region = PartialRegion(x=LIST_RECT[0], y=LIST_RECT[1], width=LIST_RECT[2] - LIST_RECT[0], height=LIST_RECT[3] - LIST_RECT[1])
            strategy = "page-change:list"
            self._append_log("页码或总页数变化，提升为整块列表区域局刷。")
        else:
            region, strategy = self._region_for_reason(reason, local_rows, changed_rows)
        self._append_log(f"排队局刷行 {local_rows} -> region=({region.x},{region.y},{region.width},{region.height}) page={self.current_page + 1}/{self.total_pages}")
        self._append_log(f"局刷策略: reason={reason}, changed_rows={changed_rows}, strategy={strategy}, force_full={force_full}, list_rect=({LIST_RECT[0]},{LIST_RECT[1]},{LIST_RECT[2]-LIST_RECT[0]},{LIST_RECT[3]-LIST_RECT[1]})")
        self._queue_region(region)

    def on_header_status_changed(self) -> None:
        self.state.wifi_ok = self.wifi_ok_var.get()
        self.state.bluetooth_ok = self.bt_ok_var.get()
        self.state.battery_ok = self.battery_ok_var.get()
        for surface in self.editor_surfaces:
            surface.refresh_static_header()
        self._save_db()
        self.sync_render_state()
        region = PartialRegion(x=TOP_RECT[0], y=TOP_RECT[1], width=TOP_RECT[2] - TOP_RECT[0], height=TOP_RECT[3] - TOP_RECT[1])
        self._append_log(f"排队顶部局刷 region=({region.x},{region.y},{region.width},{region.height}) wifi={self.wifi_ok_var.get()} bt={self.bt_ok_var.get()} battery={self.battery_ok_var.get()}")
        self._queue_region(region)

    def _region_for_rows(self, rows: list[int]) -> PartialRegion:
        boxes = row_boxes(self.state)
        picked = [boxes[idx]["row"] for idx in rows if 0 <= idx < len(boxes)]
        if not picked:
            return PartialRegion(x=LIST_RECT[0], y=LIST_RECT[1], width=LIST_RECT[2] - LIST_RECT[0], height=LIST_RECT[3] - LIST_RECT[1])
        x0 = min(b[0] for b in picked)
        y0 = min(b[1] for b in picked)
        x1 = max(b[2] for b in picked)
        y1 = max(b[3] for b in picked)
        return PartialRegion(x=x0, y=y0, width=x1 - x0, height=y1 - y0)

    def _bind_surface_refresh(self):
        for surface in self.editor_surfaces:
            surface.pull_state_to_widgets()

    def _save_db(self) -> None:
        meta = {
            "page_size": DEFAULT_PAGE_SIZE,
            "current_page": self.current_page,
            "wifi_ok": self.wifi_ok_var.get(),
            "bluetooth_ok": self.bt_ok_var.get(),
            "battery_ok": self.battery_ok_var.get(),
        }
        save_memo_state(self.app_root, self.all_items, meta)

    def _schedule_clock_tick(self) -> None:
        self.current_now = datetime.now()
        for surface in self.editor_surfaces:
            surface.refresh_time()
        next_minute = self.current_now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        delay_ms = max(500, int((next_minute - self.current_now).total_seconds() * 1000) + 120)
        self._clock_job = self.after(delay_ms, self._on_clock_tick)

    def _on_clock_tick(self) -> None:
        self.current_now = datetime.now()
        for surface in self.editor_surfaces:
            surface.refresh_time()
        self.sync_render_state()
        if self._screen_initialized and self._screen_page == self.current_page:
            self.push_top_time_partial(silent=True)
        self._schedule_clock_tick()

    def _schedule_blink_tick(self) -> None:
        self._blink_job = None

    def _on_blink_tick(self) -> None:
        # 已禁用到点闪烁，避免干扰事项刷新验证。
        self.blink_phase = False
        return

    def set_active_target(self, target: tuple) -> None:
        self.active_target = target
        self.status_var.set(f"当前编辑目标：{self._describe_target(target)}")

    def _describe_target(self, target: tuple | None) -> str:
        if not target:
            return "未选择"
        if target[0] == "cell":
            _, row, field = target
            return f"第 {row + 1} 行 / {'事情' if field == 'task' else '时间'}"
        if target[0] == "reminder":
            return f"第 {target[1] + 1} 行 / 铃铛提醒"
        return str(target)

    def _get_target_style(self, target: tuple | None) -> CellStyle | None:
        if not target or target[0] != "cell":
            return None
        _, row, field = target
        display_row = self.get_display_row(row)
        return display_row.task.style if field == "task" else display_row.time_style

    def on_toggle_bold(self, event=None):
        style = self._get_target_style(self.active_target)
        if style is None:
            return "break"
        style.bold = not style.bold
        self._write_active_style_back()
        return "break"

    def on_toggle_italic(self, event=None):
        style = self._get_target_style(self.active_target)
        if style is None:
            return "break"
        style.italic = not style.italic
        self._write_active_style_back()
        return "break"

    def on_increase_font(self, event=None):
        style = self._get_target_style(self.active_target)
        if style is None:
            return "break"
        style.size = min(40, style.size + 1)
        self._write_active_style_back()
        return "break"

    def on_decrease_font(self, event=None):
        style = self._get_target_style(self.active_target)
        if style is None:
            return "break"
        style.size = max(8, style.size - 1)
        self._write_active_style_back()
        return "break"

    def on_reset_font(self, event=None):
        style = self._get_target_style(self.active_target)
        if style is None:
            return "break"
        if self.active_target and self.active_target[2] == "due_at":
            style.size = TIME_FONT_SIZE
        else:
            style.size = DEFAULT_FONT_SIZE
        self._write_active_style_back()
        return "break"

    def _write_active_style_back(self) -> None:
        if not self.active_target or self.active_target[0] != "cell":
            return
        local_idx = self.active_target[1]
        field = self.active_target[2]
        row = self.get_or_create_row(local_idx, create=True)
        display = self.get_display_row(local_idx)
        if field == "task":
            row.task.style = copy.deepcopy(display.task.style)
        else:
            row.time_style = copy.deepcopy(display.time_style)
        self._apply_page_state()
        self._queue_region(self._region_for_rows([local_idx]))

    def on_delete_key(self, event=None):
        self.delete_selected_row()
        return "break"

    def delete_selected_row(self) -> None:
        target = self.active_target
        if not target:
            focus = self.focus_get()
            if focus is not None:
                row_index = getattr(focus, "_memo_row_index", None)
                if row_index is not None:
                    target = ("cell", int(row_index), "task")
        if not target:
            self.status_var.set("请先选中要删除的事项。")
            return
        local_idx = int(target[1])
        global_idx = self._global_slot_for_local(local_idx)
        if global_idx >= len(self.all_items):
            self.status_var.set("当前行没有可删除的事项。")
            return
        removed = self.all_items.pop(global_idx)
        self.active_target = None
        self._apply_page_state()
        if self._screen_initialized and self._screen_page == self.current_page:
            self.push_to_screen(silent=True)
        removed_name = removed.task.text or f"第 {global_idx + 1} 项"
        self._append_log(f"已删除事项：{removed_name}（原序号 {global_idx + 1}），后续事项已自动补位。")
        self.status_var.set("删除成功。")

    def generate_sample_cases(self) -> None:
        samples = [
            (True, "喝水", _fmt_future(hours=1)),
            (True, "多邻国打卡", _fmt_future(hours=3)),
            (True, "写日报", _fmt_future(days=1, hours=9)),
            (False, "明早8点起床", _fmt_future(days=1, hours=8)),
            (True, "快递取件", _fmt_future(days=1, hours=16)),
            (True, "给爸妈打电话", _fmt_future(days=1, hours=20)),
            (True, "周会材料检查", _fmt_future(days=2, hours=9)),
            (False, "联系客户", _fmt_future(days=2, hours=14)),
            (True, "交房租", _fmt_future(days=3, hours=9)),
            (True, "去超市", _fmt_future(days=3, hours=12)),
            (False, "备份资料", _fmt_future(days=4, hours=8)),
            (True, "买猫粮", _fmt_future(days=4, hours=18)),
        ]
        self.all_items = []
        for enabled, task, due in samples:
            self.all_items.append(
                ChecklistRow(
                    reminder_enabled=enabled,
                    task=StyledCell(task, CellStyle(size=DEFAULT_FONT_SIZE)),
                    due_at=due,
                    time_style=CellStyle(size=TIME_FONT_SIZE),
                )
            )
        self.current_page = 0
        self._apply_page_state()
        self._append_log(f"已生成正常测试用例，共 {len(self.all_items)} 条；最多 20 条，页数会自动扩到最多 4 页。")
        self.status_var.set("已生成测试用例。")

    def prev_page(self) -> None:
        if self.current_page <= 0:
            self.status_var.set("已经是第一页。")
            return
        self.current_page -= 1
        self._apply_page_state()
        if self._screen_initialized:
            self.push_to_screen(silent=True)

    def next_page(self) -> None:
        self.current_page = 0 if self.current_page >= self.total_pages - 1 else self.current_page + 1
        self._apply_page_state()
        if self._screen_initialized:
            self.push_to_screen(silent=True)

    def sync_render_state(self) -> None:
        self.state.page_index = self.current_page
        self.state.total_pages = self.total_pages
        self.state.wifi_ok = self.wifi_ok_var.get()
        self.state.bluetooth_ok = self.bt_ok_var.get()
        self.state.battery_ok = self.battery_ok_var.get()
        self._render_source = render_fixed_memo_image(self.state, now=self.current_now, blink_phase=False)
        for surface in self.editor_surfaces:
            surface.pull_state_to_widgets()

    def open_popout_editor(self) -> None:
        if self.popup is not None and self.popup.winfo_exists():
            self.popup.deiconify()
            self.popup.lift()
            self.popup.focus_force()
            return
        self.popup = tk.Toplevel(self)
        self.popup.title("桌面备忘录 - 放大编辑区")
        self.popup.geometry("1080x860")
        self.popup.protocol("WM_DELETE_WINDOW", self._close_popout)
        outer = ttk.Frame(self.popup, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(toolbar, text="加载当前页到墨水屏", command=self.push_to_screen).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="下一页 / 翻页", command=self.next_page).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(toolbar, text="删除当前事项", command=self.delete_selected_row).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(toolbar, text="放大编辑区与主界面共用同一页内容。", padding=(16, 0)).pack(side=tk.LEFT)
        self.popup_editor = FixedEditorSurface(outer, self, scale=2.45)
        self.popup_editor.pack(fill=tk.BOTH, expand=True)
        self.editor_surfaces.append(self.popup_editor)
        self.popup.focus_force()

    def _close_popout(self) -> None:
        if self.popup_editor is not None and self.popup_editor in self.editor_surfaces:
            self.editor_surfaces.remove(self.popup_editor)
        popup = getattr(self, "popup", None)
        if popup is not None:
            try:
                popup.destroy()
            except Exception:
                pass
        self.popup = None
        self.popup_editor = None

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

    def scan_serial_ports(self) -> None:
        try:
            from serial.tools import list_ports
        except Exception as exc:
            messagebox.showerror("扫描串口失败", f"缺少 pyserial：{exc}")
            return
        ports = list(list_ports.comports())
        values: list[str] = []
        for item in ports:
            desc = f"{item.device} | {item.description}" if item.description else item.device
            values.extend([item.device, desc])
        unique = []
        seen = set()
        for item in values:
            if item not in seen:
                unique.append(item)
                seen.add(item)
        self.port_combo["values"] = unique
        if ports and not self.serial_port_var.get().strip():
            self.serial_port_var.set(ports[0].device)
        self._append_log("=== 检测到的串口 ===\n" + ("\n".join(f"- {p.device} | {p.description}" for p in ports) if ports else "未找到可识别的设备。"))

    def save_app_config(self) -> None:
        self.config.serial_port = self._normalize_port_value(self.serial_port_var.get().strip())
        self.config.serial_baud = self.serial_baud_var.get().strip() or str(DEFAULT_BAUDRATE)
        path = save_config(self.app_root, self.config)
        self._append_log(f"联动配置已保存：{path}")

    def _build_send_result(self):
        source_image = render_fixed_memo_image(self.state, now=self.current_now, blink_phase=False)
        send_options = ConversionOptions(
            preset_key="good_display_400x300_demo",
            color_mode="bw",
            update_mode="full",
            fit_mode="stretch",
            rotation=0,
            flip_horizontal=True,
            invert=False,
            threshold=128,
            dither="none",
            variable_name="gImage_desktop_memo",
        )
        return build_conversion_result(source_image, send_options)

    def _build_partial_result(self, region: PartialRegion):
        self._append_log(f"构建局刷图像 region=({region.x},{region.y},{region.width},{region.height}) now={self.current_now.strftime('%Y/%m/%d %H:%M:%S')} blink=False")
        source_image = render_fixed_memo_image(self.state, now=self.current_now, blink_phase=False)
        full_options = ConversionOptions(
            preset_key="good_display_400x300_demo",
            color_mode="bw",
            update_mode="full",
            fit_mode="stretch",
            rotation=0,
            flip_horizontal=True,
            invert=False,
            threshold=128,
            dither="none",
            variable_name="gImage_desktop_memo_full_probe",
        )
        send_options = ConversionOptions(
            preset_key="good_display_400x300_demo",
            color_mode="bw",
            update_mode="partial",
            fit_mode="stretch",
            rotation=0,
            flip_horizontal=True,
            invert=False,
            threshold=128,
            dither="none",
            variable_name="gImage_desktop_memo_partial",
            partial_region=region,
        )
        full_result = build_conversion_result(source_image, full_options)
        partial_result = build_conversion_result(source_image, send_options)
        device_region = partial_result.partial_region_applied or region
        full_crop = self._crop_preview_bw_bytes(full_result.output_pixels, full_result.preset.width, device_region)
        self._append_log(
            f"局刷数据校验: logical=({region.x},{region.y},{region.width},{region.height}), "
            f"device=({device_region.x},{device_region.y},{device_region.width},{device_region.height}), "
            f"fullCropSum={self._checksum_bytes(full_crop)}, payloadSum={self._checksum_bytes(partial_result.exported_bytes)}, "
            f"bytes={len(partial_result.exported_bytes)}, match={full_crop == partial_result.exported_bytes}"
        )
        return partial_result

    def _send_worker(self, send_result, port: str, baud: int, success_cb, fail_cb):
        try:
            result = send_result_to_device(port=port, result=send_result, baudrate=baud)
            self.after(0, lambda: success_cb(result))
        except Exception as exc:
            tb = traceback.format_exc()
            self.after(0, lambda: fail_cb(exc, tb))


    def flash_receiver_firmware(self) -> None:
        if self.is_flashing:
            messagebox.showinfo("正在执行", "当前已经有一个编译/烧录任务在运行，请稍等。")
            return
        try:
            self.save_app_config()
            port = self._normalize_port_value(self.serial_port_var.get().strip())
            if not port:
                raise ValueError("请先填写串口，例如 COM4。")
            if not self.config.fqbn.strip():
                raise ValueError("请先填写 FQBN，例如 esp32:esp32:esp32。")
            send_result = self._build_send_result()
            self.is_flashing = True
            self.status_var.set("正在后台编译并烧录接收固件，请稍候……")
            self._append_log("开始后台编译并烧录接收固件，请不要重复点击。")

            def worker() -> None:
                try:
                    sketch_dir = create_temp_sketch(
                        esp32_project_dir=self.config.esp32_project_dir,
                        temp_build_root=self.config.temp_build_root,
                        result=send_result,
                    )
                    workflow = compile_and_upload(
                        cli_path=self.config.arduino_cli_path,
                        sketch_dir=str(sketch_dir),
                        fqbn=self.config.fqbn,
                        port=port,
                    )
                    self.after(0, lambda: self._on_flash_finished(workflow, port))
                except Exception as exc:
                    tb = traceback.format_exc()
                    self.after(0, lambda exc=exc, tb=tb: self._on_flash_error(exc, tb))

            threading.Thread(target=worker, daemon=True).start()
        except Exception as exc:
            self._append_log(f"编译/烧录失败：\n{exc}\n\n{traceback.format_exc()}")
            messagebox.showerror("烧录失败", str(exc))
            self.status_var.set("烧录失败。")

    def _on_flash_finished(self, workflow: FlashWorkflowResult, port: str) -> None:
        self.is_flashing = False
        self._append_log(workflow.combined_output)
        if workflow.ok:
            self.status_var.set(f"接收固件部署完成：{port}。")
        else:
            self.status_var.set("烧录失败，请查看日志。")
            messagebox.showwarning("烧录失败", "编译或烧录失败，请查看日志。")

    def _on_flash_error(self, exc: Exception, tb: str) -> None:
        self.is_flashing = False
        self._append_log(f"编译/烧录失败：\n{exc}\n\n{tb}")
        messagebox.showerror("烧录失败", str(exc))
        self.status_var.set("烧录失败。")

    def push_to_screen(self, silent: bool = False) -> None:
        if self._sending:
            return
        try:
            self.save_app_config()
            port = self._normalize_port_value(self.serial_port_var.get().strip())
            if not port:
                raise ValueError("请先填写串口，例如 COM4。")
            baud = int((self.serial_baud_var.get() or str(DEFAULT_BAUDRATE)).strip())
            send_result = self._build_send_result()
            self._sending = True
            if not silent:
                self.status_var.set("正在发送当前页到墨水屏……")
            threading.Thread(target=self._send_worker, args=(send_result, port, baud, self._on_full_send_finished, self._on_send_failed), daemon=True).start()
        except Exception as exc:
            self._append_log(f"串口发送失败：\n{exc}\n\n{traceback.format_exc()}")
            self.status_var.set("串口发送失败。")
            if not silent:
                messagebox.showerror("发送失败", str(exc))

    def push_top_time_partial(self, silent: bool = False) -> None:
        self._queue_region(PartialRegion(x=TOP_RECT[0], y=TOP_RECT[1], width=TOP_RECT[2] - TOP_RECT[0], height=TOP_RECT[3] - TOP_RECT[1]), silent=silent)

    def _queue_region(self, region: PartialRegion, silent: bool = True) -> None:
        if not self._screen_initialized or self._screen_page != self.current_page:
            self._append_log(f"忽略局刷请求：screen_initialized={self._screen_initialized}, screen_page={self._screen_page}, current_page={self.current_page}")
            return
        self._pending_regions.append(region)
        self._flush_pending_regions(silent=silent)

    def _merge_regions(self, regions: list[PartialRegion]) -> PartialRegion:
        x0 = min(r.x for r in regions)
        y0 = min(r.y for r in regions)
        x1 = max(r.x + r.width for r in regions)
        y1 = max(r.y + r.height for r in regions)
        return PartialRegion(x=x0, y=y0, width=x1 - x0, height=y1 - y0)

    def _flush_pending_regions(self, silent: bool = True) -> None:
        if self._sending or not self._pending_regions:
            return
        port = self._normalize_port_value(self.serial_port_var.get().strip())
        if not port:
            return
        baud = int((self.serial_baud_var.get() or str(DEFAULT_BAUDRATE)).strip())
        merged = self._merge_regions(self._pending_regions)
        self._pending_regions.clear()
        self._append_log(f"开始局刷 region=({merged.x},{merged.y},{merged.width},{merged.height}) page={self.current_page + 1}")
        send_result = self._build_partial_result(merged)
        self._sending = True
        threading.Thread(target=self._send_worker, args=(send_result, port, baud, self._on_partial_send_finished, self._on_send_failed), daemon=True).start()
        if not silent:
            self.status_var.set("正在局部刷新墨水屏……")

    def _on_full_send_finished(self, result) -> None:
        self._sending = False
        self._screen_initialized = True
        self._screen_page = self.current_page
        self._append_log(result.log_text + f"\n\n发送成功：当前第 {self.current_page + 1} 页已加载到墨水屏。")
        self.status_var.set(f"已发送到墨水屏：{result.port}")
        self._flush_pending_regions(silent=True)

    def _on_partial_send_finished(self, result) -> None:
        self._sending = False
        self._append_log(result.log_text + "\n\n局部刷新成功。")
        self.status_var.set("墨水屏已局部刷新。")
        self._flush_pending_regions(silent=True)

    def _on_send_failed(self, exc: Exception, tb: str) -> None:
        self._sending = False
        self._append_log(f"串口发送失败：\n{exc}\n\n{tb}")
        self.status_var.set("串口发送失败。")
        messagebox.showerror("发送失败", str(exc))

    def _append_log(self, text: str) -> None:
        focus_widget = self.focus_get()
        try:
            at_bottom = float(self.log_text.yview()[1]) >= 0.999
        except Exception:
            at_bottom = True
        prev_state = str(self.log_text.cget("state"))
        try:
            if prev_state == "disabled":
                self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, text.rstrip() + "\n")
            if at_bottom:
                self.log_text.see(tk.END)
        finally:
            if prev_state == "disabled":
                self.log_text.configure(state="disabled")
        try:
            if focus_widget is not None and focus_widget.winfo_exists():
                focus_widget.focus_set()
        except Exception:
            pass


def _fmt_future(days: int = 0, hours: int = 0) -> str:
    dt = datetime.now() + timedelta(days=days, hours=hours)
    return dt.strftime("%Y/%m/%d %H:%M")
