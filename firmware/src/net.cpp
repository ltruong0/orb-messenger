#include "net.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Update.h>
#include <LittleFS.h>
#include <WiFi.h>
#include <WiFiManager.h>
#include <mbedtls/sha256.h>
#include <mqtt_client.h>

#include "orb.h"
#include "pins.h"
#include "emoji.h"
#include "settings.h"

static String id, prefix, status_topic;
static esp_mqtt_client_handle_t mqtt = nullptr;
static volatile bool mqtt_connected = false;
static uint32_t down_since = 0;
static bool unreachable_shown = false;

// MQTT events arrive on the client's own task; they're queued here and handled on
// net_task so downloads never block keepalives.
struct RxMsg {
  String topic, payload;
  bool retain;
};
static QueueHandle_t rx_queue;
static String rx_partial;  // multi-part payload accumulator

static bool active = false;
static String media_base;
static String pending_stickers;  // manifest that arrived before we had media_base
static int photo_slot = 0;

// ---------------------------------------------------------------- helpers
static void publish(const String& topic, const String& payload, bool retain) {
  if (!mqtt) return;
  int r = esp_mqtt_client_publish(mqtt, topic.c_str(), payload.c_str(), payload.length(), 1, retain);
  if (r < 0) log_w("publish %s failed", topic.c_str());
}

static void publish_event(const char* type, const String& msg_id, const String& sticker = "") {
  JsonDocument d;
  d["type"] = type;
  d["msg_id"] = msg_id;
  if (!sticker.isEmpty()) d["sticker"] = sticker;
  String out;
  serializeJson(d, out);
  publish(prefix + "event", out, false);
  log_i("tx event %s", out.c_str());
}

static String sha256_file(const char* path) {
  File f = LittleFS.open(path, "r");
  if (!f) return "";
  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts(&ctx, 0);
  uint8_t buf[1024];
  while (f.available()) {
    int n = f.read(buf, sizeof(buf));
    if (n <= 0) break;
    mbedtls_sha256_update(&ctx, buf, n);
  }
  f.close();
  uint8_t out[32];
  mbedtls_sha256_finish(&ctx, out);
  mbedtls_sha256_free(&ctx);
  char hex[65];
  for (int i = 0; i < 32; i++) sprintf(hex + i * 2, "%02x", out[i]);
  return String(hex);
}

// GET media_base + path into LittleFS at dest (via a temp file). sha256 optional.
static bool download(const String& path, const char* dest, const String& sha256) {
  if (media_base.isEmpty()) {
    log_w("no media_base yet, can't fetch %s", path.c_str());
    return false;
  }
  String url = media_base + path;
  HTTPClient http;
  http.setTimeout(15000);
  http.setReuse(false);
  if (!http.begin(url)) return false;
  int code = http.GET();
  if (code != 200) {
    log_w("GET %s -> %d", url.c_str(), code);
    http.end();
    return false;
  }
  const char* tmp = "/dl.tmp";
  File f = LittleFS.open(tmp, "w");
  if (!f) {
    http.end();
    return false;
  }
  int n = http.writeToStream(&f);
  f.close();
  http.end();
  if (n <= 0) {
    log_w("GET %s: body %d", url.c_str(), n);
    LittleFS.remove(tmp);
    return false;
  }
  if (!sha256.isEmpty() && sha256_file(tmp) != sha256) {
    log_w("GET %s: sha256 mismatch", url.c_str());
    LittleFS.remove(tmp);
    return false;
  }
  LittleFS.remove(dest);
  if (!LittleFS.rename(tmp, dest)) {
    LittleFS.remove(tmp);
    return false;
  }
  log_i("fetched %s (%d B) -> %s", path.c_str(), n, dest);
  return true;
}

