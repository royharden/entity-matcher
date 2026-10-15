"""Canonical S3 controller assertions using actual moved ledger; synthetic effects only."""

import json
import socket
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from entity_matcher.shared.callledger import Ledger
from entity_matcher.shared.callledger.models import (
    BudgetAuthority,
    CallContext,
    ChildBound,
    Limits,
    OperationPlan,
)
from entity_matcher.shared.callledger.models import canonical_digest as digest
from entity_matcher.shared.callledger.pricing import (
    FundingEvidence,
    PricePolicy,
    PriceProvenance,
    PriceSnapshot,
    RequestEvidence,
)
from entity_matcher.shared.providers.adapters import (
    ModelSupport,
    ProviderConfig,
    decode_outcome,
    prepare_request,
)
from entity_matcher.shared.providers.captures import (
    CaptureError,
    FixtureIndex,
    SafeCaptureProjection,
    put_safe_projection,
    synthetic_capture,
)
from entity_matcher.shared.providers.controller import (
    Provider,
    ProviderError,
    TransportFailure,
    TransportReply,
)
from entity_matcher.shared.providers.models import GenerateRequest, TypedJudgmentRequest


class Rig:
    def __init__(self, path, flavor="openai_responses", typed=False):
        self.path = path
        self.tick = 100.0
        self.now = datetime(2026, 10, 14, 23, 0, tzinfo=UTC)
        self.reservation = "0.1"
        self.config = ProviderConfig(
            schema_version=1,
            flavor=flavor,
            endpoint_id="synthetic-endpoint",
            revision_sha256=digest("offline-doc-contract"),
            models={
                "fixture-model": ModelSupport(
                    generation=not typed,
                    typed_judgment=typed,
                    structured_output=not typed,
                    image_input=not typed,
                    streaming=not typed,
                    temperature=False,
                    reasoning_efforts=["low"],
                    reported_models=["fixture-model-reported"],
                )
            },
        )
        self.authority = BudgetAuthority(
            schema_version=1,
            authority_id="fixture-authority",
            scope_sha256=digest("fixture-scope"),
            cap_usd="0.50",
            max_outbound_attempts=10,
            run_deadline_utc="2026-10-15T00:00:00Z",
        )
        self.limits = Limits(
            schema_version=1,
            max_attempts_total=2,
            max_content_repairs=0,
            operation_deadline_s=30.0,
            attempt_timeout_s=20.0,
            run_deadline_utc="2026-10-15T00:00:00Z",
            max_outbound_attempts=10,
            max_concurrent_operations=2,
            max_total_input_tokens=4000,
            max_state_plus_longest_question_tokens=2000,
            max_request_bytes=32000,
            max_response_bytes=64000,
            max_questions=32,
            max_choice_options=64,
            max_total_output_tokens=512,
        )
        if typed:
            self.request = TypedJudgmentRequest.model_validate(
                dict(
                    schema_version=1,
                    kind="typed_judgment",
                    model="fixture-model",
                    state={"text": "SYNTHETIC_PAYLOAD_CANARY", "nonce": "nonce-1"},
                    questions={
                        "condition": {
                            "type": "noul",
                            "instructions": "A synthetic condition?",
                            "criteria": {"true": {"means": "present"}, "false": ["absent"]},
                        },
                        "category": {
                            "type": "choice",
                            "instructions": "Synthetic category",
                            "criteria": {"a": None, "b": None},
                        },
                        "score": {
                            "type": "score",
                            "instructions": {"task": "Synthetic relation"},
                            "criteria": ["unrelated", {"description": "related"}, ["same size"]],
                        },
                    },
                )
            )
        else:
            self.request = GenerateRequest.model_validate(
                dict(
                    schema_version=1,
                    kind="generate",
                    model="fixture-model",
                    messages=[
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "SYNTHETIC_PAYLOAD_CANARY"}],
                        }
                    ],
                    schema={
                        "type": "object",
                        "properties": {"tier": {"type": "string", "enum": ["cheap", "review"]}},
                        "required": ["tier"],
                        "additionalProperties": False,
                    },
                    max_output_tokens=32,
                    stream=False,
                )
            )
        self.policy = PricePolicy(
            schema_version=1,
            provider="synthetic",
            model="fixture-model",
            context_sha256=digest("synthetic-price-context"),
            cache_mode="flat_inclusive",
            input_usd_per_million="100",
            cache_read_usd_per_million=None,
            cache_write_usd_per_million=None,
            output_usd_per_million=None,
            fee_usd="0",
        )
        self.snapshot = PriceSnapshot(
            schema_version=1,
            provenance=PriceProvenance(
                origin="synthetic",
                source_url=None,
                retrieved_at="2026-10-14T00:00:00Z",
                official_verified_at=None,
                source_commit=None,
            ),
            policies=[self.policy],
            data_sha256=digest([self.policy.model_dump(mode="json")]),
        )
        self.bound = FundingEvidence(
            schema_version=1,
            snapshot_sha256=self.snapshot.snapshot_sha256,
            policy_sha256=self.policy.policy_sha256,
            provider_config_sha256=self.config.sha256,
            model="fixture-model",
            request_shape_limit_sha256=digest("synthetic-shape"),
            input_token_maximum=1000,
            output_token_maximum=None if typed else 512,
            bound_kind="synthetic_fixture",
            proof_sha256=digest("authored-count-bound"),
        )
        self.evidence = []
        self.register(self.request)

    def context(self, operation="op1", child="group", run="run1"):
        return CallContext(
            schema_version=1,
            authority_id=self.authority.authority_id,
            run_id=run,
            operation_id=operation,
            child_id=child,
            task_id="task1",
            span_id="span1",
            consumer_namespace="synthetic",
            purpose="synthetic.route",
            source_generation="g1",
            consumer_context_sha256=digest("public-context"),
            policy_sha256=digest("consumer-policy"),
            template_sha256=digest("public-template"),
            config_sha256=digest("consumer-config"),
        )

    def prepared(self, request=None):
        return prepare_request(
            request or self.request, config=self.config, context=self.context(), limits=self.limits
        )

    def register(self, request):
        prepared = self.prepared(request)
        evidence = RequestEvidence(
            schema_version=1,
            bound_evidence_sha256=self.bound.evidence_sha256,
            request_identity_sha256=prepared.request_identity_sha256,
            dispatched_envelope_sha256=prepared.dispatched_envelope_sha256,
            input_token_count=100,
            output_token_maximum=request.max_output_tokens
            if isinstance(request, GenerateRequest)
            else None,
            proof_sha256=digest("authored-request-count"),
        )
        self.evidence.append(evidence)
        return evidence

    def plan(self, *, offline=False, operation="op1", count=1, attempts=2):
        return OperationPlan(
            schema_version=1,
            operation_id=operation,
            authority_id=self.authority.authority_id,
            context_sha256=digest(self.context(operation).model_dump(mode="json")),
            children=[
                ChildBound(
                    child_id=f"child{i}",
                    purpose="synthetic.route",
                    provider_config_sha256=self.config.sha256,
                    model="fixture-model",
                    bound_evidence_sha256=self.bound.evidence_sha256,
                    max_attempts=0 if offline else attempts,
                    max_repairs=0,
                    input_token_maximum=1000,
                    output_token_maximum=self.bound.output_token_maximum,
                    fee_maximum_usd="0",
                    reserved_usd_per_attempt="0" if offline else self.reservation,
                    request_shape_limit_sha256=self.bound.request_shape_limit_sha256,
                    conditional=False,
                )
                for i in range(count)
            ],
        )

    def open(self, *, offline=False):
        return Ledger(
            self.path,
            authority=self.authority,
            price_snapshot=None if offline else self.snapshot,
            trusted_bounds=[] if offline else [self.bound],
            trusted_requests=self.evidence,
            clock=lambda: self.now,
            monotonic=lambda: self.tick,
        )

    def provider(self, mode="injected_simulation", **kwargs):
        return Provider(
            self.config,
            mode=mode,
            request_evidence=self.evidence,
            clock=lambda: self.tick,
            sleep=self.sleep,
            **kwargs,
        )

    def sleep(self, seconds):
        self.tick += seconds

    def kwargs(self, ledger, allocation, *, offline=False, operation="op1", child="child0"):
        return dict(
            purpose="synthetic.route",
            ledger=ledger,
            budget=self.authority,
            context=self.context(operation, child),
            limits=self.limits.model_copy(update={"max_outbound_attempts": 0})
            if offline
            else self.limits,
            allocation=allocation,
        )

    def body(self):
        usage = {"input_tokens": 100, "output_tokens": None}
        if self.config.flavor == "typesafe":
            return dict(
                model="fixture-model-reported",
                answers={
                    "condition": {"type": "noul", "noul": 0.8},
                    "category": {
                        "type": "choice",
                        "choice": "a",
                        "probabilities": {"a": 0.8, "b": 0.2},
                        "confidence": 0.6,
                    },
                    "score": {
                        "type": "score",
                        "score": 1.6,
                        "legend": {"0": "unrelated", "1": "related", "2": "same size"},
                        "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
                        "confidence": 0.8,
                    },
                },
                usage=usage,
            )
        if self.config.flavor == "openai_chat":
            return dict(
                model="fixture-model-reported",
                choices=[{"finish_reason": "stop", "message": {"content": '{"tier":"cheap"}'}}],
                usage={"prompt_tokens": 100, "completion_tokens": None},
            )
        if self.config.flavor == "anthropic":
            return dict(
                model="fixture-model-reported",
                content=[{"type": "text", "text": '{"tier":"cheap"}'}],
                stop_reason="end_turn",
                usage={
                    "input_tokens": 100,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": None,
                },
            )
        return dict(
            model="fixture-model-reported",
            status="completed",
            output=[
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"tier":"cheap"}'}],
                }
            ],
            usage=usage,
        )

    def capture(self):
        prepared = self.prepared()
        outcome = decode_outcome(
            json.dumps(self.body()).encode(), prepared=prepared, config=self.config, max_bytes=64000
        )
        return synthetic_capture(
            prepared,
            context=self.context(),
            outcome=outcome,
            capture_id="authored-capture",
            review_receipt_sha256=digest("authored-fixture-review"),
        )


