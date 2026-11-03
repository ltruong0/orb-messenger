"""Stand-in for the orb's NVS + LittleFS: what survives a reboot.

{state_dir}/{orb_id}/nvs.json   last_read_id, last_received_id, stk_rev
{state_dir}/{orb_id}/stk/       cached sticker PNGs + index.json {key: sha256}
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class NvsData:
    last_read_id: str | None = None
    last_received_id: str | None = None
    stk_rev: int | None = None
    stk_index: dict[str, str] = field(default_factory=dict)


class Nvs:
    def __init__(self, state_dir: Path, orb_id: str, fresh: bool = False):
        self.root = Path(state_dir).expanduser() / orb_id
        self.stk_dir = self.root / "stk"
        if fresh and self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.stk_dir.mkdir(exist_ok=True)
        self.file = self.root / "nvs.json"
        self.data = NvsData()
        if self.file.exists():
            raw = json.loads(self.file.read_text())
            self.data = NvsData(
                **{k: v for k, v in raw.items() if k in NvsData.__dataclass_fields__}
            )

    def save(self) -> None:
        self.file.write_text(json.dumps(asdict(self.data), indent=1))

    def sticker_path(self, key: str) -> Path:
        return self.stk_dir / f"{key}.png"
