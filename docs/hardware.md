# Hardware

⚠ = not yet verified. Confirm against the Waveshare wiki and demo code for this exact board revision, then remove the marker.

## Orb board: Waveshare ESP32-S3-Touch-AMOLED-1.75

| Item | Value | Status |
|---|---|---|
| MCU | ESP32-S3, 16 MB flash, octal PSRAM | ✓ (TamaPoke builds with `FlashSize=16M, PSRAM=opi`) |
| Display | 1.75" round AMOLED, 466×466, CO5300 driver over QSPI | ✓ (TamaPoke README) |
| Touch | CST9217 over I2C | ✓ (TamaPoke README) |
| Audio | speaker / mic codec | ⚠ check what's on the board and which codec |
| IMU, RTC, PMU / battery charging | | ⚠ check |
| Buttons | BOOT / PWR | ⚠ which one is free for "hold 5 s to reset Wi-Fi" |
| Free GPIO for an addressable LED | | ⚠ must find one not used by QSPI, I2C or audio |

Reference firmware for pins and init sequences: the board's Waveshare demo, and `github.com/socquique/TamaPoke` (Arduino, same board).

## Status LED

- One SK6812 or WS2812B mini (or a 4–8 LED ring) behind a translucent window, diffused.
- Power from the 5 V / VBUS rail (WS2812B wants ≥ 3.5 V), 330 Ω series resistor on data, 10–100 µF across the LED supply.
- 3.3 V data usually works for a single LED. If it's flaky, use a 74AHCT1G125 level shifter or SK6812.
- Drive it with FastLED or Adafruit_NeoPixel (RMT). Keep brightness low; it's a desk device.

| Mode | Color | Pattern |
|---|---|---|
| unread | red `#FF2D2D` | breathe ~2 s period |
| read (sender's orb) | green `#2EC86E` | 3–4 slow pulses, then off (30 s max) |
| pair | blue `#4682FF` | slow breathe |
| broker unreachable (local) | amber `#F5AA28` | slow blink |
| quiet hours | off | |

## Power

USB-C on the desk. If you add a battery later, note that WS2812s draw ~1 mA each even when dark; switch their supply with a MOSFET.

## Enclosure notes (Lee is designing it)

- Screen window ≥ 466 px active area (measure the module; the bezel isn't symmetric on every panel ⚠).
- Access to USB-C and the reset/boot button.
- A light path for the LED that doesn't bleed into the screen edge.
