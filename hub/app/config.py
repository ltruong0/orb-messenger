"""Settings from the environment (docs/hub-api.md "Ops")."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent

# Where NotoColorEmoji.ttf usually lives: Debian's fonts-noto-color-emoji
# package (the container), then a per-user cache for laptops (`make fonts`).
EMOJI_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "~/.cache/orb-messenger/fonts/NotoColorEmoji.ttf",
)


@dataclass(frozen=True)
class User:
    id: str
    name: str


@dataclass
class Settings:
    mqtt_host: str = "mosquitto.orb.svc"
    mqtt_port: int = 1883
    mqtt_user: str = "msg-hub"
    mqtt_password: str = ""
    hub_pin: str = ""
    session_secret: str = ""
    data_dir: Path = Path("/data")
    media_base_url: str = "http://orb.marcellolab.com"
    users_file: Path = HERE.parent / "users.yaml"
    contracts_dir: Path | None = None
    emoji_font: Path | None = None
    tz_posix: str = "CST6CDT,M3.2.0,M11.1.0"  # sent to orbs in `config.tz`
    code_ttl: int = 600  # pairing code lifetime, seconds
    receipt_led_seconds: float = 30.0
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
    users: list[User] = field(default_factory=list)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        s = cls(
            mqtt_host=e.get("MQTT_HOST", cls.mqtt_host),
            mqtt_port=int(e.get("MQTT_PORT", cls.mqtt_port)),
            mqtt_user=e.get("MQTT_USER", cls.mqtt_user),
            mqtt_password=e.get("MQTT_PASSWORD", ""),
            hub_pin=e.get("HUB_PIN", ""),
            session_secret=e.get("SESSION_SECRET", ""),
            data_dir=Path(e.get("DATA_DIR", "/data")),
            media_base_url=e.get("MEDIA_BASE_URL", cls.media_base_url).rstrip("/"),
            users_file=Path(e["USERS_FILE"]) if e.get("USERS_FILE") else cls.users_file,
            contracts_dir=Path(e["CONTRACTS_DIR"]) if e.get("CONTRACTS_DIR") else None,
            emoji_font=Path(e["EMOJI_FONT"]).expanduser() if e.get("EMOJI_FONT") else None,
            tz_posix=e.get("HUB_TZ", cls.tz_posix),
            listen_host=e.get("LISTEN_HOST", cls.listen_host),
            listen_port=int(e.get("LISTEN_PORT", cls.listen_port)),
        )
        s.validate()
        return s

    def validate(self) -> None:
        missing = [
            n
            for n, v in (
                ("MQTT_PASSWORD", self.mqtt_password),
                ("HUB_PIN", self.hub_pin),
                ("SESSION_SECRET", self.session_secret),
            )
            if not v
        ]
        if missing:
            raise SystemExit(f"missing required settings: {', '.join(missing)}")
        if len(self.session_secret) < 16:
            raise SystemExit("SESSION_SECRET must be at least 16 characters")
        if not self.users:
            self.users = load_users(self.users_file)

    def user(self, user_id: str) -> User | None:
        return next((u for u in self.users if u.id == user_id), None)

    def find_emoji_font(self) -> Path | None:
        candidates = [self.emoji_font] if self.emoji_font else []
        candidates += [Path(c).expanduser() for c in EMOJI_FONT_CANDIDATES]
        return next((c for c in candidates if c and c.is_file()), None)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hub.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"


def load_users(path: Path) -> list[User]:
    doc = yaml.safe_load(path.read_text()) or {}
    users = [User(id=str(u["id"]), name=str(u["name"])) for u in doc.get("users", [])]
    if not users:
        raise SystemExit(f"no users in {path}")
    return users
