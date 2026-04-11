from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

BlockKind = Literal["text", "weather", "memo"]


@dataclass
class TextStyle:
    size: int = 18
    bold: bool = False
    italic: bool = False


@dataclass
class MemoRow:
    task: str
    event_time: str
    remind_time: str


@dataclass
class MemoBlock:
    block_id: str
    kind: BlockKind
    col: int
    row: int
    colspan: int
    rowspan: int
    hidden_border: bool = False
    style: TextStyle = field(default_factory=TextStyle)
    text: str = ""
    title: str = ""
    temperature: str = "23°C"
    weather_text: str = "晴 / 待接入"
    header_style: TextStyle = field(default_factory=lambda: TextStyle(size=12, bold=True, italic=False))
    row_style: TextStyle = field(default_factory=lambda: TextStyle(size=12, bold=False, italic=False))
    memo_rows: list[MemoRow] = field(default_factory=list)


@dataclass
class MemoSheetState:
    grid_cols: int = 12
    grid_rows: int = 8
    show_grid: bool = True
    col_widths: list[int] = field(default_factory=lambda: [80] * 12)
    row_heights: list[int] = field(default_factory=lambda: [56] * 8)
    blocks: list[MemoBlock] = field(default_factory=list)


@dataclass
class LayoutMetrics:
    x_positions: list[int]
    y_positions: list[int]
    cell_boxes: dict[tuple[int, int], tuple[int, int, int, int]]



def default_sheet_state() -> MemoSheetState:
    cols = 12
    rows = 8
    return MemoSheetState(
        grid_cols=cols,
        grid_rows=rows,
        show_grid=True,
        col_widths=[80] * cols,
        row_heights=[50] + [60] * (rows - 1),
        blocks=[
            MemoBlock(
                block_id="signature_1",
                kind="text",
                col=0,
                row=0,
                colspan=12,
                rowspan=1,
                style=TextStyle(size=20, bold=True, italic=False),
                text="今天也要稳稳推进。",
            ),
            MemoBlock(
                block_id="weather_1",
                kind="weather",
                col=9,
                row=1,
                colspan=3,
                rowspan=2,
                hidden_border=False,
                style=TextStyle(size=16, bold=True, italic=False),
                title="天气",
                temperature="23°C",
                weather_text="晴 / 待接入",
            ),
            MemoBlock(
                block_id="memo_1",
                kind="memo",
                col=0,
                row=1,
                colspan=9,
                rowspan=7,
                hidden_border=False,
                style=TextStyle(size=18, bold=True, italic=False),
                title="备忘录",
                header_style=TextStyle(size=12, bold=True, italic=False),
                row_style=TextStyle(size=12, bold=False, italic=False),
                memo_rows=[
                    MemoRow("例会准备", "2026/04/08 09:30", "2026/04/08 09:00"),
                    MemoRow("给供应商回邮件", "2026/04/08 14:00", "2026/04/08 13:30"),
                    MemoRow("下班前整理桌面", "2026/04/08 18:00", "2026/04/08 17:40"),
                ],
            ),
        ],
    )



def compute_layout_metrics(state: MemoSheetState, width: int, height: int) -> LayoutMetrics:
    cols = max(1, state.grid_cols)
    rows = max(1, state.grid_rows)
    col_units = _normalize_sizes(state.col_widths, cols, 60)
    row_units = _normalize_sizes(state.row_heights, rows, 40)

    x_positions = [0]
    y_positions = [0]
    total_col_units = sum(col_units) or 1
    total_row_units = sum(row_units) or 1

    running = 0
    for unit in col_units:
        running += unit
        x_positions.append(round(width * running / total_col_units))
    running = 0
    for unit in row_units:
        running += unit
        y_positions.append(round(height * running / total_row_units))

    x_positions[-1] = width
    y_positions[-1] = height

    cell_boxes: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    for r in range(rows):
        for c in range(cols):
            cell_boxes[(c, r)] = (x_positions[c], y_positions[r], x_positions[c + 1], y_positions[r + 1])
    return LayoutMetrics(x_positions=x_positions, y_positions=y_positions, cell_boxes=cell_boxes)



