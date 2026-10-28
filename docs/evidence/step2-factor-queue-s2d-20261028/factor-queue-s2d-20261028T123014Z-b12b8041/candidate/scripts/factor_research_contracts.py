"""Explicit read-only factor archive; public previews never grant local admission."""
from __future__ import annotations

import gzip
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.factor_evaluation import LIMITATIONS, validate_report
from qtrade.research.factor_registry import FactorRegistry
from qtrade.research.factor_strategy_catalog import (
    KIND,
    SCHEMA,
    catalog_identity,
    preview_from_catalog,
    require,
    validate_catalog,
    validate_registration_request,
)
from qtrade.research.factor_strategy_contract import (
    FAMILY,
    _disabled,
    _evaluation_ref,
    _sources,
    freeze_strategy,
    strategy_spec,
    validate_strategy,
    verify_bound_strategy,
)
from qtrade.research.strategy_contract import ready_dataset
from qtrade.research.workflow import digest
from qtrade.strategies.registry import StrategyRegistry

INDEX_SCHEMA = "qtrade.factor-research-archive-index.v1"
MAX_GZIP_BYTES = 8_000_000
MAX_REPORT_BYTES = 256_000_000


def bounded_bytes(path: Path, maximum: int) -> bytes:
    """Read actual bytes with a hard bound; reject links and path aliases."""
    require(not any(part.is_symlink() for part in (path, *path.parents))
            and path.is_file(), "ARCHIVE_REGULAR_FILE_REQUIRED")
    with path.open("rb") as handle:
        value = handle.read(maximum + 1)
    require(len(value) <= maximum, "ARCHIVE_BYTE_BOUND")
    return value


