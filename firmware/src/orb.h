#pragma once
// Shared types for the two tasks. net_task (core 0) owns Wi-Fi/MQTT/HTTP/NVS,
// the Arduino loop task (core 1) owns LVGL. They only talk through these queues.
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include "pins.h"  // ORB_MODEL, ORB_LED_PIN

#ifndef ORB_FW_VERSION
#define ORB_FW_VERSION "0.0.0-dev"
#endif

String orb_id();  // "orb-" + last 2 bytes of the STA MAC, e.g. orb-1338

// net -> ui
enum class UiCmdType {
  Portal,         // a = AP ssid
  Unreachable,    // a = broker host
  Waiting,        // connected, no config yet (or forgotten)
  Pairing,        // a = code
  Active,         // json = config payload
  Inbox,          // json = inbox payload, a = photo path ("" if none), flag = unread
  InboxCleared,
  Led,            // json = led payload ("" = off)
  Notice,         // json = notice payload
  Stickers,       // sticker cache changed
  Update,         // json = target version, a = status ("downloading", "restarting", "failed: ..."), n = percent
};
struct UiCmd {
  UiCmdType type;
  String json;
  String a;
  bool flag = false;
  int n = 0;
};

// ui -> net
enum class NetCmdType { Read, React };
struct NetCmd {
  NetCmdType type;
  String msg_id;
  String sticker;
};

extern QueueHandle_t ui_queue;   // UiCmd*
extern QueueHandle_t net_queue;  // NetCmd*
void queues_init();
void ui_post(UiCmdType type, const String& json = "", const String& a = "", bool flag = false, int n = 0);
void net_post(NetCmdType type, const String& msg_id, const String& sticker = "");
