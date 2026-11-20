# Orb firmware plan

![states](../design/png/20-orb-screen-states.png)

## Stack (as built, 2026-11-03)

| Concern | Choice | Note |
|---|---|---|
| Build | PlatformIO with the `pioarduino` platform (Arduino core 3.3.12 on ESP-IDF 5.5.5) | The stock `espressif32` platform is stuck on core 2.x. pioarduino needs Python ≥ 3.10: `uv tool install --python 3.12 platformio`. |
| Display | Arduino_GFX 1.6.9, `Arduino_CO5300` over `Arduino_ESP32QSPI` at 80 MHz | Both boards. Brightness is a panel command (0x51), no backlight pin. `ORB_ROTATION` (per board in `pins.h`, 90 on the 1.43) rotates each chunk in `flush_cb` while byte-swapping; the CO5300 can't rotate itself. |
| UI | LVGL 9.6, RGB565, two 40-line partial buffers in internal DMA RAM, byte-swapped in the flush callback | A `LV_EVENT_INVALIDATE_AREA` rounder keeps windows even-aligned for the CO5300. |
| Touch | 1.43: FT3168 / CST820 polled with 20 lines of `Wire` (no INT line). 1.75: SensorLib `TouchDrvCST92xx` | Only taps and long-presses matter; coordinates are still mapped through `ORB_ROTATION` for consistency. |
| Wi-Fi setup | WiFiManager 2.0.17, blocking portal `ORB-XXXX`, extra fields for broker host/port/user/password | Build-time defaults from the gitignored `secrets.ini` seed NVS on first boot. |
| MQTT | `esp-mqtt` from the IDF (in the Arduino core): QoS 1, LWT, keepalive 30 s, auto-reconnect, 16 KB rx buffer | Events are queued to `net_task`; nothing slow runs on the client's task. |
| JSON | ArduinoJson 7 | |
| Photos + stickers | LVGL's own TJpgd and LodePNG decoders reading LittleFS through `LV_USE_FS_ARDUINO_ESP_LITTLEFS` (drive `L:`), 1 MB image cache in PSRAM | No JPEGDEC/PNGdec: fewer libraries, and media stays in flash as files. |
| HTTP | `HTTPClient::writeToStream` into LittleFS, sha256 (mbedtls) checked for stickers | |
| Time | `configTzTime` with the POSIX `tz` from `config` | quiet hours |
| LED | FastLED, compiled in only with `ORB_LED_PIN` | dropped from v1; modes are just logged |
| Emoji | `lv_imgfont` fallback on the Montserrat copies; PNGs from `/media/emoji/{seq}.png` cached in `/emo/` | decisions 46 |
| OTA | `Update` into the second app slot from `config.fw` (`partitions.csv`: 2 × 3 MB app + LittleFS) | decisions 47; `make fw-release` |

## Tasks

- `ui_task` (core 1): owns LVGL. Reads a FreeRTOS queue of UI commands. It's the only code that touches LVGL objects.
- `net_task` (core 0): Wi-Fi, MQTT, HTTP downloads, sticker sync. Posts UI commands to the queue.
- `led_task`: renders the current LED mode/pattern at ~50 Hz.
- NVS (`Preferences` namespace `orb`): `wifi_ssid`, `wifi_pass`, `mqtt_host|port|user|pass`, `tz`, `last_read`, `last_recv`, `stk_rev`. Written only by `net_task` (plus `clearWifi()` right before a restart).

## Boot sequence

1. Init display + touch + LED. Show the boot screen.
2. No Wi-Fi config, or the button held 5 s → WiFiManager portal `Orb-XXXX` (show SSID and instructions on screen).
3. Connect to Wi-Fi → SNTP.
4. MQTT connect: client id = orb id, user/pass from NVS, LWT `orb/{id}/status`=`offline` (retained), keepalive 30 s.
5. On connect: publish `status`=`online` (retained) and `hello` (retained); subscribe `orb/{id}/config`, `orb/{id}/inbox`, `orb/{id}/led`, `orb/{id}/notice`, `orb/all/stickers`.
6. Unreachable for more than 10 s → **Broker unreachable** screen; retry with backoff (1 s → 30 s max).

