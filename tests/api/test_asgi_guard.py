"""Actual receive/send boundary controls, without HTTP sockets or a fake framework."""

import asyncio
import json

import pytest

from entity_matcher.api import asgi_guard
from entity_matcher.api.access import LocalAccessPolicy
from entity_matcher.api.asgi_guard import LocalASGIGuard

POLICY = LocalAccessPolicy(21001, "T" * 32, (21002,))
ORIGIN = b"http://localhost:21002"


def scope(method="POST", *, headers=None, length=2):
    return {
        "type": "http",
        "method": method,
        "path": "/api/comparisons",
        "headers": headers
        if headers is not None
        else [
            (b"host", b"localhost:21001"),
            (b"origin", ORIGIN),
            (b"x-review-token", b"T" * 32),
            (b"content-type", b"application/json"),
            (b"content-length", str(length).encode()),
        ],
    }


def execute(request=None, messages=None, *, timeout=1.0, app=None):
    sent, calls, reads = [], [], []
    messages = list(
        messages
        if messages is not None
        else [{"type": "http.request", "body": b"{}", "more_body": False}]
    )

    async def receive():
        reads.append(True)
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def default_app(metadata, bounded_receive, send):
        calls.append(await bounded_receive())
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"access-control-allow-origin", b"*")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    async def send(message):
        sent.append(message)

    asyncio.run(
        LocalASGIGuard(app or default_app, POLICY, body_timeout_s=timeout)(
            request or scope(), receive, send
        )
    )
    return sent, calls, reads


def code(sent):
    return json.loads(sent[-1]["body"])["code"]


def test_bounded_multichunk_body_passes_exactly_once_and_cors_is_exact():
    sent, calls, reads = execute(
        messages=[
            {"type": "http.request", "body": b"{", "more_body": True},
            {"type": "http.request", "body": b"}", "more_body": False},
        ]
    )
    assert calls == [{"type": "http.request", "body": b"{}", "more_body": False}]
    assert len(reads) == 2 and sent[0]["status"] == 200
    headers = dict(sent[0]["headers"])
    assert headers[b"access-control-allow-origin"] == ORIGIN and headers[b"vary"] == b"Origin"
    assert b"access-control-allow-credentials" not in headers


@pytest.mark.parametrize(
    "key,value",
    [
        (b"host", b"attacker.invalid:21001"),
        (b"host", b"localhost:21002"),
        (b"origin", b"null"),
        (b"origin", b"https://attacker.invalid"),
        (b"x-review-token", b"synthetic-wrong-token"),
        (b"content-length", b"999999999"),
        (b"content-type", b"multipart/form-data"),
        (b"content-type", b"application/json; charset=latin-1"),
    ],
)
def test_rejected_metadata_never_receives_body_or_calls_application(key, value):
    headers = [(k, value if k == key else v) for k, v in scope()["headers"]]
    sent, calls, reads = execute(scope(headers=headers))
    assert sent[0]["status"] == 403 and calls == reads == []
    assert value not in sent[-1]["body"]


@pytest.mark.parametrize("missing", [b"host", b"origin", b"x-review-token", b"content-length"])
def test_missing_security_metadata_fails_before_body(missing):
    sent, calls, reads = execute(
        scope(headers=[(k, v) for k, v in scope()["headers"] if k != missing])
    )
    assert sent[0]["status"] == 403 and calls == reads == []


@pytest.mark.parametrize(
    "header",
    [
        (b"host", b"localhost:21001"),
        (b"content-type", b"application/json"),
        (b"transfer-encoding", b"chunked"),
        (b"x-forwarded-host", b"attacker.invalid"),
        (b"bad\xff", b"x"),
        (b"origin", b"evil\r\nCANARY"),
    ],
)
def test_duplicate_proxy_control_and_nonascii_name_refuse(header):
    sent, calls, reads = execute(scope(headers=scope()["headers"] + [header]))
    assert sent[0]["status"] == 403 and calls == reads == []
    assert b"CANARY" not in sent[-1]["body"]


def test_actual_raw_header_count_and_byte_limits_precede_application():
    for extras in ([(b"x", b"y")] * 65, [(b"x", b"y" * 16385)]):
        sent, calls, reads = execute(scope(headers=scope()["headers"] + extras))
        assert sent[0]["status"] == 403 and calls == reads == []


