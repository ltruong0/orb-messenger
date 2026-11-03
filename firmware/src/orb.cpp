#include "orb.h"

#include <esp_mac.h>

QueueHandle_t ui_queue = nullptr;
QueueHandle_t net_queue = nullptr;

String orb_id() {
  static String id;
  if (id.isEmpty()) {
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    char buf[16];
    snprintf(buf, sizeof(buf), "orb-%02x%02x", mac[4], mac[5]);
    id = buf;
  }
  return id;
}

void queues_init() {
  ui_queue = xQueueCreate(16, sizeof(UiCmd*));
  net_queue = xQueueCreate(8, sizeof(NetCmd*));
}

void ui_post(UiCmdType type, const String& json, const String& a, bool flag) {
  auto* c = new UiCmd{type, json, a, flag};
  if (xQueueSend(ui_queue, &c, pdMS_TO_TICKS(500)) != pdTRUE) {
    log_w("ui queue full, dropping cmd %d", (int)type);
    delete c;
  }
}

void net_post(NetCmdType type, const String& msg_id, const String& sticker) {
  auto* c = new NetCmd{type, msg_id, sticker};
  if (xQueueSend(net_queue, &c, pdMS_TO_TICKS(500)) != pdTRUE) {
    log_w("net queue full, dropping cmd %d", (int)type);
    delete c;
  }
}
