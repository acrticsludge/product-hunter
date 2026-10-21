from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from product_hunter.cli import (
    db_import_run_command,
    eval_command,
    extract_command,
    report_command,
    review_sheet_command,
)
from product_hunter.config import (
    CatalogSettings,
    ExtractionConfig,
    RoutingConfig,
    load_config,
    load_routing_config,
)
from product_hunter.db import Store, connect, list_executions, list_inventory
from product_hunter.evaluation import COLUMNS
from product_hunter.fetch import FetchRecord
from product_hunter.llm.base import (
    GenerationRequest,
    GenerationResponse,
    ProviderError,
    ProviderUnavailable,
    TextGenerator,
)
from product_hunter.llm.catalog import ModelCatalog, parse_model_catalog
from product_hunter.llm.discovery import DiscoveryError
from product_hunter.llm.routing import ModelRole, RouteCandidate

FIXTURE = Path(__file__).parent / "fixtures" / "hn_search.json"
BLOG_FIXTURE = Path(__file__).parent / "fixtures" / "blog.html"

# The one fixture hit that carries both quotes: a cost the team pays by hand,
# and a complaint about a missing feature with no cost behind it.
GENUINE_QUOTE = (
    "The PDF it produces is unreadable, so I retype every number into the "
    "ledger by hand and hope I do not miss a row."
)
NOT_PAIN_QUOTE = 'Our bookkeeping tool "does not export" anything.'

ANSWER = json.dumps(
    {
        "candidates": [
            {
                "quote": GENUINE_QUOTE,
                "pain_validity": "genuine",
                "reason": "recurring_manual_work",
                "pain_statement": "the team retypes every ledger number by hand",
                "severity": 3,
                "frequency": "weekly",
                "confidence": 0.8,
                "rationale": "the text says the PDF is unreadable and every number is retyped by hand",
                "domain": "finance",
                "actor": "the bookkeeper",
                "action": "retype ledger numbers by hand",
                "object": "ledger numbers",
                "consequence": "a row that can be missed in the ledger",
            },
            {
                "quote": NOT_PAIN_QUOTE,
                "pain_validity": "not_pain",
                "reason": "feature_request",
                "pain_statement": "the export tool is missing an export feature",
                "severity": 0,
                "frequency": "unknown",
                "confidence": 0.6,
                "rationale": "the text states a missing feature with no cost behind it",
            },
        ]
    }
)


class FakeGenerator:
    """A TextGenerator that answers every call with the same two candidates."""

    name = "fake"

    def __init__(self, answer: str = ANSWER) -> None:
        self._answer = answer
        self.prompts: list[str] = []

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.prompts.append(request.prompt)
        return GenerationResponse(
            text=self._answer,
            provider="fake-provider",
            model="fake-model",
            extractor_version="phase1-0.3",
            input_chars=len(request.prompt),
            latency_ms=3,
        )

    def check(self) -> dict[str, object]:
        return {"ok": True}


class UnavailableGenerator(FakeGenerator):
    """A generator whose provider check fails, so no call may be made."""

    def check(self) -> dict[str, object]:
        raise ProviderUnavailable("opencode CLI not found on PATH")


class RoutedGenerator:
    """A generator that reports the model it was built with.

    The real provider reads the provider and the model off the config it was
    built with, so a fake that does the same shows which model the router
    bound to the run.
    """

    name = "fake"

    def __init__(self, config, answer: str = ANSWER) -> None:
        self.provider_id = config.provider_id
        self.model_id = config.model_id
        self.answer = answer
        self.prompts: list[str] = []

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.prompts.append(request.prompt)
        return GenerationResponse(
            text=self.answer,
            provider=self.provider_id,
            model=self.model_id,
            extractor_version="phase1-0.3",
            input_chars=len(request.prompt),
            latency_ms=7,
        )

    def check(self) -> dict[str, object]:
        return {"ok": True}


class UnavailableRoutedGenerator(RoutedGenerator):
    """A routed generator whose provider check fails, so no call may be made."""

    def check(self) -> dict[str, object]:
        raise ProviderUnavailable("opencode CLI not found on PATH")


class ExplodingRoutedGenerator(RoutedGenerator):
    """A routed generator whose call fails, so the failure names its model."""

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.prompts.append(request.prompt)
        raise ProviderError("exploded at call time")


class FakeDiscovery:
    """A discovery that hands out one catalogue."""

    def __init__(self, catalog: ModelCatalog) -> None:
        self._catalog = catalog

    def discover(self) -> ModelCatalog:
        return self._catalog


class BrokenDiscovery:
    """A discovery whose catalogue cannot be reached."""

    def discover(self) -> ModelCatalog:
        raise DiscoveryError("model discovery failed: the opencode server is down")


def fake_catalog(*model_ids: str, provider_id: str = "opencode-go") -> ModelCatalog:
    """A catalogue holding the given models, all of them available."""
    return parse_model_catalog(
        json.dumps(
            {
                "data": [
                    {
                        "modelID": model_id,
                        "providerID": provider_id,
                        "status": "active",
                        "enabled": True,
                    }
                    for model_id in model_ids
                ]
            }
        ),
        source="test",
    )


def patch_generators(monkeypatch, answer: str = ANSWER) -> list[RoutedGenerator]:
    """Make every built generator a fake that reports the model it was bound to.

    The pinned generator and the routed one are built by the same factory, so
    the two patch points are the module the router path uses and the module
    the command uses.
    """
    built: list[RoutedGenerator] = []

    def factory(config):
        generator = RoutedGenerator(config, answer)
        built.append(generator)
        return generator

    monkeypatch.setattr("product_hunter.llm.build_generator", factory)
    monkeypatch.setattr("product_hunter.cli.build_generator", factory)
    return built


def routing_config_with(extraction: list[RouteCandidate]) -> RoutingConfig:
    """A routing config with every role bound and the extraction chain given."""
    roles = {role: [RouteCandidate(kind="pattern", value="opencode-go/*")] for role in ModelRole}
    roles[ModelRole.extraction] = extraction
    return RoutingConfig(
        version=1,
        catalog=CatalogSettings(
            refresh_interval_seconds=900, discovery_timeout_seconds=30
        ),
        roles=roles,
    )


