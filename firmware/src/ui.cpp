#include "ui.h"

#include <ArduinoJson.h>
#include <LittleFS.h>
#include <lvgl.h>
#include <src/misc/cache/instance/lv_image_cache.h>  // lv_image_cache_drop isn't in lvgl.h
#include <time.h>

#include "board.h"
#include "led.h"
#include "orb.h"
#include "settings.h"

enum class St { Boot, Portal, Unreachable, Waiting, Pairing, Idle, Unread, Show, Receipt, Night };
static const char* ST_NAMES[] = {"boot",   "portal", "unreachable", "waiting", "pairing",
                                 "idle",   "unread", "show",        "receipt", "night"};

static St state = St::Boot, prev_state = St::Idle, night_resume = St::Idle;
static lv_obj_t* root = nullptr;
static lv_obj_t* content = nullptr;
static lv_obj_t* tap_layer = nullptr;
static lv_timer_t* anim_timer = nullptr;
static lv_obj_t* eye_l = nullptr;
static lv_obj_t* eye_r = nullptr;
static lv_obj_t* eyes = nullptr;

// Current state the hub gave us
static bool has_config = false, connected = false;
static String cfg_name, cfg_quiet_start, cfg_quiet_end;
static JsonDocument msg;  // current inbox payload ({} if none)
static String msg_photo;  // LittleFS path of the fetched photo
static bool msg_unread = false;
static String last_host = "mqtt.marcellolab.com";
static String ap_name;

static uint32_t deadline = 0;    // show / receipt / night-wake timeout
static uint32_t boot_held = 0;   // BOOT button hold start
static uint32_t quiet_check = 0;
static bool screen_on = true;

static const lv_color_t WHITE = lv_color_hex(0xF4F4F8);
static const lv_color_t GREY = lv_color_hex(0x9A9AA8);
static const lv_color_t DIM = lv_color_hex(0x6E6E7A);
static const lv_color_t RED = lv_color_hex(0xFF5A5A);
static const lv_color_t AMBER = lv_color_hex(0xF5AA28);

// ---------------------------------------------------------------- helpers
static void clear() {
  if (anim_timer) {
    lv_timer_delete(anim_timer);
    anim_timer = nullptr;
  }
  lv_anim_delete(nullptr, nullptr);
  eye_l = eye_r = eyes = nullptr;
  lv_obj_clean(content);
}

static lv_obj_t* label(lv_obj_t* parent, const char* txt, const lv_font_t* font, lv_color_t color,
                       int y, int width = 330) {
  lv_obj_t* l = lv_label_create(parent);
  lv_label_set_text(l, txt);
  lv_obj_set_style_text_font(l, font, 0);
  lv_obj_set_style_text_color(l, color, 0);
  lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
  lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
  lv_obj_set_width(l, width);
  lv_obj_align(l, LV_ALIGN_CENTER, 0, y);
  return l;
}

static lv_obj_t* box(lv_obj_t* parent, int w, int h, lv_color_t color, int radius) {
  lv_obj_t* o = lv_obj_create(parent);
  lv_obj_remove_style_all(o);
  lv_obj_set_size(o, w, h);
  lv_obj_set_style_bg_color(o, color, 0);
  lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
  lv_obj_set_style_radius(o, radius, 0);
  lv_obj_remove_flag(o, (lv_obj_flag_t)(LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE));
  return o;
}

// Largest of 44 / 36 / 30 px where the text fits in max_lines inside 330 px.
static const lv_font_t* fit_font(const char* txt, int max_lines) {
  const lv_font_t* fonts[] = {&lv_font_montserrat_44, &lv_font_montserrat_36, &lv_font_montserrat_30};
  for (auto f : fonts) {
    lv_point_t sz;
    lv_text_get_size(&sz, txt, f, 0, 4, 330, LV_TEXT_FLAG_NONE);
    int lh = lv_font_get_line_height(f) + 4;
    if (sz.y <= max_lines * lh) return f;
  }
  return fonts[2];
}

