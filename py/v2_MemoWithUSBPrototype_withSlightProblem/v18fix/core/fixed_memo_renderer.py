from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageFont

DISPLAY_WIDTH = 400
DISPLAY_HEIGHT = 300
TOP_RECT = (0, 0, 400, 58)
LIST_RECT = (0, 58, 400, 300)
DEFAULT_ROW_COUNT = 5
DEFAULT_FONT_SIZE = 16
TIME_FONT_SIZE = DEFAULT_FONT_SIZE
PLACEHOLDER_PLUS = "+"
TOP_TIME_FONT_SIZE = 42
TOP_DATE_FONT_SIZE = 14
BELL_COL_W = 34
TIME_COL_W = 145
WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


@dataclass
class CellStyle:
    size: int = DEFAULT_FONT_SIZE
    bold: bool = False
    italic: bool = False


@dataclass
class StyledCell:
    text: str = ""
    style: CellStyle = field(default_factory=CellStyle)


@dataclass
class ChecklistRow:
    reminder_enabled: bool = True
    task: StyledCell = field(default_factory=StyledCell)
    due_at: str = ""
    time_style: CellStyle = field(default_factory=lambda: CellStyle(size=TIME_FONT_SIZE))


@dataclass
class FixedMemoState:
    rows: list[ChecklistRow] = field(default_factory=list)
    show_top_border: bool = True
    show_row_separators: bool = True
    page_index: int = 0
    total_pages: int = 1
    wifi_ok: bool = True
    bluetooth_ok: bool = True
    battery_ok: bool = True


def default_fixed_memo_state(row_count: int = DEFAULT_ROW_COUNT) -> FixedMemoState:
    rows: list[ChecklistRow] = []
    samples = [
        (True, "喝水", _future_str(hours=2)),
        (True, "多邻国打卡", _future_str(days=1, hours=8)),
        (True, "提醒明天记得写工作日报", _future_str(days=1, hours=19)),
        (True, "明天早上8点起床", _future_str(days=1, hours=8)),
        (False, "尝试潜水", _future_str(days=2, hours=17)),
    ]
    for idx in range(max(1, row_count)):
        enabled, task, due = samples[idx] if idx < len(samples) else (True, "", "")
        rows.append(
            ChecklistRow(
                reminder_enabled=enabled,
                task=StyledCell(task),
                due_at=due,
                time_style=CellStyle(size=TIME_FONT_SIZE),
            )
        )
    return FixedMemoState(rows=rows, page_index=0, total_pages=1)


def _future_str(days: int = 0, hours: int = 0) -> str:
    dt = datetime.now() + timedelta(days=days, hours=hours)
    return dt.strftime("%Y/%m/%d %H:%M")


def ensure_row_count(state: FixedMemoState, row_count: int) -> None:
    wanted = max(1, int(row_count))
    current = len(state.rows)
    if current == 0:
        state.rows = default_fixed_memo_state(wanted).rows
        return
    if current < wanted:
        for _ in range(current, wanted):
            state.rows.append(ChecklistRow(reminder_enabled=True, task=StyledCell(""), due_at="", time_style=CellStyle(size=TIME_FONT_SIZE)))
    elif current > wanted:
        state.rows = state.rows[:wanted]


def row_boxes(state: FixedMemoState) -> list[dict[str, tuple[int, int, int, int]]]:
    boxes: list[dict[str, tuple[int, int, int, int]]] = []
    x0, y0, x1, y1 = LIST_RECT
    n = max(1, len(state.rows))
    y_positions = [round(y0 + (y1 - y0) * i / n) for i in range(n + 1)]
    for idx in range(n):
        top = y_positions[idx]
        bottom = y_positions[idx + 1]
        bell = (x0, top, x0 + BELL_COL_W, bottom)
        task = (bell[2], top, x1 - TIME_COL_W, bottom)
        due = (task[2], top, x1, bottom)
        boxes.append({"row": (x0, top, x1, bottom), "bell": bell, "task": task, "time": due})
    return boxes


