#include "led.h"

#include "pins.h"

#if ORB_LED_PIN >= 0
#define FASTLED_INTERNAL
#include <FastLED.h>
static CRGB leds[ORB_LED_COUNT];
#endif

struct LedState {
  String mode = "off", pattern, color;
  bool unreachable = false, quiet = false;
  uint32_t since = 0;
};
static LedState st;
static SemaphoreHandle_t mux;

static uint32_t parse_color(const String& hex, uint32_t fallback) {
  if (hex.length() == 7 && hex[0] == '#') return strtoul(hex.c_str() + 1, nullptr, 16);
  return fallback;
}

static const struct {
  const char* mode;
  uint32_t color;
  const char* pattern;
} DEFAULTS[] = {
    {"unread", 0xFF2D2D, "breathe"},
    {"read", 0x2EC86E, "pulse"},
    {"pair", 0x4682FF, "breathe"},
};

static void render(void*) {
#if ORB_LED_PIN >= 0
  for (;;) {
    LedState s;
    xSemaphoreTake(mux, portMAX_DELAY);
    s = st;
    xSemaphoreGive(mux);
    uint32_t t = millis() - s.since;
    uint32_t rgb = 0;
    float level = 0;
    if (s.quiet) {
      level = 0;
    } else if (s.unreachable) {
      rgb = 0xF5AA28;
      level = (t % 2000) < 300 ? 0.5f : 0;  // slow blink
    } else if (s.mode != "off") {
      rgb = parse_color(s.color, 0xFFFFFF);
      for (auto& d : DEFAULTS)
        if (s.mode == d.mode && s.color.isEmpty()) rgb = d.color;
      String pat = s.pattern;
      if (pat.isEmpty())
        for (auto& d : DEFAULTS)
          if (s.mode == d.mode) pat = d.pattern;
      if (pat == "breathe") {
        level = 0.5f + 0.5f * sinf((t % 2000) / 2000.0f * 6.2831f - 1.5708f);
      } else if (pat == "pulse") {
        // 4 slow pulses then stay dim; the hub turns it off after 30 s anyway
        level = t < 8000 ? fabsf(sinf((t % 2000) / 2000.0f * 3.1416f)) : 0.15f;
      } else if (pat == "blink") {
        level = (t % 1000) < 500 ? 1 : 0;
      } else {
        level = 1;
      }
    }
    uint8_t r = ((rgb >> 16) & 0xFF) * level * 0.35f;  // desk device: keep it dim
    uint8_t g = ((rgb >> 8) & 0xFF) * level * 0.35f;
    uint8_t b = (rgb & 0xFF) * level * 0.35f;
    for (auto& l : leds) l = CRGB(r, g, b);
    FastLED.show();
    vTaskDelay(pdMS_TO_TICKS(20));
  }
#else
  vTaskDelete(nullptr);
#endif
}

void led_init() {
  mux = xSemaphoreCreateMutex();
#if ORB_LED_PIN >= 0
  FastLED.addLeds<WS2812B, ORB_LED_PIN, GRB>(leds, ORB_LED_COUNT);
  FastLED.clear(true);
  xTaskCreatePinnedToCore(render, "led", 3072, nullptr, 1, nullptr, 0);
  log_i("led on gpio %d x%d", ORB_LED_PIN, ORB_LED_COUNT);
#else
  log_i("no LED pin configured; LED modes are logged only");
#endif
}

void led_set(const String& mode, const String& color, const String& pattern) {
  xSemaphoreTake(mux, portMAX_DELAY);
  st.mode = mode;
  st.color = color;
  st.pattern = pattern;
  st.since = millis();
  xSemaphoreGive(mux);
  log_i("led %s %s %s", mode.c_str(), color.c_str(), pattern.c_str());
}

void led_local_unreachable(bool on) {
  xSemaphoreTake(mux, portMAX_DELAY);
  st.unreachable = on;
  st.since = millis();
  xSemaphoreGive(mux);
}

void led_quiet(bool on) {
  xSemaphoreTake(mux, portMAX_DELAY);
  st.quiet = on;
  xSemaphoreGive(mux);
}