def block_box(metrics: LayoutMetrics, block: MemoBlock, cols: int, rows: int) -> tuple[int, int, int, int]:
    col = max(0, min(cols - 1, block.col))
    row = max(0, min(rows - 1, block.row))
    end_col = max(col + 1, min(cols, col + max(1, block.colspan)))
    end_row = max(row + 1, min(rows, row + max(1, block.rowspan)))
    return (
        metrics.x_positions[col],
        metrics.y_positions[row],
        metrics.x_positions[end_col],
        metrics.y_positions[end_row],
    )



def render_sheet_image(
    state: MemoSheetState,
    width: int,
    height: int,
    selected_block_id: str | None = None,
    for_editor: bool = False,
) -> Image.Image:
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    metrics = compute_layout_metrics(state, width, height)

    if state.show_grid:
        _draw_grid(draw, metrics, width, height)

    for block in state.blocks:
        box = block_box(metrics, block, state.grid_cols, state.grid_rows)
        is_selected = for_editor and block.block_id == selected_block_id
        _draw_block(draw, box, block, is_selected=is_selected)

    return image



def _draw_grid(draw: ImageDraw.ImageDraw, metrics: LayoutMetrics, width: int, height: int) -> None:
    grid_color = 210
    for x in metrics.x_positions[1:-1]:
        draw.line((x, 0, x, height), fill=grid_color, width=1)
    for y in metrics.y_positions[1:-1]:
        draw.line((0, y, width, y), fill=grid_color, width=1)



def _draw_block(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], block: MemoBlock, is_selected: bool = False) -> None:
    x0, y0, x1, y1 = box
    if not block.hidden_border:
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=0, width=2 if is_selected else 1)
    elif is_selected:
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=90, width=2)

    if is_selected:
        handle = 8
        draw.rectangle((x1 - handle - 2, y1 - handle - 2, x1 - 2, y1 - 2), fill=0)

    if block.kind == "text":
        _draw_text_block(draw, box, block)
    elif block.kind == "weather":
        _draw_weather_block(draw, box, block)
    elif block.kind == "memo":
        _draw_memo_block(draw, box, block)



def _draw_text_block(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], block: MemoBlock) -> None:
    font = _load_font_from_style(block.style)
    _draw_text_in_box(
        draw,
        box,
        block.text.strip() or "双击这里输入内容",
        font,
        padding=8,
        align="center",
        valign="middle",
    )



def _draw_weather_block(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], block: MemoBlock) -> None:
    x0, y0, x1, y1 = box
    title_font = _load_font(max(11, block.style.size - 4), bold=True, italic=False)
    temp_font = _load_font(max(14, block.style.size + 6), bold=block.style.bold, italic=block.style.italic)
    text_font = _load_font_from_style(block.style)
    pad = 6
    inner = (x0 + pad, y0 + pad, x1 - pad, y1 - pad)
    draw.text((inner[0], inner[1]), block.title.strip() or "天气", fill=0, font=title_font)
    title_h = _text_size(draw, block.title.strip() or "天气", title_font)[1]
    temp_y = inner[1] + title_h + 4
    draw.text((inner[0], temp_y), block.temperature.strip() or "--°C", fill=0, font=temp_font)
    temp_h = _text_size(draw, block.temperature.strip() or "--°C", temp_font)[1]
    _draw_text_in_box(
        draw,
        (inner[0], temp_y + temp_h + 4, inner[2], inner[3]),
        block.weather_text.strip() or "待接入天气数据",
        text_font,
        padding=0,
        align="left",
        valign="top",
    )



