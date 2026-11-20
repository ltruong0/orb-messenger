#include "emoji.h"

static String table[EMOJI_MAX];  // index -> file path
static int table_n = 0;

// ---------------------------------------------------------------- utf-8
static uint32_t decode(const char*& p) {
  uint8_t c = (uint8_t)*p;
  if (!c) return 0;
  uint32_t cp;
  int n;
  if (c < 0x80) { cp = c; n = 0; }
  else if ((c & 0xE0) == 0xC0) { cp = c & 0x1F; n = 1; }
  else if ((c & 0xF0) == 0xE0) { cp = c & 0x0F; n = 2; }
  else if ((c & 0xF8) == 0xF0) { cp = c & 0x07; n = 3; }
  else { p++; return 0xFFFD; }
  p++;
  for (int i = 0; i < n; i++) {
    if (((uint8_t)*p & 0xC0) != 0x80) return 0xFFFD;
    cp = (cp << 6) | ((uint8_t)*p & 0x3F);
    p++;
  }
  return cp;
}

static void encode(String& out, uint32_t cp) {
  char b[5] = {0};
  if (cp < 0x80) { b[0] = cp; }
  else if (cp < 0x800) { b[0] = 0xC0 | (cp >> 6); b[1] = 0x80 | (cp & 0x3F); }
  else if (cp < 0x10000) { b[0] = 0xE0 | (cp >> 12); b[1] = 0x80 | ((cp >> 6) & 0x3F); b[2] = 0x80 | (cp & 0x3F); }
  else { b[0] = 0xF0 | (cp >> 18); b[1] = 0x80 | ((cp >> 12) & 0x3F); b[2] = 0x80 | ((cp >> 6) & 0x3F); b[3] = 0x80 | (cp & 0x3F); }
  out += b;
}

// ---------------------------------------------------------------- classes
static bool is_base(uint32_t c) {
  return (c >= 0x1F000 && c <= 0x1FAFF) ||  // pictographs, regional indicators, extended
         (c >= 0x2600 && c <= 0x27BF) ||    // misc symbols, dingbats
         (c >= 0x2300 && c <= 0x23FF) ||    // misc technical (watch, hourglass)
         (c >= 0x2B00 && c <= 0x2BFF) ||    // arrows, star
         (c >= 0x2190 && c <= 0x21FF) || (c >= 0x25AA && c <= 0x25FE) || c == 0x2934 || c == 0x2935 ||
         c == 0x3030 || c == 0x303D || c == 0x3297 || c == 0x3299 || c == 0x2122 || c == 0x2139 ||
         c == 0x24C2 || c == 0x203C || c == 0x2049 || c == 0x00A9 || c == 0x00AE;
}
static bool is_text_default(uint32_t c) { return c == 0x00A9 || c == 0x00AE || c == 0x2122; }
static bool is_modifier(uint32_t c) { return c >= 0x1F3FB && c <= 0x1F3FF; }
static bool is_tag(uint32_t c) { return c >= 0xE0020 && c <= 0xE007F; }
static bool is_regional(uint32_t c) { return c >= 0x1F1E6 && c <= 0x1F1FF; }
static bool is_keycap_base(uint32_t c) { return c == '#' || c == '*' || (c >= '0' && c <= '9'); }

static void hex(String& s, uint32_t cp) {
  char b[8];
  snprintf(b, sizeof(b), "%lx", (unsigned long)cp);
  s += b;
}

// Parses one grapheme at p. If it is an emoji, advances p past it, fills seq, returns true.
static bool take(const char*& p, String& seq) {
  const char* q = p;
  uint32_t c = decode(q);
  if (!c) return false;
  seq = "";
  if (is_keycap_base(c)) {  // 1️⃣ : digit, optional FE0F, U+20E3
    const char* r = q;
    uint32_t n = decode(r);
    if (n == 0xFE0F) n = decode(r);
    if (n != 0x20E3) return false;
    hex(seq, c); seq += "-20e3";
    p = r;
    return true;
  }
  if (!is_base(c)) return false;
  const char* r = q;
  uint32_t n = decode(r);
  if (is_text_default(c) && n != 0xFE0F) return false;  // ©, ® and ™ stay text unless asked
  hex(seq, c);
  if (is_regional(c) && is_regional(n)) {  // flag: two regional indicators
    seq += "-"; hex(seq, n);
    p = r;
    return true;
  }
  // continuation: FE0F (dropped), skin tone, tags, ZWJ + next base
  while (true) {
    r = q;
    n = decode(r);
    if (n == 0xFE0F) { q = r; continue; }
    if (is_modifier(n) || is_tag(n)) { seq += "-"; hex(seq, n); q = r; continue; }
    if (n == 0x200D) {
      const char* r2 = r;
      uint32_t b = decode(r2);
      if (!is_base(b)) break;
      seq += "-200d-"; hex(seq, b);
      q = r2;
      continue;
    }
    break;
  }
  p = q;
  return true;
}

// ---------------------------------------------------------------- api
int emoji_scan(const char* text, String* seqs, int max) {
  int n = 0;
  const char* p = text;
  String seq;
  while (*p) {
    if (!take(p, seq)) { decode(p); continue; }
    bool dup = false;
    for (int i = 0; i < n && !dup; i++) dup = seqs[i] == seq;
    if (!dup && n < max) seqs[n++] = seq;
  }
  return n;
}

String emoji_file(const String& seq) { return "/emo/" + seq + ".png"; }

void emoji_reset() { table_n = 0; }

String emoji_rewrite(const char* text) {
  String out, seq;
  const char* p = text;
  while (*p) {
    const char* start = p;
    if (take(p, seq)) {
      String file = emoji_file(seq);
      int idx = -1;
      for (int i = 0; i < table_n; i++) if (table[i] == file) { idx = i; break; }
      if (idx < 0 && table_n < EMOJI_MAX) { table[table_n] = file; idx = table_n++; }
      if (idx >= 0) encode(out, EMOJI_PUA_BASE + idx);
      continue;
    }
    decode(p);
    while (start < p) out += *start++;
  }
  return out;
}

const char* emoji_path(uint32_t cp) {
  if (cp < EMOJI_PUA_BASE || cp >= EMOJI_PUA_BASE + (uint32_t)table_n) return nullptr;
  return table[cp - EMOJI_PUA_BASE].c_str();
}