// ---------------------------------------------------------------- stickers
static void sync_stickers(const String& json) {
  JsonDocument m;
  if (deserializeJson(m, json)) return;
  int rev = m["rev"] | 0;
  if (rev == settings.stk_rev && LittleFS.exists("/stk/index.json")) {
    log_i("stickers rev %d already cached", rev);
    return;
  }
  JsonDocument idx;  // key -> sha256 of what we have
  if (File f = LittleFS.open("/stk/index.json", "r")) {
    deserializeJson(idx, f);
    f.close();
  }
  JsonDocument fresh;
  bool all_ok = true;
  for (JsonObject it : m["items"].as<JsonArray>()) {
    String key = it["key"] | "", url = it["url"] | "", sha = it["sha256"] | "";
    String dest = "/stk/" + key + ".png";
    String have = idx[key] | "";
    if (have == sha && LittleFS.exists(dest)) {
      fresh[key] = sha;
      continue;
    }
    if (download(url, dest.c_str(), sha)) fresh[key] = sha;
    else all_ok = false;
  }
  // Delete keys that are gone from the manifest.
  File dir = LittleFS.open("/stk");
  std::vector<String> gone;
  for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
    String name = f.name();
    if (!name.endsWith(".png")) continue;
    String key = name.substring(0, name.length() - 4);
    if (fresh[key].isNull()) gone.push_back("/stk/" + name);
  }
  dir.close();
  for (auto& p : gone) {
    LittleFS.remove(p);
    log_i("removed %s", p.c_str());
  }
  if (File f = LittleFS.open("/stk/index.json", "w")) {
    serializeJson(fresh, f);
    f.close();
  }
  if (all_ok) {
    settings.stk_rev = rev;
    settings.saveState();
  }
  log_i("stickers rev %d synced (%s)", rev, all_ok ? "complete" : "partial, will retry next manifest");
  ui_post(UiCmdType::Stickers);
}

// ---------------------------------------------------------------- MQTT in
// ---------------------------------------------------------------- OTA (decisions 47)
static String fw_tried;  // version attempted this boot, so a bad image can't loop

static void ota_fail(const String& version, const String& why) {
  log_e("update to %s failed: %s", version.c_str(), why.c_str());
  Update.abort();
  ui_post(UiCmdType::Update, version, "failed: " + why, false, -1);
}

// config.fw names the build every orb of this model should run. Different version
// (either direction) and matching model -> stream it into the other app slot, verify
// the sha256, reboot. The UI shows progress; nothing else happens on net_task meanwhile.
static void ota_update(JsonVariantConst fw) {
  String version = fw["version"] | "", model = fw["model"] | "", url = fw["url"] | "";
  String sha = fw["sha256"] | "";
  size_t size = fw["size"] | 0;
  if (model != ORB_MODEL || version.isEmpty() || version == ORB_FW_VERSION || version == fw_tried) return;
  if (!size || url.isEmpty() || media_base.isEmpty()) return;
  fw_tried = version;
  log_i("firmware %s -> %s (%u B)", ORB_FW_VERSION, version.c_str(), (unsigned)size);
  ui_post(UiCmdType::Update, version, "downloading", false, 0);

  HTTPClient http;
  http.setTimeout(30000);
  http.setReuse(false);
  if (!http.begin(media_base + url)) return ota_fail(version, "bad url");
  int code = http.GET();
  if (code != 200) {
    http.end();
    return ota_fail(version, "HTTP " + String(code));
  }
  if (http.getSize() != (int)size) {
    http.end();
    return ota_fail(version, "size " + String(http.getSize()) + " != " + String(size));
  }
  if (!Update.begin(size)) {
    http.end();
    return ota_fail(version, Update.errorString());
  }
  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts(&ctx, 0);
  WiFiClient* stream = http.getStreamPtr();
  static uint8_t buf[4096];
  size_t got = 0;
  int shown = -1;
  uint32_t last_data = millis();
  while (got < size && http.connected()) {
    size_t avail = stream->available();
    if (!avail) {
      if (millis() - last_data > 30000) break;
      delay(1);
      continue;
    }
    size_t n = stream->readBytes(buf, min(avail, sizeof(buf)));
    if (Update.write(buf, n) != n) break;
    mbedtls_sha256_update(&ctx, buf, n);
    got += n;
    last_data = millis();
    int pct = got * 100 / size;
    if (pct / 2 != shown / 2) {
      shown = pct;
      ui_post(UiCmdType::Update, version, "downloading", false, pct);
    }
  }
  http.end();
  unsigned char dig[32];
  mbedtls_sha256_finish(&ctx, dig);
  mbedtls_sha256_free(&ctx);
  char hex[65];
  for (int i = 0; i < 32; i++) sprintf(hex + 2 * i, "%02x", dig[i]);
  if (got != size) return ota_fail(version, "short read " + String(got) + "/" + String(size));
  if (sha != hex) return ota_fail(version, "sha256 mismatch");
  if (!Update.end(true)) return ota_fail(version, Update.errorString());
  log_i("update to %s written, restarting", version.c_str());
  ui_post(UiCmdType::Update, version, "restarting", false, 100);
  delay(1500);
  ESP.restart();
}