class Spy:
    is_test_transport = True

    def __init__(self, rig, replies):
        self.rig = rig
        self.replies = list(replies)
        self.calls = []

    def send(self, payload_json, **kwargs):
        self.calls.append((payload_json, kwargs))
        self.rig.tick += 0.01
        response = self.replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path / "provider.sqlite3")


@pytest.fixture
def factory():
    return Rig


@pytest.fixture
def spy():
    return Spy


@pytest.fixture
def reply():
    return response


def response(body, status=200, **kwargs):
    return TransportReply(status, json.dumps(body).encode(), **kwargs)


def admit(rig, provider, ledger, *, offline=False):
    limits = rig.limits.model_copy(update={"max_outbound_attempts": 0}) if offline else rig.limits
    return provider.admit_operation(
        rig.plan(offline=offline),
        ledger=ledger,
        budget=rig.authority,
        context=rig.context(),
        limits=limits,
    )


@pytest.mark.parametrize(
    "flavor,typed",
    [("openai_responses", False), ("openai_chat", False), ("typesafe", True), ("anthropic", False)],
)
def test_real_ledger_one_dispatch_actual_typed_or_structured_output(
    factory, spy, reply, tmp_path, flavor, typed
):
    rig = factory(tmp_path / "ledger.sqlite3", flavor, typed)
    transport = spy(rig, [reply(rig.body())])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit(rig, provider, ledger)
        result = provider.run(rig.request, **rig.kwargs(ledger, allocation))
        assert (
            result.status == "ok"
            and result.origin == "mock"
            and (result.current_external_cost_usd == "0")
        )
        assert (
            result.model_reported == "fixture-model-reported"
            and result.source_capture.origin == "synthetic"
        )
        assert result.source_capture.cost_usd == "0.01"
        if typed:
            assert result.typed.answers["score"].score == 1.6 and result.text is None
            assert set(json.loads(transport.calls[0][0])["questions"]) == {
                "condition",
                "category",
                "score",
            }
        else:
            assert result.parsed == {"tier": "cheap"}
        assert (
            len(transport.calls)
            == len(result.attempt_ids)
            == ledger.totals()["dispatched_attempts"]
            == 1
        )
        assert transport.calls[0][1]["max_retries"] == 0
        assert Decimal(ledger.totals()["known_spend_usd"]) == Decimal("0.01")
        assert Decimal(ledger.totals()["future_holds_usd"]) == 0
        assert ledger.operation_state(allocation) == "complete"


