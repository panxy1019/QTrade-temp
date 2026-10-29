"""Assemble exact retained actual artifacts locally; never applies or reads wrappers.

Raw mount/report bytes remain only in the local private staging directory.
Optional foundation JSONs contain reviewed hashes/owner metadata, never bodies.
"""
import argparse
import hashlib
import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path("E:/QTrade-temp")
EVIDENCE = ROOT / "cluster-evidence"
REPO = Path("E:/QTrade-git")
spec = importlib.util.spec_from_file_location("publisher_v6_bundle_composite", ROOT / "publisher_actual_composite_v6_20261028.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
a = c.a


def build(foundation_manifest=None, foundation_gates=None):
    fresh = EVIDENCE / "publisher-handover-fba51aad-20261028T113923Z"
    foundation_manifest = foundation_manifest or fresh / "foundation-manifest.json"
    foundation_gates = foundation_gates or fresh / "foundation-gates.json"
    source_raw = (EVIDENCE / "market-checkpoint-exact-git-20261028T105253Z/manifest.json").read_bytes()
    assert a.sha(source_raw) == "109fc8efe00cd0ab8009a8880114fe6697a058bfd5cc27cb2942ade19a2c0e82"
    source = json.loads(source_raw)
    roles = {"source_manifest": source_raw}
    manifest = {"new_revision": source["source_revision"], "runtime_files": source["files"],
        "release_root": "/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/releases/" + source["source_revision"],
        "actual_source_manifest_sha256": a.sha(source_raw), "actual_freeze_sha256": {}, "actual_oracle_sha256": {},
        "actual_composite_sha256": a.sha((ROOT / "publisher_actual_composite_v6_20261028.py").read_bytes()),
        "actual_origin_resolver_sha256": "64c51305865f2d7c8c6f7311dc2f9cfef0f5200adc79c738b17d335257cf131e",
        "actual_slow_probe_driver_sha256": "8b3879dac3b6d13e6e8910394f3e607046b367bd7ce4042916bb8b0b8ae8a6c5"}
    records = [
        ("baseline", "market-persistent-final-modes-nfs-20261028T105454Z", "market-current-freeze-20261028T070943Z",
         "market-persistent-final-modes-benchmark-20261028.py", "market-current-nfs-independent-oracle-20261028T105823Z",
         "7b79cecb0fcb10457bf0d996099611053e7bc80e6338aa4c89ad10fe48b7c887", "c927428f42a3e82eec63f9667ff87560c56d6cf5ab757464388fbe1c81053995", None),
        ("current", "market-persistent-continuity-nfs-20261028T105730Z", "market-current-freeze-20261028T100718Z",
         "market-persistent-final-continuity-benchmark-20261028.py", "market-current-nfs-independent-oracle-20261028T110027Z",
         "a8be89d9c1e142c5731790c1b1d9c6cfb2504a92f1f4ba7f06791db29c08dbae", "7eea8ec9906f6cf2983e3a8097d493e1ff0dc92d61c0a51c0e038d9b7ed254d5",
         EVIDENCE / "v6-round-stdout-readonly-20261028T111637Z"),
    ]
    for scope, folder, freeze_folder, driver, oracle_folder, report_hash, oracle_hash, capture in records:
        parent = EVIDENCE / folder
        roles["report_" + scope] = (parent / "benchmark-report.json").read_bytes()
        assert a.sha(roles["report_" + scope]) == report_hash
        roles["freeze_" + scope] = (EVIDENCE / freeze_folder / "freeze.json").read_bytes()
        roles["driver_" + scope] = (ROOT / driver).read_bytes()
        roles["oracle_" + scope] = (EVIDENCE / oracle_folder / "oracle-report.json").read_bytes()
        assert a.sha(roles["oracle_" + scope]) == oracle_hash
        manifest["actual_freeze_sha256"][scope] = a.sha(roles["freeze_" + scope])
        manifest["actual_oracle_sha256"][scope] = oracle_hash
        for side in ("before", "after"):
            roles["mount_" + scope + "_" + side] = (parent / (side + "-mountinfo.raw")).read_bytes()
        for row in json.loads(roles["report_" + scope])["results"]:
            label = row["pass_name"]
            roles["round_" + scope + "_" + label] = ((capture or parent) / (label + "-stdout.log")).read_bytes()
    oracle = json.loads(roles["oracle_baseline"])
    manifest["baseline_import_sha256"] = {path: a.sha(subprocess.check_output(
        ["git", "show", oracle["baseline_revision"] + ":" + path], cwd=REPO)) for path in oracle["baseline_imports"]}
    roles["origin_preflight"] = (EVIDENCE / "publisher-v6-origin-ro-20261028T112725Z/result.json").read_bytes()
    assert a.sha(roles["origin_preflight"]) == "de54859bd4e4448899772b96342bad521415c94bdde8e99b888ab4771efd4b39"
    roles["fault_map_original"] = (ROOT / "publisher-v5-independent-review-20261028T105905Z/fault-coverage-map.json").read_bytes()
    mapping = json.loads(roles["fault_map_original"])
    for name, value in mapping["xml_reports"].items():
        path = Path(value["source"])
        assert path.suffix == ".xml" and path.resolve().is_relative_to(ROOT)
        roles["xml_" + name] = path.read_bytes()
    extra = EVIDENCE / "market-fault-boundaries-tests-20261028T110931Z"
    roles["xml_additional5"] = (extra / "tests.xml").read_bytes()
    roles["additional_test_manifest"] = (extra / "manifest.json").read_bytes()
    roles["additional_test_source"] = (REPO / "tests/test_market_incremental_failure_boundaries.py").read_bytes()
    slow = EVIDENCE / "market-same-nfs-latency-20261028T112633Z"
    roles["slow_nfs_probe"] = (slow / "benchmark-report.json").read_bytes()
    assert a.sha(roles["slow_nfs_probe"]) == "dc837c767ef00ad26f01a467637b9939bec165872fc243faee73504e76a0f83f"
    roles["slow_probe_driver"] = (ROOT / "market-same-nfs-latency-probe-20261028.py").read_bytes()
    for side in ("before", "after"):
        roles["mount_slow_" + side] = (slow / (side + "-mountinfo.raw")).read_bytes()
    roles["foundation_runtime"] = (fresh / "fresh-foundation.json").read_bytes()
    assert a.sha(roles["foundation_runtime"]) == "a3efba4c6555c9988c0494061a8f26ecead797581106d1f13419189c5dc87ed5"
    roles["supervisor_semantics_raw"] = (ROOT / "supervisor-semantics-safe-evidence-20261028/rpc-pass/summary.json").read_bytes()
    assert a.sha(roles["supervisor_semantics_raw"]) == "4d0ded50acb12a3658e9ac5e2a810413008822a5cb59a34b05a72fb50af79166"
    assert set(roles) == c.artifact_roles()
    bundle = {"schema_version": "qtrade.publisher-handover-gates.v6", "artifact_files": {
        name: {"file": name + ".raw", "sha256": a.sha(data)} for name, data in roles.items()}}
    if foundation_manifest is not None:
        existing = json.loads(foundation_manifest.read_bytes())
        assert all(existing.get(key, value) == value for key, value in manifest.items())
        manifest = {**existing, **manifest}
    if foundation_gates is not None:
        existing = json.loads(foundation_gates.read_bytes())
        bundle.update({key: existing[key] for key in ("supervisor_runtime", "supervisor_semantics")})
    # Operator digest uses the existing ensure_ascii default. All bundle keys and
    # values are ASCII, so this is also a.digest(bundle).
    manifest["gate_bundle_sha256"] = a.digest(bundle)
    return manifest, bundle, roles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foundation-manifest", type=Path)
    parser.add_argument("--foundation-gates", type=Path)
    args = parser.parse_args()
    manifest, bundle, roles = build(args.foundation_manifest, args.foundation_gates)
    operator_spec = importlib.util.spec_from_file_location("publisher_v6_actual_operator_check",
        ROOT / "publisher_handover_v6_preparation_20261028.py")
    operator = importlib.util.module_from_spec(operator_spec)
    operator_spec.loader.exec_module(operator)
    result = operator.check_loaded_evidence(manifest, bundle, roles)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    destination = ROOT / ("publisher-v6-actual-bundle-" + stamp)
    destination.mkdir(exist_ok=False)
    private = destination / "gate-evidence"
    private.mkdir()
    for name, data in roles.items():
        (private / (name + ".raw")).write_bytes(data)
    for name, value in (("manifest.json", manifest), ("gates.json", bundle), ("safe-check-result.json", result)):
        (destination / name).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    public = REPO / "docs/evidence/p0-publisher-factor-20261028" / ("handover-v6-composite-" + stamp)
    public.mkdir()
    (public / "safe-check-result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    (public / "artifact-hashes.json").write_text(json.dumps(bundle["artifact_files"], sort_keys=True, indent=2) + "\n")
    print(json.dumps({"state": result["state"], "actual_executions": result["actual_semantic_executions"],
        "fault_scenarios": result["fault_coverage"]["covered_scenarios"], "private_package": str(destination),
        "safe_archive": str(public), "operator_foundation_bound": True,
        "production_apply_authorized": False}, sort_keys=True))


if __name__ == "__main__":
    main()