def parse_due(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d %H:%M")
    except Exception:
        return None


def format_due_display(value: str, now: datetime | None = None) -> str:
    dt = parse_due(value)
    if not dt:
        return ""
    return dt.strftime("%m月/%d日 %H:%M")


def due_rows_for_blink(state: FixedMemoState, now: datetime | None = None) -> list[int]:
    now = now or datetime.now()
    due: list[int] = []
    for idx, row in enumerate(state.rows):
        dt = parse_due(row.due_at)
        if row.reminder_enabled and dt and dt <= now:
            due.append(idx)
    return due


def render_fixed_memo_image(state: FixedMemoState, now: datetime | None = None, blink_phase: bool = False) -> Image.Image:
    now = now or datetime.now()
    image = Image.new("L", (DISPLAY_WIDTH, DISPLAY_HEIGHT), 255)
    draw = ImageDraw.Draw(image)

    _draw_top_bar(draw, now, state)
    _draw_checklist(draw, state, now, blink_phase)
    _draw_page_indicator(draw, state)
    return image


def _draw_top_bar(draw: ImageDraw.ImageDraw, now: datetime, state: FixedMemoState) -> None:
    x0, y0, x1, y1 = TOP_RECT
    line_y = y1 - 1
    if state.show_top_border:
        draw.line((x0, line_y, x1, line_y), fill=0, width=1)

    time_text = now.strftime("%H:%M")
    date_text = now.strftime("%m/%d")
    weekday_text = WEEKDAY_NAMES[now.weekday()]

    time_font = _load_font(TOP_TIME_FONT_SIZE, bold=False, italic=False)
    date_font = _load_font(TOP_DATE_FONT_SIZE, bold=False, italic=False)
    _draw_single_line_text_in_box(draw, (58, y0 + 0, 244, y1 - 8), time_text, time_font, padding=0, align="center", valign="middle")
    # Place date + weekday on the border line band.
    _draw_single_line_text_in_box(draw, (276, y1 - 20, 333, y1 - 2), date_text, date_font, padding=0, align="right", valign="middle")
    _draw_single_line_text_in_box(draw, (336, y1 - 20, 392, y1 - 2), weekday_text, date_font, padding=0, align="right", valign="middle")

    _draw_wifi_icon(draw, (302, 8, 320, 23), ok=state.wifi_ok)
    _draw_bt_icon(draw, (325, 7, 340, 24), ok=state.bluetooth_ok)
    _draw_battery_icon(draw, (370, 8, 392, 21), ok=state.battery_ok)


def _draw_checklist(draw: ImageDraw.ImageDraw, state: FixedMemoState, now: datetime, blink_phase: bool) -> None:
    boxes = row_boxes(state)
    blinking_rows = set(due_rows_for_blink(state, now)) if blink_phase else set()
    for idx, row in enumerate(state.rows):
        row_box = boxes[idx]["row"]
        invert = idx in blinking_rows
        if invert:
            draw.rectangle((row_box[0], row_box[1], row_box[2], row_box[3] - 2), fill=0)
        ink = 255 if invert else 0
        is_placeholder = row.task.text.strip() == PLACEHOLDER_PLUS and not row.due_at.strip()
        if row.task.text.strip() or row.due_at.strip() or is_placeholder:
            _draw_bell_icon(draw, boxes[idx]["bell"], row.reminder_enabled, ink=ink)
        task_align = "center" if is_placeholder else "left"
        task_padding = 0 if is_placeholder else 2
        _draw_single_line_text_in_box(draw, boxes[idx]["task"], row.task.text, _load_font_from_style(row.task.style), padding=task_padding, align=task_align, valign="middle", fill=ink)
        time_text = "" if is_placeholder else format_due_display(row.due_at, now)
        _draw_single_line_text_in_box(draw, boxes[idx]["time"], time_text, _load_font_from_style(row.time_style), padding=5, align="right", valign="middle", fill=ink)
        if state.show_row_separators and idx < len(state.rows) - 1:
            y = row_box[3] - 2
            _draw_dashed_line(draw, (36, y, DISPLAY_WIDTH - 10, y), dash=8, gap=4, fill=0)


def _draw_page_indicator(draw: ImageDraw.ImageDraw, state: FixedMemoState) -> None:
    if state.total_pages <= 1:
        return
    font = _load_font(12, bold=False, italic=False)
    text = f"{state.page_index + 1}-{state.total_pages}"
    _draw_single_line_text_in_box(draw, (356, DISPLAY_HEIGHT - 20, 395, DISPLAY_HEIGHT - 2), text, font, padding=0, align="right", valign="middle")


def _draw_dashed_line(draw: ImageDraw.ImageDraw, line: tuple[int, int, int, int], dash: int = 7, gap: int = 4, fill: int = 0) -> None:
    x0, y0, x1, y1 = line
    x = x0
    while x < x1:
        draw.line((x, y0, min(x + dash, x1), y1), fill=fill, width=1)
        x += dash + gap


def _draw_bell_icon(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], enabled: bool, ink: int = 0) -> None:
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    draw.arc((cx - 7, cy - 9, cx + 7, cy + 5), start=200, end=-20, fill=ink, width=2)
    draw.line((cx - 6, cy - 1, cx - 6, cy + 5), fill=ink, width=2)
    draw.line((cx + 6, cy - 1, cx + 6, cy + 5), fill=ink, width=2)
    draw.line((cx - 7, cy + 5, cx + 7, cy + 5), fill=ink, width=2)
    draw.line((cx, cy - 11, cx, cy - 8), fill=ink, width=2)
    draw.ellipse((cx - 2, cy + 6, cx + 2, cy + 10), fill=ink, outline=ink)
    if not enabled:
        draw.line((cx - 9, cy + 9, cx + 10, cy - 10), fill=ink, width=2)


