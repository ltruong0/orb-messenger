// Orb Messenger firmware. See docs/firmware.md for the task split and state machine.
#include <Arduino.h>
#include <lvgl.h>

#include "board.h"
#include "led.h"
#include "net.h"
#include "orb.h"
#include "settings.h"
#include "ui.h"

void setup() {
  Serial.begin(115200);
  delay(200);
  log_i("orb %s fw %s, heap %u KB psram %u KB", orb_id().c_str(), ORB_FW_VERSION,
        ESP.getFreeHeap() / 1024, ESP.getPsramSize() / 1024);
  settings.load();
  queues_init();
  if (!board_init()) log_e("board init failed; continuing anyway");
  ui_init();
  led_init();
  net_start();
}

void loop() {
  ui_loop();
  lv_timer_handler();
  delay(5);
}
