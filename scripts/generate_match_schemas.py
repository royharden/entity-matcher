"""Generate owned model schemas only; does not implement or copy foreign wire."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from entity_matcher.shared.match.models import SCHEMA_MODELS


def schemas() -> dict[str, dict]:
    return {model.__name__ + ".schema.json": model.model_json_schema() for model in SCHEMA_MODELS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parents[1] / "schemas/match"
    )
    args = parser.parse_args()
    expected = schemas()
    if args.check:
        actual = {path.name for path in args.out.glob("*.schema.json")}
        if actual != set(expected):
            print("Model schema inventory mismatch")
            return 1
        for name, schema in expected.items():
            path = args.out / name
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                print(f"Invalid model schema file: {name}")
                return 1
            if value != schema:
                print(f"Model schema drift: {name}")
                return 1
        print(f"Model schema parity: {len(expected)} schemas")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    for name, schema in expected.items():
        (args.out / name).write_text(
            json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"Generated {len(expected)} owned model schemas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
