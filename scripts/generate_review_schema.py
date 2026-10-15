"""Generate/check the owned nonauthorizing review-page schema only."""

import argparse
import json
from pathlib import Path

from entity_matcher.pipeline.review_page import ProductReviewPage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "schemas/review/product-review-page.schema.json"
    schema = ProductReviewPage.model_json_schema()
    if args.check:
        if not target.exists() or json.loads(target.read_bytes()) != schema:
            print("Review-page schema drift")
            return 1
        print("Review-page schema parity: 1 schema")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print("Generated owned review-page schema")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
