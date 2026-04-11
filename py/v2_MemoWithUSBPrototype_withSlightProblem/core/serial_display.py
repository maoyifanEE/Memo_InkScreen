from __future__ import annotations

import struct
import time
from dataclasses import dataclass

import serial

from .models import ConversionResult

MAGIC = b"EPD1"
MODE_BW_FULL = 0
MODE_BW_PARTIAL = 1
MODE_GRAY4_FULL = 2


@dataclass
class SerialSendResult:
    ok: bool
    log: str


def build_packet(result: ConversionResult) -> bytes:
    if result.options.color_mode == "gray4":
        mode = MODE_GRAY4_FULL
        x = 0
        y = 0
        w = result.preset.width
        h = result.preset.height
    elif result.options.update_mode == "partial":
        region = result.partial_region_applied
        if region is None:
            raise ValueError("partial mode requires partial region")
        mode = MODE_BW_PARTIAL
        x, y, w, h = region.x, region.y, region.width, region.height
    else:
        mode = MODE_BW_FULL
        x = 0
        y = 0
        w = result.preset.width
        h = result.preset.height

    payload = bytes(result.exported_bytes)
    header = struct.pack(
        "<4sBBHHHHHHI",
        MAGIC,
        1,
        mode,
        result.preset.width,
        result.preset.height,
        x,
        y,
        w,
        h,
        len(payload),
    )
    return header + payload


def send_display_update(result: ConversionResult, port: str, baudrate: int = 921600, timeout: float = 8.0) -> SerialSendResult:
    packet = build_packet(result)
    logs = [f"打开串口: {port} @ {baudrate}", f"发送字节数: {len(packet)}"]

    ser = None
    try:
        ser = serial.Serial(port=port, baudrate=baudrate, timeout=0.2, write_timeout=5)
        time.sleep(1.8)  # ESP32 经常在打开串口时复位
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        ser.write(packet)
        ser.flush()
        logs.append("数据已发送，等待设备确认...")

        deadline = time.time() + timeout
        while time.time() < deadline:
            line = ser.readline()
            if not line:
                continue
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                logs.append(text)
            if "EPD:OK" in text:
                return SerialSendResult(True, "\n".join(logs))
            if "EPD:ERR" in text:
                return SerialSendResult(False, "\n".join(logs))

        logs.append("等待设备确认超时。")
        return SerialSendResult(False, "\n".join(logs))
    except Exception as exc:
        logs.append(f"串口发送失败: {exc}")
        return SerialSendResult(False, "\n".join(logs))
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
