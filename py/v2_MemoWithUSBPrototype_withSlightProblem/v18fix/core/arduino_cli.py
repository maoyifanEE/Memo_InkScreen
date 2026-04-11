from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CliRunResult:
    ok: bool
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined_output(self) -> str:
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append(self.stderr.strip())
        return "\n".join(parts).strip()


@dataclass
class BoardPortInfo:
    port: str
    board_name: str = ""
    fqbn: str = ""
    protocol: str = ""

    @property
    def display_text(self) -> str:
        details = []
        if self.board_name:
            details.append(self.board_name)
        if self.fqbn:
            details.append(self.fqbn)
        if self.protocol:
            details.append(self.protocol)
        detail_text = " | ".join(details)
        return f"{self.port} | {detail_text}" if detail_text else self.port


@dataclass
class FlashWorkflowResult:
    ok: bool
    sketch_dir: str
    compile_result: CliRunResult
    upload_result: CliRunResult | None

    @property
    def combined_output(self) -> str:
        lines = [f"临时工程目录: {self.sketch_dir}"]
        lines.append("\n=== compile ===")
        lines.append(self.compile_result.combined_output or "(no output)")
        if self.upload_result is not None:
            lines.append("\n=== upload ===")
            lines.append(self.upload_result.combined_output or "(no output)")
        return "\n".join(lines)


def guess_arduino_cli_path() -> str:
    candidates: list[str] = []
    which = shutil.which("arduino-cli") or shutil.which("arduino-cli.exe")
    if which:
        candidates.append(which)

    local = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("ProgramFiles")
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    home = str(Path.home())

    for base in (local, program_files, program_files_x86, home):
        if not base:
            continue
        candidates.extend(
            [
                os.path.join(base, "Programs", "Arduino IDE", "resources", "app", "lib", "backend", "resources", "arduino-cli.exe"),
                os.path.join(base, "Arduino IDE", "resources", "app", "lib", "backend", "resources", "arduino-cli.exe"),
                os.path.join(base, "Arduino CLI", "arduino-cli.exe"),
                os.path.join(base, "arduino-cli", "arduino-cli.exe"),
                os.path.join(base, "Arduino15", "arduino-cli.exe"),
            ]
        )

    for path in candidates:
        if path and Path(path).exists():
            return str(Path(path))

    focused_roots = []
    for base in (local, program_files, program_files_x86):
        if base:
            focused_roots.append(Path(base) / "Programs" / "Arduino IDE")
            focused_roots.append(Path(base) / "Arduino IDE")

    for root in focused_roots:
        if not root.exists():
            continue
        try:
            for found in root.rglob("arduino-cli.exe"):
                return str(found)
        except Exception:
            continue
    return ""


def run_cli(command: list[str], cwd: str | None = None, timeout: int = 600) -> CliRunResult:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    return CliRunResult(
        ok=completed.returncode == 0,
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def check_cli_available(cli_path: str) -> CliRunResult:
    if not cli_path:
        raise FileNotFoundError("未设置 arduino-cli 路径。")
    if not Path(cli_path).exists():
        raise FileNotFoundError(f"arduino-cli 不存在：{cli_path}")
    return run_cli([cli_path, "version"], timeout=30)


def _sanitize_port_text(raw: str) -> str:
    raw = str(raw or "").strip()
    if not raw:
        return ""

    if raw.startswith("{") and "COM" in raw.upper():
        match = re.search(r"['\"]address['\"]\s*:\s*['\"]([^'\"]+)['\"]", raw, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()

    raw = raw.split("|")[0].strip()

    match = re.search(r"\bCOM\d+\b", raw, flags=re.IGNORECASE)
    if match:
        return match.group(0).upper()

    match = re.search(r"(/dev/(?:tty|cu)\S+)", raw)
    if match:
        return match.group(1)

    return raw


def list_board_ports(cli_path: str) -> list[BoardPortInfo]:
    check = check_cli_available(cli_path)
    if not check.ok:
        raise RuntimeError(check.combined_output or "arduino-cli 无法运行。")

    result = run_cli([cli_path, "board", "list", "--json"], timeout=30)
    boards: list[BoardPortInfo] = []

    if result.ok:
        boards.extend(_parse_board_list_json(result.stdout))
        if boards:
            return boards

    fallback = run_cli([cli_path, "board", "list"], timeout=30)
    if not fallback.ok:
        raise RuntimeError(fallback.combined_output or "端口扫描失败。")
    return _parse_board_list_text(fallback.stdout)


def _parse_board_list_json(text: str) -> list[BoardPortInfo]:
    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    items: list[dict] = []
    if isinstance(data, list):
        items = [x for x in data if isinstance(x, dict)]
    elif isinstance(data, dict):
        if isinstance(data.get("detected_ports"), list):
            items = [x for x in data["detected_ports"] if isinstance(x, dict)]
        elif isinstance(data.get("ports"), list):
            items = [x for x in data["ports"] if isinstance(x, dict)]

    boards: list[BoardPortInfo] = []
    for item in items:
        port = ""
        board_name = ""
        fqbn = ""
        protocol = str(item.get("protocol") or "").strip()

        port_field = item.get("port")
        if isinstance(port_field, dict):
            port = str(port_field.get("address") or port_field.get("label") or port_field.get("name") or "").strip()
            protocol = protocol or str(port_field.get("protocol") or port_field.get("protocol_label") or "").strip()
        else:
            port = str(port_field or item.get("address") or item.get("name") or "").strip()

        if isinstance(item.get("matching_boards"), list) and item["matching_boards"]:
            board = item["matching_boards"][0]
            if isinstance(board, dict):
                board_name = str(board.get("name") or "").strip()
                fqbn = str(board.get("fqbn") or "").strip()

        port = _sanitize_port_text(port)
        if port:
            boards.append(BoardPortInfo(port=port, board_name=board_name, fqbn=fqbn, protocol=protocol))
    return boards


def _parse_board_list_text(text: str) -> list[BoardPortInfo]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    boards: list[BoardPortInfo] = []
    for line in lines:
        if line.lower().startswith("port"):
            continue
        parts = [part for part in line.split() if part]
        if not parts:
            continue
        port = _sanitize_port_text(parts[0])
        if port:
            boards.append(BoardPortInfo(port=port))
    return boards


def compile_and_upload(
    cli_path: str,
    sketch_dir: str,
    fqbn: str,
    port: str,
    verify_after_upload: bool = False,
) -> FlashWorkflowResult:
    compile_cmd = [cli_path, "compile", "--fqbn", fqbn, str(sketch_dir), "--export-binaries", "--clean"]
    compile_result = run_cli(compile_cmd, cwd=sketch_dir, timeout=1200)
    if not compile_result.ok:
        return FlashWorkflowResult(False, str(sketch_dir), compile_result, None)

    upload_cmd = [cli_path, "upload", "-p", _sanitize_port_text(port), "--fqbn", fqbn, str(sketch_dir)]
    if verify_after_upload:
        upload_cmd.append("--verify")
    upload_result = run_cli(upload_cmd, cwd=sketch_dir, timeout=1200)
    ok = compile_result.ok and upload_result.ok
    return FlashWorkflowResult(ok, str(sketch_dir), compile_result, upload_result)