def decode_json(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")))
    except (UnicodeError, RecursionError, OverflowError) as error:
        raise ValueError("FACTOR_ARCHIVE_JSON_INVALID") from error


class EvaluationArchive:
    """Bounded immutable gzip reader; it creates no evaluation database."""

    def __init__(self, root: Path, entries: list[dict[str, str]]):
        self.root, self.entries = root, entries

    def path(self, identity: str) -> Path:
        require(type(identity) is str and re.fullmatch(r"factor-eval-[a-f0-9]{32}", identity)
                is not None, "EVALUATION_ID")
        return self.root / "reports" / (identity + ".json.gz")

    def get(self, evaluation_id: str) -> dict[str, Any]:
        entries = [entry for entry in self.entries if entry["evaluation_id"] == evaluation_id]
        require(len(entries) == 1, "EVALUATION_NOT_APPROVED")
        entry = entries[0]
        path = self.path(evaluation_id)
        compressed = bounded_bytes(path, MAX_GZIP_BYTES)
        require(sha256(compressed).hexdigest() == entry["gzip_sha256"], "REPORT_PHYSICAL_HASH")
        from io import BytesIO

        try:
            with gzip.GzipFile(fileobj=BytesIO(compressed)) as handle:
                raw = handle.read(MAX_REPORT_BYTES + 1)
        except (EOFError, OSError) as error:
            raise ValueError("FACTOR_ARCHIVE_GZIP_INVALID") from error
        require(len(raw) <= MAX_REPORT_BYTES, "REPORT_EXPANSION_BOUND")
        report = validate_report(decode_json(raw))
        require(_evaluation_ref(report) == {key: entry[key] for key in (
            "evaluation_id", "specification_sha256", "report_manifest_sha256")}
            and report["documents"]["specification"]["data_binding"]["data_version_id"]
            == entry["data_version_id"], "REPORT_REFERENCE")
        require(bounded_bytes(path, MAX_GZIP_BYTES) == compressed, "REPORT_CHANGED_DURING_READ")
        return report


class FactorResearchContracts:
    """Opt-in local resolver. No default archive, discovery or lifecycle promotion."""

    def __init__(self, base: Path, database: Path, revision: str,
                 registry: StrategyRegistry, archive_root: Path):
        _disabled()
        require(re.fullmatch(r"[a-f0-9]{40}", revision) is not None, "FULL_REVISION_REQUIRED")
        require(archive_root.is_dir() and not any(part.is_symlink()
            for part in (archive_root, *archive_root.parents)), "EXPLICIT_ARCHIVE_REQUIRED")
        self.base, self.database, self.revision = base, database, revision
        self.registry, self.archive_root = registry, archive_root.resolve()
        self._cache: tuple[str, dict[str, Any]] | None = None

    def root(self, identity: str) -> Path:
        require(type(identity) is str and re.fullmatch(r"[a-f0-9]{64}", identity) is not None,
                "DATA_IDENTITY")
        return self.base / "unified-data/qlib-versions/versions" / identity

    def inputs(self) -> tuple[dict[str, Any], str]:
        """Cache keys use actual hashes/admission rows, never mtimes or sizes alone."""
        _disabled()
        index = decode_json(bounded_bytes(self.archive_root / "index.json", 100_000))
        require(type(index) is dict and set(index) == {*AUTHORITY, "schema_version",
            "registry_sha256", "evaluations", "templates", "evidence_sha256"}
            and index["schema_version"] == INDEX_SCHEMA and all(type(index.get(key))
            is type(value) and index[key] == value for key, value in AUTHORITY.items())
            and signed({k: v for k, v in index.items() if k != "evidence_sha256"}) == index,
            "ARCHIVE_INDEX")
        entries, templates = index["evaluations"], index["templates"]
        require(type(entries) is list and 1 <= len(entries) <= 4 and type(templates) is list
            and 1 <= len(templates) <= 16, "ARCHIVE_SCOPE_BOUND")
        seen = set()
        for entry in entries:
            require(type(entry) is dict and set(entry) == {"evaluation_id",
                "specification_sha256", "report_manifest_sha256", "data_version_id", "gzip_sha256"}
                and type(entry["evaluation_id"]) is str and re.fullmatch(
                r"factor-eval-[a-f0-9]{32}", entry["evaluation_id"]) is not None,
                "ARCHIVE_EVALUATION")
            require(all(type(entry[key]) is str and re.fullmatch(r"[a-f0-9]{64}", entry[key])
                is not None for key in entry if key != "evaluation_id"), "ARCHIVE_REFERENCE")
            require(entry["evaluation_id"] not in seen, "ARCHIVE_DUPLICATE_EVALUATION")
            seen.add(entry["evaluation_id"])
        registry = self.archive_root / "factors.sqlite"
        for suffix in ("-wal", "-journal"):
            sidecar = registry.with_name(registry.name + suffix)
            require(not sidecar.exists() or bounded_bytes(sidecar, 0) == b"",
                    "IMMUTABLE_REGISTRY_REQUIRED")
        require(sha256(bounded_bytes(registry, 2_000_000)).hexdigest() == index["registry_sha256"],
                "REGISTRY_PHYSICAL_HASH")
        reader = EvaluationArchive(self.archive_root, entries)
        admissions = []
        for entry in entries:
            require(sha256(bounded_bytes(reader.path(entry["evaluation_id"]), MAX_GZIP_BYTES)
                ).hexdigest() == entry["gzip_sha256"], "REPORT_PHYSICAL_HASH")
            root = self.root(entry["data_version_id"])
            with closing(sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro",
                                          uri=True, timeout=3)) as connection:
                row = connection.execute("SELECT version_id,manifest_sha256,actual_end,admitted_at "
                    "FROM data_versions WHERE version_id=?", (entry["data_version_id"],)).fetchone()
                events = connection.execute("SELECT event_id,payload FROM data_events "
                    "WHERE version_id=? AND kind='QLIB_DATA_READY' ORDER BY sequence LIMIT 2",
                    (entry["data_version_id"],)).fetchall()
            admissions.append([row, events, sha256(bounded_bytes(root / "version.json",
                                                               8_000_000)).hexdigest()])
        return index, digest([index, _sources(), admissions])

    def publication(self, *, now: datetime | None = None) -> dict[str, Any]:
        index, key = self.inputs()
        if self._cache is None or self._cache[0] != key:
            factors = FactorRegistry(self.archive_root / "factors.sqlite", read_only=True)
            reader = EvaluationArchive(self.archive_root, index["evaluations"])
            templates, datasets = [], {}
            for spec in index["templates"]:
                require(type(spec) is dict and spec.get("parent") is None, "TEMPLATE_PARENT")
            approved = {entry["evaluation_id"] for entry in index["evaluations"]}
            require({spec["evaluation_ref"]["evaluation_id"] for spec in index["templates"]}
                    == approved, "TEMPLATE_EVALUATIONS")
            # Hold only one expanded report at a time, including on a four-report
            # archive. The heartbeat cache contains compact descriptors only.
            for entry in index["evaluations"]:
                report = reader.get(entry["evaluation_id"])
                for spec in index["templates"]:
                    if spec["evaluation_ref"]["evaluation_id"] != entry["evaluation_id"]:
                        continue
                    definition = freeze_strategy(spec, factors=factors, evaluation=report,
                                                   source_revision=self.revision)
                    binding = definition["evaluation_context"]["data_binding"]
                    identity = binding["data_version_id"]
                    if identity not in datasets:
                        require(binding == ready_dataset(self.root(identity), self.database),
                                "LOCAL_READY_BINDING")
                        calendar = report["documents"]["specification"]["calendar"]
                        datasets[identity] = {"data_binding": binding, "calendar": calendar,
                                              "calendar_sha256": digest(calendar)}
                    else:
                        require(datasets[identity]["data_binding"] == binding,
                                "AMBIGUOUS_DATA_BINDING")
                    templates.append(definition)
            require(self.inputs()[1] == key, "ARCHIVE_CHANGED_DURING_RESOLUTION")
            self._cache = (key, {"datasets": list(datasets.values()), "templates": templates})
        parents = []
        for record in self.registry.list(family=FAMILY)[-100:]:
            if record.status.value != "EXPERIMENT" or record.role.value != "NONE":
                continue
            definition = validate_strategy(self.registry.research_contract(record.spec.strategy_id))
            parents.append({"strategy_id": record.spec.strategy_id,
                "strategy_version": record.spec.version,
                "contract_sha256": definition["evidence_sha256"], "family": FAMILY,
                "status": "EXPERIMENT", "role": "NONE"})
        value = {**AUTHORITY, "schema_version": SCHEMA, "worker": {
            "heartbeat_at": (now or datetime.now(UTC)).isoformat(),
            "source_revision": self.revision, "implementation_sources": _sources(),
            "supported_kinds": [KIND]}, **self._cache[1], "parents": parents,
            "limitations": LIMITATIONS}
        value["catalog_sha256"] = catalog_identity(value)
        return validate_catalog(signed(value), now=now)

    def verify(self, request: dict[str, Any], *, register: bool = False) -> dict[str, Any]:
        request = validate_registration_request(request)
        require(request["source_revision"] == self.revision, "WORKER_SOURCE_CHANGED")
        catalog = self.publication()
        require(request["expected_catalog_sha256"] == catalog["catalog_sha256"], "CATALOG_CHANGED")
        definition = preview_from_catalog(catalog, request["factor_spec"])
        require(definition["strategy_version"] == request["expected_strategy_version"]
            and definition["evaluation_context"]["data_binding"]["data_version_id"]
            == request["data_version_id"], "EXPECTED_DEFINITION_CHANGED")
        index, key = self.inputs()
        factors = FactorRegistry(self.archive_root / "factors.sqlite", read_only=True)
        reader = EvaluationArchive(self.archive_root, index["evaluations"])
        root = self.root(request["data_version_id"])
        verify_bound_strategy(definition, factors=factors, evaluations=reader,
                               version_root=root, data_database=self.database)
        require(self.inputs()[1] == key, "ARCHIVE_CHANGED_BEFORE_REGISTRATION")
        if register:
            # The registry revalidates the strict factor contract and commits the
            # job/spec/parent association in its existing single transaction.
            self.registry.register_factor_research_experiment(
                strategy_spec(definition, now=datetime.now(UTC)), contract=definition,
                job_id=request["job_id"], actor=request["actor"])
        return definition

    def completion(self, request: dict[str, Any], output: Path) -> dict[str, Any]:
        """Reconcile the child's committed link, not a newly changed parent catalog."""
        request = validate_registration_request(request)
        receipt = decode_json(bounded_bytes(output / "registration.json", 300_000))
        definition = validate_strategy(decode_json(bounded_bytes(
            output / "strategy-definition.json", 256_000)), verify_runtime_source=True)
        expected = registration_receipt(request, definition, receipt["registered_at"])
        require(receipt == expected and definition["source"]["revision"] == self.revision
            and self.registry.research_contract(definition["strategy_id"]) == definition,
            "COMPLETION_BINDING")
        record = self.registry.get(definition["strategy_id"])
        require(record.status.value == "EXPERIMENT" and record.role.value == "NONE"
            and any(row["job_id"] == request["job_id"] and row["actor"] == request["actor"]
                and row["registered_at"] == receipt["registered_at"]
                for row in self.registry.research_jobs(definition["strategy_id"], limit=200)),
            "COMPLETION_JOB_ASSOCIATION")
        return cast(dict[str, Any], receipt)