@pytest.mark.parametrize(
    "messages,expected",
    [
        (
            [{"type": "http.request", "body": b"{", "more_body": False}],
            "actual_body_length_disagrees",
        ),
        (
            [{"type": "http.request", "body": b"{}CANARY", "more_body": False}],
            "actual_body_length_disagrees",
        ),
        ([{"type": "http.disconnect"}], "request_disconnected"),
        ([{"type": "wrong", "body": b"{}"}], "invalid_body_message"),
        ([{"type": "http.request", "body": "{}"}], "invalid_body_message"),
        ([{"type": "http.request", "body": b"{}", "more_body": 1}], "invalid_body_message"),
    ],
)
def test_actual_body_mismatch_surplus_disconnect_and_invalid_messages_have_zero_effects(
    messages, expected
):
    sent, calls, reads = execute(messages=messages)
    assert code(sent) == expected and calls == [] and reads
    assert b"CANARY" not in sent[-1]["body"]


def test_chunk_bound_rejects_endless_empty_chunks(monkeypatch):
    monkeypatch.setattr(asgi_guard, "MAX_BODY_CHUNKS", 2)
    sent, calls, reads = execute(
        messages=[{"type": "http.request", "body": b"", "more_body": True}] * 3
    )
    assert code(sent) == "body_chunk_count_exceeded" and calls == [] and len(reads) == 2


def test_actual_buffer_ceiling_is_checked_before_append_independently_of_declared_length(
    monkeypatch,
):
    # A smaller trusted test ceiling exercises the actual receive buffer branch;
    # the production20MiB constant is not a claim about server/process memory.
    monkeypatch.setattr(asgi_guard, "MAX_FILE_BYTES", 1)
    sent, calls, reads = execute()
    assert code(sent) == "actual_body_too_large" and calls == [] and len(reads) == 1


@pytest.mark.parametrize("deadline", [True, 0, -1.0, float("nan"), float("inf"), 31.0])
def test_explicit_finite_bounded_receive_deadline_required(deadline):
    with pytest.raises(ValueError):
        LocalASGIGuard(None, POLICY, body_timeout_s=deadline)


def test_deadline_and_busy_refusals_have_no_downstream_effects():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls, sent = [], [[], []]

        async def app(metadata, receive, send):
            calls.append(await receive())
            entered.set()
            await release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        async def receive():
            return {"type": "http.request", "body": b"{}"}

        async def send0(message):
            sent[0].append(message)

        async def send1(message):
            sent[1].append(message)

        guard = LocalASGIGuard(app, POLICY)
        first = asyncio.create_task(guard(scope(), receive, send0))
        await entered.wait()
        await guard(scope(), receive, send1)
        assert sent[1][0]["status"] == 503 and code(sent[1]) == "api_busy" and len(calls) == 1
        release.set()
        await first

        async def never():
            await asyncio.Event().wait()

        sent[1].clear()
        await LocalASGIGuard(app, POLICY, body_timeout_s=0.001)(scope(), never, send1)
        assert sent[1][0]["status"] == 408 and code(sent[1]) == "request_body_timeout"
        assert len(calls) == 1

    asyncio.run(run())


def test_exact_preflight_has_no_body_or_application_effect():
    headers = [
        (b"host", b"localhost:21001"),
        (b"origin", ORIGIN),
        (b"access-control-request-method", b"POST"),
        (b"access-control-request-headers", b"content-type, x-review-token"),
    ]
    sent, calls, reads = execute(scope("OPTIONS", headers=headers))
    actual = dict(sent[0]["headers"])
    assert sent[0]["status"] == 204 and calls == reads == []
    assert actual[b"access-control-allow-origin"] == ORIGIN
    assert actual[b"access-control-allow-methods"] == b"POST"
    assert b"access-control-allow-credentials" not in actual
    bad = headers[:-1] + [(b"access-control-request-headers", b"authorization")]
    sent, calls, reads = execute(scope("OPTIONS", headers=bad))
    assert sent[0]["status"] == 403 and calls == reads == []


def test_read_request_does_not_receive_or_expose_an_uploaded_body():
    headers = [(b"host", b"localhost:21001")]
    sent, calls, reads = execute(scope("GET", headers=headers))
    assert sent[0]["status"] == 200 and reads == [] and calls[0]["body"] == b""
    assert b"access-control-allow-origin" not in dict(sent[0]["headers"])