static void on_config(const String& json) {
  JsonDocument d;
  if (deserializeJson(d, json)) return;
  String st = d["state"] | "";
  if (st == "pending") {
    active = false;
    ui_post(UiCmdType::Pairing, "", d["code"] | "");
    return;
  }
  active = true;
  media_base = d["media_base"] | "";
  String tz = d["tz"] | "";
  if (!tz.isEmpty() && tz != settings.tz) {
    settings.tz = tz;
    settings.saveNetwork();
    setenv("TZ", tz.c_str(), 1);
    tzset();
  }
  ui_post(UiCmdType::Active, json);
  if (!pending_stickers.isEmpty()) {
    String m = pending_stickers;
    pending_stickers = "";
    sync_stickers(m);
  }
  if (!d["fw"].isNull()) ota_update(d["fw"]);
}

// Emoji in the text are drawn from small PNGs the hub renders on request.
static void fetch_emoji(const char* text) {
  String seqs[EMOJI_MAX];
  int n = emoji_scan(text, seqs, EMOJI_MAX);
  for (int i = 0; i < n; i++) {
    String file = emoji_file(seqs[i]);
    if (LittleFS.exists(file)) continue;
    if (!download("/media/emoji/" + seqs[i] + ".png", file.c_str(), "")) log_w("emoji %s not fetched", seqs[i].c_str());
  }
}

static void on_inbox(const String& json) {
  JsonDocument d;
  if (deserializeJson(d, json)) return;
  String mid = d["id"] | "";
  String photo;
  if (String(d["type"] | "") == "photo") {
    photo_slot ^= 1;
    photo = photo_slot ? "/p1.jpg" : "/p0.jpg";
    if (!download(d["media"] | "", photo.c_str(), "")) photo = "";
  }
  fetch_emoji(d["text"] | "");
  bool unread = mid != settings.last_read_id;
  if (unread && mid != settings.last_received_id) {
    publish_event("received", mid);
    settings.last_received_id = mid;
    settings.saveState();
  }
  ui_post(UiCmdType::Inbox, json, photo, unread);
}

static void handle_rx(RxMsg* m) {
  String kind = m->topic == "orb/all/stickers" ? "stickers" : m->topic.substring(m->topic.lastIndexOf('/') + 1);
  log_i("rx %s%s %d B", kind.c_str(), m->retain ? " (retained)" : "", m->payload.length());
  if (m->payload.isEmpty()) {
    if (kind == "config") {
      active = false;
      ui_post(UiCmdType::Waiting);
    } else if (kind == "inbox") {
      ui_post(UiCmdType::InboxCleared);
    } else if (kind == "led") {
      ui_post(UiCmdType::Led, "");
    }
    return;
  }
  if (kind == "config") on_config(m->payload);
  else if (kind == "inbox") on_inbox(m->payload);
  else if (kind == "led") ui_post(UiCmdType::Led, m->payload);
  else if (kind == "notice") ui_post(UiCmdType::Notice, m->payload);
  else if (kind == "stickers") {
    if (media_base.isEmpty()) pending_stickers = m->payload;
    else sync_stickers(m->payload);
  }
}

