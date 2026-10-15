"""Exact S5 memory assertions with explicit synthetic helper closure; no semantic proof."""

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from entity_matcher.shared.cited import (
    POINTER_ADAPTER,
    ArtifactRef,
    DuplicateResolverError,
    Finding,
    Registry,
    ResolutionContext,
)
from entity_matcher.shared.cited.assessment import assess_required_evidence
from entity_matcher.shared.cited.memory import (
    LINE_KEY,
    SECTION_KEY,
    MemoryBounds,
    MemorySource,
    MemoryTextResolver,
    SectionSpan,
    install_supplied_text,
)

RAW = "a" * 64
ARTIFACT = "b" * 64
PAGE = "c" * 64
QUERY = "d" * 64


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def wire(source_type="supplied_text", subtype="line", kind="file:line", locator=None):
    """Construct synthetic wire inputs; hashes are placeholders, not captured source proof."""
    return {
        "schema_version": 1,
        "kind": kind,
        "source_type": source_type,
        "locator_subtype": subtype,
        "source_id": "source-1",
        "source_sha256": RAW,
        "source_revision": "generation-1",
        "evidence_artifact_sha256": ARTIFACT,
        "provenance_ref": None,
        "locator": {"line_start": 1, "line_end": 2} if locator is None else locator,
    }


def region(**changes):
    result = {
        "document_id": "source-1",
        "page_id": "page-1",
        "page_index": 0,
        "page_sha256": PAGE,
        "page_digest_algorithm": "rgba8-source-grid-v1",
        "source_grid_profile": "retained-rgba8/1",
        "width_px": 100,
        "height_px": 80,
        "units": "px",
        "origin": "top-left",
        "bbox_px": None,
        "word_ids": None,
        "line_ids": ["line-1"],
        "excerpt_sha256": None,
        "transform_ref": None,
    }
    result.update(changes)
    return result


def snapshot():
    return {
        "repository_id": "source-1",
        "source_kind": "fixture_inventory",
        "snapshot_id": RAW,
        "inventory_sha256": RAW,
    }