def test_complete_forwards_exact_accounting_and_payload_binding(rig, spy, reply):
    transport = spy(rig, [reply(rig.body())])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit(rig, provider, ledger)
        result = provider.complete(
            [m.model_dump(mode="json") for m in rig.request.messages],
            schema=rig.request.output_schema,
            model=rig.request.model,
            max_output_tokens=32,
            **rig.kwargs(ledger, allocation),
        )
        assert result.status == "ok"
        assert transport.calls[0][0] == rig.prepared().payload_json
        with pytest.raises(TypeError):
            provider.complete([], model="fixture-model")


@pytest.mark.parametrize("mode", ["mock", "strict_replay"])
def test_zero_quota_real_admission_before_strict_lookup_no_transport(rig, spy, mode):
    transport = spy(rig, [])
    provider = rig.provider(
        mode=mode,
        fixtures=FixtureIndex([rig.capture()]),
        capture_mode="fixture_replay",
        transport=transport,
    )
    with rig.open(offline=True) as ledger:
        allocation = admit(rig, provider, ledger, offline=True)
        kwargs = rig.kwargs(ledger, allocation, offline=True)
        before = ledger.connection.total_changes
        view = provider.validate_replay_allocation(rig.request, **kwargs)
        assert ledger.connection.total_changes == before
        assert (
            view.mode == "offline"
            and view.used_attempts == view.remaining_attempts == view.max_attempts == 0
        )
        result = provider.run(rig.request, **kwargs)
        assert result.status == "ok" and result.origin == "mock"
        assert result.source_capture.cost_usd is None and result.source_capture.latency_ms is None
        assert result.first_output_ms is None and result.first_output_kind == "unavailable"
        assert (
            result.attempt_ids == []
            and result.current_external_cost_usd == "0"
            and (not transport.calls)
        )
        assert ledger.totals()["dispatched_attempts"] == 0
        assert Decimal(ledger.totals()["total_exposure_usd"]) == 0


