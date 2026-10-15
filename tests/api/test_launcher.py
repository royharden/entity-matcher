"""Launch boundary controls; fake runners never establish an actual listener."""

import sys
from types import SimpleNamespace

import pytest

from entity_matcher.api import launcher
from entity_matcher.api.access import LocalAccessPolicy
from entity_matcher.api.fastapi_app import APICapabilityUnavailable


def test_missing_or_invalid_environment_is_closed_without_echoing_values():
    for environment, code in [
        ({}, "review_token_required"),
        (
            {launcher.REVIEW_TOKEN_ENV: "unsafe value with private text"},
            "api_launch_configuration_invalid",
        ),
    ]:
        with pytest.raises(launcher.APILaunchError) as error:
            launcher.policy_from_environment(21001, (), environment)
        assert str(error.value) == code


def test_launch_configuration_fixes_loopback_and_disables_proxy_and_access_logs(monkeypatch):
    """Catches an accidentally public bind, proxy trust, token repr or reload worker."""
    policy = LocalAccessPolicy(21001, "T" * 32, (21002,))
    app = object()
    captured = {}

    def config(actual_app, **options):
        assert actual_app is app
        captured.update(options)
        return options

    monkeypatch.setattr(launcher, "create_app", lambda actual: app if actual is policy else None)
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(Config=config, Server=lambda c: c))
    launcher.make_server(policy, max_requests=5)
    assert captured["host"] == "127.0.0.1" and captured["port"] == 21001
    assert captured["workers"] == 1 and captured["reload"] is False
    assert captured["proxy_headers"] is False and captured["forwarded_allow_ips"] == ""
    assert captured["access_log"] is False and captured["log_config"] is None
    assert captured["limit_max_requests"] == 5 and captured["timeout_graceful_shutdown"] == 2
    assert captured["http"] == "h11" and captured["ws"] == "none"
    assert policy.write_token not in repr(captured)


@pytest.mark.parametrize("maximum", [False, 0, -1, 10001])
def test_invalid_run_bounds_refuse_before_optional_import_or_application(monkeypatch, maximum):
    def forbidden(*args):
        raise AssertionError("invalid launch reached application")

    monkeypatch.setattr(launcher, "create_app", forbidden)
    with pytest.raises(launcher.APILaunchError, match="^api_launch_configuration_invalid$"):
        launcher.make_server(LocalAccessPolicy(21001, "T" * 32), max_requests=maximum)


def test_missing_uvicorn_is_named_without_a_substitute(monkeypatch):
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    with pytest.raises(APICapabilityUnavailable, match="^uvicorn_dependency_unavailable$"):
        launcher.make_server(LocalAccessPolicy(21001, "T" * 32))


@pytest.mark.parametrize(
    "failure,code",
    [
        (OSError("sensitive synthetic detail"), "api_listener_unavailable"),
        (SystemExit(1), "api_listener_unavailable"),
        (RuntimeError("sensitive synthetic detail"), "api_server_failed"),
    ],
)
def test_runner_failures_never_report_success_or_arbitrary_error_text(monkeypatch, failure, code):
    def fail():
        raise failure

    monkeypatch.setattr(
        launcher, "make_server", lambda *a, **k: SimpleNamespace(run=fail, started=False)
    )
    with pytest.raises(launcher.APILaunchError) as error:
        launcher.run_server(LocalAccessPolicy(21001, "T" * 32))
    assert str(error.value) == code


def test_unstarted_runner_cannot_claim_a_stopped_api(monkeypatch):
    monkeypatch.setattr(
        launcher, "make_server", lambda *a, **k: SimpleNamespace(run=lambda: None, started=False)
    )
    with pytest.raises(launcher.APILaunchError, match="^api_listener_unavailable$"):
        launcher.run_server(LocalAccessPolicy(21001, "T" * 32))
