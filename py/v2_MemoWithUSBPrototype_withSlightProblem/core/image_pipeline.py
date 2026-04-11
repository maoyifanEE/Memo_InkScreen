from __future__ import annotations

import math
from dataclasses import replace

from PIL import Image, ImageOps

from .models import ConversionDebugInfo, ConversionOptions, ConversionResult, PartialRegion
from .presets import get_preset
from .export_c import build_c_array_text, sanitize_variable_name


GRAY4_LEVELS = [0, 85, 170, 255]
GRAY4_CODES = [0b00, 0b01, 0b10, 0b11]  # 00=黑, 01=深灰, 10=浅灰, 11=白


class ConversionError(ValueError):
    pass


def load_image(path: str) -> Image.Image:
    image = Image.open(path)
    return image.convert("RGB")


def build_conversion_result(source_image: Image.Image, options: ConversionOptions) -> ConversionResult:
    preset = get_preset(options.preset_key)
    normalized_options = replace(options, variable_name=sanitize_variable_name(options.variable_name))
    debug = ConversionDebugInfo()

    if normalized_options.color_mode not in preset.supported_color_modes:
        raise ConversionError("当前预设不支持该颜色模式。")
    if normalized_options.update_mode not in preset.supported_update_modes:
        raise ConversionError("当前预设不支持该刷新模式。")

    prepared = prepare_canvas(
        source_image=source_image,
        width=preset.width,
        height=preset.height,
        fit_mode=normalized_options.fit_mode,
        rotation=normalized_options.rotation,
        flip_horizontal=normalized_options.flip_horizontal,
    )

    if normalized_options.invert:
        prepared = ImageOps.invert(prepared.convert("RGB"))

    if normalized_options.color_mode == "bw":
        preview_pixels, packed = convert_bw(prepared, normalized_options)
        region_applied = None
        exported_bytes = packed

        if normalized_options.update_mode == "partial":
            requested_region = normalized_options.partial_region
            if normalized_options.flip_horizontal:
                requested_region = transform_partial_region_for_mirror_x(requested_region, preset.width, preset.height)
                debug.messages.append(
                    f"局刷区域因 mirror-x 变换为 x={requested_region.x}, y={requested_region.y}, "
                    f"w={requested_region.width}, h={requested_region.height}。"
                )
            region_applied = normalize_partial_region(requested_region, preset.width, preset.height)
            exported_bytes = crop_bw_bytes(preview_pixels, preset.width, preset.height, region_applied, debug)
            debug.messages.append(
                f"局刷区域已按字节对齐为 x={region_applied.x}, y={region_applied.y}, "
                f"w={region_applied.width}, h={region_applied.height}。"
            )

        exported_c_text = build_c_array_text(
            variable_name=normalized_options.variable_name,
            values=exported_bytes,
            bytes_per_line=16,
            comment=build_comment_text(preset.width, preset.height, normalized_options, region_applied),
        )

        return ConversionResult(
            preset=preset,
            options=normalized_options,
            input_preview_size=source_image.size,
            output_preview_size=(preset.width, preset.height),
            output_preview_mode="bw",
            output_pixels=preview_pixels,
            exported_bytes=exported_bytes,
            exported_c_text=exported_c_text,
            debug=debug,
            partial_region_applied=region_applied,
        )

    if normalized_options.update_mode == "partial":
        raise ConversionError("当前示例驱动没有 4 灰局刷接口，建议先用全屏 4 灰导出。")

    preview_pixels, packed_2bit = convert_gray4(prepared, normalized_options)
    plane24, plane26 = split_gray4_to_debug_planes(packed_2bit)
    debug.plane24 = plane24
    debug.plane26 = plane26
    debug.messages.append("4 灰导出为 2bit 打包数组；同时附带 0x24 / 0x26 两路调试位面。")

    exported_c_text = build_c_array_text(
        variable_name=normalized_options.variable_name,
        values=packed_2bit,
        bytes_per_line=16,
        comment=build_comment_text(preset.width, preset.height, normalized_options, None),
    )

    return ConversionResult(
        preset=preset,
        options=normalized_options,
        input_preview_size=source_image.size,
        output_preview_size=(preset.width, preset.height),
        output_preview_mode="gray4",
        output_pixels=preview_pixels,
        exported_bytes=packed_2bit,
        exported_c_text=exported_c_text,
        debug=debug,
        partial_region_applied=None,
    )