def _draw_memo_block(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], block: MemoBlock) -> None:
    x0, y0, x1, y1 = box
    pad = 6
    inner_x0 = x0 + pad
    inner_y0 = y0 + pad
    inner_x1 = x1 - pad
    inner_y1 = y1 - pad

    title_font = _load_font_from_style(block.style)
    header_font = _load_font_from_style(block.header_style)
    row_font = _load_font_from_style(block.row_style)

    title_text = block.title.strip() or "备忘录"
    draw.text((inner_x0, inner_y0), title_text, fill=0, font=title_font)
    title_h = _text_size(draw, title_text, title_font)[1]

    table_y0 = inner_y0 + title_h + 6
    if table_y0 >= inner_y1 - 12:
        return

    headers = ["事情", "发生时间", "提醒时间"]
    col_fracs = (0.42, 0.29, 0.29)
    total_w = inner_x1 - inner_x0
    col_x = [inner_x0]
    running = inner_x0
    for frac in col_fracs[:-1]:
        running += round(total_w * frac)
        col_x.append(running)
    col_x.append(inner_x1)

    header_h = max(20, _text_size(draw, "事情", header_font)[1] + 8)
    draw.rectangle((inner_x0, table_y0, inner_x1, table_y0 + header_h), outline=0, width=1)
    for boundary in col_x[1:-1]:
        draw.line((boundary, table_y0, boundary, inner_y1), fill=0, width=1)

    for idx, label in enumerate(headers):
        _draw_text_in_box(
            draw,
            (col_x[idx] + 3, table_y0 + 2, col_x[idx + 1] - 3, table_y0 + header_h - 2),
            label,
            header_font,
            padding=0,
            align="center",
            valign="middle",
        )

    rows = list(block.memo_rows)
    if not rows:
        rows = [MemoRow(task="（待填写）", event_time="xxxx/xx/xx xx:xx", remind_time="xxxx/xx/xx xx:xx")]

    available_h = max(20, inner_y1 - (table_y0 + header_h))
    row_count = max(1, min(len(rows), max(1, available_h // max(18, _text_size(draw, "样例", row_font)[1] + 8))))
    row_h = max(18, available_h // row_count)

    start_y = table_y0 + header_h
    for row_index in range(row_count):
        row = rows[row_index]
        top = start_y + row_index * row_h
        bottom = min(inner_y1, top + row_h)
        draw.line((inner_x0, bottom, inner_x1, bottom), fill=0, width=1)
        values = [row.task, row.event_time, row.remind_time]
        for col in range(3):
            _draw_text_in_box(
                draw,
                (col_x[col] + 3, top + 2, col_x[col + 1] - 3, bottom - 2),
                values[col] or "-",
                row_font,
                padding=0,
                align="left" if col == 0 else "center",
                valign="middle",
            )



def _normalize_sizes(values: list[int], count: int, fallback: int) -> list[int]:
    src = [max(20, int(v)) for v in values[:count]]
    while len(src) < count:
        src.append(fallback)
    return src



def _draw_text_in_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font,
    padding: int = 4,
    align: str = "left",
    valign: str = "top",
) -> None:
    x0, y0, x1, y1 = box
    x0 += padding
    y0 += padding
    x1 -= padding
    y1 -= padding
    if x1 <= x0 or y1 <= y0:
        return

    lines = _wrap_text(draw, text, font, max_width=x1 - x0)
    if not lines:
        return

    line_heights = [_text_size(draw, line, font)[1] for line in lines]
    total_h = sum(line_heights) + max(0, len(lines) - 1) * 2

    if valign == "middle":
        cursor_y = y0 + max(0, (y1 - y0 - total_h) // 2)
    elif valign == "bottom":
        cursor_y = max(y0, y1 - total_h)
    else:
        cursor_y = y0

    for line, line_h in zip(lines, line_heights):
        line_w = _text_size(draw, line, font)[0]
        if align == "center":
            cursor_x = x0 + max(0, (x1 - x0 - line_w) // 2)
        elif align == "right":
            cursor_x = max(x0, x1 - line_w)
        else:
            cursor_x = x0
        draw.text((cursor_x, cursor_y), line, fill=0, font=font)
        cursor_y += line_h + 2
        if cursor_y > y1:
            break



def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    source_lines = text.split("\n")
    wrapped: list[str] = []
    for raw_line in source_lines:
        if not raw_line:
            wrapped.append("")
            continue
        current = ""
        for ch in raw_line:
            candidate = current + ch
            if _text_size(draw, candidate, font)[0] <= max_width or not current:
                current = candidate
            else:
                wrapped.append(current)
                current = ch
        if current:
            wrapped.append(current)
    return wrapped



def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    if not text:
        text = " "
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]



def _load_font_from_style(style: TextStyle):
    return _load_font(style.size, bold=style.bold, italic=style.italic)



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
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ])
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()
