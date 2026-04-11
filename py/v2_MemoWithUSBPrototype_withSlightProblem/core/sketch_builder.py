from __future__ import annotations

import shutil
from pathlib import Path

from .export_c import build_c_array_text, sanitize_variable_name
from .models import ConversionResult


TEMP_SKETCH_NAME = "epd_serial_receiver"


def create_temp_sketch(
    esp32_project_dir: str,
    temp_build_root: str,
    result: ConversionResult | None = None,
) -> Path:
    return create_serial_receiver_sketch(esp32_project_dir, temp_build_root)


def create_serial_receiver_sketch(
    esp32_project_dir: str,
    temp_build_root: str,
) -> Path:
    source_dir = Path(esp32_project_dir).expanduser().resolve()
    temp_root = Path(temp_build_root).expanduser().resolve()
    sketch_dir = temp_root / TEMP_SKETCH_NAME

    required = [source_dir / "EPD.cpp", source_dir / "EPD.h"]
    for path in required:
        if not path.exists():
            raise FileNotFoundError(f"缺少文件：{path}")

    if sketch_dir.exists():
        shutil.rmtree(sketch_dir)
    sketch_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source_dir / "EPD.cpp", sketch_dir / "EPD.cpp")
    shutil.copy2(source_dir / "EPD.h", sketch_dir / "EPD.h")
    (sketch_dir / f"{TEMP_SKETCH_NAME}.ino").write_text(build_serial_receiver_ino(), encoding="utf-8")
    return sketch_dir


def build_image_header_for_single_display(result: ConversionResult) -> tuple[str, str]:
    var_name = sanitize_variable_name(result.options.variable_name or "gImage_single")
    parts = ["#pragma once", ""]
    parts.append(result.exported_c_text.strip())

    if result.options.color_mode == "bw" and result.options.update_mode == "partial":
        white_bytes = [0xFF] * result.preset.bw_bytes
        parts.append("")
        parts.append(
            build_c_array_text(
                "gImage_basemap_white",
                white_bytes,
                bytes_per_line=16,
                comment=f"white basemap for partial update, {result.preset.width}x{result.preset.height}",
            )
        )

    return "\n".join(parts).strip() + "\n", var_name


