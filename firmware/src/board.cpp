#include "board.h"

#include <Arduino_GFX_Library.h>
#include <LittleFS.h>
#include <Wire.h>
#include <lvgl.h>

#include "pins.h"
#if ORB_HAS_AXP2101
#define XPOWERS_CHIP_AXP2101
#include <XPowersLib.h>
static XPowersPMU pmu;
#endif
#if ORB_TOUCH_CST92XX
#include <TouchDrvCSTXXX.hpp>
static TouchDrvCST92xx touch;
#endif

static Arduino_DataBus* bus = nullptr;
static Arduino_CO5300* gfx = nullptr;
static bool touch_ok = false;
static uint8_t touch_addr = 0;
static uint8_t cur_brightness = 70;

// Two partial buffers of 40 lines in internal DMA-capable RAM.
static const int BUF_LINES = 40;
static lv_display_t* disp = nullptr;

static String i2c_scan() {
  String found;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) found += " 0x" + String(a, HEX);
  }
  return found;
}

static void flush_cb(lv_display_t* d, const lv_area_t* area, uint8_t* px_map) {
  int32_t w = lv_area_get_width(area), h = lv_area_get_height(area);
  // LVGL renders little-endian RGB565; the panel wants big-endian.
  uint16_t* p = (uint16_t*)px_map;
  for (int32_t i = 0, n = w * h; i < n; i++) p[i] = __builtin_bswap16(p[i]);
  gfx->draw16bitBeRGBBitmap(area->x1, area->y1, p, w, h);
  lv_display_flush_ready(d);
}

// CO5300 wants even column/row starts and odd ends.
static void rounder_cb(lv_event_t* e) {
  lv_area_t* a = (lv_area_t*)lv_event_get_param(e);
  a->x1 &= ~1;
  a->y1 &= ~1;
  a->x2 |= 1;
  a->y2 |= 1;
}

#if ORB_TOUCH_FOCALTECH
// FT3168 / CST820: 0x02 = finger count, then XH XL YH YL (12-bit coords).
static bool focaltech_read(int16_t& x, int16_t& y) {
  // Stop between the register write and the read: FocalTech parts NACK the
  // repeated-start form now and then, which the core logs as an error.
  Wire.beginTransmission(touch_addr);
  Wire.write(0x02);
  if (Wire.endTransmission(true) != 0) return false;
  if (Wire.requestFrom(touch_addr, (uint8_t)5) != 5) return false;
  uint8_t n = Wire.read() & 0x0F;
  uint8_t xh = Wire.read(), xl = Wire.read(), yh = Wire.read(), yl = Wire.read();
  if (n == 0 || n > 5) return false;
  x = ((xh & 0x0F) << 8) | xl;
  y = ((yh & 0x0F) << 8) | yl;
  return true;
}

static bool touch_init() {
  for (uint8_t a : {(uint8_t)0x38, (uint8_t)0x15}) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      touch_addr = a;
      break;
    }
  }
  if (!touch_addr) return false;
  if (touch_addr == 0x38) {  // FT3168: power mode register, 0 = active scanning
    Wire.beginTransmission(touch_addr);
    Wire.write(0xA5);
    Wire.write(0x00);
    Wire.endTransmission();
  }
  log_i("touch %s at 0x%02x", touch_addr == 0x38 ? "FT3168" : "CST820", touch_addr);
  return true;
}
#endif

static void touch_read_cb(lv_indev_t* indev, lv_indev_data_t* data) {
  data->state = LV_INDEV_STATE_RELEASED;
  if (!touch_ok) return;
#if ORB_TOUCH_FOCALTECH
  int16_t x, y;
  if (focaltech_read(x, y)) {
    data->point.x = x;
    data->point.y = y;
    data->state = LV_INDEV_STATE_PRESSED;
  }
#elif ORB_TOUCH_CST92XX
  const TouchPoints& pts = touch.getTouchPoints();
  if (pts.hasPoints()) {
    const TouchPoint& p = pts.getPoint(0);
    data->point.x = p.x;
    data->point.y = p.y;
    data->state = LV_INDEV_STATE_PRESSED;
  }
#endif
}