def _draw_wifi_icon(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], ok: bool = True, ink: int = 0) -> None:
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    draw.arc((x0, y0 + 3, x1, y1 + 7), 215, 325, fill=ink, width=2)
    draw.arc((x0 + 3, y0 + 6, x1 - 3, y1 + 4), 220, 320, fill=ink, width=2)
    draw.arc((x0 + 6, y0 + 9, x1 - 6, y1 + 1), 230, 310, fill=ink, width=2)
    draw.ellipse((cx - 1, y1 - 1, cx + 1, y1 + 1), fill=ink, outline=ink)
    if not ok:
        draw.line((x0 + 1, y1 + 1, x1 - 1, y0 + 1), fill=ink, width=2)


def _draw_bt_icon(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], ok: bool = True, ink: int = 0) -> None:
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    draw.line((cx, y0, cx, y1), fill=ink, width=2)
    draw.line((cx, y0, x1 - 1, y0 + 5), fill=ink, width=2)
    draw.line((cx, y0 + 8, x1 - 1, y0 + 3), fill=ink, width=2)
    draw.line((cx, y0 + 8, x1 - 1, y1 - 2), fill=ink, width=2)
    draw.line((cx, y1, x1 - 1, y0 + 11), fill=ink, width=2)
    if not ok:
        draw.line((x0 + 1, y1 - 1, x1 - 1, y0 + 1), fill=ink, width=2)


def _draw_battery_icon(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], ok: bool = True, ink: int = 0) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle((x0, y0 + 1, x1 - 4, y1), outline=ink, width=2)
    draw.rectangle((x1 - 4, y0 + 4, x1 - 1, y1 - 3), outline=ink, width=2)
    if ok:
        draw.rectangle((x0 + 3, y0 + 4, x1 - 8, y1 - 3), fill=ink)
    else:
        draw.line((x0 + 3, y1 - 2, x1 - 8, y0 + 3), fill=ink, width=2)


def _draw_single_line_text_in_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font,
    padding: int = 1,
    align: str = "left",
    valign: str = "middle",
    fill: int = 0,
) -> None:
    x0, y0, x1, y1 = box
    x0 += padding
    y0 += padding
    x1 -= padding
    y1 -= padding
    if x1 <= x0 or y1 <= y0:
        return
    fitted = _fit_single_line(draw, text or "", font, x1 - x0)
    line_w, line_h = _text_size(draw, fitted, font)
    if valign == "middle":
        cursor_y = y0 + max(0, (y1 - y0 - line_h) // 2)
    elif valign == "bottom":
        cursor_y = max(y0, y1 - line_h)
    else:
        cursor_y = y0
    if align == "center":
        cursor_x = x0 + max(0, (x1 - x0 - line_w) // 2)
    elif align == "right":
        cursor_x = max(x0, x1 - line_w)
    else:
        cursor_x = x0
    draw.text((cursor_x, cursor_y), fitted, fill=fill, font=font)


def _fit_single_line(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    text = (text or "").replace("\r", " ").replace("\n", " ")
    if _text_size(draw, text, font)[0] <= max_width:
        return text
    ellipsis = "..."
    if _text_size(draw, ellipsis, font)[0] > max_width:
        return ""
    current = text
    while current and _text_size(draw, current + ellipsis, font)[0] > max_width:
        current = current[:-1]
    return current + ellipsis


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    if text == "":
        text = " "
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _load_font_from_style(style: CellStyle):
    return _load_font(style.size, style.bold, style.italic)


def _load_font(size: int, bold: bool = False, italic: bool = False):
    size = max(8, int(size))
    candidates: list[str] = []
    if bold and italic:
        candidates.extend([
            "C:/Windows/Fonts/msyhbi.ttc",
            "C:/Windows/Fonts/arialbi.ttf",
        ])
    elif bold:
        candidates.extend([
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ])
    elif italic:
        candidates.extend([
            "C:/Windows/Fonts/msyhl.ttc",
            "C:/Windows/Fonts/ariali.ttf",
        ])
    candidates.extend([
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ])
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()
