#!/usr/bin/env python3
"""Validate contracts/examples against contracts/schemas.

examples/<schema>[.<variant>].json         must validate
examples/invalid/<schema>[.<variant>].json must NOT validate

Usage: python contracts/validate.py   (needs: pip install jsonschema)
"""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).parent
schemas = {}
for p in sorted((ROOT / "schemas").glob("*.schema.json")):
    schema = json.loads(p.read_text())
    Draft202012Validator.check_schema(schema)
    schemas[p.name.split(".")[0]] = Draft202012Validator(schema)

failures = 0
for p in sorted((ROOT / "examples").rglob("*.json")):
    name = p.name.split(".")[0]
    expect_valid = p.parent.name != "invalid"
    validator = schemas.get(name)
    if validator is None:
        print(f"FAIL {p.relative_to(ROOT)}: no schema named {name!r}")
        failures += 1
        continue
    errors = list(validator.iter_errors(json.loads(p.read_text())))
    ok = (not errors) if expect_valid else bool(errors)
    print(f"{'ok  ' if ok else 'FAIL'} {p.relative_to(ROOT)}")
    if not ok:
        failures += 1
        for e in errors:
            print(f"     {list(e.absolute_path)}: {e.message}")
        if not expect_valid:
            print("     expected this example to be rejected")

print(f"\n{len(schemas)} schemas, {failures} failure(s)")
sys.exit(1 if failures else 0)
