"""Consumer-side owner/SDK/network denial around every published purpose control."""

import importlib.abc
import socket
import sys

import pytest


@pytest.fixture(autouse=True)
def deny_original_provider_and_network(monkeypatch):
    class Guard(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in {"llm_router_eval", "openai", "typesafe", "httpx"}:
                raise RuntimeError("owner/provider disabled")

    guard = Guard()
    sys.meta_path.insert(0, guard)

    def denied(*args, **kwargs):
        raise RuntimeError("network disabled")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    yield
    sys.meta_path.remove(guard)
