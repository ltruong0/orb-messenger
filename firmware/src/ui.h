#pragma once
// LVGL screens and the orb state machine (docs/firmware.md). Runs on the Arduino
// loop task; everything arrives through ui_queue.
#include <Arduino.h>

void ui_init();
void ui_loop();  // drain the queue, timers, quiet hours, BOOT button