def seed_repo(root: Path) -> str:
    """Store the HN fixture as a raw capture and record the fetch that made it."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["hits"] = [hit for hit in payload["hits"] if hit["objectID"] == "9000005"]
    raw = json.dumps(payload).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    raw_path = Path("data") / "raw" / "hn_algolia" / f"{digest}.json"
    target = root / raw_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    record = FetchRecord(
        fetch_id="fetch_test",
        source_id="hn_algolia",
        requested_url="https://hn.algolia.com/api/v1/search?query=invoice",
        final_url="https://hn.algolia.com/api/v1/search?query=invoice",
        status=200,
        content_type="application/json",
        raw_sha256=digest,
        raw_path=raw_path.as_posix(),
        bytes=len(raw),
        fetched_at="2026-10-20T09:00:00+00:00",
    )
    log = root / "data" / "raw" / "fetches.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    return digest


def seed_blog_repo(root: Path) -> str:
    """Store the blog fixture as a raw capture of a source that may not be analyzed."""
    raw = BLOG_FIXTURE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    raw_path = Path("data") / "raw" / "blog_http" / f"{digest}.html"
    target = root / raw_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    record = FetchRecord(
        fetch_id="fetch_blog",
        source_id="blog_http",
        requested_url="https://blog.example.com/bookkeeping",
        final_url="https://blog.example.com/bookkeeping",
        status=200,
        content_type="text/html",
        raw_sha256=digest,
        raw_path=raw_path.as_posix(),
        bytes=len(raw),
        fetched_at="2026-10-20T09:00:00+00:00",
    )
    log = root / "data" / "raw" / "fetches.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    return digest


def run_extract(root: Path, run_id: str = "test-run") -> int:
    """Extract the seeded capture with a fake provider."""
    generator = FakeGenerator()
    assert isinstance(generator, TextGenerator)
    return extract_command(
        limit=1,
        repo_root=root,
        config=load_config(),
        generator=generator,
        run_id=run_id,
    )


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_routed_extract(
    root: Path,
    run_id: str,
    discovery,
    *,
    routing_config: RoutingConfig | None = None,
    config: ExtractionConfig | None = None,
) -> int:
    """Extract the seeded capture with the model the router chooses."""
    return extract_command(
        limit=1,
        repo_root=root,
        config=config if config is not None else load_config(),
        discovery=discovery,
        routing_config=routing_config,
        run_id=run_id,
    )


def two_chunk_config() -> ExtractionConfig:
    """The extraction config that splits the seeded capture into two chunks.

    The capture is one document of 162 characters, so a chunk of 120 with an
    overlap of 20 cuts it in two.
    """
    config = load_config()
    limits = config.limits.model_copy(
        update={"chunk_chars": 120, "chunk_overlap_chars": 20}
    )
    return config.model_copy(update={"limits": limits})


def test_extract_command_resolves_the_model_through_the_router(
    tmp_path, capsys, monkeypatch
):
    """The extraction runs the model the role resolved to, not the pinned one."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash")
    built = patch_generators(monkeypatch)

    code = run_routed_extract(tmp_path, "routed-run", FakeDiscovery(catalog))

    assert code == 0
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "routed-run"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    block = manifest["model_routing"]
    assert block["role"] == "extraction"
    assert block["provider_id"] == "opencode-go"
    assert block["model_id"] == "alpha-flash"
    assert block["selection_source"] == "pattern"
    assert block["fallback_index"] == 0
    assert block["is_fallback"] is False
    assert block["catalog_version"] == catalog.catalog_version
    assert block["config_version"] == load_routing_config().config_version
    assert block["resolved_at"]
    # The generator ran the model the router chose, not the pinned one.
    assert built[0].model_id == "alpha-flash"
    assert built[0].model_id != load_config().model_id
    calls = read_jsonl(out / "calls.jsonl")
    assert calls[0]["model"] == "alpha-flash"