def test_changed_nonce_strict_miss_has_no_capture_no_fallback(factory, spy, tmp_path):
    rig = factory(tmp_path / "ledger.sqlite3", "typesafe", True)
    original = rig.capture()
    request = rig.request.model_copy(
        update={"state": {"text": "SYNTHETIC_PAYLOAD_CANARY", "nonce": "nonce-2"}}
    )
    rig.register(request)
    transport = spy(rig, [])
    provider = rig.provider(
        "strict_replay",
        fixtures=FixtureIndex([original]),
        capture_mode="fixture_replay",
        transport=transport,
    )
    with rig.open(offline=True) as ledger:
        allocation = admit(rig, provider, ledger, offline=True)
        result = provider.run(request, **rig.kwargs(ledger, allocation, offline=True))
        assert result.status == "cache_miss" and result.error.code == "cache_miss"
        assert result.source_capture is None and result.model_reported is None
        assert result.typed is None and result.parsed is None and (result.text is None)
        assert (
            result.attempt_ids == []
            and result.current_external_cost_usd == "0"
            and (not transport.calls)
        )
        assert ledger.operation_state(allocation) == "partial"
        assert ledger.totals()["dispatched_attempts"] == 0


def test_replay_needs_actual_zero_child_and_explicit_zero_outbound(rig):
    provider = rig.provider(
        "strict_replay", fixtures=FixtureIndex([rig.capture()]), capture_mode="fixture_replay"
    )
    with rig.open(offline=True) as ledger:
        allocation = ledger.admit_operation(
            rig.plan(offline=True), context=rig.context(), limits=rig.limits, mode="offline"
        )
        with pytest.raises(ValueError, match="zero_quota_required"):
            provider.validate_replay_allocation(rig.request, **rig.kwargs(ledger, allocation))
        forged = allocation.model_copy(update={"allocation_id": "fake"})
        with pytest.raises(ValueError, match="allocation_mismatch"):
            provider.run(rig.request, **rig.kwargs(ledger, forged, offline=True))
        assert ledger.totals()["dispatched_attempts"] == 0


def test_capture_flag_cannot_turn_authored_fixture_into_live_observation(rig):
    record = rig.capture()
    fake = replace(record, source=record.source.model_copy(update={"origin": "live"}))
    with pytest.raises(CaptureError, match="unverified_live_capture"):
        FixtureIndex([fake])
    changed = replace(record, content_json=record.content_json + b" ")
    with pytest.raises(CaptureError, match="capture_hash"):
        FixtureIndex([changed])


