"""Schema validation for every payload the sim sends or receives.

Schemas come from contracts/schemas in the repo (found by walking up from
this file), or from $ORB_CONTRACTS_DIR. A payload that fails validation is a
contract violation: the sim stops rather than guessing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class ContractError(Exception):
    """A payload (or a fetched asset) violates docs/mqtt-contract.md."""

    def __init__(self, kind: str, problems: list[str], payload: Any = None):
        self.kind = kind
        self.problems = problems
        self.payload = payload
        detail = "; ".join(problems)
        super().__init__(f"{kind}: {detail}")


def find_schemas_dir(start: Path | None = None) -> Path:
    if env := os.environ.get("ORB_CONTRACTS_DIR"):
        return Path(env)
    here = start or Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "contracts" / "schemas"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("contracts/schemas not found; set ORB_CONTRACTS_DIR")


class Contracts:
    def __init__(self, schemas_dir: Path | None = None):
        self.dir = schemas_dir or find_schemas_dir()
        self.validators: dict[str, Draft202012Validator] = {}
        for p in sorted(self.dir.glob("*.schema.json")):
            schema = json.loads(p.read_text())
            Draft202012Validator.check_schema(schema)
            self.validators[p.name.split(".")[0]] = Draft202012Validator(schema)
        if not self.validators:
            raise FileNotFoundError(f"no *.schema.json in {self.dir}")

    def kinds(self) -> list[str]:
        return sorted(self.validators)

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
