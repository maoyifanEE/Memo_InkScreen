from __future__ import annotations

from .models import EpdPreset

CURRENT_PROJECT_PRESET = EpdPreset(
    key="good_display_400x300_demo",
    name="当前项目 / Good Display 400x300",
    width=400,
    height=300,
    supported_color_modes=("bw", "gray4"),
    supported_update_modes=("full", "partial"),
    notes=(
        "对应示例里的 EPD_WIDTH=400、EPD_HEIGHT=300。"
        "黑白全屏数组 15000 字节；4 灰全屏数组 30000 字节。"
    ),
)

PRESETS: dict[str, EpdPreset] = {
    CURRENT_PROJECT_PRESET.key: CURRENT_PROJECT_PRESET,
}


def get_preset(key: str) -> EpdPreset:
    if key not in PRESETS:
        raise KeyError(f"未知预设: {key}")
    return PRESETS[key]
