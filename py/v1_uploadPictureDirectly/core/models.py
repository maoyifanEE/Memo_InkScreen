from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

ColorMode = Literal["bw", "gray4"]
UpdateMode = Literal["full", "partial"]
FitMode = Literal["contain", "cover", "stretch"]
RotationMode = Literal[0, 90, 180, 270]
DitherMode = Literal["none", "floyd"]


@dataclass(frozen=True)
class EpdPreset:
    key: str
    name: str
    width: int
    height: int
    supported_color_modes: tuple[ColorMode, ...]
    supported_update_modes: tuple[UpdateMode, ...]
    notes: str = ""

    @property
    def bw_bytes(self) -> int:
        return self.width * self.height // 8

    @property
    def gray4_bytes(self) -> int:
        return self.width * self.height // 4


@dataclass
class PartialRegion:
    x: int = 0
    y: int = 0
    width: int = 128
    height: int = 64


@dataclass
class ConversionOptions:
    preset_key: str
    color_mode: ColorMode = "bw"
    update_mode: UpdateMode = "full"
    fit_mode: FitMode = "contain"
    rotation: RotationMode = 0
    flip_horizontal: bool = False
    invert: bool = False
    threshold: int = 128
    dither: DitherMode = "none"
    variable_name: str = "gImage_custom"
    partial_region: PartialRegion = field(default_factory=PartialRegion)


@dataclass
class ConversionDebugInfo:
    plane24: Optional[list[int]] = None
    plane26: Optional[list[int]] = None
    messages: list[str] = field(default_factory=list)


@dataclass
class ConversionResult:
    preset: EpdPreset
    options: ConversionOptions
    input_preview_size: tuple[int, int]
    output_preview_size: tuple[int, int]
    output_preview_mode: ColorMode
    output_pixels: list[int]
    exported_bytes: list[int]
    exported_c_text: str
    debug: ConversionDebugInfo
    partial_region_applied: Optional[PartialRegion] = None

    @property
    def byte_count(self) -> int:
        return len(self.exported_bytes)
