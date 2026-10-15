"""Source-only lazy/missing-framework controls; NOT actual FastAPI integration."""

import importlib.abc
import subprocess
import sys

import pytest

from entity_matcher.api.access import LocalAccessPolicy
from entity_matcher.api.fastapi_app import APICapabilityUnavailable, create_app


def test_actual_module_import_does_not_load_framework_or_start_anything():
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys;import entity_matcher.api.fastapi_app;assert 'fastapi' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_named_missing_dependency_branch_does_not_substitute_a_framework():
    class Deny(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] == "fastapi":
                raise ImportError("synthetic framework denial")

    guard = Deny()
    sys.meta_path.insert(0, guard)
    try:
        with pytest.raises(APICapabilityUnavailable, match="fastapi_dependency_unavailable"):
            create_app(LocalAccessPolicy(21001, "T" * 32))
    finally:
        sys.meta_path.remove(guard)