static bool time_valid() {
  time_t now = time(nullptr);
  struct tm t;
  localtime_r(&now, &t);
  return t.tm_year >= 2024 - 1900;
}

static String clock_text(long ts) {
  if (!time_valid()) return "";
  time_t t = ts;
  struct tm tmv;
  localtime_r(&t, &tmv);
  char buf[8];
  strftime(buf, sizeof(buf), "%H:%M", &tmv);
  return String(buf);
}

static void set_state(St s) {
  if (s != state) log_i("state %s -> %s", ST_NAMES[(int)state], ST_NAMES[(int)s]);
  state = s;
}

// ---------------------------------------------------------------- screens
static void show_portal() {
  clear();
  set_state(St::Portal);
  label(content, "set me up", &lv_font_montserrat_20, GREY, -120);
  label(content, "join the Wi-Fi network", &lv_font_montserrat_24, WHITE, -60);
  label(content, ap_name.c_str(), &lv_font_montserrat_36, WHITE, -10);
  label(content, "then open\nhttp://192.168.4.1", &lv_font_montserrat_20, GREY, 70);
  label(content, orb_id().c_str(), &lv_font_montserrat_16, DIM, 140);
}

static lv_obj_t* spinner(lv_obj_t* parent, lv_color_t color, int y) {
  lv_obj_t* s = lv_spinner_create(parent);
  lv_spinner_set_anim_params(s, 1200, 70);
  lv_obj_set_size(s, 72, 72);
  lv_obj_set_style_arc_width(s, 8, LV_PART_MAIN);
  lv_obj_set_style_arc_width(s, 8, LV_PART_INDICATOR);
  lv_obj_set_style_arc_color(s, lv_color_hex(0x2A2A36), LV_PART_MAIN);
  lv_obj_set_style_arc_color(s, color, LV_PART_INDICATOR);
  lv_obj_remove_flag(s, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_align(s, LV_ALIGN_CENTER, 0, y);
  return s;
}

static void show_unreachable() {
  clear();
  set_state(St::Unreachable);
  spinner(content, AMBER, -90);
  label(content, "Can't reach the hub...", &lv_font_montserrat_30, WHITE, 10);
  String sub = last_host + " \xC2\xB7 hold BOOT 5 s to\nredo Wi-Fi setup";
  label(content, sub.c_str(), &lv_font_montserrat_16, GREY, 75);
}

static void show_waiting() {
  clear();
  set_state(St::Waiting);
  spinner(content, lv_color_hex(0x4682FF), -90);
  label(content, "Connected", &lv_font_montserrat_30, WHITE, 10);
  label(content, "waiting for the hub to notice me", &lv_font_montserrat_16, GREY, 60);
  label(content, orb_id().c_str(), &lv_font_montserrat_16, DIM, 130);
}

static void show_pairing(const String& code) {
  clear();
  set_state(St::Pairing);
  label(content, "pair me", &lv_font_montserrat_20, GREY, -135);
  lv_obj_t* c = label(content, code.c_str(), &lv_font_montserrat_48, WHITE, -40);
  lv_obj_set_style_text_letter_space(c, 6, 0);
  lv_obj_update_layout(c);
  lv_obj_set_style_transform_pivot_x(c, lv_obj_get_width(c) / 2, 0);
  lv_obj_set_style_transform_pivot_y(c, lv_obj_get_height(c) / 2, 0);
  lv_obj_set_style_transform_scale(c, 512, 0);  // 2x: montserrat tops out at 48 px
  label(content, orb_id().c_str(), &lv_font_montserrat_20, GREY, 60);
  label(content, "enter this code in the app", &lv_font_montserrat_20, WHITE, 115);
}

static void blink_anim_cb(void* obj, int32_t v) { lv_obj_set_height((lv_obj_t*)obj, v); }

static void blink_timer_cb(lv_timer_t* t) {
  if (!eye_l) return;
  for (auto e : {eye_l, eye_r}) {
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, e);
    lv_anim_set_exec_cb(&a, blink_anim_cb);
    lv_anim_set_values(&a, 96, 8);
    lv_anim_set_duration(&a, 90);
    lv_anim_set_playback_duration(&a, 110);
    lv_anim_start(&a);
  }
  // Drift a few px every so often so static pixels don't burn in.
  if (random(0, 8) == 0 && eyes) lv_obj_align(eyes, LV_ALIGN_CENTER, random(-8, 9), random(-8, 9));
  lv_timer_set_period(t, random(3000, 7000));
}