def test_429_then_invalid_output_uses_only_two_no_content_repair(rig, spy, reply):
    bad = rig.body()
    bad["output"][0]["content"][0]["text"] = '{"tier":"invented"}'
    transport = spy(
        rig, [reply({}, 429, headers={"Retry-After": "0"}), reply(bad), reply(rig.body())]
    )
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit(rig, provider, ledger)
        result = provider.run(rig.request, **rig.kwargs(ledger, allocation))
        assert result.status == "invalid_output" and result.parsed is None
        assert len(transport.calls) == ledger.totals()["dispatched_attempts"] == 2
        assert len(transport.replies) == 1
        assert Decimal(ledger.totals()["unknown_liability_usd"]) == Decimal("0.1")
        assert Decimal(ledger.totals()["known_spend_usd"]) == Decimal("0.01")
        assert result.source_capture.cost_usd is None


def test_retry_after_beyond_remaining_deadline_does_not_send_again(rig, spy, reply):
    transport = spy(rig, [reply({}, 429, headers={"Retry-After": "60"}), reply(rig.body())])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit(rig, provider, ledger)
        result = provider.run(rig.request, **rig.kwargs(ledger, allocation))
        assert result.status == "provider_error" and len(transport.calls) == 1
        assert Decimal(ledger.totals()["unknown_liability_usd"]) == Decimal("0.1")
        assert Decimal(ledger.totals()["future_holds_usd"]) == 0


def test_lost_response_retains_actual_reserved_liability_and_no_faux_capture(rig, spy):
    transport = spy(rig, [TransportFailure("unknown_dispatch")])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit(rig, provider, ledger)
        with pytest.raises(ProviderError, match="unknown_dispatch"):
            provider.run(rig.request, **rig.kwargs(ledger, allocation))
        assert ledger.totals()["dispatched_attempts"] == 1
        assert Decimal(ledger.totals()["unknown_liability_usd"]) == Decimal("0.1")
        assert Decimal(ledger.totals()["future_holds_usd"]) == 0
    with rig.open() as restarted:
        assert Decimal(restarted.totals()["unknown_liability_usd"]) == Decimal("0.1")


def test_streamed_incomplete_billed_output_retains_liability_without_fake_ttft(rig, spy, reply):
    rig.policy = rig.policy.model_copy(update={"output_usd_per_million": "1"})
    data = rig.snapshot.model_dump(mode="json")
    data["policies"] = [rig.policy.model_dump(mode="json")]
    data["data_sha256"] = digest(data["policies"])
    rig.snapshot = PriceSnapshot.model_validate(data)
    rig.bound = rig.bound.model_copy(
        update={
            "snapshot_sha256": rig.snapshot.snapshot_sha256,
            "policy_sha256": rig.policy.policy_sha256,
        }
    )
    rig.evidence = []
    rig.register(rig.request)
    rig.reservation = "0.11"
    body = rig.body()
    body["status"] = "incomplete"
    transport = spy(rig, [reply(body, first_output_kind="chunk", first_output_ms=2.0)])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit(rig, provider, ledger)
        result = provider.run(rig.request, **rig.kwargs(ledger, allocation))
        assert (
            result.status == "invalid_output"
            and result.first_output_kind == "chunk"
            and (result.first_output_ms == 2.0)
        )
        assert (
            result.latency_ms > result.first_output_ms
            and result.normalized_usage.output_tokens is None
        )
        assert Decimal(ledger.totals()["unknown_liability_usd"]) == Decimal("0.11")
        assert result.source_capture.cost_usd is None


def test_missing_explicit_accounting_and_payload_mutation_fail_before_transport(rig, spy, reply):
    transport = spy(rig, [reply(rig.body())])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit(rig, provider, ledger)
        kwargs = rig.kwargs(ledger, allocation)
        for key in ["purpose", "ledger", "budget", "context", "limits", "allocation"]:
            bad = {**kwargs}
            del bad[key]
            with pytest.raises(TypeError):
                provider.run(rig.request, **bad)
        changed = rig.request.model_copy(update={"max_output_tokens": 31})
        with pytest.raises(ProviderError, match="untrusted_request"):
            provider.run(changed, **kwargs)
        assert not transport.calls and ledger.totals()["dispatched_attempts"] == 0


