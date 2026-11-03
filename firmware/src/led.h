#pragma once
// Status LED. With ORB_LED_PIN < 0 (no LED wired yet) it only logs mode changes.
#include <Arduino.h>

void led_init();
void led_set(const String& mode, const String& color, const String& pattern);  // from the hub's led topic
void led_local_unreachable(bool on);  // amber slow blink while the broker is down
void led_quiet(bool on);              // quiet hours: dark, remember the mode
