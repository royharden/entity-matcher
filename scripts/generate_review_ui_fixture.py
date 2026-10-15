"""Authored tiny UI interchange fixture from the actual producer; no independent cohort input."""

import argparse
import csv
import io
from pathlib import Path

from entity_matcher.pipeline.review_page import encode_review_page, make_review_page
from entity_matcher.pipeline.workspace import create_workspace


def source(record_id, brand):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
    writer.writerow(("id", "name", "brand", "size", "unit", "price", "category", "description"))
    writer.writerow(
        (record_id, "Café Tea", brand, "100", "g", "1", "Drinks", "<script>literal text</script>")
    )
    return stream.getvalue().encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    workspace = create_workspace(source("one", "Acme"), source("two", "Other"))
    expected = encode_review_page(make_review_page(workspace))
    target = Path(__file__).resolve().parents[1] / "web/tests/fixtures/review-page.json"
    if args.check:
        if not target.exists() or target.read_bytes() != expected:
            print("Actual producer/UI fixture drift")
            return 1
        print("Actual producer/UI fixture parity: 1 authored page")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(expected)
    print("Generated one authored producer/UI page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
