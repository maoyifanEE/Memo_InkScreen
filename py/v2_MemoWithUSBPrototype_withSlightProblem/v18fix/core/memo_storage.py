from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .fixed_memo_renderer import ChecklistRow, CellStyle, StyledCell


def _db_path(app_root: Path) -> Path:
    return app_root / "memo_data.sqlite3"


def init_db(app_root: Path) -> Path:
    path = _db_path(app_root)
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memo_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memo_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sort_order INTEGER NOT NULL,
                reminder_enabled INTEGER NOT NULL,
                task_text TEXT NOT NULL,
                due_at TEXT NOT NULL,
                task_style_json TEXT NOT NULL,
                time_style_json TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    return path


def load_state(app_root: Path) -> tuple[list[ChecklistRow], dict[str, object]]:
    path = init_db(app_root)
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        meta = {
            "page_size": 5,
            "current_page": 0,
            "wifi_ok": True,
            "bluetooth_ok": True,
            "battery_ok": True,
        }
        for key, value in cur.execute("SELECT key, value FROM memo_meta"):
            try:
                meta[key] = json.loads(value)
            except Exception:
                meta[key] = value
        items: list[ChecklistRow] = []
        rows = cur.execute(
            "SELECT reminder_enabled, task_text, due_at, task_style_json, time_style_json FROM memo_items ORDER BY sort_order, id"
        ).fetchall()
        for reminder_enabled, task_text, due_at, task_style_json, time_style_json in rows:
            items.append(
                ChecklistRow(
                    reminder_enabled=bool(reminder_enabled),
                    task=StyledCell(task_text, _style_from_json(task_style_json)),
                    due_at=due_at,
                    time_style=_style_from_json(time_style_json),
                )
            )
        return items, meta
    finally:
        conn.close()


def save_state(app_root: Path, items: list[ChecklistRow], meta: dict[str, object]) -> Path:
    path = init_db(app_root)
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM memo_items")
        for sort_order, row in enumerate(items):
            cur.execute(
                """
                INSERT INTO memo_items(sort_order, reminder_enabled, task_text, due_at, task_style_json, time_style_json)
                VALUES(?,?,?,?,?,?)
                """,
                (
                    sort_order,
                    1 if row.reminder_enabled else 0,
                    row.task.text,
                    row.due_at,
                    _style_to_json(row.task.style),
                    _style_to_json(row.time_style),
                ),
            )
        cur.execute("DELETE FROM memo_meta")
        for key, value in meta.items():
            cur.execute("INSERT INTO memo_meta(key, value) VALUES(?, ?)", (key, json.dumps(value, ensure_ascii=False)))
        conn.commit()
        return path
    finally:
        conn.close()


def _style_to_json(style: CellStyle) -> str:
    return json.dumps({"size": style.size, "bold": style.bold, "italic": style.italic}, ensure_ascii=False)


def _style_from_json(raw: str) -> CellStyle:
    try:
        data = json.loads(raw)
        return CellStyle(size=int(data.get("size", 16)), bold=bool(data.get("bold", False)), italic=bool(data.get("italic", False)))
    except Exception:
        return CellStyle()
