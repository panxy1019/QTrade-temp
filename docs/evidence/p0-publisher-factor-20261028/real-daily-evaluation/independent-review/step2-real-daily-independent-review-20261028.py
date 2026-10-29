"""Local-only evidence review. No cluster, NFS, network, source or production writes."""
from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

BASE = Path(r"E:\QTrade-temp\cluster-evidence\step2-real-daily-20261028T045123Z")
REPO = Path(r"E:\QTrade-git")
OUTPUT = Path(r"E:\QTrade-temp\step2-real-daily-independent-review-20261028")
checks = {}


def check(name, condition):
    checks[name] = bool(condition)
    if not condition:
        raise AssertionError(name)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load(name):
    return json.loads((BASE / name).read_bytes())


def signed_valid(value):
    body = {key: item for key, item in value.items() if key != "evidence_sha256"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return digest(raw) == value["evidence_sha256"]


receipt, request, manifest = load("receipt.json"), load("request.json"), load("source-manifest.json")
before, after = load("before.json"), load("after.json")
revision = subprocess.check_output(["git", "rev-parse", "8e038c8"], cwd=REPO).decode().strip()
check("receipt_signed_hash", signed_valid(receipt))
check("control_snapshot_signed_hashes", signed_valid(before) and signed_valid(after))
check("source_revision_exact", revision == receipt["source_revision"] == manifest["source_revision"])
check("source_manifest_count_266", len(manifest["runtime_files"]) == receipt["verified_runtime_files"] == 266)
paths = sorted(manifest["runtime_files"])
check("source_paths_bounded", all(not Path(name).is_absolute() and ".." not in Path(name).parts
                                  and "\n" not in name for name in paths))
batch_input = "".join(f"{revision}:{name}\n" for name in paths).encode()
batch = subprocess.run(["git", "cat-file", "--batch"], input=batch_input,
                       cwd=REPO, capture_output=True, check=True).stdout
offset, blobs = 0, {}
for name in paths:
    end = batch.index(b"\n", offset)
    header = batch[offset:end].decode().split()
    check("source_git_blob_type_" + name, len(header) == 3 and header[1] == "blob")
    size = int(header[2])
    raw = batch[end + 1:end + 1 + size]
    check("source_git_sha256_" + name, digest(raw) == manifest["runtime_files"][name])
    blobs[name] = raw
    offset = end + 1 + size + 1
check("source_git_batch_complete", offset == len(batch))
for name in ("src/qtrade/research/factor_evaluation.py", "src/qtrade/research/factor_registry.py"):
    check("local_validation_source_exact_" + name, (REPO / name).read_bytes() == blobs[name])

compressed = (BASE / "report.json.gz").read_bytes()
check("report_gzip_sha256", digest(compressed) == receipt["report_gzip_sha256"])
raw = gzip.decompress(compressed)
check("report_raw_sha256", digest(raw) == receipt["report_raw_sha256"])
check("report_raw_size", len(raw) == receipt["report_raw_bytes"])
report = json.loads(raw)
from qtrade.research.factor_evaluation import LIMITATIONS, validate_report

validated = validate_report(report)
check("validate_report_hashes_policy_and_numerical_replay", validated == report)
check("receipt_reference_exact", all(report[key] == value for key, value in receipt["reference"].items()))
documents = report["documents"]
spec = documents["specification"]
check("request_exact", request == spec["request"])
check("receipt_summary_exact", receipt["summary"] == documents["summary"])
check("report_and_receipt_nonproduction_state", report["state"] == "DIAGNOSTICS_COMPUTED"
      and receipt["state"] == "NONPRODUCTION_DIAGNOSTICS_ARCHIVED")
check("before_after_byte_identity", (BASE / "before.json").read_bytes() == (BASE / "after.json").read_bytes())
check("before_after_control_identity", before == after and receipt["before_after_controls_equal"] is True)
check("controls_cover_expected_owners_books_context", len(before["owners"]) == 10
      and {"books", "rolling", "forward_instance_id", "legacy_status"} == set(before["frozen_controls"])
      and len(before["frozen_controls"]["books"]["books"]) == 14
      and {"active", "approval_sha256", "data_version", "data_event_id", "model_states"} == set(before["context"]))
check("nonproduction_authority_preserved", all(value["live_trading_enabled"] is False
      and value["external_order_transmission"] is False and value["point_in_time"] is False
      and value["promotion"] == "NOT_ELIGIBLE" for value in (receipt, before, after, report, spec)))
check("no_production_or_delivery_claims", receipt["production_stores_written"] is False
      and receipt["whole_step1_accepted"] is False and receipt["public_api_or_browser_delivered"] is False)
check("limits_and_historical_claim_preserved", documents["summary"]["limitations"] == LIMITATIONS
      and documents["summary"]["historical_computation_claim"] is False)
check("exact_891_current_selected_universe", request["instruments"] == sorted(set(request["instruments"]))
      and len(request["instruments"]) == receipt["scope_symbols"] == 891)
check("exact_four_factor_refs", len(request["ordered_factor_refs"]) == receipt["factor_count"] == 4)
sessions = [item["session"] for item in request["decisions"]]
check("exact_five_decisions", sessions == receipt["decision_sessions"]
      == ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"])
check("retrospective_close_label_contract", request["knowledge_mode"] == "RETROSPECTIVE_RECONSTRUCTION"
      and spec["label"]["formula"] == "close[t+h]/close[t]-1"
      and request["label_horizon_sessions"] == 1 and spec["label"]["exit_offset"] == 1
      and spec["cost_policy"]["executable_return_claim"] is False)
check("dataset_version_exact", spec["data_binding"]["data_version_id"] == receipt["data_version"]
      == "09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407"
      and documents["version_manifest"]["version_id"] == receipt["data_version"]
      and spec["data_binding"]["actual_end"] == "2026-10-27")
check("label_endpoints_within_frozen_version", all(
    item["label_exit_session"] is not None and item["label_exit_session"] <= "2026-10-27"
    for item in documents["eligibility_ledger"]))
check("dependency_work_budget", len(request["instruments"]) * len(sessions) * sum(
    value["required_closes"] for value in documents["factor_definitions"])
    == receipt["dependency_cells"] == 307395 and receipt["dependency_cells"] <= 1_000_000)
check("all_saved_code_snapshots_match_git", all(
    item["source"].encode() == blobs["src/qtrade/" + name]
    and item["sha256"] == manifest["runtime_files"]["src/qtrade/" + name]
    for name, item in documents["source_snapshot"].items()))
check("factor_sources_match_git", all(
    definition["source"]["revision"] == revision
    and all(manifest["runtime_files"][name] == sha for name, sha in definition["source"]["files"].items())
    for definition in documents["factor_definitions"]))

daily_table, reasons, unknown_clocks = [], {}, {}
for index, definition in enumerate(documents["factor_definitions"]):
    key = str(index)
    rows = documents["daily_metrics"][key]
    counts = [{name: row[name] for name in ("session", "pairs", "feature_valid", "label_valid")}
              for row in rows]
    check("daily_pairs_receipt_exact_" + key, counts == receipt["daily_pair_counts"][key])
    check("daily_candidates_and_pair_bounds_" + key, all(row["candidates"] == 891
          and 0 <= row["pairs"] <= min(row["feature_valid"], row["label_valid"]) <= 891
          for row in rows))
    ledger = [item for item in documents["eligibility_ledger"] if item["factor_index"] == index]
    reason_counts = {"feature": dict(Counter(item["feature_reason"] for item in ledger
                                            if item["feature_reason"])),
                     "label": dict(Counter(item["label_reason"] for item in ledger
                                          if item["label_reason"]))}
    reasons[definition["alias"]] = reason_counts
    unknown_clocks[definition["alias"]] = sum(
        item["clock_reason"] == "DEPENDENCY_AVAILABILITY_UNKNOWN" for item in ledger)
    check("exclusion_ledger_complete_" + key, len(ledger) == 4455
          and sum(row["feature_valid"] for row in rows) + sum(reason_counts["feature"].values()) == 4455
          and sum(row["label_valid"] for row in rows) + sum(reason_counts["label"].values()) == 4455)
    for row in rows:
        day_samples = [item for item in documents["samples"]
                       if item["factor_index"] == index and item["session"] == row["session"]]
        feature = {item["instrument"] for item in day_samples if item["factor_value"] is not None}
        label = {item["instrument"] for item in day_samples if item["raw_label"] is not None}
        check("independent_pairs_" + key + "_" + row["session"], len(day_samples) == 891
              and len(feature) == row["feature_valid"] and len(label) == row["label_valid"]
              and len(feature & label) == row["pairs"])
    daily_table.append({"factor": definition["alias"], "pairs": [row["pairs"] for row in rows],
                        "valid_pairs_total": sum(row["pairs"] for row in rows)})

check("no_recorded_failure_or_stderr", (BASE / "failure.json").stat().st_size == 0
      and (BASE / "stderr.log").stat().st_size == 0)
result = {
    "state": "PASS_LOCAL_INDEPENDENT_REVIEW", "reviewed_at": datetime.now(UTC).isoformat(),
    "source_revision": revision, "evaluation_reference": receipt["reference"],
    "checks_passed": sum(checks.values()), "checks_failed": sum(not value for value in checks.values()),
    "git_runtime_files_verified": len(paths),
    "evidence_files_sha256": {path.name: digest(path.read_bytes()) for path in sorted(BASE.iterdir())
                              if path.is_file()},
    "report_raw_sha256": digest(raw), "report_raw_bytes": len(raw),
    "before_after_sha256": digest((BASE / "before.json").read_bytes()),
    "controls_scope": {"owners": 10,
                       "book_snapshots": len(before["frozen_controls"]["books"]["books"])},
    "daily_pairs": daily_table, "exclusion_reasons": reasons, "unknown_clock_rows": unknown_clocks,
    "scope": {"symbols": 891, "factors": 4, "decisions": sessions,
              "data_version": receipt["data_version"], "data_actual_end": "2026-10-27",
              "knowledge_mode": request["knowledge_mode"]},
    "runtime_receipt": {"elapsed_seconds": receipt["elapsed_seconds"],
                        "peak_rss_kib": receipt["peak_rss_kib"],
                        "peak_rss_mib": receipt["peak_rss_kib"] / 1024},
    "limitations": LIMITATIONS + ["LOCAL_CAPTURE_REPLAY_NOT_FRESH_REMOTE_STATE_VERIFICATION",
        "SOURCE_PRICES_AND_NFS_ARCHIVE_NOT_REOPENED_IN_THIS_REVIEW",
        "PROCESS_RSS_IS_CAPTURED_OPERATOR_MEASUREMENT_NOT_INDEPENDENT_REMEASUREMENT",
        "FIVE_POSTHOC_DAYS_NOT_ALPHA_VALIDITY_OR_PROSPECTIVE_PERFORMANCE_ACCEPTANCE"],
    "all_checks": checks,
}
OUTPUT.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
lines = [
    "# STEP2 真实日频隔离结果独立本地复核", "",
    f"结论：PASS。{len(checks)}项本地检查通过；未调用集群/NFS/网络，未修改源码或生产。", "",
    f"源码：`{revision}`；source-manifest全部266文件逐一与本地Git blob SHA256一致。",
    "receipt规范化内容摘要、gzip摘要、解压后61,775,392字节报告摘要均匹配；",
    "`validate_report`通过完整文档哈希、固定研究限制/政策与归档样本数值重算。",
    "报告源码快照及因子定义源码均与该Git版本一致，receipt/ref/request/summary相互吻合。", "",
    "范围：固定数据版本`09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407`，",
    "实际结束2026-10-27；891个显式证券，4个既有因子，2026-10-20/21/22/23/26五个会话。",
    "close-to-close h=1标签均在冻结版本内；307,395依赖单元，事后重建模式，毛标签诊断。", "",
    "|因子|10-20|10-21|10-22|10-23|10-26|合计|", "|---|---:|---:|---:|---:|---:|---:|",
]
for row in daily_table:
    lines.append("|" + row["factor"] + "|" + "|".join(map(str, row["pairs"]))
                 + "|" + str(row["valid_pairs_total"]) + "|")
lines += ["", "逐日候选均891；特征/标签缺失均有原因账本，无填价、零收益或漏报剔除。",
    "before/after字节完全相同，涵盖10个owner、模型/数据/批准上下文、14本账簿、rolling、",
    "forward实例与legacy状态；这是本地保存的前后快照一致，不能替代新的远端检查。", "",
    "保留所有筛选/生存者偏差、已看历史、PIT/主表/退市/状态/复权未认证限制；",
    "依赖时钟未知仍为空，未声称历史实际计算、净收益、显著性、生产/API/网页或STEP1验收。",
    f"操作收据耗时{receipt['elapsed_seconds']:.3f}s，峰值RSS{receipt['peak_rss_kib']/1024:.3f}MiB，",
    "本次没有重测资源或认证长期SLA。", "",
    "限制：未重新打开原始价格/NFS归档；只复核已捕获载荷、Git源和报告内部数值一致性。",
    "五天事后诊断不证明因子优越性或新的前向收益。详细安全可归档检查/摘要见同名JSON。", "",
    "复现：在E:/QTrade-git设PYTHONPATH=src后，以本地Python运行同名.py；仅写本地复核.md/.json。",
]
OUTPUT.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps({"state": result["state"], "checks_passed": result["checks_passed"],
                  "checks_failed": result["checks_failed"], "git_files": len(paths),
                  "review_markdown": str(OUTPUT.with_suffix(".md")),
                  "review_json": str(OUTPUT.with_suffix(".json")),
                  "daily_pairs": daily_table}, ensure_ascii=False))
