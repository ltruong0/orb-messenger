#pragma once
// NVS-backed settings (Preferences namespace "orb"). Written only from net_task,
// except clearWifi() which the button handler calls right before a restart.
#include <Arduino.h>

struct Settings {
  String wifi_ssid, wifi_pass;
  String mqtt_host = "mqtt.marcellolab.com";
  uint16_t mqtt_port = 1883;
  String mqtt_user = "orb";
  String mqtt_pass;
  String tz = "CST6CDT,M3.2.0,M11.1.0";
  String last_read_id, last_received_id;
  int stk_rev = 0;

  void load();
  void saveNetwork();  // wifi + mqtt + tz
  void saveState();    // last_*_id, stk_rev
  void clearWifi();
  bool hasWifi() const { return !wifi_ssid.isEmpty(); }
};

extern Settings settings;