static void show_idle() {
  clear();
  set_state(St::Idle);
  eyes = lv_obj_create(content);
  lv_obj_remove_style_all(eyes);
  lv_obj_set_size(eyes, 200, 120);
  lv_obj_remove_flag(eyes, (lv_obj_flag_t)(LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE));
  lv_obj_align(eyes, LV_ALIGN_CENTER, 0, -30);
  eye_l = box(eyes, 56, 96, WHITE, 28);
  eye_r = box(eyes, 56, 96, WHITE, 28);
  lv_obj_set_align(eye_l, LV_ALIGN_CENTER);
  lv_obj_set_align(eye_r, LV_ALIGN_CENTER);
  lv_obj_set_x(eye_l, -70);
  lv_obj_set_x(eye_r, 70);
  if (!cfg_name.isEmpty()) label(content, cfg_name.c_str(), &lv_font_montserrat_20, GREY, 70);
  anim_timer = lv_timer_create(blink_timer_cb, random(3000, 7000), nullptr);
}

static void heart(lv_obj_t* parent, int y) {
  // Two circles and a rotated square, like the mockup's flat heart.
  lv_obj_t* h = lv_obj_create(parent);
  lv_obj_remove_style_all(h);
  lv_obj_set_size(h, 110, 100);
  lv_obj_remove_flag(h, (lv_obj_flag_t)(LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE));
  lv_obj_add_flag(h, LV_OBJ_FLAG_OVERFLOW_VISIBLE);
  lv_obj_align(h, LV_ALIGN_CENTER, 0, y);
  lv_obj_t* sq = box(h, 62, 62, RED, 6);
  lv_obj_align(sq, LV_ALIGN_CENTER, 0, 8);
  lv_obj_set_style_transform_pivot_x(sq, 31, 0);
  lv_obj_set_style_transform_pivot_y(sq, 31, 0);
  lv_obj_set_style_transform_rotation(sq, 450, 0);
  lv_obj_t* c1 = box(h, 60, 60, RED, LV_RADIUS_CIRCLE);
  lv_obj_align(c1, LV_ALIGN_CENTER, -22, -20);
  lv_obj_t* c2 = box(h, 60, 60, RED, LV_RADIUS_CIRCLE);
  lv_obj_align(c2, LV_ALIGN_CENTER, 22, -20);
}

static void show_unread() {
  clear();
  set_state(St::Unread);
  heart(content, -75);
  String t = "New from " + String(msg["from_name"] | "someone");
  label(content, t.c_str(), &lv_font_montserrat_30, WHITE, 35);
  label(content, "tap to open", &lv_font_montserrat_20, GREY, 85);
}

static bool sticker_path(String& out) {
  const char* key = msg["sticker"] | "";
  if (!*key) return false;
  String p = String("/stk/") + key + ".png";
  if (!LittleFS.exists(p)) return false;
  out = "L:" + p;
  return true;
}

