#include "ui.h"

#include <ArduinoJson.h>
#include <LittleFS.h>
#include <lvgl.h>
#include <src/misc/cache/instance/lv_image_cache.h>  // lv_image_cache_drop isn't in lvgl.h
#include <time.h>

#include "board.h"
#include "emoji.h"
#include "led.h"
#include "orb.h"
#include "settings.h"

enum class St { Boot, Portal, Unreachable, Waiting, Pairing, Idle, Unread, Show, Receipt, Night, Sleep, Nothing, Updating };
static const char* ST_NAMES[] = {"boot", "portal", "unreachable", "waiting", "pairing", "idle", "unread",
                                 "show", "receipt", "night", "sleep", "nothing", "updating"};

#ifndef ORB_SLEEP_AFTER_MS
#define ORB_SLEEP_AFTER_MS (3UL * 60 * 60 * 1000)  // screen off after 3 h without a tap or a message
#endif
#ifndef ORB_RESHOW_MS
#define ORB_RESHOW_MS (10UL * 60 * 1000)  // a tap re-opens a read message for this long
#endif

static St state = St::Boot, prev_state = St::Idle, night_resume = St::Idle;
static lv_obj_t* root = nullptr;
static lv_obj_t* content = nullptr;
static lv_obj_t* tap_layer = nullptr;
static lv_timer_t* anim_timer = nullptr;
static lv_obj_t* eye_l = nullptr;
static lv_obj_t* eye_r = nullptr;
static lv_obj_t* eyes = nullptr;
static lv_obj_t* upd_arc = nullptr;
static lv_obj_t* upd_pct = nullptr;
static lv_obj_t* upd_status = nullptr;

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
static uint32_t last_activity = 0;  // last tap / message / receipt, for sleep
static uint32_t read_at = 0;        // when the current message was opened (0 = not this boot)
static int drift_x = 0, drift_y = -70;  // idle eyes offset, nudged now and then against burn-in

// Montserrat copies whose fallback is an image font of the hub-rendered emoji PNGs.
static lv_font_t F44, F36, F30, F20;
static lv_font_t* emoji_font = nullptr;

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
  eye_l = eye_r = eyes = upd_arc = upd_pct = upd_status = nullptr;
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
  const lv_font_t* fonts[] = {&F44, &F36, &F30};
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

// "14:02" if ts is today, else the weekday; "" when the clock isn't set.
static String when_text(long ts) {
  if (!ts || !time_valid()) return "";
  time_t now = time(nullptr), t = ts;
  struct tm a, b;
  localtime_r(&now, &a);
  localtime_r(&t, &b);
  char buf[12];
  strftime(buf, sizeof(buf), a.tm_yday == b.tm_yday && a.tm_year == b.tm_year ? "%H:%M" : "%A", &b);
  return String(buf);
}

static void activity() { last_activity = millis(); }

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

// ---------------------------------------------------------------- idle face
// Two rounded boxes. Every few seconds one "mood" plays as a go-hold-return
// animation, so the face never sits still (burn-in) and feels alive.
static const int EYE_W = 56, EYE_H = 96, EYE_DX = 70, EYE_Y = -70;

static void ex_h(void* o, int32_t v) { lv_obj_set_height((lv_obj_t*)o, v); }
static void ex_w(void* o, int32_t v) { lv_obj_set_width((lv_obj_t*)o, v); }
static void ex_x(void* o, int32_t v) { lv_obj_set_x((lv_obj_t*)o, v); }
static void ex_y(void* o, int32_t v) { lv_obj_set_y((lv_obj_t*)o, v); }

// from -> to over `go` ms, hold, back over `back` ms; `plays` times with 120 ms between.
static void tween(lv_obj_t* o, lv_anim_exec_xcb_t cb, int32_t from, int32_t to, uint32_t go,
                  uint32_t hold, uint32_t back, uint32_t plays = 1) {
  lv_anim_t a;
  lv_anim_init(&a);
  lv_anim_set_var(&a, o);
  lv_anim_set_exec_cb(&a, cb);
  lv_anim_set_values(&a, from, to);
  lv_anim_set_duration(&a, go);
  lv_anim_set_playback_delay(&a, hold);
  lv_anim_set_playback_duration(&a, back);
  lv_anim_set_repeat_count(&a, plays);
  lv_anim_set_repeat_delay(&a, 120);
  lv_anim_start(&a);
}

enum Mood { Blink, DoubleBlink, Wink, Look, Happy, Sleepy, Surprise, Curious, Bounce, Asleep, MOOD_COUNT };
static const uint8_t MOOD_WEIGHT[MOOD_COUNT] = {10, 2, 2, 4, 2, 2, 2, 2, 1, 2};