static void mqtt_event(void*, esp_event_base_t, int32_t, void* data) {
  auto* e = (esp_mqtt_event_handle_t)data;
  switch (e->event_id) {
    case MQTT_EVENT_CONNECTED: {
      mqtt_connected = true;
      log_i("mqtt connected as %s", id.c_str());
      publish(status_topic, "online", true);
      JsonDocument h;
      h["fw"] = ORB_FW_VERSION;
      h["model"] = ORB_MODEL;
      h["w"] = 466;
      h["h"] = 466;
      JsonArray caps = h["caps"].to<JsonArray>();
      caps.add("touch");
      if (ORB_LED_PIN >= 0) caps.add("led");
      String hello;
      serializeJson(h, hello);
      publish(prefix + "hello", hello, true);
      for (const char* t : {"config", "inbox", "led", "notice"})
        esp_mqtt_client_subscribe(mqtt, (prefix + t).c_str(), 1);
      esp_mqtt_client_subscribe(mqtt, "orb/all/stickers", 1);
      ui_post(UiCmdType::Waiting);  // config (retained) follows right away if we're known
      break;
    }
    case MQTT_EVENT_DISCONNECTED:
      if (mqtt_connected) log_w("mqtt disconnected");
      mqtt_connected = false;
      break;
    case MQTT_EVENT_DATA: {
      if (e->current_data_offset == 0) rx_partial = "";
      rx_partial.concat(e->data, e->data_len);
      if (e->current_data_offset + e->data_len < e->total_data_len) break;
      auto* m = new RxMsg{String(e->topic, e->topic_len), rx_partial, e->retain};
      rx_partial = "";
      if (xQueueSend(rx_queue, &m, 0) != pdTRUE) {
        log_w("rx queue full");
        delete m;
      }
      break;
    }
    case MQTT_EVENT_ERROR:
      log_w("mqtt error type %d", e->error_handle ? e->error_handle->error_type : -1);
      break;
    default:
      break;
  }
}

static void mqtt_start() {
  static String host = settings.mqtt_host, user = settings.mqtt_user, pass = settings.mqtt_pass;
  esp_mqtt_client_config_t cfg = {};
  cfg.broker.address.hostname = host.c_str();
  cfg.broker.address.port = settings.mqtt_port;
  cfg.broker.address.transport = MQTT_TRANSPORT_OVER_TCP;
  cfg.credentials.client_id = id.c_str();
  cfg.credentials.username = user.c_str();
  cfg.credentials.authentication.password = pass.c_str();
  cfg.session.last_will.topic = status_topic.c_str();
  cfg.session.last_will.msg = "offline";
  cfg.session.last_will.msg_len = 7;
  cfg.session.last_will.qos = 1;
  cfg.session.last_will.retain = true;
  cfg.session.keepalive = 30;
  cfg.network.reconnect_timeout_ms = 5000;
  cfg.network.timeout_ms = 10000;
  cfg.buffer.size = 16384;
  cfg.buffer.out_size = 4096;
  mqtt = esp_mqtt_client_init(&cfg);
  esp_mqtt_client_register_event(mqtt, MQTT_EVENT_ANY, mqtt_event, nullptr);
  esp_mqtt_client_start(mqtt);
}

