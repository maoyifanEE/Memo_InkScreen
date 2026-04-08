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

static const uint32_t SERIAL_BAUD = 115200;
static const uint32_t READ_TIMEOUT_MS = 15000;
static uint8_t rxBuffer[EPD_ARRAY * 2];
static uint8_t baseMap[EPD_ARRAY];
static bool partialBaseLoaded = false;
static uint32_t partialCount = 0;

void setupPins() {
  pinMode(13, INPUT);
  pinMode(12, OUTPUT);
  pinMode(14, OUTPUT);
  pinMode(27, OUTPUT);
  SPI.beginTransaction(SPISettings(10000000, MSBFIRST, SPI_MODE0));
  SPI.begin();
}

void fillBaseWhite() {
  memset(baseMap, 0xFF, sizeof(baseMap));
}

uint32_t checksum32(const uint8_t* data, size_t len) {
  uint32_t sum = 0;
  for (size_t i = 0; i < len; ++i) {
    sum += data[i];
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

void updateBaseMapRegion(uint16_t x, uint16_t y, uint16_t w, uint16_t h, const uint8_t* data) {
  uint16_t stride = EPD_WIDTH / 8;
  uint16_t rowBytes = w / 8;
  for (uint16_t row = 0; row < h; ++row) {
    memcpy(baseMap + (y + row) * stride + (x / 8), data + row * rowBytes, rowBytes);
  }
}

bool renderFullBw(const uint8_t* data, size_t len) {
  if (len != EPD_ARRAY) {
    Serial.println("ERR LEN_MISMATCH");
    return false;
  }
  EPD_HW_Init();
  EPD_WhiteScreen_ALL(data);
  EPD_DeepSleep();
  memcpy(baseMap, data, EPD_ARRAY);
  partialBaseLoaded = false;
  partialCount = 0;
  return true;
}

bool renderFullGray4(const uint8_t* data, size_t len) {
  if (len != EPD_ARRAY * 2) {
    Serial.println("ERR LEN_MISMATCH");
    return false;
  }
  EPD_HW_Init_4G();
  EPD_WhiteScreen_ALL_4G(data);
  EPD_DeepSleep();
  partialBaseLoaded = false;
  partialCount = 0;
  return true;
}

bool renderPartialBw(uint16_t x, uint16_t y, uint16_t w, uint16_t h, const uint8_t* data, size_t len) {
  if ((w % 8) != 0) {
    Serial.println("ERR PARTIAL_ALIGN");
    return false;
  }
  if (((uint32_t)w * (uint32_t)h) / 8U != len) {
    Serial.println("ERR LEN_MISMATCH");
    return false;
  }
  if (!partialBaseLoaded) {
    EPD_HW_Init();
    EPD_SetRAMValue_BaseMap(baseMap);
    partialBaseLoaded = true;
    partialCount = 0;
  }
  EPD_Dis_Part(x, y, data, h, w);
  updateBaseMapRegion(x, y, w, h, data);
  partialCount++;
  if (partialCount >= 5) {
    partialBaseLoaded = false;
  }
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

  Serial.println("READY");
  if (!readExact(rxBuffer, payloadLen, READ_TIMEOUT_MS)) {
    Serial.println("ERR TIMEOUT");
    return;
  }
  if (checksum32(rxBuffer, payloadLen) != checksum) {
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
  fillBaseWhite();
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