// Plays one mood; returns roughly how long it takes.
static uint32_t play_mood(Mood m) {
  lv_obj_t* both[] = {eye_l, eye_r};
  switch (m) {
    case Blink:
      for (auto e : both) tween(e, ex_h, EYE_H, 8, 90, 0, 110);
      return 200;
    case DoubleBlink:
      for (auto e : both) tween(e, ex_h, EYE_H, 8, 80, 0, 90, 2);
      return 460;
    case Wink:
      tween(random(2) ? eye_l : eye_r, ex_h, EYE_H, 8, 100, 250, 140);
      return 490;
    case Look: {
      int d = random(2) ? 24 : -24;
      tween(eye_l, ex_x, -EYE_DX, -EYE_DX + d, 220, 900, 260);
      tween(eye_r, ex_x, EYE_DX, EYE_DX + d, 220, 900, 260);
      return 1380;
    }
    case Happy:
      for (auto e : both) {
        tween(e, ex_h, EYE_H, 34, 180, 1400, 260);
        tween(e, ex_y, 0, -16, 180, 1400, 260);
      }
      return 1840;
    case Sleepy:
      for (auto e : both) {
        tween(e, ex_h, EYE_H, 46, 900, 2200, 500);
        tween(e, ex_y, 0, 14, 900, 2200, 500);
      }
      return 3600;
    case Surprise:
      for (auto e : both) {
        tween(e, ex_w, EYE_W, 74, 140, 700, 220);
        tween(e, ex_h, EYE_H, 118, 140, 700, 220);
      }
      return 1060;
    case Curious: {
      lv_obj_t* up = random(2) ? eye_l : eye_r;
      lv_obj_t* dn = up == eye_l ? eye_r : eye_l;
      tween(up, ex_h, EYE_H, 114, 240, 1200, 260);
      tween(up, ex_y, 0, -10, 240, 1200, 260);
      tween(dn, ex_h, EYE_H, 70, 240, 1200, 260);
      tween(dn, ex_y, 0, 10, 240, 1200, 260);
      return 1700;
    }
    case Bounce:
      tween(eyes, ex_y, drift_y, drift_y - 16, 110, 0, 130, 2);
      return 600;
    case Asleep: {
      // eyes close to thin lines and a "z z Z" floats up and fades
      for (auto e : both) tween(e, ex_h, EYE_H, 10, 600, 3400, 400);
      lv_obj_t* z = label(content, "z z Z", &lv_font_montserrat_24, GREY, drift_y - 70, 120);
      lv_obj_align(z, LV_ALIGN_CENTER, drift_x + 75, drift_y - 70);
      lv_obj_set_style_text_letter_space(z, 4, 0);
      lv_anim_t a;
      lv_anim_init(&a);
      lv_anim_set_var(&a, z);
      lv_anim_set_exec_cb(&a, ex_y);
      lv_anim_set_values(&a, drift_y - 70, drift_y - 130);
      lv_anim_set_delay(&a, 500);
      lv_anim_set_duration(&a, 3200);
      lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
      lv_anim_start(&a);
      lv_anim_set_exec_cb(&a, [](void* o, int32_t v) { lv_obj_set_style_opa((lv_obj_t*)o, v, 0); });
      lv_anim_set_values(&a, 255, 0);
      lv_anim_set_delay(&a, 1500);
      lv_anim_set_duration(&a, 2200);
      lv_anim_set_path_cb(&a, lv_anim_path_linear);
      lv_anim_set_completed_cb(&a, [](lv_anim_t* an) { lv_obj_delete((lv_obj_t*)an->var); });
      lv_anim_start(&a);
      return 4500;
    }
    default:
      return 0;
  }
}

static void mood_timer_cb(lv_timer_t* t) {
  if (!eye_l) return;
  int total = 0;
  for (auto w : MOOD_WEIGHT) total += w;
  int r = random(0, total), m = 0;
  while (r >= MOOD_WEIGHT[m]) r -= MOOD_WEIGHT[m++];
  uint32_t dur = play_mood((Mood)m);
  // Nudge the whole face a few px now and then so no pixel is lit for hours.
  if (m != Bounce && random(0, 6) == 0) {
    drift_x = random(-8, 9);
    drift_y = EYE_Y + random(-8, 9);
    lv_obj_align(eyes, LV_ALIGN_CENTER, drift_x, drift_y);
  }
  lv_timer_set_period(t, dur + random(1800, 5200));
}

