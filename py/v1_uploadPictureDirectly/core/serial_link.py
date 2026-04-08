from __future__ import annotations

import time
from dataclasses import dataclass

from .models import ConversionResult

SERIAL_PROTOCOL_VERSION = "EPD_SERIAL_V1"
DEFAULT_BAUDRATE = 115200


@dataclass
class SerialSendResult:
    ok: bool
    port: str
    baudrate: int
    log_text: str


def _checksum32(data: list[int]) -> int:
    return sum(data) & 0xFFFFFFFF


def _build_header_line(result: ConversionResult) -> str:
    region = result.partial_region_applied
    if region is None:
        x = 0
        y = 0
        width = result.preset.width
        height = result.preset.height
    else:
        x = region.x
        y = region.y
        width = region.width
        height = region.height

    return (
        f"SEND {result.options.color_mode} {result.options.update_mode} "
        f"{result.preset.width} {result.preset.height} "
        f"{x} {y} {width} {height} {len(result.exported_bytes)} {_checksum32(result.exported_bytes)}\n"
    )


def _prepare_serial_port(ser) -> None:
    """Try to avoid auto-reset and give the ESP32 time to boot."""
    for attr_name, value in (("dtr", False), ("rts", False)):
        try:
            setattr(ser, attr_name, value)
        except Exception:
            pass

    time.sleep(0.25)

    try:
        ser.reset_output_buffer()
    except Exception:
        pass


def _wait_for_handshake(ser, log_lines: list[str]) -> str:
    # The ESP32 prints one PONG in setup(), and also answers PING in loop().
    # Keep both paths alive: first listen briefly for a spontaneous boot message,
    # then actively ping several times.
    boot_line = _read_until_meaningful_line(ser, timeout_s=1.8)
    if boot_line:
        log_lines.append(f"启动输出: {boot_line}")
        if boot_line.startswith("PONG") and SERIAL_PROTOCOL_VERSION in boot_line:
            return boot_line

    last = boot_line
    for _ in range(8):
        try:
            ser.write(b"PING\n")
            ser.flush()
        except Exception:
            pass
        last = _read_until_meaningful_line(ser, timeout_s=1.2)
        if last and last.startswith("PONG") and SERIAL_PROTOCOL_VERSION in last:
            return last
    return last


def send_result_to_device(port: str, result: ConversionResult, baudrate: int = DEFAULT_BAUDRATE) -> SerialSendResult:
    try:
        import serial  # type: ignore
    except Exception as exc:
        raise RuntimeError("缺少 pyserial，请先执行：pip install pyserial") from exc

    log_lines: list[str] = [f"串口: {port}", f"波特率: {baudrate}"]
    header_line = _build_header_line(result)
    payload = bytes(result.exported_bytes)

    with serial.Serial(port=port, baudrate=baudrate, timeout=0.3, write_timeout=10) as ser:
        _prepare_serial_port(ser)

        pong = _wait_for_handshake(ser, log_lines)
        if "PONG" not in pong:
            raise RuntimeError(f"设备未返回握手信息。收到: {pong or '(empty)'}")
        log_lines.append(f"握手: {pong}")

        ser.write(header_line.encode("utf-8"))
        ser.flush()
        ready = _read_until_meaningful_line(ser, timeout_s=3.0)
        if "READY" not in ready:
            raise RuntimeError(f"设备未进入接收状态。收到: {ready or '(empty)'}")
        log_lines.append(f"设备响应: {ready}")

        ser.write(payload)
        ser.flush()
        final_line = _read_until_meaningful_line(ser, timeout_s=20.0)
        if final_line.startswith("OK"):
            log_lines.append(f"发送结果: {final_line}")
            return SerialSendResult(True, port, baudrate, "\n".join(log_lines))
        raise RuntimeError(f"设备返回错误: {final_line or '(empty)'}")


def _read_until_meaningful_line(ser, timeout_s: float) -> str:
    deadline = time.time() + timeout_s
    cache: list[str] = []
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        cache.append(text)
        if text.startswith(("PONG", "READY", "OK", "ERR")):
            return text
    return cache[-1] if cache else ""