def prepare_canvas(
    source_image: Image.Image,
    width: int,
    height: int,
    fit_mode: str,
    rotation: int,
    flip_horizontal: bool,
) -> Image.Image:
    image = source_image.convert("RGB")
    if rotation:
        image = image.rotate(-rotation, expand=True)
    if flip_horizontal:
        image = ImageOps.mirror(image)

    if fit_mode == "stretch":
        return image.resize((width, height), Image.Resampling.LANCZOS)

    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        raise ConversionError("输入图片尺寸异常。")

    scale_x = width / src_w
    scale_y = height / src_h

    if fit_mode == "cover":
        scale = max(scale_x, scale_y)
        resized = image.resize((max(1, round(src_w * scale)), max(1, round(src_h * scale))), Image.Resampling.LANCZOS)
        left = max(0, (resized.width - width) // 2)
        top = max(0, (resized.height - height) // 2)
        return resized.crop((left, top, left + width, top + height))

    scale = min(scale_x, scale_y)
    resized = image.resize((max(1, round(src_w * scale)), max(1, round(src_h * scale))), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), "white")
    left = (width - resized.width) // 2
    top = (height - resized.height) // 2
    canvas.paste(resized, (left, top))
    return canvas


def convert_bw(image: Image.Image, options: ConversionOptions) -> tuple[list[int], list[int]]:
    gray = image.convert("L")
    if options.dither == "floyd":
        bw = gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        preview_pixels = [255 if value else 0 for value in bw.getdata()]
    else:
        preview_pixels = [255 if value >= options.threshold else 0 for value in gray.getdata()]

    packed = pack_bw_pixels(preview_pixels, image.width, image.height)
    return preview_pixels, packed


def convert_gray4(image: Image.Image, options: ConversionOptions) -> tuple[list[int], list[int]]:
    gray = image.convert("L")
    preview_pixels: list[int] = []
    codes: list[int] = []

    for value in gray.getdata():
        code, preview = quantize_gray4(value)
        codes.append(code)
        preview_pixels.append(preview)

    packed = pack_gray4_codes(codes)
    return preview_pixels, packed


def quantize_gray4(value: int) -> tuple[int, int]:
    if value < 64:
        return GRAY4_CODES[0], GRAY4_LEVELS[0]
    if value < 128:
        return GRAY4_CODES[1], GRAY4_LEVELS[1]
    if value < 192:
        return GRAY4_CODES[2], GRAY4_LEVELS[2]
    return GRAY4_CODES[3], GRAY4_LEVELS[3]


def pack_bw_pixels(pixels: list[int], width: int, height: int) -> list[int]:
    result: list[int] = []
    row_stride = width
    for y in range(height):
        row = pixels[y * row_stride : (y + 1) * row_stride]
        for x in range(0, width, 8):
            byte = 0
            chunk = row[x : x + 8]
            if len(chunk) < 8:
                chunk = chunk + [255] * (8 - len(chunk))
            for pixel in chunk:
                bit = 1 if pixel >= 128 else 0
                byte = (byte << 1) | bit
            result.append(byte)
    return result


def pack_gray4_codes(codes: list[int]) -> list[int]:
    result: list[int] = []
    for i in range(0, len(codes), 4):
        group = codes[i : i + 4]
        if len(group) < 4:
            group = group + [0b11] * (4 - len(group))
        byte = 0
        for code in group:
            byte = (byte << 2) | (code & 0b11)
        result.append(byte)
    return result


def split_gray4_to_debug_planes(packed_2bit: list[int]) -> tuple[list[int], list[int]]:
    plane24: list[int] = []
    plane26: list[int] = []

    for i in range(0, len(packed_2bit), 2):
        byte1 = packed_2bit[i]
        byte2 = packed_2bit[i + 1] if i + 1 < len(packed_2bit) else 0xFF
        byte24 = 0
        byte26 = 0
        for src in (byte1, byte2):
            for shift in (6, 4, 2, 0):
                code = (src >> shift) & 0b11
                bit24 = code & 0b01
                bit26 = (code >> 1) & 0b01
                byte24 = (byte24 << 1) | bit24
                byte26 = (byte26 << 1) | bit26
        plane24.append(byte24)
        plane26.append(byte26)
    return plane24, plane26


def transform_partial_region_for_mirror_x(region: PartialRegion, canvas_width: int, canvas_height: int) -> PartialRegion:
    if region.width <= 0 or region.height <= 0:
        raise ConversionError("局刷区域宽高必须大于 0。")
    x = max(0, min(region.x, canvas_width - 1))
    y = max(0, min(region.y, canvas_height - 1))
    width = min(region.width, canvas_width - x)
    height = min(region.height, canvas_height - y)
    mirrored_x = canvas_width - (x + width)
    mirrored_x = max(0, min(mirrored_x, canvas_width - width))
    return PartialRegion(x=mirrored_x, y=y, width=width, height=height)


def normalize_partial_region(region: PartialRegion, canvas_width: int, canvas_height: int) -> PartialRegion:
    if region.width <= 0 or region.height <= 0:
        raise ConversionError("局刷区域宽高必须大于 0。")

    x = max(0, min(region.x, canvas_width - 1))
    y = max(0, min(region.y, canvas_height - 1))
    width = min(region.width, canvas_width - x)
    height = min(region.height, canvas_height - y)

    aligned_x = (x // 8) * 8
    right = min(canvas_width, x + width)
    aligned_right = math.ceil(right / 8) * 8
    aligned_right = min(canvas_width, aligned_right)
    aligned_width = max(8, aligned_right - aligned_x)

    if aligned_x + aligned_width > canvas_width:
        aligned_width = canvas_width - aligned_x
        aligned_width = max(8, (aligned_width // 8) * 8)

    return PartialRegion(
        x=aligned_x,
        y=y,
        width=aligned_width,
        height=height,
    )


def crop_bw_bytes(
    preview_pixels: list[int],
    canvas_width: int,
    canvas_height: int,
    region: PartialRegion,
    debug: ConversionDebugInfo,
) -> list[int]:
    result: list[int] = []
    for y in range(region.y, region.y + region.height):
        row = preview_pixels[y * canvas_width : (y + 1) * canvas_width]
        cropped = row[region.x : region.x + region.width]
        result.extend(pack_bw_pixels(cropped, region.width, 1))
    debug.messages.append(
        f"局刷字节长度 = {len(result)}，计算方式 = {region.width} * {region.height} / 8。"
    )
    return result


def build_comment_text(
    width: int,
    height: int,
    options: ConversionOptions,
    partial_region: PartialRegion | None,
) -> str:
    extra = ", flip=mirror-x" if options.flip_horizontal else ""
    if partial_region is None:
        return f"{width}x{height}, mode={options.color_mode}, update={options.update_mode}{extra}"
    return (
        f"partial x={partial_region.x}, y={partial_region.y}, "
        f"w={partial_region.width}, h={partial_region.height}, mode={options.color_mode}{extra}"
    )


def build_preview_image(result: ConversionResult) -> Image.Image:
    image = Image.new("L", result.output_preview_size)
    image.putdata(result.output_pixels)
    return image