static void show_idle() {
  clear();
  set_state(St::Idle);
  eyes = lv_obj_create(content);
  lv_obj_remove_style_all(eyes);
  lv_obj_set_size(eyes, 320, 160);  // room for look (+-24 px) and surprise without clipping
  lv_obj_remove_flag(eyes, (lv_obj_flag_t)(LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE));
  lv_obj_add_flag(eyes, LV_OBJ_FLAG_OVERFLOW_VISIBLE);
  lv_obj_align(eyes, LV_ALIGN_CENTER, drift_x, drift_y);
  eye_l = box(eyes, EYE_W, EYE_H, WHITE, 28);
  eye_r = box(eyes, EYE_W, EYE_H, WHITE, 28);
  lv_obj_set_align(eye_l, LV_ALIGN_CENTER);
  lv_obj_set_align(eye_r, LV_ALIGN_CENTER);
  lv_obj_set_x(eye_l, -EYE_DX);
  lv_obj_set_x(eye_r, EYE_DX);
  if (!cfg_name.isEmpty()) label(content, cfg_name.c_str(), &lv_font_montserrat_20, GREY, 70);
  anim_timer = lv_timer_create(mood_timer_cb, random(2000, 5000), nullptr);
}

// Tap in idle with nothing recent: calm eyes and a note, back to idle after a moment.
static void show_nothing_new() {
  clear();
  set_state(St::Nothing);
  deadline = millis() + 2500;
  for (int dx : {-EYE_DX, EYE_DX}) {
    lv_obj_t* e = box(content, EYE_W, 30, WHITE, 15);
    lv_obj_align(e, LV_ALIGN_CENTER, dx, -70);
  }
  label(content, "no new messages", &lv_font_montserrat_24, WHITE, 20);
  if (!msg.isNull()) {
    String from = msg["from_name"] | "";
    String when = when_text(msg["ts"] | 0L);
    String sub = "last one from " + from + (when.isEmpty() ? "" : " \xC2\xB7 " + when);
    label(content, sub.c_str(), &lv_font_montserrat_16, DIM, 70);
  }
}

