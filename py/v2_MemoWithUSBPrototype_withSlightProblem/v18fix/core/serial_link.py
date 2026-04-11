from __future__ import annotations

import threading
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
    reused_connection: bool = False
    reboot_detected: bool = False


class _SerialSession:
    def __init__(self, port: str, baudrate: int) -> None:
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.lock = threading.Lock()

    def ensure_open(self, log_lines: list[str]):
        try:
            import serial  # type: ignore
        except Exception as exc:
            raise RuntimeError("缺少 pyserial，请先执行：pip install pyserial") from exc

        reused = self.ser is not None and bool(getattr(self.ser, "is_open", False))
        if reused:
            return self.ser, True

        ser = serial.Serial()
        ser.port = self.port
        ser.baudrate = self.baudrate
        ser.timeout = 0.3
        ser.write_timeout = 10
        ser.dsrdtr = False
        ser.rtscts = False
        try:
            ser.dtr = False
            ser.rts = False
        except Exception:
            pass
        ser.open()
        try:
            ser.reset_output_buffer()
        except Exception:
            pass
        self.ser = ser
        log_lines.append("串口会话: 新建连接")
        return ser, False

    def close(self) -> None:
        ser = self.ser
        self.ser = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass


_sessions: dict[tuple[str, int], _SerialSession] = {}
_sessions_lock = threading.Lock()


def _get_session(port: str, baudrate: int) -> _SerialSession:
    key = (port, baudrate)
    with _sessions_lock:
        session = _sessions.get(key)
        if session is None:
            session = _SerialSession(port, baudrate)
            _sessions[key] = session
        return session


def close_all_serial_sessions() -> None:
    with _sessions_lock:
        sessions = list(_sessions.values())
        _sessions.clear()
    for session in sessions:
        session.close()


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


def _read_until_meaningful_line(ser, timeout_s: float, log_lines: list[str] | None = None, label: str = "串口") -> str:
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
        if log_lines is not None:
            log_lines.append(f"{label}: {text}")
        if text.startswith(("PONG", "READY", "OK", "ERR")):
            return text
    return cache[-1] if cache else ""


def _wait_for_handshake(ser, log_lines: list[str], allow_boot_log: bool) -> tuple[str, bool]:
    reboot_detected = False
    if allow_boot_log:
        boot_line = _read_until_meaningful_line(ser, timeout_s=1.8, log_lines=log_lines, label="启动输出")
        if boot_line:
            reboot_detected = True
        if boot_line and boot_line.startswith("PONG") and SERIAL_PROTOCOL_VERSION in boot_line:
            return boot_line, reboot_detected

    last = ""
    for _ in range(6):
        try:
            ser.write(b"PING\n")
            ser.flush()
        except Exception:
            pass
        last = _read_until_meaningful_line(ser, timeout_s=1.0, log_lines=log_lines, label="PING返回")
        if last and last.startswith("PONG") and SERIAL_PROTOCOL_VERSION in last:
            return last, reboot_detected
    return last, reboot_detected


def send_result_to_device(port: str, result: ConversionResult, baudrate: int = DEFAULT_BAUDRATE) -> SerialSendResult:
    log_lines: list[str] = [f"串口: {port}", f"波特率: {baudrate}"]
    header_line = _build_header_line(result)
    payload = bytes(result.exported_bytes)

    session = _get_session(port, baudrate)
    with session.lock:
        ser, reused = session.ensure_open(log_lines)
        if reused:
            log_lines.append("串口会话: 复用已有连接")

        try:
            pong, reboot_detected = _wait_for_handshake(ser, log_lines, allow_boot_log=not reused)
            if "PONG" not in pong:
                raise RuntimeError(f"设备未返回握手信息。收到: {pong or '(empty)'}")
            log_lines.append(f"握手: {pong}")
            if reboot_detected:
                log_lines.append("提示: 本次串口连接检测到设备刚启动/重启。")

            log_lines.append(f"发送头: {header_line.strip()}")
            ser.write(header_line.encode("utf-8"))
            ser.flush()
            ready = _read_until_meaningful_line(ser, timeout_s=3.0, log_lines=log_lines, label="READY阶段")
            if "READY" not in ready:
                raise RuntimeError(f"设备未进入接收状态。收到: {ready or '(empty)'}")
            log_lines.append(f"设备响应: {ready}")

            ser.write(payload)
            ser.flush()
            final_line = _read_until_meaningful_line(ser, timeout_s=20.0, log_lines=log_lines, label="最终返回")
            if final_line.startswith("OK"):
                log_lines.append(f"发送结果: {final_line}")
                return SerialSendResult(
                    True,
                    port,
                    baudrate,
                    "\n".join(log_lines),
                    reused_connection=reused,
                    reboot_detected=reboot_detected,
                )
            raise RuntimeError(f"设备返回错误: {final_line or '(empty)'}")
        except Exception:
            session.close()
            raise