def test_the_run_records_the_model_executions(tmp_path, capsys, monkeypatch):
    """Every model call of the run is recorded with its provenance."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash")
    patch_generators(monkeypatch)

    code = run_routed_extract(tmp_path, "executions-run", FakeDiscovery(catalog))

    assert code == 0
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "executions-run"
    rows = read_jsonl(out / "model_executions.jsonl")
    assert len(rows) == 1
    row = rows[0]
    assert row["execution_id"]
    assert row["run_id"] == "executions-run"
    assert row["stage"] == "PAIN_EXTRACTION"
    assert row["role"] == "extraction"
    assert row["provider_id"] == "opencode-go"
    assert row["model_id"] == "alpha-flash"
    assert row["selection_source"] == "pattern"
    assert row["fallback_index"] == 0
    assert row["is_fallback"] is False
    assert row["catalog_version"] == catalog.catalog_version
    assert row["prompt_version"] == load_config().prompt_version
    assert row["config_version"] == load_routing_config().config_version
    assert row["latency_ms"] == 7
    assert row["status"] == "ok"
    assert row["error"] is None
    assert row["created_at"]


def test_a_vanished_model_falls_through_to_the_next_candidate(
    tmp_path, capsys, monkeypatch
):
    """A model that left the catalogue is skipped, and the run continues."""
    seed_repo(tmp_path)
    catalog = fake_catalog("beta-flash")
    built = patch_generators(monkeypatch)
    routing = routing_config_with(
        [
            RouteCandidate(kind="model", value="opencode-go/vanished"),
            RouteCandidate(kind="pattern", value="opencode-go/*-flash"),
        ]
    )

    code = run_routed_extract(
        tmp_path, "vanished-run", FakeDiscovery(catalog), routing_config=routing
    )

    assert code == 0
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "vanished-run"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_routing"]["model_id"] == "beta-flash"
    assert built[0].model_id == "beta-flash"
    calls = read_jsonl(out / "calls.jsonl")
    assert calls[0]["status"] == "ok"
    assert calls[0]["model"] == "beta-flash"


def test_the_manifest_names_the_model_the_router_chose(
    tmp_path, capsys, monkeypatch
):
    """The manifest names the model the router chose, not the pinned one."""
    seed_repo(tmp_path)
    catalog = fake_catalog("beta-flash")
    patch_generators(monkeypatch)
    routing = routing_config_with(
        [
            RouteCandidate(kind="model", value="opencode-go/vanished"),
            RouteCandidate(kind="pattern", value="opencode-go/*-flash"),
        ]
    )

    code = run_routed_extract(
        tmp_path, "manifest-run", FakeDiscovery(catalog), routing_config=routing
    )

    assert code == 0
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "manifest-run"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider"] == "opencode-go"
    assert manifest["model"] == "beta-flash"
    # The model that ran is not the model pinned in the extraction config.
    assert manifest["model"] != load_config().model_id
    # The routing block keeps the whole selection.
    assert manifest["model_routing"]["model_id"] == "beta-flash"
    assert manifest["model_routing"]["selection_source"] == "pattern"


def test_the_import_records_the_policy_of_the_selected_model(
    tmp_path, capsys, monkeypatch
):
    """The store names the model the router chose, not the pinned one."""
    seed_repo(tmp_path)
    catalog = fake_catalog("beta-flash")
    patch_generators(monkeypatch)
    routing = routing_config_with(
        [
            RouteCandidate(kind="model", value="opencode-go/vanished"),
            RouteCandidate(kind="pattern", value="opencode-go/*-flash"),
        ]
    )
    code = run_routed_extract(
        tmp_path, "policy-run", FakeDiscovery(catalog), routing_config=routing
    )
    assert code == 0
    capsys.readouterr()

    db_path = tmp_path / "product_hunter.db"
    store = Store.open(db_path)
    store.close()

    code = db_import_run_command("policy-run", db_path=db_path, repo_root=tmp_path)

    assert code == 0
    capsys.readouterr()
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT model_policy_version FROM research_runs WHERE run_id = ?",
            ("policy-run",),
        ).fetchone()
        assert row is not None
        assert row["model_policy_version"] == "opencode-go/beta-flash"
    finally:
        conn.close()


def test_a_broken_router_runs_the_extraction_on_the_pinned_model(
    tmp_path, capsys, monkeypatch
):
    """A router that cannot answer never stops the run."""
    seed_repo(tmp_path)
    built = patch_generators(monkeypatch)

    code = run_routed_extract(tmp_path, "broken-run", BrokenDiscovery())

    assert code == 0
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "broken-run"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    block = manifest["model_routing"]
    assert block["selection_source"] == "default"
    assert block["model_id"] == load_config().model_id
    assert block["reason"]
    assert "no available model" in block["reason"]
    # The model pinned in the extraction config ran the extraction.
    assert built[0].model_id == load_config().model_id
    calls = read_jsonl(out / "calls.jsonl")
    assert calls[0]["model"] == load_config().model_id
    # The run says why the router was not the one that chose the model.
    rejects = read_jsonl(out / "rejects.jsonl")
    assert len(rejects) == 1
    assert rejects[0]["reason_code"] == "routing_failed"
    assert "no available model" in rejects[0]["detail"]


def test_the_rejection_names_the_model_that_ran(tmp_path, capsys, monkeypatch):
    """A rejection made while a routed model ran names that model."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash", provider_id="other-provider")
    routing = routing_config_with(
        [RouteCandidate(kind="pattern", value="other-provider/*")]
    )

    def factory(config):
        return UnavailableRoutedGenerator(config)

    monkeypatch.setattr("product_hunter.llm.build_generator", factory)
    monkeypatch.setattr("product_hunter.cli.build_generator", factory)

    code = run_routed_extract(
        tmp_path, "reject-run", FakeDiscovery(catalog), routing_config=routing
    )

    assert code == 1
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "reject-run"
    rejects = read_jsonl(out / "rejects.jsonl")
    assert len(rejects) == 1
    assert rejects[0]["reason_code"] == "provider_check_failed"
    # The record names the model the router chose, not the pinned one.
    assert rejects[0]["provider"] == "other-provider"
    assert rejects[0]["model"] == "alpha-flash"
    assert rejects[0]["provider"] != load_config().provider_id
    assert rejects[0]["model"] != load_config().model_id


def test_the_routing_rejection_names_the_pinned_model(tmp_path, capsys, monkeypatch):
    """A rejection made after the router failed names the model that ran."""
    seed_repo(tmp_path)
    patch_generators(monkeypatch)

    code = run_routed_extract(tmp_path, "pinned-reject-run", BrokenDiscovery())

    assert code == 0
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "pinned-reject-run"
    rejects = read_jsonl(out / "rejects.jsonl")
    assert len(rejects) == 1
    assert rejects[0]["reason_code"] == "routing_failed"
    # The router could not answer, so the pinned model ran the extraction.
    assert rejects[0]["provider"] == load_config().provider_id
    assert rejects[0]["model"] == load_config().model_id


def test_a_failed_call_is_recorded_with_its_reason(tmp_path, capsys, monkeypatch):
    """A call that failed keeps its reason in the execution row."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash")
    patch_generators(monkeypatch, answer="not json at all")

    code = run_routed_extract(tmp_path, "failed-run", FakeDiscovery(catalog))

    assert code == 1
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "failed-run"
    rows = read_jsonl(out / "model_executions.jsonl")
    assert len(rows) == 1
    assert rows[0]["status"] == "error"
    # The call's own status code and message, so the failure keeps its reason.
    assert rows[0]["error"].startswith("invalid_json: ")
    assert "not valid JSON" in rows[0]["error"]
    assert rows[0]["model_id"] == "alpha-flash"


def test_a_failed_routed_call_names_the_model_that_ran(tmp_path, capsys, monkeypatch):
    """A call that fails at call time is attributed to the routed model."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash")

    def factory(config):
        return ExplodingRoutedGenerator(config)

    monkeypatch.setattr("product_hunter.llm.build_generator", factory)
    monkeypatch.setattr("product_hunter.cli.build_generator", factory)

    code = run_routed_extract(tmp_path, "exploded-run", FakeDiscovery(catalog))

    assert code == 1
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "exploded-run"
    # The call record names the model that was called, not the pinned one.
    calls = read_jsonl(out / "calls.jsonl")
    assert len(calls) == 1
    assert calls[0]["status"] == "provider_error"
    assert calls[0]["provider"] == "opencode-go"
    assert calls[0]["model"] == "alpha-flash"
    assert calls[0]["model"] != load_config().model_id
    # The execution row names the same model as the call it came from.
    rows = read_jsonl(out / "model_executions.jsonl")
    assert len(rows) == 1
    assert rows[0]["status"] == "error"
    assert rows[0]["provider_id"] == "opencode-go"
    assert rows[0]["model_id"] == "alpha-flash"
    assert rows[0]["model_id"] != load_config().model_id
    assert "exploded at call time" in rows[0]["error"]