## State machine

| State | Enter when | Screen | Exit |
|---|---|---|---|
| PORTAL | no Wi-Fi config / reset | setup instructions | portal saved |
| UNREACHABLE | Wi-Fi or MQTT down > 10 s | spinner, LED amber | connected |
| PAIRING | `config.state == pending` | 4-digit code, LED blue | `config.state == active` |
| IDLE | active, nothing unread | eyes; a random mood every 2–7 s (blink, wink, look, happy, sleepy, surprise, curious, bounce); drift a few px (burn-in) | new unread inbox; tap → SHOW (recent) or NOTHING; 3 h → SLEEP |
| UNREAD | new message | "New from {from_name}" + heart, LED red | tap → SHOW |
| SHOW | tap in UNREAD, or tap in IDLE within 10 min of reading | text / sticker / photo, emoji inline | 60 s timeout or tap → IDLE |
| RECEIPT | `notice` received | happy eyes + "{by} read it", LED green | 30 s or tap → previous |
| NIGHT | inside quiet hours | screen off; unread is kept | tap wakes for 30 s; end of quiet hours |
| SLEEP | 3 h with no tap, message or receipt (from IDLE/UNREAD) | screen off | tap, an unread message, a receipt, a pairing code |
| NOTHING | tap in IDLE when the last message was read > 10 min ago (or never this boot) | calm eyes + "no new messages" + when the last one came | 2.5 s or tap |
| UPDATING | `config.fw` names another version for this model | progress ring, version, "don't unplug me"; taps and messages ignored | reboot, or 15 s after a failure |

Events published:
- **On inbox arrival:** `event {type:"received", msg_id}`, at most once per msg id.
- **On first SHOW of a message:** `event {type:"read"}`, then store `last_read_id`.
- **On long-press (≥ 600 ms) in SHOW:** `event {type:"react", sticker:"purple-heart"}`, plus a small "sent ♥" animation. The hub owns everything that follows.

## Text layout

Inside the 330 × 330 safe square. Auto-size: try 44 → 36 → 30 px and pick the largest where the text fits in ≤ 4 lines. Center both ways. Caption under a sticker: 30 px, 1–2 lines.

## Sticker sync

When `orb/all/stickers` arrives with a new `rev`: diff it against `/stk/index.json`, download changed or missing keys (verify `sha256`), delete removed ones, then write the index and `stk/rev`. Missing sticker at render time → show the caption only.

## Done checklist

- [x] Display + touch + LVGL (picture confirmed; rotation fixed 2026-11-16)
- [x] Portal and NVS settings, button reset (portal used to provision orb-1338)
- [x] MQTT connect with LWT; hello; pairing screen (orb-1338 paired and active against the cluster)
- [x] Idle eyes, unread, show text / sticker / photo, receipt (all seen on orb-1338; night and unreachable only by code review)
- [x] Sleep after 3 h, re-show window, moods, inline emoji (2026-11-20)
- [x] OTA via `make fw-release` (2026-11-20)
- [ ] LED modes: dropped from v1
- [x] Sticker sync
- [ ] 24 h soak: reconnects cleanly after AP reboot and broker restart, no heap leak (log free heap/PSRAM hourly)

## Updating orbs over the air

1. Bump `ORB_FW_VERSION` in `firmware/platformio.ini` (semver).
2. `make fw-release`: builds, logs in to the hub with `HUB_PIN` from `.env`, uploads `firmware.bin` with the version and the model for `ORB_BOARD`.
3. The hub republishes `config` with `fw` to every active orb of that model. Each orb shows "updating", streams the image into its spare app slot, verifies the sha256 and reboots; the Orbs tab shows the new `fw` once it says hello again. Rollback = upload the older build. Removing the firmware in the app stops the rollout but doesn't downgrade anything.

The partition table (`firmware/partitions.csv`) cannot change over the air; that needs USB (`make fw-flash`) and wipes LittleFS.