@pytest.mark.parametrize("transfer_delay", [25.0, 31.0])
def test_transfer_delay_cannot_reuse_stale_timeout_headroom(
    rig, spy, reply, monkeypatch, transfer_delay
):
    """what_bug_this_catches: lock/commit delay extending the actual operation deadline."""
    transport = spy(rig, [reply(rig.body())])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit(rig, provider, ledger)
        original = ledger.begin_attempt

        def delayed(*args, **kwargs):
            attempt = original(*args, **kwargs)
            rig.tick += transfer_delay
            return attempt

        monkeypatch.setattr(ledger, "begin_attempt", delayed)
        if transfer_delay == 25.0:
            assert provider.run(rig.request, **rig.kwargs(ledger, allocation)).status == "ok"
            assert transport.calls[0][1]["timeout_s"] == pytest.approx(5.0)
        else:
            with pytest.raises(ProviderError, match="deadline"):
                provider.run(rig.request, **rig.kwargs(ledger, allocation))
            assert not transport.calls
            assert Decimal(ledger.totals()["unknown_liability_usd"]) == Decimal("0.1")


@pytest.mark.parametrize("status", [401, 403])
def test_auth_response_has_closed_auth_code_and_no_retry(rig, spy, reply, status):
    """what_bug_this_catches: a generic error hiding the batch's mandatory auth-stop signal."""
    transport = spy(
        rig, [reply({"error": {"message": "SYNTHETIC_AUTH_CANARY"}}, status), reply(rig.body())]
    )
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = admit(rig, provider, ledger)
        result = provider.run(rig.request, **rig.kwargs(ledger, allocation))
        assert result.status == "provider_error" and result.error.code == "auth"
        assert len(transport.calls) == 1 and len(transport.replies) == 1
        assert Decimal(ledger.totals()["unknown_liability_usd"]) == Decimal("0.1")
        assert "SYNTHETIC_AUTH_CANARY" not in str(result.error)
        assert (
            '"error_code":"auth"'
            in ledger.connection.execute("SELECT settlement FROM attempts").fetchone()[0]
        )


def test_controller_network_denial_and_payload_canary_positive_control(
    rig, spy, reply, tmp_path, monkeypatch
):

    def blocked(*_args, **_kwargs):
        raise RuntimeError("Network positive control")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    with pytest.raises(RuntimeError, match="positive control"):
        socket.socket()
    body = rig.body()
    body["usage"]["headers"] = {"Authorization": "SYNTHETIC_PAYLOAD_CANARY"}
    body["usage"]["error"] = "SYNTHETIC_PAYLOAD_CANARY"
    transport = spy(rig, [reply(body)])
    provider = rig.provider(transport=transport)
    with rig.open() as ledger:
        allocation = provider.admit_operation(
            rig.plan(),
            ledger=ledger,
            budget=rig.authority,
            context=rig.context(),
            limits=rig.limits,
        )
        result = provider.run(rig.request, **rig.kwargs(ledger, allocation))
        assert result.status == "ok" and provider.capture_mode == "disabled"
        target = tmp_path / "events.jsonl"
        ledger.export_jsonl(target)
        files = [p for p in tmp_path.iterdir() if p.is_file()]
        assert not any((b"SYNTHETIC_PAYLOAD_CANARY" in path.read_bytes() for path in files))
        assert b'"usage_redacted_fields":true' in target.read_bytes()
        target.write_bytes(target.read_bytes() + b"SYNTHETIC_PAYLOAD_CANARY")
        assert any((b"SYNTHETIC_PAYLOAD_CANARY" in path.read_bytes() for path in files))
        ledger.export_jsonl(target)
        assert not any((b"SYNTHETIC_PAYLOAD_CANARY" in path.read_bytes() for path in files))
        assert Decimal(ledger.totals()["known_spend_usd"]) == Decimal("0.01")


def test_strict_fixture_path_with_network_disabled_and_no_fallback(rig, monkeypatch):

    def blocked(*_args, **_kwargs):
        raise RuntimeError("Network positive control")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    with pytest.raises(RuntimeError):
        socket.socket()
    provider = rig.provider(
        "strict_replay", fixtures=FixtureIndex([rig.capture()]), capture_mode="fixture_replay"
    )
    zero = rig.limits.model_copy(update={"max_outbound_attempts": 0})
    with rig.open(offline=True) as ledger:
        allocation = provider.admit_operation(
            rig.plan(offline=True),
            ledger=ledger,
            budget=rig.authority,
            context=rig.context(),
            limits=zero,
        )
        assert (
            provider.run(rig.request, **rig.kwargs(ledger, allocation, offline=True)).origin
            == "mock"
        )
        assert ledger.totals()["dispatched_attempts"] == 0


