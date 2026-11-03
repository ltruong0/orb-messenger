"""Terminal rendering: one timestamped line per thing that happens."""

from __future__ import annotations

import sys
import time
from collections import deque

ICONS = {
    "net": "▶",
    "screen": "●",
    "led": "◐",
    "tx": "↑",
    "rx": "↓",
    "notice": "◎",
    "warn": "!",
    "error": "✖",
    "info": "·",
}

COLORS = {
    "net": "36",
    "screen": "1;37",
    "led": "35",
    "tx": "32",
    "rx": "34",
    "notice": "33",
    "warn": "33",
    "error": "1;31",
    "info": "90",
}


class Console:
    def __init__(self, prefix: str = "", color: bool | None = None, keep: int = 500):
        self.prefix = prefix
        self.color = sys.stdout.isatty() if color is None else color
        self.lines: deque[tuple[str, str]] = deque(maxlen=keep)

    def say(self, kind: str, text: str) -> None:
        self.lines.append((kind, text))
        icon = ICONS.get(kind, "·")
        stamp = time.strftime("%H:%M:%S")
        line = f"{stamp}  {self.prefix}{icon} {text}"
        if self.color:
            line = f"\x1b[{COLORS.get(kind, '0')}m{line}\x1b[0m"
        print(line, flush=True)

    def screen(self, state: str, headline: str, sub: str = "") -> None:
        body = f"{state.upper():<12} {headline}"
        if sub:
            body += f"   {sub}"
        self.say("screen", body)


def led_text(led: dict) -> str:
    mode = led.get("mode", "off")
    if mode == "off":
        return "LED off"
    return f"LED {mode} {led.get('color', '')} {led.get('pattern', '')}".rstrip()
