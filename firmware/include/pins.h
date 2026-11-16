#pragma once
// Board pin maps. Select with -DORB_BOARD=143 (default) or 175 in platformio.ini.
// Both are Waveshare ESP32-S3 round 466x466 AMOLEDs with a CO5300 panel over QSPI;
// they differ in pins, touch chip and whether there is a PMU. See docs/hardware.md.
#ifndef ORB_BOARD
#define ORB_BOARD 143
#endif

#define LCD_W 466
#define LCD_H 466
// ORB_ROTATION: degrees clockwise the rendered image is turned before it hits the
// panel (0/90/180/270). Per-board default below; override with -DORB_ROTATION=.
#define BOOT_BUTTON 0  // BOOT is a plain GPIO on both boards, pressed = low

#if ORB_BOARD == 143
// ESP32-S3-Touch-AMOLED-1.43: pins from the schematic (verified by two independent
// ports: ON6PH/capsule-radar-1.43 and HermannBjorgvin/Clawdmeter). No PMU; the
// panel + touch rail is switched by AMOLED_EN. Touch is FT3168 (0x38) on early
// units or CST820 (0x15) on current ones, no INT/RST lines, same register map.
#define ORB_MODEL "ws-s3-amoled-1.43"
#define LCD_SDIO0 11
#define LCD_SDIO1 12
#define LCD_SDIO2 13
#define LCD_SDIO3 14
#define LCD_SCLK 10
#define LCD_CS 9
#define LCD_RESET 21
#define LCD_EN 42
#define LCD_COL_OFFSET 0
#define IIC_SDA 47
#define IIC_SCL 48
#define ORB_TOUCH_FOCALTECH 1
// The panel's scan origin is a quarter turn off from "USB-C at the bottom".
#ifndef ORB_ROTATION
#define ORB_ROTATION 90
#endif
#elif ORB_BOARD == 175
// ESP32-S3-Touch-AMOLED-1.75: Waveshare demo pin_config.h (via socquique/TamaPoke),
// cross-checked with the ESPHome device page. AXP2101 PMU: BLDO1 feeds the panel.
#define ORB_MODEL "ws-s3-amoled-1.75"
#define LCD_SDIO0 4
#define LCD_SDIO1 5
#define LCD_SDIO2 6
#define LCD_SDIO3 7
#define LCD_SCLK 38
#define LCD_CS 12
#define LCD_RESET 39
#define LCD_COL_OFFSET 6
#define IIC_SDA 15
#define IIC_SCL 14
#define TP_INT 11
#define TP_RESET 40
#define ORB_HAS_AXP2101 1
#define ORB_TOUCH_CST92XX 1
#define PA_ENABLE 46
#else
#error "unknown ORB_BOARD"
#endif

// Status LED: not on either board. Wire a WS2812/SK6812 to a free header GPIO and set
// -DORB_LED_PIN=<gpio> (and optionally -DORB_LED_COUNT=<n>) in secrets.ini.
#ifndef ORB_LED_PIN
#define ORB_LED_PIN -1
#endif
#ifndef ORB_LED_COUNT
#define ORB_LED_COUNT 1
#endif

#ifndef ORB_ROTATION
#define ORB_ROTATION 0
#endif