def registration_receipt(request: dict[str, Any], definition: dict[str, Any],
                         registered_at: str) -> dict[str, Any]:
    request = validate_registration_request(request)
    definition = validate_strategy(definition)
    clock = datetime.fromisoformat(registered_at)
    require(clock.tzinfo is not None and clock.utcoffset() is not None
            and clock <= datetime.now(UTC), "RECEIPT_CLOCK")
    require(request["factor_spec"] == definition["spec"] and request["expected_strategy_version"]
        == definition["strategy_version"] and request["source_revision"] == definition["source"][
        "revision"] and request["data_version_id"] == definition["evaluation_context"][
        "data_binding"]["data_version_id"], "RECEIPT_REQUEST")
    return signed({**AUTHORITY, "schema_version": "qtrade.factor-strategy-registration.v1",
        "kind": KIND, "state": "COMPLETE", "registration_only": True,
        "job_id": request["job_id"], "actor": request["actor"], "registered_at": registered_at,
        "strategy_id": definition["strategy_id"],
        "strategy_version": definition["strategy_version"],
        "contract_sha256": definition["evidence_sha256"], "catalog_sha256": request[
        "expected_catalog_sha256"], "source_revision": request["source_revision"],
        "data_version_id": request["data_version_id"], "factor_spec_sha256": request[
        "factor_spec_sha256"], "factor_ref": definition["spec"]["factor_ref"],
        "evaluation_ref": definition["spec"]["evaluation_ref"], "status": "EXPERIMENT",
        "role": "NONE", "backtest_implemented": False, "paper_admission": False,
        "model_activation": False, "limitations": LIMITATIONS})


def rejection_receipt(request: dict[str, Any], rejected_at: str) -> dict[str, Any]:
    """Only pre-registration validation may emit this conclusive rejection."""
    request = validate_registration_request(request)
    clock = datetime.fromisoformat(rejected_at)
    require(clock.tzinfo is not None and clock.utcoffset() is not None
            and clock <= datetime.now(UTC), "REJECTION_CLOCK")
    return signed({**AUTHORITY, "schema_version": "qtrade.factor-strategy-rejection.v1",
        "kind": KIND, "state": "REJECTED", "registration_only": True,
        "job_id": request["job_id"], "request_sha256": digest(request),
        "rejected_at": rejected_at, "error_code": "IMMUTABLE_FACTOR_INPUT_REJECTED",
        "source_revision": request["source_revision"],
        "data_version_id": request["data_version_id"]})