static void show_message() {
  clear();
  set_state(St::Show);
  deadline = millis() + 60000;
  const char* type = msg["type"] | "text";
  const char* text = msg["text"] | "";
  String from = msg["from_name"] | "";
  String when = clock_text(msg["ts"] | 0L);
  String head = when.isEmpty() ? from : from + " \xC2\xB7 " + when;

  if (strcmp(type, "photo") == 0) {
    if (!msg_photo.isEmpty() && LittleFS.exists(msg_photo)) {
      String src = "L:" + msg_photo;
      lv_image_cache_drop(src.c_str());
      lv_obj_t* img = lv_image_create(content);
      lv_image_set_src(img, src.c_str());
      lv_obj_center(img);
    } else {
      label(content, "[photo]", &lv_font_montserrat_24, GREY, -20);
    }
    lv_obj_t* pill = box(content, 0, 36, lv_color_hex(0x101016), 18);
    lv_obj_set_style_bg_opa(pill, LV_OPA_80, 0);
    lv_obj_set_style_pad_hor(pill, 16, 0);
    lv_obj_set_width(pill, LV_SIZE_CONTENT);
    lv_obj_t* l = lv_label_create(pill);
    String pf = "from " + from;
    lv_label_set_text(l, pf.c_str());
    lv_obj_set_style_text_font(l, &lv_font_montserrat_16, 0);
    lv_obj_set_style_text_color(l, WHITE, 0);
    lv_obj_center(l);
    lv_obj_align(pill, LV_ALIGN_CENTER, 0, 150);
    if (*text) {
      lv_obj_t* cap = label(content, text, &lv_font_montserrat_20, WHITE, 100, 300);
      lv_obj_set_style_bg_color(cap, lv_color_hex(0x101016), 0);
      lv_obj_set_style_bg_opa(cap, LV_OPA_70, 0);
      lv_obj_set_style_pad_all(cap, 8, 0);
      lv_obj_set_style_radius(cap, 10, 0);
    }
    return;
  }

  if (strcmp(type, "sticker") == 0) {
    String src;
    if (sticker_path(src)) {
      lv_obj_t* img = lv_image_create(content);
      lv_image_set_src(img, src.c_str());
      lv_obj_align(img, LV_ALIGN_CENTER, 0, *text ? -55 : -10);
    } else {
      label(content, msg["sticker"] | "", &lv_font_montserrat_30, GREY, -55);
    }
    if (*text) label(content, text, &lv_font_montserrat_30, WHITE, 65);
    label(content, head.c_str(), &lv_font_montserrat_16, DIM, -175);
    return;
  }

  label(content, head.c_str(), &lv_font_montserrat_16, DIM, -150);
  label(content, text, fit_font(text, 4), WHITE, -15);
  label(content, "hold to send a heart back", &lv_font_montserrat_16, GREY, 150);
}