def test_a_failed_call_falls_through_and_both_calls_are_recorded(
    tmp_path, capsys, monkeypatch
):
    """A model that fails at call time is retried, and both calls are recorded."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash", "beta-flash")

    def factory(config):
        if config.model_id == "alpha-flash":
            return ExplodingRoutedGenerator(config)
        return RoutedGenerator(config)

    monkeypatch.setattr("product_hunter.llm.build_generator", factory)
    monkeypatch.setattr("product_hunter.cli.build_generator", factory)

    code = run_routed_extract(tmp_path, "fallback-run", FakeDiscovery(catalog))

    assert code == 0
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "fallback-run"
    # The chunk was extracted by the model that answered.
    calls = read_jsonl(out / "calls.jsonl")
    assert len(calls) == 1
    assert calls[0]["status"] == "ok"
    assert calls[0]["model"] == "beta-flash"
    # Both provider calls are recorded, one per attempt.
    rows = read_jsonl(out / "model_executions.jsonl")
    assert len(rows) == 2
    assert rows[0]["status"] == "error"
    assert "exploded at call time" in rows[0]["error"]
    assert rows[0]["model_id"] == "alpha-flash"
    assert rows[0]["fallback_index"] == 0
    assert rows[1]["status"] == "ok"
    assert rows[1]["error"] is None
    assert rows[1]["model_id"] == "beta-flash"
    # The call that only succeeded on the second candidate is the fallback it
    # was.
    assert rows[1]["fallback_index"] == 1
    assert rows[1]["is_fallback"] is True
    assert rows[0]["execution_id"] != rows[1]["execution_id"]
    # And the run names the model the router chose, not the model that
    # answered: the run's policy does not depend on provider luck.
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider"] == "opencode-go"
    assert manifest["model"] == "alpha-flash"
    assert manifest["model_routing"]["model_id"] == "alpha-flash"
    assert manifest["model_routing"]["fallback_index"] == 0
    assert manifest["model_routing"]["is_fallback"] is False


def test_every_candidate_failing_records_a_rejection_and_the_attempts(
    tmp_path, capsys, monkeypatch
):
    """A chain that answers nowhere fails the chunk, and the run goes on."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash", "beta-flash")

    def factory(config):
        return ExplodingRoutedGenerator(config)

    monkeypatch.setattr("product_hunter.llm.build_generator", factory)
    monkeypatch.setattr("product_hunter.cli.build_generator", factory)

    code = run_routed_extract(tmp_path, "exhausted-run", FakeDiscovery(catalog))

    assert code == 1
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "exhausted-run"
    # The chunk failed into a rejection.
    rejects = read_jsonl(out / "rejects.jsonl")
    assert len(rejects) == 1
    assert rejects[0]["reason_code"] == "provider_error"
    # The failed calls are recorded anyway.
    rows = read_jsonl(out / "model_executions.jsonl")
    assert len(rows) == 2
    assert [row["status"] for row in rows] == ["error", "error"]
    assert "exploded at call time" in rows[0]["error"]
    assert rows[0]["model_id"] == "alpha-flash"
    assert rows[1]["model_id"] == "beta-flash"
    # The run left its records behind.
    assert (out / "manifest.json").is_file()
    calls = read_jsonl(out / "calls.jsonl")
    assert len(calls) == 1
    assert calls[0]["status"] == "provider_error"
    # The call and the rejection name the candidate that failed, which is the
    # second one: the chain fell through and the last candidate is the one
    # that failed.
    assert calls[0]["model"] == "beta-flash"
    assert rejects[0]["model"] == "beta-flash"
    # And they agree with the execution row of that attempt.
    assert calls[0]["model"] == rows[1]["model_id"]
    assert calls[0]["provider"] == rows[1]["provider_id"]


def test_every_chunk_fails_with_the_reason_the_models_were_down(
    tmp_path, capsys, monkeypatch
):
    """A run whose models are down fails every chunk with the same reason."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash", "beta-flash")

    def factory(config):
        return ExplodingRoutedGenerator(config)

    monkeypatch.setattr("product_hunter.llm.build_generator", factory)
    monkeypatch.setattr("product_hunter.cli.build_generator", factory)

    code = run_routed_extract(
        tmp_path,
        "down-run",
        FakeDiscovery(catalog),
        config=two_chunk_config(),
    )

    assert code == 1
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "down-run"
    calls = read_jsonl(out / "calls.jsonl")
    # The capture was cut in two, so the run made two calls.
    assert len(calls) == 2
    for call in calls:
        # Both chunks failed with the reason the models were down, and not
        # with "no available model", which is what the exhausted chain says.
        assert call["status"] == "provider_error"
        assert "exploded at call time" in call["error"]
        assert "no available model" not in call["error"]
        assert call["model"] == "beta-flash"
    rejects = read_jsonl(out / "rejects.jsonl")
    assert len(rejects) == 2
    assert {reject["reason_code"] for reject in rejects} == {"provider_error"}


def test_a_candidate_refused_at_the_check_is_recorded(
    tmp_path, capsys, monkeypatch
):
    """The candidate the provider refuses is in the execution file, and why."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash", "beta-flash")

    class RefusesThePrimary(RoutedGenerator):
        """A generator whose check refuses the primary candidate."""

        def check(self) -> dict[str, object]:
            if self.model_id == "alpha-flash":
                raise ProviderUnavailable("alpha-flash is not in the registry")
            return {"ok": True}

    def factory(config):
        return RefusesThePrimary(config)

    monkeypatch.setattr("product_hunter.llm.build_generator", factory)
    monkeypatch.setattr("product_hunter.cli.build_generator", factory)

    code = run_routed_extract(tmp_path, "refused-run", FakeDiscovery(catalog))

    assert code == 0
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "refused-run"
    rows = read_jsonl(out / "model_executions.jsonl")
    # The refused candidate is the first row, and it says why it was skipped.
    assert len(rows) == 2
    assert rows[0]["model_id"] == "alpha-flash"
    assert rows[0]["fallback_index"] == 0
    assert rows[0]["status"] == "error"
    assert "not in the registry" in rows[0]["error"]
    # And the candidate that answered is the second row.
    assert rows[1]["model_id"] == "beta-flash"
    assert rows[1]["fallback_index"] == 1
    assert rows[1]["is_fallback"] is True
    assert rows[1]["status"] == "ok"
    # The run names the model the router chose, not the one that answered.
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["model"] == "alpha-flash"


