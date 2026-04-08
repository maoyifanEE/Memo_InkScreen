from __future__ import annotations

import os
import re
from typing import Iterable


def sanitize_variable_name(name: str) -> str:
    raw = (name or "gImage_custom").strip()
    if not raw:
        raw = "gImage_custom"
    raw = raw.replace(" ", "_")
    raw = re.sub(r"[^0-9A-Za-z_]", "_", raw)
    if raw[0].isdigit():
        raw = f"gImage_{raw}"
    return raw


def hex_upper(value: int) -> str:
    return f"0X{value:02X}"


def chunked(values: list[int], size: int) -> list[list[int]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def build_c_array_text(
    variable_name: str,
    values: list[int],
    bytes_per_line: int = 16,
    comment: str = "",
) -> str:
    var = sanitize_variable_name(variable_name)
    lines: list[str] = []
    if comment:
        lines.append(f"/* {comment} */")
    lines.append(f"const unsigned char {var}[{len(values)}] = {{")
    for group in chunked(values, bytes_per_line):
        lines.append("    " + ", ".join(hex_upper(v) for v in group) + ",")
    lines.append("};")
    return "\n".join(lines)


def build_gray4_debug_text(variable_name: str, plane24: list[int], plane26: list[int]) -> str:
    var = sanitize_variable_name(variable_name)
    return (
        build_c_array_text(f"{var}_plane24", plane24, comment="4-gray debug plane for command 0x24")
        + "\n\n"
        + build_c_array_text(f"{var}_plane26", plane26, comment="4-gray debug plane for command 0x26")
    )


def default_output_basename(image_path: str, variable_name: str) -> str:
    base = os.path.splitext(os.path.basename(image_path))[0] if image_path else variable_name
    return sanitize_variable_name(base)