// ---------------------------------------------------------------- Wi-Fi
static void run_portal() {
  WiFiManager wm;
  wm.setDebugOutput(false);
  wm.setTitle("Orb Messenger");
  WiFiManagerParameter p_host("host", "Broker host", settings.mqtt_host.c_str(), 64);
  WiFiManagerParameter p_port("port", "Broker port", String(settings.mqtt_port).c_str(), 6);
  WiFiManagerParameter p_user("user", "Broker user", settings.mqtt_user.c_str(), 32);
  WiFiManagerParameter p_pass("pass", "Broker password", settings.mqtt_pass.c_str(), 64, "type=\"password\"");
  wm.addParameter(&p_host);
  wm.addParameter(&p_port);
  wm.addParameter(&p_user);
  wm.addParameter(&p_pass);
  wm.setConfigPortalBlocking(true);
  wm.setConfigPortalTimeout(0);
  String ap = "Orb-" + id.substring(4);
  ap.toUpperCase();
  wm.setAPCallback([&](WiFiManager*) { ui_post(UiCmdType::Portal, "", ap); });
  log_i("starting portal %s", ap.c_str());
  ui_post(UiCmdType::Portal, "", ap);
  bool ok = wm.startConfigPortal(ap.c_str());
  if (ok && WiFi.isConnected()) {
    settings.wifi_ssid = WiFi.SSID();
    settings.wifi_pass = WiFi.psk();
    settings.mqtt_host = p_host.getValue();
    settings.mqtt_port = atoi(p_port.getValue());
    settings.mqtt_user = p_user.getValue();
    settings.mqtt_pass = p_pass.getValue();
    settings.saveNetwork();
    log_i("portal saved ssid=%s broker=%s", settings.wifi_ssid.c_str(), settings.mqtt_host.c_str());
  } else {
    log_w("portal ended without a connection");
  }
  delay(500);
  ESP.restart();
}

static void wifi_connect() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(id.c_str());
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(settings.wifi_ssid.c_str(), settings.wifi_pass.c_str());
  log_i("wifi connecting to %s", settings.wifi_ssid.c_str());
  uint32_t start = millis();
  while (!WiFi.isConnected()) {
    if (!unreachable_shown && millis() - start > 10000) {
      unreachable_shown = true;
      ui_post(UiCmdType::Unreachable, "", "wifi: " + settings.wifi_ssid);
    }
    delay(250);
  }
  log_i("wifi up: %s", WiFi.localIP().toString().c_str());
}

// ---------------------------------------------------------------- task
static void net_task(void*) {
  id = orb_id();
  prefix = "orb/" + id + "/";
  status_topic = prefix + "status";
  rx_queue = xQueueCreate(16, sizeof(RxMsg*));

  if (!settings.hasWifi()) run_portal();  // never returns
  wifi_connect();
  setenv("TZ", settings.tz.c_str(), 1);
  tzset();
  configTzTime(settings.tz.c_str(), "pool.ntp.org", "time.google.com");
  mqtt_start();
  down_since = millis();

  uint32_t last_heap = 0;
  for (;;) {
    RxMsg* m = nullptr;
    while (xQueueReceive(rx_queue, &m, 0) == pdTRUE) {
      handle_rx(m);
      delete m;
    }
    NetCmd* c = nullptr;
    while (xQueueReceive(net_queue, &c, 0) == pdTRUE) {
      if (c->type == NetCmdType::Read) {
        publish_event("read", c->msg_id);
        settings.last_read_id = c->msg_id;
        settings.saveState();
      } else if (c->type == NetCmdType::React) {
        publish_event("react", c->msg_id, c->sticker);
      }
      delete c;
    }

    bool up = mqtt_connected && WiFi.isConnected();
    if (up) {
      down_since = 0;
      unreachable_shown = false;
    } else {
      if (!down_since) down_since = millis();
      if (!unreachable_shown && millis() - down_since > 10000) {
        unreachable_shown = true;
        log_w("broker unreachable for 10 s");
        ui_post(UiCmdType::Unreachable, "", settings.mqtt_host);
      }
    }

    if (millis() - last_heap > 3600000UL) {
      last_heap = millis();
      log_i("heap free %u KB, psram free %u KB, rssi %d", ESP.getFreeHeap() / 1024,
            ESP.getFreePsram() / 1024, WiFi.RSSI());
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

void net_start() { xTaskCreatePinnedToCore(net_task, "net", 24576, nullptr, 2, nullptr, 0); }
