"""Positive controls for owner import and network refusal; synthetic tests only."""

import importlib
import socket

import pytest


def test_original_owner_import_guard_fires():
    with pytest.raises(RuntimeError, match="disabled"):
        importlib.import_module("llm_router_eval")


def test_network_guard_fires():
    with pytest.raises(RuntimeError, match="disabled"):
        socket.create_connection(("example.invalid", 443))