bool board_init() {
  pinMode(BOOT_BUTTON, INPUT_PULLUP);
#ifdef LCD_EN
  pinMode(LCD_EN, OUTPUT);
  digitalWrite(LCD_EN, HIGH);  // panel + touch power rail
  delay(20);
#endif
  Wire.begin(IIC_SDA, IIC_SCL, 400000);
  Wire.setTimeOut(50);
  {
    // Bus scan at boot: cheap, and the first thing to look at when a chip is "not found".
    String found = i2c_scan();
    log_i("i2c devices on sda=%d scl=%d:%s", IIC_SDA, IIC_SCL, found.isEmpty() ? " none" : found.c_str());
  }

#if ORB_HAS_AXP2101
  // The OLED's VDD hangs off the AXP2101's BLDO1. After a full battery drain the
  // PMU resets with that rail off, so enable it before touching the panel.
  if (pmu.begin(Wire, AXP2101_SLAVE_ADDRESS, IIC_SDA, IIC_SCL)) {
    pmu.setBLDO1Voltage(3300);
    pmu.enableBLDO1();
    pmu.disableIRQ(XPOWERS_AXP2101_ALL_IRQ);
    pmu.clearIrqStatus();
    log_i("AXP2101 ok, vbus=%d batt=%d%%", pmu.isVbusIn(), pmu.getBatteryPercent());
  } else {
    log_w("AXP2101 not found; panel may stay dark");
  }
  delay(10);
#endif

  bus = new Arduino_ESP32QSPI(LCD_CS, LCD_SCLK, LCD_SDIO0, LCD_SDIO1, LCD_SDIO2, LCD_SDIO3);
  gfx = new Arduino_CO5300(bus, LCD_RESET, 0, LCD_W, LCD_H, LCD_COL_OFFSET, 0, 0, 0);
  if (!gfx->begin(80000000)) {
    log_e("gfx->begin failed");
    return false;
  }
  gfx->fillScreen(RGB565_BLACK);
  board_set_brightness(cur_brightness);

#if ORB_TOUCH_FOCALTECH
  touch_ok = touch_init();
#elif ORB_TOUCH_CST92XX
  touch.setPins(TP_RESET, TP_INT);
  touch_ok = touch.begin(Wire, CST92XX_SLAVE_ADDRESS, IIC_SDA, IIC_SCL);
  if (touch_ok) touch.setMaxCoordinates(LCD_W, LCD_H);
#endif
  log_i("touch %s", touch_ok ? "ok" : "FAIL");

  if (!LittleFS.begin(true)) {
    log_e("LittleFS mount failed");
  } else {
    if (!LittleFS.exists("/stk")) LittleFS.mkdir("/stk");
    log_i("LittleFS %u/%u KB used", LittleFS.usedBytes() / 1024, LittleFS.totalBytes() / 1024);
  }

  lv_init();
  lv_tick_set_cb([]() -> uint32_t { return millis(); });
  disp = lv_display_create(LCD_W, LCD_H);
  size_t buf_bytes = LCD_W * BUF_LINES * 2;
  void* b1 = heap_caps_malloc(buf_bytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
  void* b2 = heap_caps_malloc(buf_bytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
  if (!b1 || !b2) {
    log_e("no RAM for draw buffers");
    return false;
  }
  lv_display_set_color_format(disp, LV_COLOR_FORMAT_RGB565);
  lv_display_set_buffers(disp, b1, b2, buf_bytes, LV_DISPLAY_RENDER_MODE_PARTIAL);
  lv_display_set_flush_cb(disp, flush_cb);
  lv_display_add_event_cb(disp, rounder_cb, LV_EVENT_INVALIDATE_AREA, nullptr);

  lv_indev_t* indev = lv_indev_create();
  lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);
  lv_indev_set_read_cb(indev, touch_read_cb);
  lv_indev_set_long_press_time(indev, 600);
  return true;
}

void board_set_brightness(uint8_t percent) {
  if (percent > 100) percent = 100;
  cur_brightness = percent;
  // Keep a floor so "0" from the hub still shows something; quiet hours use board_screen.
  uint8_t v = percent == 0 ? 0 : 20 + (uint16_t)percent * 235 / 100;
  gfx->setBrightness(v);
}

void board_screen(bool on) {
  if (on) {
    gfx->displayOn();
    board_set_brightness(cur_brightness);
  } else {
    gfx->setBrightness(0);
    gfx->displayOff();
  }
}

bool board_boot_pressed() { return digitalRead(BOOT_BUTTON) == LOW; }