static void heart(lv_obj_t* parent, int y) {
  // Two circles and a rotated square, like the mockup's flat heart.
  lv_obj_t* h = lv_obj_create(parent);
  lv_obj_remove_style_all(h);
  lv_obj_set_size(h, 110, 100);
  lv_obj_remove_flag(h, (lv_obj_flag_t)(LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE));
  lv_obj_add_flag(h, LV_OBJ_FLAG_OVERFLOW_VISIBLE);
  lv_obj_align(h, LV_ALIGN_CENTER, 0, y);
  // Diamond of side S centred at (0, 8); the circles sit on the midpoints of its
  // two upper edges (+-S/4, 8 - S/4) with diameter S so they hide its side corners.
  const int S = 60;
  lv_obj_t* sq = box(h, S, S, RED, 4);
  lv_obj_align(sq, LV_ALIGN_CENTER, 0, 8);
  lv_obj_set_style_transform_pivot_x(sq, S / 2, 0);
  lv_obj_set_style_transform_pivot_y(sq, S / 2, 0);
  lv_obj_set_style_transform_rotation(sq, 450, 0);
  lv_obj_t* c1 = box(h, S + 2, S + 2, RED, LV_RADIUS_CIRCLE);
  lv_obj_align(c1, LV_ALIGN_CENTER, -S / 4, 8 - S / 4);
  lv_obj_t* c2 = box(h, S + 2, S + 2, RED, LV_RADIUS_CIRCLE);
  lv_obj_align(c2, LV_ALIGN_CENTER, S / 4, 8 - S / 4);
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
  emoji_reset();
  String text_s = emoji_rewrite(msg["text"] | "");
  const char* text = text_s.c_str();
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
      lv_obj_t* cap = label(content, text, &F20, WHITE, 100, 300);
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
    if (*text) label(content, text, &F30, WHITE, 65);
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

// OTA in progress: the orb is deliberately unresponsive, say so.
static void show_updating(const String& version) {
  clear();
  set_state(St::Updating);
  deadline = 0;
  if (!screen_on) {
    screen_on = true;
    board_screen(true);
  }
  label(content, "updating", &lv_font_montserrat_20, GREY, -150);
  String v = "to " + version;
  label(content, v.c_str(), &lv_font_montserrat_24, WHITE, -112);
  upd_arc = lv_arc_create(content);
  lv_obj_remove_style(upd_arc, nullptr, LV_PART_KNOB);
  lv_obj_set_size(upd_arc, 150, 150);
  lv_arc_set_rotation(upd_arc, 270);
  lv_arc_set_bg_angles(upd_arc, 0, 360);
  lv_arc_set_range(upd_arc, 0, 100);
  lv_arc_set_value(upd_arc, 0);
  lv_obj_set_style_arc_width(upd_arc, 10, LV_PART_MAIN);
  lv_obj_set_style_arc_width(upd_arc, 10, LV_PART_INDICATOR);
  lv_obj_set_style_arc_color(upd_arc, lv_color_hex(0x2A2A36), LV_PART_MAIN);
  lv_obj_set_style_arc_color(upd_arc, lv_color_hex(0x4682FF), LV_PART_INDICATOR);
  lv_obj_remove_flag(upd_arc, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_align(upd_arc, LV_ALIGN_CENTER, 0, -5);
  upd_pct = label(content, "0%", &lv_font_montserrat_36, WHITE, -5, 140);
  upd_status = label(content, "downloading", &lv_font_montserrat_16, GREY, 105);
  label(content, "don't unplug me", &lv_font_montserrat_16, DIM, 150);
}

static void update_progress(const String& version, const String& status, int pct) {
  if (state != St::Updating) show_updating(version);
  if (status.startsWith("failed")) {
    lv_label_set_text(upd_status, status.c_str());
    lv_obj_set_style_text_color(upd_status, RED, 0);
    lv_obj_set_style_arc_color(upd_arc, RED, LV_PART_INDICATOR);
    deadline = millis() + 15000;  // then back to whatever we were doing
    return;
  }
  lv_label_set_text(upd_status, status.c_str());
  if (pct >= 0) {
    lv_arc_set_value(upd_arc, pct);
    lv_label_set_text_fmt(upd_pct, "%d%%", pct);
  }
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

// ---------------------------------------------------------------- sleep (inactivity)
static void enter_sleep() {
  log_i("no activity for %lu min, screen off", (unsigned long)(ORB_SLEEP_AFTER_MS / 60000));
  set_state(St::Sleep);
  clear();
  screen_on = false;
  board_screen(false);
}

// Panel back on without drawing; the caller picks the screen.
static void wake_screen() {
  set_state(St::Idle);
  screen_on = true;
  board_screen(true);
  activity();
}

static void leave_sleep() {
  wake_screen();
  show_resting();
}

// ---------------------------------------------------------------- input
static void on_tap() {
  activity();
  switch (state) {
    case St::Sleep:
      leave_sleep();
      break;
    case St::Nothing:
      show_idle();
      break;
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
      read_at = millis();
      show_message();
      break;
    case St::Idle:
      // Re-open only something recent; an old read message stays in the phone's history.
      if (!msg.isNull() && read_at && millis() - read_at < ORB_RESHOW_MS) show_message();
      else show_nothing_new();
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
  activity();
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
      if (state != St::Portal && state != St::Night && state != St::Sleep && state != St::Updating) show_unreachable();
      break;
    case UiCmdType::Waiting:
      connected = true;
      led_local_unreachable(false);
      if (!has_config || state == St::Unreachable) {
        has_config = false;
        if (state != St::Night && state != St::Sleep && state != St::Updating) show_waiting();
      }
      break;
    case UiCmdType::Pairing:
      connected = true;
      has_config = false;
      led_local_unreachable(false);
      if (state == St::Sleep) wake_screen();
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
      if (new_id != cur_id) {
        read_at = 0;
        activity();
      }
      if (state == St::Night || state == St::Updating) break;  // shown later
      if (state == St::Sleep) {
        if (!msg_unread) break;  // a replayed, already-read message doesn't wake the screen
        wake_screen();
      }
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
      if (state == St::Night || state == St::Portal || state == St::Unreachable || state == St::Updating) break;
      if (state == St::Sleep) wake_screen();
      activity();
      if (state != St::Receipt) prev_state = state;
      String by = d["by"] | "someone";
      String type = d["type"] | "";
      show_receipt(type == "reaction" ? by + " sent " + String(d["sticker"] | "a sticker") : by + " read it");
      break;
    }
    case UiCmdType::Stickers:
      if (state == St::Show) show_message();
      break;
    case UiCmdType::Update:
      update_progress(c->json, c->a, c->n);
      break;
  }
}

// ---------------------------------------------------------------- public
static const void* emoji_cb(const lv_font_t*, uint32_t unicode, uint32_t, int32_t* offset_y, void*) {
  const char* file = emoji_path(unicode);
  if (!file) return nullptr;
  static char src[48];
  snprintf(src, sizeof(src), "L:%s", file);
  *offset_y = 0;
  return src;
}

static void fonts_init() {
  emoji_font = lv_imgfont_create(44, emoji_cb, nullptr);
  F44 = lv_font_montserrat_44;
  F36 = lv_font_montserrat_36;
  F30 = lv_font_montserrat_30;
  F20 = lv_font_montserrat_20;
  for (lv_font_t* f : {&F44, &F36, &F30, &F20}) f->fallback = emoji_font;
}

void ui_init() {
  fonts_init();
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
  activity();
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
    if (state == St::Show || state == St::Nothing) show_idle();
    else if (state == St::Updating) show_resting();
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
    if (q && state != St::Night && state != St::Portal && state != St::Unreachable && state != St::Updating) enter_night();
    else if (!q && state == St::Night) leave_night();
  }

  // Burn-in guard: nothing happened for a long time, turn the panel off.
  if ((state == St::Idle || state == St::Unread) && now - last_activity > ORB_SLEEP_AFTER_MS) enter_sleep();

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
