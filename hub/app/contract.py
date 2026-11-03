"""Outbound payloads are validated against contracts/schemas before publish.

The hub is the only writer of config/inbox/led/notice/stickers, so a schema
failure here is a hub bug and is raised, not logged."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class ContractError(Exception):
    def __init__(self, kind: str, problems: list[str], payload: Any = None):
        self.kind = kind
        self.problems = problems
        self.payload = payload
        super().__init__(f"{kind}: {'; '.join(problems)}")


def find_schemas_dir(explicit: Path | None = None) -> Path:
    if explicit:
        return explicit
    if env := os.environ.get("CONTRACTS_DIR"):
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contracts" / "schemas"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("contracts/schemas not found; set CONTRACTS_DIR")


class Contracts:
    def __init__(self, schemas_dir: Path | None = None):
        self.dir = find_schemas_dir(schemas_dir)
        self.validators: dict[str, Draft202012Validator] = {}
        for p in sorted(self.dir.glob("*.schema.json")):
            schema = json.loads(p.read_text())
            Draft202012Validator.check_schema(schema)
            self.validators[p.name.split(".")[0]] = Draft202012Validator(schema)
        if not self.validators:
            raise FileNotFoundError(f"no *.schema.json in {self.dir}")

    def validate(self, kind: str, data: Any) -> None:
        validator = self.validators.get(kind)
        if validator is None:
            raise ContractError(kind, [f"no schema named {kind!r}"], data)
        problems = [
            f"{'/'.join(str(x) for x in e.absolute_path) or '<root>'}: {e.message}"
            for e in validator.iter_errors(data)
        ]
        if problems:
            raise ContractError(kind, problems, data)

    def parse(self, kind: str, payload: bytes) -> Any:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ContractError(kind, [f"not valid UTF-8 JSON: {e}"], payload) from e
        self.validate(kind, data)
        return data


def dumps(data: Any) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
