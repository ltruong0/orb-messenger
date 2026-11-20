# Hardware

⚠ = not yet verified. Confirm against the Waveshare wiki and demo code for this exact board revision, then remove the marker.

## Orb boards

Two Waveshare round 466×466 AMOLEDs are supported; `-DORB_BOARD` in `firmware/platformio.ini` picks the pin map (`firmware/include/pins.h`). The first unit Lee received (2026-11-03) is the **1.43**, found by its I2C bus answering on GPIO 47/48 rather than 15/14.

### ESP32-S3-Touch-AMOLED-1.43 (in hand)

| Item | Value | Status |
|---|---|---|
| MCU | ESP32-S3R8, 16 MB flash (quad), 8 MB octal PSRAM | ✓ esptool + boot log |
| Display | 1.43" round AMOLED 466×466, CO5300 over QSPI (Waveshare says "SH8601"; only the CO5300 driver produces output, per two independent ports) | ✓ pins from the schematic via `ON6PH/capsule-radar-1.43` and `HermannBjorgvin/Clawdmeter`; ✓ picture confirmed 2026-11-16 |
| Orientation | Panel scan origin is a quarter turn off with USB-C at the bottom: `ORB_ROTATION 90` (clockwise, software, in `pins.h`). The CO5300 has no row/column-exchange bit, so hardware rotation is not an option | ✓ Lee eyeballed |
| QSPI pins | CS 9, SCLK 10, D0–D3 11/12/13/14, RST 21, **AMOLED_EN 42 (drive high)**, column offset 6 (without it the last six visible columns stay unwritten and show green) | ✓ |
| I2C | SDA 47, SCL 48. Boot scan: 0x38 FT3168 touch, 0x51 PCF85063 RTC, 0x6B QMI8658 IMU, 0x7E unknown | ✓ measured |
| Touch | FT3168 at 0x38 (early units) or CST820 at 0x15 (current units); no INT/RST lines, polled; same register map (0x02 count, XH XL YH YL) | ✓ FT3168 found |
| PMU / audio | none (battery is a raw ADC on GPIO 4, charge LED only); no codec, no speaker | ✓ |
| Buttons | BOOT = GPIO0 (pressed low), RESET = EN | ✓ |
| Header GPIO | wiki lists 1, 2, 3, 5, 6, 7, 16, 17, 18, 45, 46 as free | ⚠ confirm on the 8-pin header; avoid 45/46 (strapping) |
| LED | none on board; use `-DORB_LED_PIN=<header gpio>` | |

### ESP32-S3-Touch-AMOLED-1.75 (original plan, not in hand)

| Item | Value | Status |
|---|---|---|
| Display | CO5300 over QSPI: SDIO0–3 = 4/5/6/7, SCLK 38, CS 12, RST 39, column offset 6 | ✓ Waveshare demo via TamaPoke + ESPHome |
| Touch | CST9217 at 0x5A, INT 11, RST 40 (SensorLib `TouchDrvCST92xx`) | ✓ same sources |
| I2C | SDA 15, SCL 14 | ✓ |
| PMU | AXP2101 at 0x34; **BLDO1 = 3.3 V feeds the panel** and must be enabled before the display init | ✓ TamaPoke |
| Audio | ES8311 codec + ES7210 over I2S (MCK 42, BCK 9, WS 45, DO 8, DI 10), PA enable 46 | ✓ |
| Also | PCF85063 RTC, QMI8658 IMU, TCA9554 expander, SD on 1/2/3/41 | ⚠ expander use unknown |
| Buttons | BOOT GPIO0; PWR through the PMU (PKEY irq) | |

## Status LED (dropped from v1, 2026-11-20)

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

- Screen window: measure the module in hand (1.43" active area), the bezel isn't symmetric on every panel ⚠.
- Access to USB-C and the reset/boot button.
- A light path for the LED that doesn't bleed into the screen edge.
