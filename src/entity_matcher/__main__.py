"""Keyless read-only inspection; starts no service and performs no model dispatch."""

from .cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