def domain_wires():
    base = [
        wire(),
        wire(
            "configured_log",
            locator={"line_start": 1, "line_end": 2, "view_kind": "redacted_result"},
        ),
        wire(
            "repository_file",
            locator={
                **snapshot(),
                "path": "src/a.py",
                "line_start": 1,
                "line_end": 1,
                "definition_kind": "function",
            },
        ),
        wire(
            "code_existence_report",
            "report_item",
            "doc:section",
            {
                **snapshot(),
                "item_id": QUERY,
                "reference_id": QUERY,
                "query_sha256": PAGE,
                "coverage_sha256": ARTIFACT,
            },
        ),
        wire("supplied_text", "section", "doc:section", {"section_id": "release-1"}),
        wire("document_page", "region", "doc:page:bbox", region()),
        wire("screenshot_collection", "source_region", "doc:page:bbox", region()),
        wire(
            "product_catalog",
            "record_field",
            "row:id",
            {
                "dataset_id": "source-1",
                "record_id": "record-1",
                "field_id": "name",
                "quote": "  é Fixture NAME  ",
                "excerpt_sha256": digest("  é Fixture NAME  "),
            },
        ),
        {**wire("event_table", "row", "row:id"), "locator": None},
        {**wire("metric_table", "metric", "row:id"), "locator": None},
        wire("saved_web", "fragment", "url#fragment", {"fragment_id": "anchor-1"}),
        wire("advisory_record", "id", "advisory:id", {"advisory_id": "GHSA-fixture"}),
    ]
    for value in base:
        if value["source_type"] == "configured_log":
            value["source_revision"] = f"sha256:{RAW}"
        if value["source_type"] == "product_catalog":
            value["source_revision"] = f"rg1:{RAW}"
            field = {
                "algorithm": "product-record-field-evidence-v1",
                "dataset_id": "source-1",
                "record_id": "record-1",
                "source_sha256": RAW,
                "source_revision": f"rg1:{RAW}",
                "field_id": "name",
                "raw_value": value["locator"]["quote"],
            }
            value["evidence_artifact_sha256"] = digest(
                json.dumps(
                    field,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        if value["source_type"] in ("repository_file", "code_existence_report"):
            value["source_revision"] = f"snapshot:fixture_inventory:{RAW}"
        if value["source_type"] == "repository_file":
            value["evidence_artifact_sha256"] = RAW
    return base


def sha(value):
    return hashlib.sha256(value).hexdigest()


class Access:
    reference = ArtifactRef(artifact_id="access-1", artifact_sha256="a" * 64, profile_id="access/1")

    def __init__(self, answer="allow"):
        self.answer, self.calls = (answer, [])

    def decision(self, namespace_id, source_id):
        self.calls.append((namespace_id, source_id))
        return self.answer


class Generation:
    reference = ArtifactRef(
        artifact_id="generation-1", artifact_sha256="b" * 64, profile_id="generation/1"
    )

    def __init__(self, source):
        self.source, self.calls = (source, [])

    def current(self, namespace_id, source_id):
        self.calls.append((namespace_id, source_id))
        return self.source


DEFAULT_RAW = "Heading\r\n  é Exact CASE\r\nOther release\n".encode()


def source(
    raw=DEFAULT_RAW,
    *,
    namespace="notes-1",
    revision="raw-generation-1",
    sections=None,
    newline_policy="crlf-to-lf",
    bounds=None,
):
    return MemorySource.from_bytes(
        namespace_id=namespace,
        source_id="notes",
        source_revision=revision,
        raw=raw,
        newline_policy=newline_policy,
        sections=(SectionSpan("release-1", 1, 2),) if sections is None else sections,
        bounds=MemoryBounds() if bounds is None else bounds,
    )


def setup(retained=None, access=None, current=None):
    retained = retained or source()
    access = access or Access()
    generation = Generation(retained.metadata if current is None else current)
    resolver = MemoryTextResolver((retained,), access=access, generation=generation)
    registry = Registry()
    install_supplied_text(registry, resolver)
    context = ResolutionContext(
        namespace_id=retained.namespace_id,
        access_policy_ref=access.reference,
        generation_policy_ref=generation.reference,
        required_scope="text",
    )
    return (retained, access, generation, resolver, registry, context)


def pointer(retained, *, section=False, quote="é Exact CASE", start=2, end=2):
    metadata = retained.metadata
    locator = {"section_id": "release-1"} if section else {"line_start": start, "line_end": end}
    if quote is not None:
        locator.update(quote=quote, excerpt_sha256=sha(quote.encode()))
    return POINTER_ADAPTER.validate_python(
        {
            "schema_version": 1,
            "kind": "doc:section" if section else "file:line",
            "source_type": "supplied_text",
            "locator_subtype": "section" if section else "line",
            "source_id": metadata.source_id,
            "source_sha256": metadata.source_sha256,
            "source_revision": metadata.source_revision,
            "evidence_artifact_sha256": metadata.evidence_artifact_sha256,
            "locator": locator,
        }
    )


@pytest.mark.parametrize("section", [False, True])
def test_actual_positive_line_and_section_quotes_preserve_evidence(section):
    retained, access, generation, resolver, registry, context = setup()
    result = registry.resolve(pointer(retained, section=section), context)
    assert result.status == "resolved" and result.scope == "text"
    assert result.excerpt == "é Exact CASE"
    assert result.excerpt_sha256 == sha("é Exact CASE".encode())
    assert result.checked_source_sha256 == sha(retained.raw)
    assert result.checked_artifact_sha256 == retained.metadata.evidence_artifact_sha256
    assert len(access.calls) == len(generation.calls) == 2


def test_raw_canonical_and_artifact_preimages_recomputed_without_normalizing_quotes():
    retained = source()
    assert retained.metadata.source_sha256 == sha(retained.raw)
    assert retained.canonical == "Heading\n  é Exact CASE\nOther release\n".encode()
    assert retained.metadata.canonical_text_sha256 == sha(retained.canonical)
    assert len(retained.lines) == 3
    artifact = {
        "algorithm": "supplied-text-memory/1",
        "namespace_id": retained.namespace_id,
        "source_id": "notes",
        "source_revision": "raw-generation-1",
        "source_sha256": sha(retained.raw),
        "canonical_text_sha256": sha(retained.canonical),
        "newline_policy": "crlf-to-lf",
        "lines": [asdict(line) for line in retained.lines],
        "sections": [asdict(span) for span in retained.sections],
    }
    preimage = json.dumps(
        artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    assert retained.metadata.evidence_artifact_sha256 == sha(preimage)
    for line in retained.lines:
        raw_line = retained.raw[line.source_byte_start : line.source_byte_end]
        canonical_line = retained.canonical[line.canonical_byte_start : line.canonical_byte_end]
        assert raw_line.replace(b"\r\n", b"\n") == canonical_line


@pytest.mark.parametrize(
    "quote", ["é exact case", "é Exact CASE", "é  Exact CASE", "Other release", "é Exact CASE\r\n"]
)
def test_quote_cannot_be_repaired_or_borrowed_from_other_window(quote):
    retained, _, _, _, registry, context = setup()
    result = registry.resolve(pointer(retained, quote=quote), context)
    assert result.status == "invalid" and result.excerpt is None


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("id", "invalid"),
        ("revision", "stale"),
        ("raw_hash", "stale"),
        ("artifact_hash", "stale"),
        ("outside_line", "invalid"),
        ("section", "missing"),
        ("spatial", "unsupported"),
        ("policy_ref", "unsupported"),
        ("namespace", "missing"),
    ],
)
def test_wrong_source_scope_hash_and_policy_fail_closed(mutation, expected):
    retained, _, _, _, registry, context = setup()
    value = pointer(retained, section=mutation == "section").model_dump()
    if mutation == "id":
        value["source_id"] = "other-notes"
    elif mutation == "revision":
        value["source_revision"] = "raw-generation-2"
    elif mutation == "raw_hash":
        value["source_sha256"] = "c" * 64
    elif mutation == "artifact_hash":
        value["evidence_artifact_sha256"] = "d" * 64
    elif mutation == "outside_line":
        value["locator"].update(line_start=4, line_end=4)
    elif mutation == "section":
        value["locator"]["section_id"] = "unknown-section"
    elif mutation == "spatial":
        context = context.model_copy(update={"required_scope": "spatial"})
    elif mutation == "policy_ref":
        context = context.model_copy(
            update={
                "access_policy_ref": ArtifactRef(
                    artifact_id="wrong-policy", artifact_sha256="a" * 64, profile_id="access/1"
                )
            }
        )
    elif mutation == "namespace":
        context = context.model_copy(update={"namespace_id": "other-namespace"})
    result = registry.resolve(POINTER_ADAPTER.validate_python(value), context)
    assert result.status == expected and result.excerpt is None


@pytest.mark.parametrize("answer", ["deny", "unknown", None, True])
def test_unknown_or_denied_access_does_not_consult_generation_or_inspect_buffer(answer):
    retained, access, generation, _, registry, context = setup(access=Access(answer))
    candidate = pointer(retained)
    object.__setattr__(retained, "raw", b"tampered")
    result = registry.resolve(candidate, context)
    assert result.status == "unsupported" and result.reason == "access_not_allowed"
    assert not generation.calls and len(access.calls) == 1


def test_unknown_generation_missing_buffer_and_stale_actual_bytes_are_distinct():
    retained, _, generation, _, registry, context = setup()
    candidate = pointer(retained)
    generation.source = None
    assert registry.resolve(candidate, context).reason == "current_generation_unknown"
    access = Access()
    generation.source = retained.metadata
    empty = MemoryTextResolver((), access=access, generation=generation)
    assert empty.resolve(candidate, context).status == "missing"
    object.__setattr__(retained, "raw", b"tampered")
    result = registry.resolve(candidate, context)
    assert result.status == "invalid" and result.reason == "retained_source_mismatch"


def test_mutable_canonical_view_cannot_be_retained_even_if_equal_now():
    retained, _, _, _, registry, context = setup()
    candidate = pointer(retained)
    object.__setattr__(retained, "canonical", bytearray(retained.canonical))
    assert registry.resolve(candidate, context).reason == "retained_source_mismatch"


def test_section_bounds_are_hashed_and_cannot_borrow_outside_text():
    retained, _, _, _, registry, context = setup()
    assert (
        registry.resolve(pointer(retained, section=True, quote="Other release"), context).status
        == "invalid"
    )
    changed = source(sections=(SectionSpan("release-1", 1, 3),))
    assert changed.metadata.source_sha256 == retained.metadata.source_sha256
    assert changed.metadata.evidence_artifact_sha256 != retained.metadata.evidence_artifact_sha256
    _, _, _, _, changed_registry, changed_context = setup(changed)
    assert (
        changed_registry.resolve(pointer(retained, section=True), changed_context).status == "stale"
    )


def test_undeclared_actions_never_reach_source_policy():
    retained, access, generation, _, registry, context = setup()
    value = pointer(retained).model_dump()
    value["execute"] = "source instruction"
    assert registry.resolve(value, context).status == "invalid"
    assert not access.calls and (not generation.calls)


def test_policy_exceptions_never_disclose_source_or_error_body():
    retained, access, _, _, registry, context = setup()

    def error(*args):
        raise RuntimeError("private-source-canary")

    access.decision = error
    result = registry.resolve(pointer(retained), context)
    assert result.status == "unsupported"
    assert "private-source-canary" not in result.model_dump_json()


@pytest.mark.parametrize("change", ["access", "generation", "retained"])
def test_revocation_or_source_mutation_during_read_cannot_disclose(change):
    retained, access, generation, _, registry, context = setup()
    candidate = pointer(retained)
    calls = 0

    def changing_current(namespace, source_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "generation":
                return source(raw=b"Changed\n", sections=(), revision="raw-generation-2").metadata
            if change == "retained":
                object.__setattr__(retained, "raw", b"tampered")
        return retained.metadata

    generation.current = changing_current
    if change == "access":
        original = access.decision

        def revoking(namespace, source_id):
            result = original(namespace, source_id)
            return "deny" if len(access.calls) == 2 else result

        access.decision = revoking
    result = registry.resolve(candidate, context)
    assert result.status != "resolved" and result.excerpt is None


def test_equal_bytes_in_other_namespace_do_not_retarget_artifact_or_source():
    first = source()
    other = source(namespace="other-namespace")
    assert first.metadata.source_sha256 == other.metadata.source_sha256
    assert first.metadata.evidence_artifact_sha256 != other.metadata.evidence_artifact_sha256
    _, _, _, _, registry, context = setup(other)
    assert registry.resolve(pointer(first), context).status == "stale"


@pytest.mark.parametrize(
    "raw,line_count",
    [
        (b"", 0),
        (b"\n", 1),
        (b"A\n", 1),
        (b"A", 1),
        (b"A\rB", 1),
        ("A\u2028B\n".encode(), 1),
        (b"A\r\nB\n", 2),
    ],
)
def test_physical_line_mapping_is_lf_only_without_phantom_line(raw, line_count):
    retained = source(raw=raw, sections=())
    assert len(retained.lines) == len(retained.metadata.line_map) == line_count
    assert retained.metadata.raw_byte_length == len(raw)
    if not raw:
        _, _, _, _, registry, context = setup(retained)
        assert (
            registry.resolve(pointer(retained, quote=None, start=1, end=1), context).status
            == "invalid"
        )


def test_identity_and_crlf_profiles_never_cross_validate_quote_or_artifact():
    raw = b"One\r\nTwo\r\n"
    identity = source(raw, sections=(), newline_policy="identity")
    normalized = source(raw, sections=(), newline_policy="crlf-to-lf")
    assert identity.metadata.source_sha256 == normalized.metadata.source_sha256
    assert (
        identity.metadata.evidence_artifact_sha256 != normalized.metadata.evidence_artifact_sha256
    )
    _, _, _, _, registry, context = setup(identity)
    result = registry.resolve(pointer(identity, quote="One\r\nTwo", start=1, end=2), context)
    assert result.status == "resolved" and result.excerpt == "One\r\nTwo"
    assert (
        registry.resolve(pointer(normalized, quote="One\nTwo", start=1, end=2), context).status
        == "stale"
    )


@pytest.mark.parametrize(
    "case", ["invalid_utf8", "mutable", "bytes", "lines", "sections", "duplicate_sections"]
)
def test_buffer_and_section_limits_refuse_whole_source_without_truncating(case):
    kwargs = {"raw": b"A\nB\n", "sections": ()}
    if case == "invalid_utf8":
        kwargs["raw"] = b"\xff"
    elif case == "mutable":
        kwargs["raw"] = bytearray(b"A")
    elif case == "bytes":
        kwargs["bounds"] = MemoryBounds(max_source_bytes=3)
    elif case == "lines":
        kwargs["bounds"] = MemoryBounds(max_lines=1)
    elif case == "sections":
        kwargs["sections"] = (SectionSpan("outside", 1, 3),)
    else:
        kwargs["sections"] = (SectionSpan("same", 1, 1), SectionSpan("same", 2, 2))
    with pytest.raises((ValueError, TypeError)):
        source(**kwargs)


def test_collection_bounds_and_setup_duplicates_have_no_partial_override():
    retained = source()
    access, generation = (Access(), Generation(retained.metadata))
    for collection, bounds in [
        ((retained, retained), MemoryBounds()),
        ((retained,), MemoryBounds(max_source_bytes=1)),
        ((retained,), MemoryBounds(max_retained_bytes=1)),
    ]:
        with pytest.raises(ValueError):
            MemoryTextResolver(collection, access=access, generation=generation, bounds=bounds)
    resolver = MemoryTextResolver((retained,), access=access, generation=generation)
    registry = Registry()
    registry.register(SECTION_KEY, resolver)
    with pytest.raises(DuplicateResolverError):
        install_supplied_text(registry, resolver)
    assert registry.installed_domains == frozenset({SECTION_KEY})


def test_no_quote_returns_actual_window_but_oversize_never_truncates():
    retained, _, _, _, registry, context = setup()
    result = registry.resolve(pointer(retained, quote=None), context)
    assert result.status == "resolved" and result.excerpt == "  é Exact CASE\n"
    big = source(raw=b"a" * 32769, sections=())
    _, _, _, _, registry, context = setup(big)
    assert (
        registry.resolve(pointer(big, quote=None, start=1, end=1), context).reason
        == "excerpt_limit_exceeded"
    )


def test_mandatory_evidence_downgrade_preserves_inputs_diagnoses_and_counts():
    retained, _, _, _, registry, context = setup()
    valid = pointer(retained)
    invalid = pointer(retained, quote="Other release")
    finding = Finding(
        id="finding-1",
        text="Still a semantic claim",
        verdict="partial",
        probability=1.0,
        evidence=[valid, invalid],
    )
    result = assess_required_evidence(finding, registry, context)
    assert result.finding.verdict == "unsupported" and result.finding.probability == 1.0
    assert result.finding.evidence == finding.evidence and finding.verdict == "partial"
    assert (result.required_count, result.resolved_count, result.unresolved_count) == (2, 1, 1)
    assert dict(result.status_counts) == {"invalid": 1, "resolved": 1}
    assert dict(result.reason_counts) == {"quote_outside_window": 1}
    positive = assess_required_evidence(
        Finding(id="positive", text="Potential change", verdict="partial", evidence=[valid]),
        registry,
        context,
    )
    assert positive.finding.verdict == "partial" and positive.resolved_count == 1
    unsupported = Finding(id="unsupported", text="Unknown", verdict="unsupported", evidence=[valid])
    assert assess_required_evidence(unsupported, registry, context).finding.verdict == "unsupported"
    empty = Finding(id="empty", text="Unknown", verdict="partial", evidence=[])
    assert assess_required_evidence(empty, registry, context).finding.verdict == "unsupported"


def test_supplied_source_does_not_enable_foreign_domain_fallback():
    retained, _, _, resolver, registry, context = setup()
    for value in domain_wires():
        if value["source_type"] != "supplied_text":
            candidate = POINTER_ADAPTER.validate_python(value)
            assert registry.resolve(candidate, context).status == "unsupported"
            assert resolver.resolve(candidate, context).status == "unsupported"
    assert registry.installed_domains == frozenset({LINE_KEY, SECTION_KEY})


def test_actual_detached_memory_consumer_requires_no_node_or_owner_app(tmp_path):
    root = Path(__file__).resolve().parents[2]
    namespace = tmp_path / "portable"
    namespace.mkdir()
    (namespace / "__init__.py").write_text("", encoding="utf-8")
    destination = namespace / "cited"
    destination.mkdir()
    for name in (
        "__init__.py",
        "models.py",
        "registry.py",
        "schema.py",
        "memory.py",
        "assessment.py",
    ):
        shutil.copyfile(
            root / "src" / "entity_matcher" / "shared" / "cited" / name, destination / name
        )
    script = r"""
import sys,json,os,socket,subprocess
from pathlib import Path
from pydantic import BaseModel
owner=Path(sys.argv[2]).resolve()
sys.path=[path for path in sys.path if Path(path).resolve() not in (owner,owner/"src")]
sys.path.insert(0,sys.argv[1])
class DependencyProbe(BaseModel):
    count:int
assert DependencyProbe(count=1).count==1
original_getenv=os.getenv
def forbidden(*args,**kwargs):
    raise AssertionError("source consumer attempted ungranted effect")
def guarded_getenv(name,*args):
    if name=="PYDANTIC_DISABLE_PLUGINS":return original_getenv(name,*args)
    return forbidden(name,*args)
os.getenv=guarded_getenv
socket.create_connection=forbidden
socket.socket.connect=forbidden
subprocess.run=forbidden
Path.read_bytes=forbidden
Path.read_text=forbidden
import importlib,importlib.abc
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self,name,path=None,target=None):
        if name.split('.')[0] in {'changelog_advisor','entity_matcher','openai','httpx','typesafe'}:
            raise RuntimeError('owner or provider disabled')
sys.meta_path.insert(0,Guard())
for name in ('changelog_advisor','entity_matcher','openai'):
    try: importlib.import_module(name)
    except RuntimeError: pass
    else: raise AssertionError('owner import canary did not reject')
try: socket.create_connection(('example.invalid',443))
except AssertionError: pass
else: raise AssertionError('network canary did not reject')
from portable.cited import ArtifactRef,Finding,POINTER_ADAPTER,Registry,ResolutionContext
from portable.cited.memory import MemorySource,MemoryTextResolver,SectionSpan,install_supplied_text
from portable.cited.assessment import assess_required_evidence
data=MemorySource.from_bytes(namespace_id="notes-1",source_id="notes",source_revision="raw-1",
    raw="Release\r\nActual é change\r\n".encode(),newline_policy="crlf-to-lf",
    sections=(SectionSpan("release-1",1,2),))
class Access:
    reference=ArtifactRef(artifact_id="access",artifact_sha256="a"*64,profile_id="access/1")
    def decision(self,namespace_id,source_id):
        return "allow" if (namespace_id,source_id)==("notes-1","notes") else "unknown"
class Generation:
    reference=ArtifactRef(artifact_id="generation",artifact_sha256="b"*64,profile_id="generation/1")
    def current(self,namespace_id,source_id):
        return data.metadata if (namespace_id,source_id)==("notes-1","notes") else None
access,generation=Access(),Generation()
context=ResolutionContext(namespace_id="notes-1",access_policy_ref=access.reference,
    generation_policy_ref=generation.reference,required_scope="text")
registry=Registry()
install_supplied_text(registry,MemoryTextResolver((data,),access=access,generation=generation))
common=dict(schema_version=1,source_type="supplied_text",source_id="notes",
    source_sha256=data.metadata.source_sha256,source_revision="raw-1",
    evidence_artifact_sha256=data.metadata.evidence_artifact_sha256)
line=POINTER_ADAPTER.validate_python(dict(**common,kind="file:line",locator_subtype="line",
    locator=dict(line_start=2,line_end=2)))
section=POINTER_ADAPTER.validate_python(dict(**common,kind="doc:section",locator_subtype="section",
    locator=dict(section_id="release-1")))
assert registry.resolve(line,context).excerpt=="Actual é change\n"
assert registry.resolve(section,context).excerpt=="Release\nActual é change\n"
finding=Finding(id="claim-1",text="A potential semantic change",verdict="partial",
    probability=1.0,evidence=[line,section])
assessment=assess_required_evidence(finding,registry,context)
assert assessment.finding.verdict=="partial" and assessment.resolved_count==2
assert not any(name.startswith(("changelog_advisor","openai","typesafe","requests","httpx"))
    for name in sys.modules)
print(json.dumps(dict(actual_resolved=2,semantic_verdict=assessment.finding.verdict,
    owner_app_imported=False,node_calls=0)))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(tmp_path), str(root)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "actual_resolved": 2,
        "semantic_verdict": "partial",
        "owner_app_imported": False,
        "node_calls": 0,
    }
