#pragma once
// Hardware bring-up: PMU rail for the panel, CO5300 over QSPI, CST9217 touch,
// LittleFS and the LVGL display/input drivers. UI-task only (except board_boot_pressed).
#include <Arduino.h>

bool board_init();
void board_set_brightness(uint8_t percent);  // 0..100
void board_screen(bool on);                  // panel off for quiet hours
bool board_boot_pressed();                   // BOOT button is held
