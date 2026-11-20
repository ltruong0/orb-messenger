#pragma once
// Inline emoji in message text (decisions 46). The hub renders each emoji grapheme
// to a small PNG at /media/emoji/<seq>.png where <seq> is the codepoints in lowercase
// hex joined by '-' (U+FE0F dropped). net_task fetches them into LittleFS /emo/, the UI
// swaps each grapheme for a private-use codepoint that an LVGL image font maps back
// to the file.
#include <Arduino.h>

#define EMOJI_MAX 24                 // distinct emoji per message
#define EMOJI_PUA_BASE 0xE000        // U+E000.. stand in for table entries

// Distinct emoji sequences in text (as "1f600", "1f468-200d-1f469"). Returns the count.
int emoji_scan(const char* text, String* seqs, int max);

// Clears the UI's emoji table (call once per message before emoji_rewrite).
void emoji_reset();
// Copies text with every emoji grapheme replaced by a table codepoint.
String emoji_rewrite(const char* text);
// LittleFS path ("/emo/<seq>.png") for a table codepoint, or nullptr.
const char* emoji_path(uint32_t codepoint);
// LittleFS path for a sequence.
String emoji_file(const String& seq);
