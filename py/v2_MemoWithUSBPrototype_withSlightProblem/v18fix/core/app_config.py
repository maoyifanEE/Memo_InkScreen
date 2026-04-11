from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


CONFIG_FILE_NAME = "user_settings.json"


@dataclass
class AppConfig:
    arduino_cli_path: str = ""
    esp32_project_dir: str = ""
    fqbn: str = "esp32:esp32:esp32"
    serial_port: str = ""
    temp_build_root: str = ""
    serial_baud: str = "115200"


def get_default_config_path(app_root: Path) -> Path:
    return app_root / CONFIG_FILE_NAME


def build_default_config(app_root: Path) -> AppConfig:
    guessed_project = (app_root.parent.parent / "esp32").resolve()
    guessed_temp = (app_root / "generated").resolve()
    config = AppConfig(
        esp32_project_dir=str(guessed_project) if guessed_project.exists() else "",
        temp_build_root=str(guessed_temp),
    )
    return config


def load_config(app_root: Path) -> AppConfig:
    config_path = get_default_config_path(app_root)
    default = build_default_config(app_root)
    if not config_path.exists():
        return default

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return default

    merged = asdict(default)
    merged.update({k: v for k, v in data.items() if k in merged and isinstance(v, str)})
    return AppConfig(**merged)


def save_config(app_root: Path, config: AppConfig) -> Path:
    config_path = get_default_config_path(app_root)
    config_path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path
