# Orb firmware

PlatformIO project for the Waveshare round AMOLED orbs. Arduino core 3.x via
[pioarduino](https://github.com/pioarduino/platform-espressif32), LVGL 9, Arduino_GFX.

```
make fw-secrets      # firmware/secrets.ini from .env (broker password; Wi-Fi if WIFI_SSID/WIFI_PASS are set)
make fw-build        # compile
make fw-flash        # compile + flash over USB-C (FW_PORT=/dev/cu.usbmodemXXX if there are several)
make fw-monitor      # serial log (needs a real terminal)
```

`pio` comes from `uv tool install --python 3.12 platformio` (pioarduino needs Python ≥ 3.10;
the Makefile falls back to `~/.local/bin/pio`).

## Boards

`-DORB_BOARD=143` (default) or `175` in `platformio.ini` selects the pin map in
`include/pins.h`. The hello payload's `model` says which one an orb runs. Both are
466×466 CO5300 panels over QSPI; the 1.43 has no PMU and a panel-enable GPIO, the 1.75 has
an AXP2101 whose BLDO1 rail feeds the panel. Boot log line `i2c devices on sda=.. scl=..`
is the first thing to check on a new unit. `ORB_ROTATION` (degrees clockwise, default 90 on the
1.43, 0 on the 1.75) turns the image in software; override it with `-DORB_ROTATION=` if a
unit is mounted differently.

## First boot

1. No Wi-Fi in NVS → the orb starts an access point `ORB-XXXX` and shows it on screen.
   Join it from a phone, open `http://192.168.4.1`, pick the home Wi-Fi, check the broker
   fields (host `mqtt.marcellolab.com`, user `orb`, the orb password) and save. The orb reboots.
   With `WIFI_SSID` / `WIFI_PASS` in `.env` and `make fw-secrets` before flashing, this step
   is skipped.
2. The orb connects, publishes `hello`, and shows the pairing code the hub assigns.
   Approve it in the app (Orbs tab) with that code.
3. Hold BOOT for 5 s at any time to forget Wi-Fi and go back to step 1.

## Layout

| File | What |
|---|---|
| `src/main.cpp` | setup + the LVGL loop (core 1) |
| `src/board.*` | PMU / panel enable, CO5300 over QSPI, touch, LittleFS, LVGL display + input drivers |
| `src/ui.*` | screens and the state machine from `docs/firmware.md` |
| `src/net.*` | `net_task` (core 0): Wi-Fi or WiFiManager portal, SNTP, esp-mqtt with LWT, media downloads, sticker sync |
| `src/led.*` | status LED patterns (FastLED; logs only until `ORB_LED_PIN` is set) |
| `src/settings.*` | NVS (`Preferences`) with build-time defaults from `secrets.ini` |
| `src/orb.*` | orb id from the MAC, the two queues between the tasks |
| `include/lv_conf.h` | LVGL config (generated from the 9.6 template; see the header comment) |
| `partitions.csv` | 4 MB app, ~12 MB LittleFS (`spiffs` label: that's what the LittleFS lib looks for) |
