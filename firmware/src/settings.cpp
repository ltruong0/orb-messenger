#include "settings.h"

#include <Preferences.h>

Settings settings;
static const char* NS = "orb";

// Build-time defaults from the gitignored secrets.ini (make fw-secrets).
#ifndef ORB_WIFI_SSID
#define ORB_WIFI_SSID ""
#endif
#ifndef ORB_WIFI_PASS
#define ORB_WIFI_PASS ""
#endif
#ifndef ORB_MQTT_HOST
#define ORB_MQTT_HOST "mqtt.marcellolab.com"
#endif
#ifndef ORB_MQTT_PORT
#define ORB_MQTT_PORT 1883
#endif
#ifndef ORB_MQTT_USER
#define ORB_MQTT_USER "orb"
#endif
#ifndef ORB_MQTT_PASS
#define ORB_MQTT_PASS ""
#endif

void Settings::load() {
  Preferences p;
  p.begin(NS, false);  // read-write so the namespace is created on first boot
  auto str = [&](const char* k, const String& dflt) { return p.isKey(k) ? p.getString(k) : dflt; };
  wifi_ssid = str("wifi_ssid", ORB_WIFI_SSID);
  wifi_pass = str("wifi_pass", ORB_WIFI_PASS);
  mqtt_host = str("mqtt_host", ORB_MQTT_HOST);
  mqtt_port = p.isKey("mqtt_port") ? p.getUShort("mqtt_port") : ORB_MQTT_PORT;
  mqtt_user = str("mqtt_user", ORB_MQTT_USER);
  mqtt_pass = str("mqtt_pass", ORB_MQTT_PASS);
  tz = str("tz", tz);
  last_read_id = str("last_read", "");
  last_received_id = str("last_recv", "");
  stk_rev = p.isKey("stk_rev") ? p.getInt("stk_rev") : 0;
  p.end();
}

void Settings::saveNetwork() {
  Preferences p;
  p.begin(NS, false);
  p.putString("wifi_ssid", wifi_ssid);
  p.putString("wifi_pass", wifi_pass);
  p.putString("mqtt_host", mqtt_host);
  p.putUShort("mqtt_port", mqtt_port);
  p.putString("mqtt_user", mqtt_user);
  p.putString("mqtt_pass", mqtt_pass);
  p.putString("tz", tz);
  p.end();
}

void Settings::saveState() {
  Preferences p;
  p.begin(NS, false);
  p.putString("last_read", last_read_id);
  p.putString("last_recv", last_received_id);
  p.putInt("stk_rev", stk_rev);
  p.end();
}

void Settings::clearWifi() {
  Preferences p;
  p.begin(NS, false);
  p.remove("wifi_ssid");
  p.remove("wifi_pass");
  p.end();
  wifi_ssid = "";
  wifi_pass = "";
}