def test_optional_safe_projection_hook_rejects_payload_and_stale_generation():
    projection = SafeCaptureProjection(
        namespace="synthetic",
        request_identity_sha256=digest("request"),
        source_generation="g1",
        dependency_ids=["dep1"],
        expected_generation_sha256=digest("generation-1"),
        status="ok",
        capture_sha256=digest("capture"),
        current_external_cost_usd="0",
    )

    class Store:
        def __init__(self):
            self.generation = digest("generation-1")
            self.saved = []

        def put_safe(self, value, *, expected_generation_sha256):
            if self.generation != expected_generation_sha256:
                return False
            self.saved.append(value)
            return True

    store = Store()
    assert put_safe_projection(store, projection) is True
    store.saved.clear()
    store.generation = digest("deleted-generation")
    assert put_safe_projection(store, projection) is False and (not store.saved)
    with pytest.raises(ValidationError):
        SafeCaptureProjection.model_validate(
            {**projection.model_dump(), "caption": "SYNTHETIC_PAYLOAD_CANARY"}
        )


@pytest.fixture(autouse=True)
def deny_original_owner_and_network(monkeypatch):
    import importlib.abc
    import sys

    class Guard(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in {"llm_router_eval", "openai", "typesafe", "httpx"}:
                raise RuntimeError("original owner/provider disabled")

    guard = Guard()
    sys.meta_path.insert(0, guard)

    def denied(*args, **kwargs):
        raise RuntimeError("network disabled")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    assert not any(n == "llm_router_eval" or n.startswith("llm_router_eval.") for n in sys.modules)
    yield
    sys.meta_path.remove(guard)


def test_owner_and_network_negative_canaries_are_active():
    import importlib

    with pytest.raises(RuntimeError, match="disabled"):
        importlib.import_module("llm_router_eval")
    with pytest.raises(RuntimeError, match="disabled"):
        socket.create_connection(("example.invalid", 443))


def test_real_ledger_cumulative_holds_across_operations_refuse_fanout(rig):
    from entity_matcher.shared.callledger import LedgerError

    provider = rig.provider(transport=Spy(rig, []))
    with rig.open() as ledger:
        for operation in ("one", "two"):
            provider.admit_operation(
                rig.plan(operation=operation),
                ledger=ledger,
                budget=rig.authority,
                context=rig.context(operation),
                limits=rig.limits,
            )
        assert Decimal(ledger.totals()["future_holds_usd"]) == Decimal("0.4")
        with pytest.raises(LedgerError, match="budget_rejected"):
            provider.admit_operation(
                rig.plan(operation="three"),
                ledger=ledger,
                budget=rig.authority,
                context=rig.context("three"),
                limits=rig.limits,
            )
        assert ledger.totals()["dispatched_attempts"] == 0
        assert Decimal(ledger.totals()["total_exposure_usd"]) == Decimal("0.4")


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_generation", "g2"),
        ("purpose", "other"),
        ("consumer_namespace", "foreign"),
        ("policy_sha256", "f" * 64),
    ],
)
def test_context_substitution_refuses_before_replay_or_dispatch(rig, field, value):
    from entity_matcher.shared.callledger import LedgerError

    transport = Spy(rig, [])
    provider = rig.provider(
        "strict_replay",
        fixtures=FixtureIndex([rig.capture()]),
        capture_mode="fixture_replay",
        transport=transport,
    )
    with rig.open(offline=True) as ledger:
        allocation = admit(rig, provider, ledger, offline=True)
        kwargs = rig.kwargs(ledger, allocation, offline=True)
        kwargs["context"] = kwargs["context"].model_copy(update={field: value})
        before = ledger.connection.total_changes
        with pytest.raises((ValueError, LedgerError, ProviderError)):
            provider.run(rig.request, **kwargs)
        assert before == ledger.connection.total_changes
        assert ledger.totals()["dispatched_attempts"] == 0 and not transport.calls