def test_every_answered_call_is_the_row_of_the_attempt_that_answered_it(
    tmp_path, capsys, monkeypatch
):
    """Two chunks, two answered calls, and each one has its own row."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash", "beta-flash")
    answers = ["not json at all", json.dumps({"candidates": []})]

    class Scripted(RoutedGenerator):
        """A generator that answers with the next scripted answer."""

        def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.prompts.append(request.prompt)
            return GenerationResponse(
                text=answers.pop(0),
                provider=self.provider_id,
                model=self.model_id,
                extractor_version="phase1-0.3",
                input_chars=len(request.prompt),
                latency_ms=7,
            )

    def factory(config):
        if config.model_id == "alpha-flash":
            return ExplodingRoutedGenerator(config)
        return Scripted(config)

    monkeypatch.setattr("product_hunter.llm.build_generator", factory)
    monkeypatch.setattr("product_hunter.cli.build_generator", factory)

    code = run_routed_extract(
        tmp_path,
        "two-chunk-run",
        FakeDiscovery(catalog),
        config=two_chunk_config(),
    )

    assert code == 0
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "two-chunk-run"
    calls = read_jsonl(out / "calls.jsonl")
    assert len(calls) == 2
    # The first chunk's answer was not usable, the second one was.
    assert [call["status"] for call in calls] == ["invalid_json", "ok"]
    rows = read_jsonl(out / "model_executions.jsonl")
    # One row per provider call: the failed one, and one per answered chunk.
    assert len(rows) == 3
    assert rows[0]["model_id"] == "alpha-flash"
    assert rows[0]["status"] == "error"
    assert "exploded at call time" in rows[0]["error"]
    # The row of the first answered attempt carries the outcome of its own
    # call, and not the outcome of the call that came after it.
    assert rows[1]["model_id"] == "beta-flash"
    assert rows[1]["status"] == "error"
    assert rows[1]["error"].startswith("invalid_json: ")
    # And the row of the second answered attempt is the one that is ok.
    assert rows[2]["model_id"] == "beta-flash"
    assert rows[2]["status"] == "ok"
    assert rows[2]["error"] is None


def test_a_corrupt_provenance_file_refuses_the_import(tmp_path, capsys):
    """A run whose provenance cannot be read is not imported, and says why."""
    seed_repo(tmp_path)
    run_extract(tmp_path)
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "test-run"
    (out / "model_executions.jsonl").write_text(
        '{"execution_id": "exec-1"}\n', encoding="utf-8"
    )

    db_path = tmp_path / "product_hunter.db"
    store = Store.open(db_path)
    store.close()

    code = db_import_run_command("test-run", db_path=db_path, repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    # The message names the file and says what to do about it.
    assert "model_executions.jsonl" in payload["error"]
    assert "not imported" in payload["error"]
    conn = connect(db_path)
    try:
        run = conn.execute(
            "SELECT count(*) FROM research_runs WHERE run_id = ?", ("test-run",)
        ).fetchone()
        assert run is not None
        assert run[0] == 0
    finally:
        conn.close()


def test_a_failed_provenance_write_rolls_the_run_import_back(
    tmp_path, capsys, monkeypatch
):
    """The run and its provenance are one transaction, or neither lands."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash")
    patch_generators(monkeypatch)
    code = run_routed_extract(tmp_path, "rollback-run", FakeDiscovery(catalog))
    assert code == 0
    capsys.readouterr()

    db_path = tmp_path / "product_hunter.db"
    store = Store.open(db_path)
    store.close()

    def boom(conn, executions):
        raise sqlite3.OperationalError("the provenance write failed")

    monkeypatch.setattr("product_hunter.db.store.record_model_executions", boom)

    code = db_import_run_command("rollback-run", db_path=db_path, repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False

    conn = connect(db_path)
    try:
        # The run did not land, so it did not land without its provenance.
        run = conn.execute(
            "SELECT count(*) FROM research_runs WHERE run_id = ?", ("rollback-run",)
        ).fetchone()
        assert run is not None
        assert run[0] == 0
        assert list_executions(conn, run_id="rollback-run") == []
        assert list_inventory(conn) == []
        bindings = conn.execute(
            "SELECT count(*) FROM model_role_bindings"
        ).fetchone()
        assert bindings is not None
        assert bindings[0] == 0
    finally:
        conn.close()


def test_a_run_without_provenance_still_imports(tmp_path, capsys):
    """A run that recorded no routing imports, and the store keeps no routing."""
    seed_repo(tmp_path)
    run_extract(tmp_path)
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "test-run"
    # Such a run has no routing state at all.
    assert not (out / "model_executions.jsonl").exists()
    assert not (out / "model_catalog.json").exists()

    db_path = tmp_path / "product_hunter.db"
    store = Store.open(db_path)
    store.close()

    code = db_import_run_command("test-run", db_path=db_path, repo_root=tmp_path)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    conn = connect(db_path)
    try:
        run = conn.execute(
            "SELECT count(*) FROM research_runs WHERE run_id = ?", ("test-run",)
        ).fetchone()
        assert run is not None
        assert run[0] == 1
        assert list_executions(conn, run_id="test-run") == []
        assert list_inventory(conn) == []
        bindings = conn.execute(
            "SELECT count(*) FROM model_role_bindings"
        ).fetchone()
        assert bindings is not None
        assert bindings[0] == 0
    finally:
        conn.close()


def test_importing_a_run_writes_the_model_provenance(tmp_path, capsys, monkeypatch):
    """The import stores the model calls and the model the role resolved to."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash")
    patch_generators(monkeypatch)
    code = run_routed_extract(tmp_path, "import-run", FakeDiscovery(catalog))
    assert code == 0
    capsys.readouterr()

    db_path = tmp_path / "product_hunter.db"
    store = Store.open(db_path)
    store.close()

    code = db_import_run_command("import-run", db_path=db_path, repo_root=tmp_path)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True

    conn = connect(db_path)
    try:
        rows = list_executions(conn, run_id="import-run")
        assert len(rows) == 1
        assert rows[0].model_id == "alpha-flash"
        assert rows[0].provider_id == "opencode-go"
        assert rows[0].role == "extraction"
        assert rows[0].stage == "PAIN_EXTRACTION"
        assert rows[0].status == "ok"
        assert rows[0].selection_source == "pattern"
        binding = conn.execute(
            "SELECT * FROM model_role_bindings WHERE role = ?", ("extraction",)
        ).fetchone()
        assert binding is not None
        assert binding["model_id"] == "alpha-flash"
        assert binding["selection_source"] == "pattern"
    finally:
        conn.close()


def test_importing_the_same_run_twice_succeeds(tmp_path, capsys, monkeypatch):
    """A run whose binding write failed is recoverable by importing it again."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash")
    patch_generators(monkeypatch)
    code = run_routed_extract(tmp_path, "twice-run", FakeDiscovery(catalog))
    assert code == 0
    capsys.readouterr()

    db_path = tmp_path / "product_hunter.db"
    store = Store.open(db_path)
    store.close()

    code = db_import_run_command("twice-run", db_path=db_path, repo_root=tmp_path)
    assert code == 0
    capsys.readouterr()

    code = db_import_run_command("twice-run", db_path=db_path, repo_root=tmp_path)

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    conn = connect(db_path)
    try:
        # The second import did not duplicate the execution rows.
        rows = list_executions(conn, run_id="twice-run")
        assert len(rows) == 1
        assert rows[0].model_id == "alpha-flash"
    finally:
        conn.close()