def build_serial_receiver_ino() -> str:
    return r'''#include <ESP32epdx.h>
#include "EPD.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// Vendor EPD.h omits this declaration, but EPD.cpp defines it.
void EPD_Part_Update(void);

static const uint32_t SERIAL_BAUD = 115200;
static const uint32_t READ_TIMEOUT_MS = 15000;
static uint8_t rxBuffer[EPD_ARRAY * 2];
static uint8_t frameCurrent[EPD_ARRAY];
static uint8_t framePrevious[EPD_ARRAY];
static uint8_t partialPrevRegionBuffer[EPD_ARRAY];
static uint8_t partialRegionBuffer[EPD_ARRAY];
static bool framebufferValid = false;
static bool panelAwake = false;
static bool partialModeArmed = false;

void setupPins() {
  pinMode(13, INPUT);
  pinMode(12, OUTPUT);
  pinMode(14, OUTPUT);
  pinMode(27, OUTPUT);
  SPI.beginTransaction(SPISettings(10000000, MSBFIRST, SPI_MODE0));
  SPI.begin();
}

void waitBusy() {
  uint32_t start = millis();
  while (1) {
    if (isEPD_W21_BUSY == 0) {
      break;
    }
    if (millis() - start > 20000) {
      start = millis();
      Serial.println("DBG busy_wait_20s");
    }
    delay(1);
  }
}

void hwReset() {
  EPD_W21_RST_0;
  delay(10);
  EPD_W21_RST_1;
  delay(10);
}

void fillFrameWhite(uint8_t* target) {
  memset(target, 0xFF, EPD_ARRAY);
}

uint32_t checksum32(const uint8_t* data, size_t len) {
  uint32_t sum = 0;
  for (size_t i = 0; i < len; ++i) {
    sum += data[i];
  }
  return sum;
}

uint32_t checksumRegion(const uint8_t* data, uint16_t x, uint16_t y, uint16_t w, uint16_t h) {
  const uint16_t stride = EPD_WIDTH / 8;
  const uint16_t rowBytes = w / 8;
  uint32_t sum = 0;
  for (uint16_t row = 0; row < h; ++row) {
    const uint8_t* src = data + (y + row) * stride + (x / 8);
    for (uint16_t i = 0; i < rowBytes; ++i) {
      sum += src[i];
    }
  }
  return sum;
}

bool readExact(uint8_t* target, size_t len, uint32_t timeoutMs) {
  size_t received = 0;
  uint32_t start = millis();
  while (received < len) {
    if (Serial.available() > 0) {
      int value = Serial.read();
      if (value >= 0) {
        target[received++] = (uint8_t)value;
        start = millis();
      }
    } else if (millis() - start > timeoutMs) {
      return false;
    } else {
      delay(1);
    }
  }
  return true;
}

void updateFrameRegion(uint8_t* target, uint16_t x, uint16_t y, uint16_t w, uint16_t h, const uint8_t* data) {
  const uint16_t stride = EPD_WIDTH / 8;
  const uint16_t rowBytes = w / 8;
  for (uint16_t row = 0; row < h; ++row) {
    memcpy(target + (y + row) * stride + (x / 8), data + row * rowBytes, rowBytes);
  }
}

void extractFrameRegion(const uint8_t* source, uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint8_t* out) {
  const uint16_t stride = EPD_WIDTH / 8;
  const uint16_t rowBytes = w / 8;
  for (uint16_t row = 0; row < h; ++row) {
    memcpy(out + row * rowBytes, source + (y + row) * stride + (x / 8), rowBytes);
  }
}

void initPartialEngine() {
  hwReset();
  waitBusy();
  EPD_W21_WriteCMD(0x12);
  waitBusy();

  EPD_W21_WriteCMD(0x01);
  EPD_W21_WriteDATA((EPD_HEIGHT - 1) % 256);
  EPD_W21_WriteDATA((EPD_HEIGHT - 1) / 256);
  EPD_W21_WriteDATA(0x00);

  EPD_W21_WriteCMD(0x21);
  EPD_W21_WriteDATA(0x00);
  EPD_W21_WriteDATA(0x00);

  EPD_W21_WriteCMD(0x3C);
  EPD_W21_WriteDATA(0x80);

  EPD_W21_WriteCMD(0x11);
  EPD_W21_WriteDATA(0x01);

  EPD_W21_WriteCMD(0x44);
  EPD_W21_WriteDATA(0x00);
  EPD_W21_WriteDATA(EPD_WIDTH / 8 - 1);

  EPD_W21_WriteCMD(0x45);
  EPD_W21_WriteDATA((EPD_HEIGHT - 1) % 256);
  EPD_W21_WriteDATA((EPD_HEIGHT - 1) / 256);
  EPD_W21_WriteDATA(0x00);
  EPD_W21_WriteDATA(0x00);

  EPD_W21_WriteCMD(0x4E);
  EPD_W21_WriteDATA(0x00);
  EPD_W21_WriteCMD(0x4F);
  EPD_W21_WriteDATA((EPD_HEIGHT - 1) % 256);
  EPD_W21_WriteDATA((EPD_HEIGHT - 1) / 256);
  waitBusy();
}

void armPartialMode() {
  if (!panelAwake) {
    initPartialEngine();
    panelAwake = true;
    partialModeArmed = false;
  }
  if (!partialModeArmed) {
    EPD_W21_WriteCMD(0x21);
    EPD_W21_WriteDATA(0x00);
    EPD_W21_WriteDATA(0x00);
    EPD_W21_WriteCMD(0x3C);
    EPD_W21_WriteDATA(0x80);
    partialModeArmed = true;
  }
}

void writeRegionToCmd(uint8_t cmd, uint16_t x, uint16_t y, uint16_t w, uint16_t h, const uint8_t* data) {
  uint16_t xStart = x / 8;
  uint16_t xEnd = xStart + (w / 8) - 1;

  // The controller's Y RAM addressing is bottom-origin in the same mode used by the
  // vendor full-screen path (0x11 = 0x01). Convert our top-origin logical window into
  // controller-space addresses so partial windows land on the correct physical rows.
  uint16_t yStart = (EPD_HEIGHT - 1) - y;
  uint16_t yEnd = (EPD_HEIGHT - 1) - (y + h - 1);

  EPD_W21_WriteCMD(0x44);
  EPD_W21_WriteDATA(xStart);
  EPD_W21_WriteDATA(xEnd);
  EPD_W21_WriteCMD(0x45);
  EPD_W21_WriteDATA(yStart % 256);
  EPD_W21_WriteDATA(yStart / 256);
  EPD_W21_WriteDATA(yEnd % 256);
  EPD_W21_WriteDATA(yEnd / 256);
  EPD_W21_WriteCMD(0x4E);
  EPD_W21_WriteDATA(xStart);
  EPD_W21_WriteCMD(0x4F);
  EPD_W21_WriteDATA(yStart % 256);
  EPD_W21_WriteDATA(yStart / 256);

  EPD_W21_WriteCMD(cmd);
  const uint32_t total = ((uint32_t)w * (uint32_t)h) / 8U;
  for (uint32_t i = 0; i < total; ++i) {
    EPD_W21_WriteDATA(data[i]);
  }
}

bool renderFullBw(const uint8_t* data, size_t len) {
  if (len != EPD_ARRAY) {
    Serial.println("ERR LEN_MISMATCH");
    return false;
  }
  Serial.print("DBG full_bw checksum=");
  Serial.println(checksum32(data, len));
  EPD_HW_Init();
  // Prime the controller basemap for later partial updates.
  EPD_SetRAMValue_BaseMap(data);
  Serial.println("DBG basemap primed");
  memcpy(frameCurrent, data, EPD_ARRAY);
  memcpy(framePrevious, data, EPD_ARRAY);
  framebufferValid = true;
  panelAwake = true;
  partialModeArmed = false;
  return true;
}

bool renderFastBw(const uint8_t* data, size_t len) {
  if (len != EPD_ARRAY) {
    Serial.println("ERR LEN_MISMATCH");
    return false;
  }
  Serial.print("DBG fast_bw checksum=");
  Serial.println(checksum32(data, len));
  EPD_HW_Init_Fast();
  EPD_WhiteScreen_ALL_Fast(data);
  memcpy(frameCurrent, data, EPD_ARRAY);
  memcpy(framePrevious, data, EPD_ARRAY);
  framebufferValid = true;
  panelAwake = true;
  partialModeArmed = false;
  return true;
}

bool renderFullGray4(const uint8_t* data, size_t len) {
  if (len != EPD_ARRAY * 2) {
    Serial.println("ERR LEN_MISMATCH");
    return false;
  }
  Serial.print("DBG gray4 checksum=");
  Serial.println(checksum32(data, len));
  EPD_HW_Init_4G();
  EPD_WhiteScreen_ALL_4G(data);
  framebufferValid = false;
  panelAwake = true;
  partialModeArmed = false;
  return true;
}

bool renderPartialBw(uint16_t x, uint16_t y, uint16_t w, uint16_t h, const uint8_t* data, size_t len) {
  if ((x % 8) != 0 || (w % 8) != 0) {
    Serial.println("ERR PARTIAL_ALIGN");
    return false;
  }
  if (((uint32_t)w * (uint32_t)h) / 8U != len) {
    Serial.println("ERR LEN_MISMATCH");
    return false;
  }
  if (!framebufferValid) {
    Serial.println("ERR NO_FRAMEBUFFER");
    return false;
  }
  if (x + w > EPD_WIDTH || y + h > EPD_HEIGHT) {
    Serial.println("ERR PARTIAL_RANGE");
    return false;
  }

  const uint32_t regionBytes = ((uint32_t)w * (uint32_t)h) / 8U;
  if (regionBytes > sizeof(partialRegionBuffer)) {
    Serial.println("ERR PARTIAL_TMP_OVERFLOW");
    return false;
  }

  uint32_t beforeCk = checksumRegion(frameCurrent, x, y, w, h);
  extractFrameRegion(framePrevious, x, y, w, h, partialPrevRegionBuffer);
  updateFrameRegion(frameCurrent, x, y, w, h, data);
  extractFrameRegion(frameCurrent, x, y, w, h, partialRegionBuffer);
  uint32_t afterCk = checksumRegion(frameCurrent, x, y, w, h);
  Serial.print("DBG partial x="); Serial.print(x);
  Serial.print(" y="); Serial.print(y);
  Serial.print(" ctrlYStart="); Serial.print((EPD_HEIGHT - 1) - y);
  Serial.print(" ctrlYEnd="); Serial.print((EPD_HEIGHT - 1) - (y + h - 1));
  Serial.print(" w="); Serial.print(w);
  Serial.print(" h="); Serial.print(h);
  Serial.print(" bytes="); Serial.print(regionBytes);
  Serial.print(" before="); Serial.print(beforeCk);
  Serial.print(" after="); Serial.print(afterCk);
  Serial.print(" prevSum="); Serial.print(checksum32(partialPrevRegionBuffer, regionBytes));
  Serial.print(" currSum="); Serial.println(checksum32(partialRegionBuffer, regionBytes));

  // Drive the controller with explicit old/new data for the target region.
  // This avoids relying on whatever happens to still be in the controller's
  // 0x26 RAM for that window after prior partial updates.
  armPartialMode();
  writeRegionToCmd(0x26, x, y, w, h, partialPrevRegionBuffer);
  writeRegionToCmd(0x24, x, y, w, h, partialRegionBuffer);
  EPD_Part_Update();
  // Keep both our software copy and the controller's previous-image RAM in sync.
  writeRegionToCmd(0x26, x, y, w, h, partialRegionBuffer);
  updateFrameRegion(framePrevious, x, y, w, h, partialRegionBuffer);
  return true;
}

void handleSend(String line) {
  char color[16] = {0};
  char updateMode[16] = {0};
  unsigned int displayW = 0;
  unsigned int displayH = 0;
  unsigned int x = 0;
  unsigned int y = 0;
  unsigned int regionW = 0;
  unsigned int regionH = 0;
  unsigned long payloadLen = 0;
  unsigned long checksum = 0;

  int parsed = sscanf(
    line.c_str(),
    "SEND %15s %15s %u %u %u %u %u %u %lu %lu",
    color,
    updateMode,
    &displayW,
    &displayH,
    &x,
    &y,
    &regionW,
    &regionH,
    &payloadLen,
    &checksum
  );

  if (parsed != 10) {
    Serial.println("ERR BAD_HEADER");
    return;
  }
  if (displayW != EPD_WIDTH || displayH != EPD_HEIGHT) {
    Serial.println("ERR PRESET_MISMATCH");
    return;
  }
  if (payloadLen > sizeof(rxBuffer)) {
    Serial.println("ERR PAYLOAD_TOO_LARGE");
    return;
  }

  Serial.print("DBG header ");
  Serial.println(line);
  Serial.println("READY");
  if (!readExact(rxBuffer, payloadLen, READ_TIMEOUT_MS)) {
    Serial.println("ERR TIMEOUT");
    return;
  }
  uint32_t actualChecksum = checksum32(rxBuffer, payloadLen);
  if (actualChecksum != checksum) {
    Serial.print("DBG checksum actual="); Serial.print(actualChecksum);
    Serial.print(" expect="); Serial.println(checksum);
    Serial.println("ERR CHECKSUM");
    return;
  }

  bool ok = false;
  if (strcmp(color, "bw") == 0 && strcmp(updateMode, "full") == 0) {
    ok = renderFullBw(rxBuffer, payloadLen);
  } else if (strcmp(color, "gray4") == 0 && strcmp(updateMode, "full") == 0) {
    ok = renderFullGray4(rxBuffer, payloadLen);
  } else if (strcmp(color, "bw") == 0 && strcmp(updateMode, "partial") == 0) {
    ok = renderPartialBw((uint16_t)x, (uint16_t)y, (uint16_t)regionW, (uint16_t)regionH, rxBuffer, payloadLen);
  } else {
    Serial.println("ERR UNSUPPORTED_MODE");
    return;
  }

  if (ok) {
    Serial.print("OK ");
    Serial.print(color);
    Serial.print(" ");
    Serial.println(updateMode);
  }
}

void setup() {
  setupPins();
  fillFrameWhite(frameCurrent);
  fillFrameWhite(framePrevious);
  Serial.begin(SERIAL_BAUD);
  Serial.setTimeout(2000);
  delay(200);
  Serial.println("PONG EPD_SERIAL_V1");
}

void loop() {
  if (!Serial.available()) {
    delay(2);
    return;
  }

  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) {
    return;
  }
  if (line == "PING") {
    Serial.println("PONG EPD_SERIAL_V1");
    return;
  }
  if (line.startsWith("SEND ")) {
    handleSend(line);
    return;
  }
  Serial.println("ERR UNKNOWN_COMMAND");
}
'''
