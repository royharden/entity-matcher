"""CLI launch validation/delegation only; no sockets or optional runner execution."""

import importlib
import json

import pytest

from entity_matcher.api.launcher import REVIEW_TOKEN_ENV

cli = importlib.import_module("entity_matcher.cli.main")


@pytest.mark.parametrize(
    "environment,options,code",
    [
        ({}, [], "review_token_required"),
        ({REVIEW_TOKEN_ENV: "private invalid value"}, [], "api_launch_configuration_invalid"),
        (
            {REVIEW_TOKEN_ENV: "T" * 32},
            ["--frontend-port", "0"],
            "api_launch_configuration_invalid",
        ),
        ({REVIEW_TOKEN_ENV: "T" * 32}, ["--max-requests", "0"], "api_launch_configuration_invalid"),
    ],
)
def test_launch_refusal_happens_before_runner_without_echoing_token(
    monkeypatch, capsys, environment, options, code
):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid launch reached runner")

    monkeypatch.setattr(cli.os, "environ", environment)
    monkeypatch.setattr(cli, "run_server", forbidden)
    assert cli.main(["serve", "--port", "21001", *options]) == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {"stage": "serve", "status": "unavailable", "issue_code": code}
    assert "private invalid value" not in output and "T" * 32 not in output


def test_only_explicit_loopback_port_and_server_environment_reach_runner(monkeypatch, capsys):
    token = "T" * 32

    def run(policy, *, max_requests):
        assert policy.api_port == 21001 and policy.frontend_ports == (21002,)
        assert policy.write_token == token and max_requests == 5
        return {"stage": "serve", "status": "stopped"}

    monkeypatch.setattr(cli.os, "environ", {REVIEW_TOKEN_ENV: token})
    monkeypatch.setattr(cli, "run_server", run)
    assert (
        cli.main(["serve", "--port", "21001", "--frontend-port", "21002", "--max-requests", "5"])
        == 0
    )
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[0]) == {
        "stage": "serve",
        "status": "starting",
        "bind": "127.0.0.1",
        "port": 21001,
    }
    assert json.loads(lines[1])["status"] == "stopped"
    assert token not in "\n".join(lines)


def test_public_host_override_is_not_a_supported_launch_option(monkeypatch, capsys):
    monkeypatch.setattr(cli.os, "environ", {})
    with pytest.raises(SystemExit) as error:
        cli.main(["serve", "--port", "21001", "--host", "0.0.0.0"])
    assert error.value.code == 2
    assert "starting" not in capsys.readouterr().out