static void show_receipt(const String& line) {
  clear();
  set_state(St::Receipt);
  deadline = millis() + 30000;
  for (int dx : {-70, 70}) {
    lv_obj_t* a = lv_arc_create(content);
    lv_obj_remove_style(a, nullptr, LV_PART_KNOB);
    lv_obj_remove_style(a, nullptr, LV_PART_INDICATOR);
    lv_obj_set_size(a, 70, 70);
    lv_arc_set_bg_angles(a, 190, 350);
    lv_obj_set_style_arc_width(a, 12, LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(a, true, LV_PART_MAIN);
    lv_obj_set_style_arc_color(a, WHITE, LV_PART_MAIN);
    lv_obj_remove_flag(a, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_align(a, LV_ALIGN_CENTER, dx, -45);
  }
  label(content, line.c_str(), &lv_font_montserrat_30, WHITE, 50);
}

static void sent_heart_anim() {
  lv_obj_t* l = label(tap_layer, "sent " LV_SYMBOL_OK, &lv_font_montserrat_20, RED, 130);
  lv_anim_t a;
  lv_anim_init(&a);
  lv_anim_set_var(&a, l);
  lv_anim_set_exec_cb(&a, [](void* o, int32_t v) { lv_obj_set_style_opa((lv_obj_t*)o, v, 0); });
  lv_anim_set_values(&a, 255, 0);
  lv_anim_set_delay(&a, 900);
  lv_anim_set_duration(&a, 600);
  lv_anim_set_completed_cb(&a, [](lv_anim_t* an) { lv_obj_delete((lv_obj_t*)an->var); });
  lv_anim_start(&a);
}

// Pick the "resting" screen for the current hub state.
static void show_resting() {
  if (!connected) return show_unreachable();
  if (!has_config) return show_waiting();
  if (!msg.isNull() && msg_unread) return show_unread();
  show_idle();
}

// ---------------------------------------------------------------- quiet hours
static bool in_quiet_hours() {
  if (cfg_quiet_start.isEmpty() || cfg_quiet_end.isEmpty() || !time_valid()) return false;
  time_t now = time(nullptr);
  struct tm t;
  localtime_r(&now, &t);
  int m = t.tm_hour * 60 + t.tm_min;
  int s = cfg_quiet_start.substring(0, 2).toInt() * 60 + cfg_quiet_start.substring(3, 5).toInt();
  int e = cfg_quiet_end.substring(0, 2).toInt() * 60 + cfg_quiet_end.substring(3, 5).toInt();
  return s <= e ? (m >= s && m < e) : (m >= s || m < e);
}

static void enter_night() {
  if (state == St::Night) return;
  night_resume = state == St::Receipt ? prev_state : state;
  set_state(St::Night);
  screen_on = false;
  board_screen(false);
  led_quiet(true);
}

static void leave_night() {
  set_state(night_resume);
  screen_on = true;
  board_screen(true);
  led_quiet(false);
  show_resting();
}

// ---------------------------------------------------------------- input
static void on_tap() {
  switch (state) {
    case St::Night:
      // wake for 30 s
      screen_on = true;
      board_screen(true);
      show_resting();
      set_state(St::Night);  // still night; deadline puts the screen back to sleep
      deadline = millis() + 30000;
      break;
    case St::Unread:
      if (msg_unread) {
        net_post(NetCmdType::Read, msg["id"] | "");
        msg_unread = false;
      }
      show_message();
      break;
    case St::Idle:
      if (!msg.isNull()) show_message();
      break;
    case St::Show:
      show_idle();
      break;
    case St::Receipt:
      set_state(prev_state);
      show_resting();
      break;
    default:
      break;
  }
}

static void on_long_press() {
  if (state == St::Show && !msg.isNull()) {
    net_post(NetCmdType::React, msg["id"] | "", "purple-heart");
    sent_heart_anim();
  }
}

static void tap_event_cb(lv_event_t* e) {
  lv_event_code_t code = lv_event_get_code(e);
  if (code == LV_EVENT_SHORT_CLICKED) on_tap();
  else if (code == LV_EVENT_LONG_PRESSED) on_long_press();
}

// ---------------------------------------------------------------- commands
static void apply_config(const String& json) {
  JsonDocument d;
  if (deserializeJson(d, json)) return;
  has_config = true;
  cfg_name = d["name"] | "";
  if (d["brightness"].is<int>()) board_set_brightness(d["brightness"].as<int>());
  cfg_quiet_start = d["quiet"]["start"] | "";
  cfg_quiet_end = d["quiet"]["end"] | "";
  if (state == St::Pairing || state == St::Waiting || state == St::Unreachable) {
    show_resting();
  } else if (state == St::Idle) {
    show_idle();  // name may have changed
  }
}

static void handle(UiCmd* c) {
  switch (c->type) {
    case UiCmdType::Portal:
      ap_name = c->a;
      show_portal();
      break;
    case UiCmdType::Unreachable:
      connected = false;
      if (!c->a.isEmpty()) last_host = c->a;
      led_local_unreachable(true);
      if (state != St::Portal && state != St::Night) show_unreachable();
      break;
    case UiCmdType::Waiting:
      connected = true;
      led_local_unreachable(false);
      if (!has_config || state == St::Unreachable) {
        has_config = false;
        if (state != St::Night) show_waiting();
      }
      break;
    case UiCmdType::Pairing:
      connected = true;
      has_config = false;
      led_local_unreachable(false);
      if (state != St::Night) show_pairing(c->a);
      break;
    case UiCmdType::Active:
      connected = true;
      led_local_unreachable(false);
      apply_config(c->json);
      break;
    case UiCmdType::Inbox: {
      JsonDocument d;
      if (deserializeJson(d, c->json)) break;
      String new_id = d["id"] | "";
      String cur_id = msg["id"] | "";
      msg = d;
      msg_photo = c->a;
      msg_unread = c->flag;
      if (state == St::Night) break;  // shown when the night ends or on wake
      if (msg_unread) {
        if (state == St::Receipt) prev_state = St::Unread;
        else show_unread();
      } else if (new_id != cur_id || state == St::Unread) {
        if (state == St::Idle || state == St::Unread || state == St::Show) show_idle();
      }
      break;
    }
    case UiCmdType::InboxCleared:
      msg.clear();
      msg_unread = false;
      if (state == St::Unread || state == St::Show) show_idle();
      break;
    case UiCmdType::Led: {
      JsonDocument d;
      if (c->json.isEmpty() || deserializeJson(d, c->json)) {
        led_set("off", "", "");
      } else {
        led_set(d["mode"] | "off", d["color"] | "", d["pattern"] | "");
      }
      break;
    }
    case UiCmdType::Notice: {
      JsonDocument d;
      if (deserializeJson(d, c->json)) break;
      if (state == St::Night || state == St::Portal || state == St::Unreachable) break;
      if (state != St::Receipt) prev_state = state;
      String by = d["by"] | "someone";
      String type = d["type"] | "";
      show_receipt(type == "reaction" ? by + " sent " + String(d["sticker"] | "a sticker") : by + " read it");
      break;
    }
    case UiCmdType::Stickers:
      if (state == St::Show) show_message();
      break;
  }
}

// ---------------------------------------------------------------- public
void ui_init() {
  root = lv_screen_active();
  lv_obj_set_style_bg_color(root, lv_color_black(), 0);
  lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
  lv_obj_remove_flag(root, LV_OBJ_FLAG_SCROLLABLE);
  content = lv_obj_create(root);
  lv_obj_remove_style_all(content);
  lv_obj_set_size(content, LV_PCT(100), LV_PCT(100));
  lv_obj_remove_flag(content, (lv_obj_flag_t)(LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE));
  tap_layer = lv_obj_create(root);
  lv_obj_remove_style_all(tap_layer);
  lv_obj_set_size(tap_layer, LV_PCT(100), LV_PCT(100));
  lv_obj_add_flag(tap_layer, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_remove_flag(tap_layer, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_add_event_cb(tap_layer, tap_event_cb, LV_EVENT_ALL, nullptr);

  label(content, "orb", &lv_font_montserrat_36, WHITE, -20);
  label(content, orb_id().c_str(), &lv_font_montserrat_16, DIM, 30);
  lv_timer_handler();
}

void ui_loop() {
  UiCmd* c = nullptr;
  while (xQueueReceive(ui_queue, &c, 0) == pdTRUE) {
    handle(c);
    delete c;
  }

  uint32_t now = millis();
  if (deadline && now > deadline) {
    deadline = 0;
    if (state == St::Show) show_idle();
    else if (state == St::Receipt) {
      set_state(prev_state);
      show_resting();
    } else if (state == St::Night && screen_on) {
      screen_on = false;
      board_screen(false);
    }
  }

  if (now - quiet_check > 15000) {
    quiet_check = now;
    bool q = in_quiet_hours();
    if (q && state != St::Night && state != St::Portal && state != St::Unreachable) enter_night();
    else if (!q && state == St::Night) leave_night();
  }

  // Hold BOOT for 5 s: forget Wi-Fi and reboot into the portal.
  if (board_boot_pressed()) {
    if (!boot_held) boot_held = now;
    else if (now - boot_held > 5000) {
      log_w("BOOT held 5 s: clearing Wi-Fi and restarting");
      settings.clearWifi();
      delay(100);
      ESP.restart();
    }
  } else {
    boot_held = 0;
  }
}
