"""Show an honest keyless setup state without starting infrastructure."""

import json


def main() -> None:
    print(
        json.dumps(
            {
                "application": "entity-matcher",
                "status": "scaffold",
                "matching_available": False,
                "next_step": "See README.md for current setup and implementation status.",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