def test_importing_a_run_without_model_executions_still_succeeds(
    tmp_path, capsys
):
    """A run written before the router recorded no model provenance."""
    seed_repo(tmp_path)
    run_extract(tmp_path)
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "test-run"
    # Such a run has no model executions file at all.
    assert not (out / "model_executions.jsonl").exists()

    db_path = tmp_path / "product_hunter.db"
    store = Store.open(db_path)
    store.close()

    code = db_import_run_command("test-run", db_path=db_path, repo_root=tmp_path)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    conn = connect(db_path)
    try:
        # The run imported, and it recorded no model provenance.
        assert list_executions(conn, run_id="test-run") == []
    finally:
        conn.close()


def test_the_run_records_the_catalogue_and_the_import_fills_the_inventory(
    tmp_path, capsys, monkeypatch
):
    """The run keeps the catalogue it decided against, and the import keeps it."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash")
    patch_generators(monkeypatch)
    code = run_routed_extract(tmp_path, "catalog-run", FakeDiscovery(catalog))
    assert code == 0
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "catalog-run"

    recorded = out / "model_catalog.json"
    assert recorded.is_file()
    payload = json.loads(recorded.read_text(encoding="utf-8"))
    assert payload["catalog_version"] == catalog.catalog_version
    assert payload["source"] == "test"
    assert [entry["identity"]["model_id"] for entry in payload["entries"]] == [
        "alpha-flash"
    ]

    db_path = tmp_path / "product_hunter.db"
    store = Store.open(db_path)
    store.close()

    code = db_import_run_command("catalog-run", db_path=db_path, repo_root=tmp_path)

    assert code == 0
    capsys.readouterr()
    conn = connect(db_path)
    try:
        rows = list_inventory(conn)
        assert len(rows) == 1
        row = rows[0]
        assert row.provider_id == "opencode-go"
        assert row.model_id == "alpha-flash"
        assert row.availability == "available"
        assert row.retired_at is None
        # The stamps are the catalogue's own discovery, not the import time.
        assert row.first_seen_at == catalog.discovered_at.isoformat()
        assert row.last_seen_at == catalog.discovered_at.isoformat()
        catalogs = conn.execute(
            "SELECT * FROM model_catalogs WHERE catalog_version = ?",
            (catalog.catalog_version,),
        ).fetchone()
        assert catalogs is not None
        assert catalogs["model_count"] == 1
        assert catalogs["discovered_at"] == catalog.discovered_at.isoformat()
        # The catalogue is hashed with the other artifacts of the run.
        artifact = conn.execute(
            "SELECT * FROM research_artifacts WHERE run_id = ? AND path_or_uri = ?",
            ("catalog-run", "model_catalog.json"),
        ).fetchone()
        assert artifact is not None
        assert artifact["content_hash"] == hashlib.sha256(
            recorded.read_bytes()
        ).hexdigest()
    finally:
        conn.close()


def test_a_broken_router_records_no_catalogue(tmp_path, capsys, monkeypatch):
    """A run whose router failed has no catalogue, and none is invented."""
    seed_repo(tmp_path)
    patch_generators(monkeypatch)

    code = run_routed_extract(tmp_path, "no-catalog-run", BrokenDiscovery())

    assert code == 0
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "no-catalog-run"
    assert not (out / "model_catalog.json").exists()


def test_importing_a_run_without_a_catalogue_leaves_the_inventory_alone(
    tmp_path, capsys
):
    """A run written before the router recorded a catalogue still imports."""
    seed_repo(tmp_path)
    run_extract(tmp_path)
    capsys.readouterr()
    out = tmp_path / "data" / "processed" / "test-run"
    # Such a run has no catalogue file at all.
    assert not (out / "model_catalog.json").exists()

    db_path = tmp_path / "product_hunter.db"
    store = Store.open(db_path)
    store.close()

    code = db_import_run_command("test-run", db_path=db_path, repo_root=tmp_path)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    conn = connect(db_path)
    try:
        # The run imported, and it left the inventory alone.
        assert list_inventory(conn) == []
        catalogs = conn.execute("SELECT count(*) FROM model_catalogs").fetchone()
        assert catalogs is not None
        assert catalogs[0] == 0
    finally:
        conn.close()


def test_importing_the_same_run_twice_does_not_duplicate_the_inventory(
    tmp_path, capsys, monkeypatch
):
    """The inventory of a run imported twice holds one row per model."""
    seed_repo(tmp_path)
    catalog = fake_catalog("alpha-flash")
    patch_generators(monkeypatch)
    code = run_routed_extract(tmp_path, "catalog-twice-run", FakeDiscovery(catalog))
    assert code == 0
    capsys.readouterr()

    db_path = tmp_path / "product_hunter.db"
    store = Store.open(db_path)
    store.close()

    code = db_import_run_command(
        "catalog-twice-run", db_path=db_path, repo_root=tmp_path
    )
    assert code == 0
    capsys.readouterr()
    code = db_import_run_command(
        "catalog-twice-run", db_path=db_path, repo_root=tmp_path
    )

    assert code == 0
    capsys.readouterr()
    conn = connect(db_path)
    try:
        rows = list_inventory(conn)
        assert len(rows) == 1
        assert rows[0].model_id == "alpha-flash"
        assert rows[0].availability == "available"
        assert rows[0].retired_at is None
        assert rows[0].first_seen_at == catalog.discovered_at.isoformat()
        catalogs = conn.execute("SELECT count(*) FROM model_catalogs").fetchone()
        assert catalogs is not None
        assert catalogs[0] == 1
    finally:
        conn.close()


def test_extract_command_writes_the_run(tmp_path, capsys):
    digest = seed_repo(tmp_path)
    config = load_config()

    code = run_extract(tmp_path)

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["run_id"] == "test-run"
    assert summary["documents"] == 1
    assert summary["chunks"] == 1
    assert summary["calls"] == 1
    assert summary["candidates"] == 2
    assert summary["accepted"] == 1
    assert summary["rejected"] == 0
    assert summary["output_dir"] == str(tmp_path / "data" / "processed" / "test-run")

    out = tmp_path / "data" / "processed" / "test-run"
    for name in (
        "documents.jsonl",
        "candidates.jsonl",
        "pain_points.jsonl",
        "calls.jsonl",
        "rejects.jsonl",
        "manifest.json",
    ):
        assert (out / name).is_file()

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "test-run"
    assert manifest["documents"] == 1
    assert manifest["chunks"] == 1
    assert manifest["calls"] == 1
    assert manifest["candidates"] == 2
    assert manifest["accepted"] == 1
    assert manifest["rejected"] == 0
    assert manifest["failures"] == 0
    assert manifest["source_ids"] == ["hn_algolia"]
    assert manifest["provider"] == config.provider_id
    assert manifest["model"] == config.model_id
    assert manifest["extractor_version"] == config.extractor_version
    assert manifest["prompt_version"] == config.prompt_version
    assert manifest["normalization_version"] == config.normalization_version
    assert manifest["limits"]["max_candidates_per_chunk"] == config.limits.max_candidates_per_chunk
    assert manifest["started_at"] <= manifest["completed_at"]

    candidates = read_jsonl(out / "candidates.jsonl")
    assert len(candidates) == 2
    accepted = [candidate for candidate in candidates if candidate["accepted"]]
    assert len(accepted) == 1
    genuine = accepted[0]
    assert genuine["quote"] == GENUINE_QUOTE
    assert genuine["provider"] == "fake-provider"
    assert genuine["model"] == "fake-model"
    assert genuine["extractor_version"] == config.extractor_version
    assert genuine["prompt_version"] == config.prompt_version
    assert genuine["raw_sha256"] == digest
    assert genuine["raw_path"].startswith("data/raw/hn_algolia/")
    assert genuine["source_id"] == "hn_algolia"
    assert genuine["pain_id"].startswith("pp_")
    assert genuine["char_end"] > genuine["char_start"]

    rejected = [candidate for candidate in candidates if not candidate["accepted"]]
    assert len(rejected) == 1
    assert rejected[0]["pain_validity"] == "not_pain"
    assert rejected[0]["pain_id"] is None

    assert len(read_jsonl(out / "pain_points.jsonl")) == 1
    assert read_jsonl(out / "rejects.jsonl") == []
    calls = read_jsonl(out / "calls.jsonl")
    assert len(calls) == 1
    assert calls[0]["status"] == "ok"
    assert calls[0]["accepted_count"] == 1


def test_report_command_reports_the_run(tmp_path, capsys):
    seed_repo(tmp_path)
    run_extract(tmp_path)
    capsys.readouterr()

    out = tmp_path / "report.md"
    code = report_command("test-run", out, repo_root=tmp_path)

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["run_id"] == "test-run"
    assert report["normalization_version"] == load_config().normalization_version
    assert report["documents"] == 1
    assert report["chunks"] == 1
    assert report["calls"] == 1
    assert report["candidates"] == 2
    assert report["accepted"] == 1
    assert report["rejected"] == 0
    assert report["failures"] == 0
    assert report["quote_exact"] + report["quote_canonical"] == 2
    assert report["normalization_keys"] == 1
    assert report["duplicate_text_hashes"] == 0
    assert report["source_counts"] == {
        "hn_algolia": {"documents": 1, "candidates": 2, "accepted": 1}
    }
    assert out.is_file()
    written = out.read_text(encoding="utf-8")
    assert "# Run report" in written
    assert "## Limitations" in written


def test_report_command_without_a_run_exits_one(tmp_path, capsys):
    code = report_command("no-such-run", repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_report_command_refuses_an_unsafe_run_id(tmp_path, capsys):
    code = report_command("../escape", repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "invalid run id" in payload["error"]


def test_review_sheet_command_writes_a_blank_sheet(tmp_path, capsys):
    seed_repo(tmp_path)
    run_extract(tmp_path)
    capsys.readouterr()

    out = tmp_path / "review.csv"
    code = review_sheet_command("test-run", out, repo_root=tmp_path)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["run_id"] == "test-run"
    assert payload["rows"] == 2
    assert payload["path"] == str(out)

    with out.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert [row["model_validity"] for row in rows] == ["genuine", "not_pain"]
    for row in rows:
        assert row["human_valid_pain"] == ""
        assert row["human_severity"] == ""
        assert row["human_frequency"] == ""
        assert row["notes"] == ""
        assert row["verbatim_ok"] == "true"
        assert row["quote"]


def test_review_sheet_command_without_a_run_exits_one(tmp_path, capsys):
    code = review_sheet_command("no-such-run", tmp_path / "review.csv", repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_review_sheet_command_refuses_malformed_candidates(tmp_path, capsys):
    run_dir = tmp_path / "data" / "processed" / "broken-run"
    run_dir.mkdir(parents=True)
    (run_dir / "candidates.jsonl").write_text('{"candidate_id": "cand_1"}\n', encoding="utf-8")

    code = review_sheet_command("broken-run", tmp_path / "review.csv", repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_eval_command_scores_a_filled_sheet(tmp_path, capsys):
    seed_repo(tmp_path)
    run_extract(tmp_path)
    sheet = tmp_path / "sheet.csv"
    review_sheet_command("test-run", sheet, repo_root=tmp_path)
    capsys.readouterr()

    with sheet.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row, verdict, severity in zip(rows, ("yes", "no"), ("3", "")):
        row["human_valid_pain"] = verdict
        row["human_severity"] = severity
        row["human_frequency"] = "weekly" if verdict == "yes" else ""
    labels = tmp_path / "labels.csv"
    with labels.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)

    out = tmp_path / "report.md"
    code = eval_command(labels, out, repo_root=tmp_path)

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == 2
    assert report["labeled"] == 2
    assert report["coverage"] == 1.0
    assert report["quote_exact"] == 2
    assert report["quote_failed"] == 0
    assert report["model_genuine"] == 1
    assert report["true_positive"] == 1
    assert report["false_positive"] == 0
    assert report["false_negative"] == 0
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0
    assert report["severity_n"] == 1
    assert report["severity_exact"] == 1
    assert out.is_file()
    assert "# Extraction evaluation" in out.read_text(encoding="utf-8")


def test_eval_command_refuses_malformed_labels(tmp_path, capsys):
    labels = tmp_path / "labels.csv"
    labels.write_text("candidate_id,doc_id\ncand_1,doc_1\n", encoding="utf-8")

    code = eval_command(labels, tmp_path / "report.md", repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_extract_command_without_fetches_exits_one(tmp_path, capsys):
    code = extract_command(
        limit=1,
        repo_root=tmp_path,
        config=load_config(),
        generator=FakeGenerator(),
        run_id="empty-run",
    )

    assert code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["run_id"] == "empty-run"
    assert summary["documents"] == 0
    assert summary["calls"] == 0
    assert "no fetches" in summary["error"]


def test_extract_command_rejects_a_hash_mismatch(tmp_path, capsys):
    digest = seed_repo(tmp_path)
    (tmp_path / "data" / "raw" / "hn_algolia" / f"{digest}.json").write_bytes(b"{}")

    code = run_extract(tmp_path, run_id="bad-run")

    assert code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["documents"] == 0
    assert summary["calls"] == 0
    rejects = read_jsonl(tmp_path / "data" / "processed" / "bad-run" / "rejects.jsonl")
    assert len(rejects) == 1
    assert rejects[0]["reason_code"] == "raw_hash_mismatch"
    assert rejects[0]["doc_id"] is None
    assert rejects[0]["fetch_id"] == "fetch_test"
    assert rejects[0]["source_id"] == "hn_algolia"
    assert rejects[0]["extractor_version"] == "phase1-0.3"
    assert rejects[0]["prompt_version"] == "extract-v3"


def test_extract_command_refuses_an_unsafe_run_id(tmp_path, capsys):
    code = extract_command(
        limit=1,
        repo_root=tmp_path,
        config=load_config(),
        generator=FakeGenerator(),
        run_id="../escape",
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "invalid run id" in payload["error"]
    assert not (tmp_path / "data" / "escape").exists()
    assert not (tmp_path / "escape").exists()


def test_extract_command_fails_closed_when_the_provider_check_fails(tmp_path, capsys):
    """The provider is verified first, so a dead provider costs no model call."""
    seed_repo(tmp_path)
    generator = UnavailableGenerator()

    code = extract_command(
        limit=1,
        repo_root=tmp_path,
        config=load_config(),
        generator=generator,
        run_id="unavailable-run",
    )

    assert code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["run_id"] == "unavailable-run"
    assert summary["documents"] == 1
    assert summary["calls"] == 0
    assert summary["candidates"] == 0
    assert summary["rejected"] == 1
    assert "provider check failed" in summary["error"]
    assert generator.prompts == []

    out = tmp_path / "data" / "processed" / "unavailable-run"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["calls"] == 0
    assert manifest["candidates"] == 0
    assert manifest["rejected"] == 1
    assert read_jsonl(out / "candidates.jsonl") == []
    # The dead provider leaves a record of its own.
    rejects = read_jsonl(out / "rejects.jsonl")
    assert len(rejects) == 1
    assert rejects[0]["reason_code"] == "provider_check_failed"
    assert rejects[0]["doc_id"] is None
    assert rejects[0]["fetch_id"] is None
    assert rejects[0]["source_id"] is None
    assert "opencode CLI not found on PATH" in rejects[0]["detail"]
    assert rejects[0]["provider"] == load_config().provider_id
    assert rejects[0]["model"] == load_config().model_id
    assert rejects[0]["extractor_version"] == "phase1-0.3"
    assert rejects[0]["prompt_version"] == "extract-v3"
    assert rejects[0]["created_at"]


def test_extract_command_rejects_a_source_without_analysis_use(tmp_path, capsys):
    """blog_http is disabled and allows no use, so its capture is never read."""
    seed_blog_repo(tmp_path)
    generator = FakeGenerator()

    code = extract_command(
        limit=1,
        repo_root=tmp_path,
        config=load_config(),
        generator=generator,
        run_id="blocked-run",
    )

    assert code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["documents"] == 0
    assert summary["chunks"] == 0
    assert summary["calls"] == 0
    assert summary["candidates"] == 0
    assert summary["rejected"] == 1
    assert generator.prompts == []
    out = tmp_path / "data" / "processed" / "blocked-run"
    assert read_jsonl(out / "documents.jsonl") == []
    rejects = read_jsonl(out / "rejects.jsonl")
    assert len(rejects) == 1
    assert rejects[0]["reason_code"] == "source_disabled"
    assert rejects[0]["doc_id"] is None
    assert rejects[0]["fetch_id"] == "fetch_blog"
    assert rejects[0]["source_id"] == "blog_http"
    assert rejects[0]["extractor_version"] == "phase1-0.3"
    assert rejects[0]["prompt_version"] == "extract-v3"
