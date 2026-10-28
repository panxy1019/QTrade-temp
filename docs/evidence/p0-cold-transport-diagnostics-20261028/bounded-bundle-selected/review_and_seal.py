from __future__ import annotations

import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
FIRST = ROOT.parent / "p0-bounded-git-fixture-20261028T135524Z-d8621924"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def file_entry(path: Path) -> dict:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": digest(data)}


def inspect_trial(root: Path, expected_checks: int, expected_errors: int) -> dict:
    result = load(root / "result.json")
    commands = load(root / "commands.json")
    checks = load(root / "checks.json")
    xml = ET.parse(root / "results.xml").getroot()
    assert xml.tag == "testsuite"
    assert int(xml.attrib["tests"]) == len(xml.findall("testcase")) == expected_checks + expected_errors
    assert int(xml.attrib["errors"]) == len(xml.findall(".//error")) == expected_errors
    assert int(xml.attrib["failures"]) == len(xml.findall(".//failure")) == 0
    assert len(checks) == result["checks"] == expected_checks
    assert all(check["passed"] is True for check in checks)
    assert len(commands) == result["command_count"]
    for command in commands:
        assert command["argv"][0].lower().endswith("git.exe")
        assert not {"push", "fetch", "pull", "send-pack", "receive-pack"}.intersection(command["argv"])
        for stream in ("stdout", "stderr"):
            entry = command[stream]
            assert file_entry(root / entry["path"]) == {key: entry[key] for key in ("bytes", "sha256")}
        if "clone" in command["argv"]:
            urls = [item for item in command["argv"] if item.startswith("file:")]
            assert len(urls) == 1 and "QTrade-temp" in urls[0]
        if command["offline_phase"]:
            assert "protocol.file.allow=never" in command["argv"]
    original_manifest = load(root / "manifest.json")
    for name, expected in original_manifest["files"].items():
        assert file_entry(root / name) == expected, name
    return {"root": str(root), "state": result["state"], "checks": expected_checks,
            "execution_errors": expected_errors, "commands": len(commands),
            "result": file_entry(root / "result.json"), "xml": file_entry(root / "results.xml"),
            "driver": file_entry(root / "run_fixture.py"), "original_manifest": file_entry(root / "manifest.json")}


def main() -> None:
    first = inspect_trial(FIRST, 12, 1)
    final = inspect_trial(ROOT, 25, 0)
    result = load(ROOT / "result.json")
    commands = load(ROOT / "commands.json")
    expected_files = result["frozen"]["target_files"]
    for item in expected_files:
        expected = {"bytes": item["bytes"], "sha256": item["sha256"]}
        assert file_entry(ROOT / "source-unavailable-offline" / item["path"]) == expected
        assert file_entry(ROOT / "cold-current-tree" / item["path"]) == expected
        positive_reads = [c for c in commands if c["offline_phase"] and c["returncode"] == 0
                          and c["argv"][-3:] == ["cat-file", "blob", item["oid"]]
                          and str(ROOT / "cold-receiver" / "base.git") in c["argv"]]
        assert len(positive_reads) == 1
        assert {k: positive_reads[0]["stdout"][k] for k in ("bytes", "sha256")} == expected
    header, pack = (ROOT / "increment.bundle").read_bytes().split(b"\n\n", 1)
    lines = header.decode("ascii").splitlines()
    assert len(lines) == 3 and lines[0] == "# v2 git bundle"
    assert lines[1].startswith("-" + result["frozen"]["base"] + " ")
    assert lines[2] == result["frozen"]["target"] + " refs/heads/frozen-head"
    assert int.from_bytes(pack[8:12], "big") == 9
    assert file_entry(ROOT / "increment.bundle") == {"bytes": result["increment_bundle"]["bundle_bytes"],
                                                      "sha256": result["increment_bundle"]["bundle_sha256"]}
    assert file_entry(ROOT / "base.tar") == {k: result["base_package"][k] for k in ("bytes", "sha256")}
    assert result["metadata_only_result"]["bundle_verify_returncode"] == 0
    assert result["metadata_only_result"]["unbundle_returncode"] == 0
    assert result["metadata_only_result"]["fsck_returncode"] == 2
    assert len(result["metadata_only_result"]["target_blob_missing"]) == 4
    assert result["positive_result"]["fsck_returncode"] == 0
    combined_uncompressed = sum(item["bytes"] for item in result["positive_result"]["physical_objects"].values())
    assert combined_uncompressed < 2_000_000
    report = {"schema": "qtrade.local-bounded-git-independent-evidence-review.v1",
              "state": "PASS", "first_failed_execution_retained": first, "final": final,
              "target_files_independently_read": len(expected_files),
              "target_blob_bytes": sum(f["bytes"] for f in expected_files),
              "physical_combined_uncompressed_object_bytes": combined_uncompressed,
              "command_stream_sha_checks": sum(len(load(root / "commands.json")) * 2 for root in (FIRST, ROOT)),
              "notes": ["Independent evidence consistency review; no Git command rerun and no production or network operation.",
                        "Header parser gates are experimental local fixture code, not installed production validation.",
                        "First execution error is preserved; final pass only applies to final driver bytes."]}
    (ROOT / "independent-evidence-review.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    members = {}
    for trial in (FIRST, ROOT):
        files = load(trial / "manifest.json")["files"]
        for name in files:
            path = trial / name
            members[path.relative_to(ROOT.parent).as_posix()] = file_entry(path)
        for name in ("manifest.json", "ACCEPTANCE.md", "reproduce.ps1", "review_and_seal.py", "independent-evidence-review.json",
                     "local-git-config-help.stdout.bin", "local-git-config-help.stderr.bin", "local-git-config-help.json"):
            path = trial / name
            if path.is_file():
                members[path.relative_to(ROOT.parent).as_posix()] = file_entry(path)
    manifest = {"schema": "qtrade.local-bounded-git-seal.v1", "root": str(ROOT), "files": members,
                "first_failure_retained": True, "fixtures_only": True,
                "network_transport": False, "production_access": False, "push": False}
    output = ROOT / "sealed-manifest.json"
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"review": "PASS", "manifest": str(output), "manifest_sha256": digest(output.read_bytes()),
                      "manifest_files": len(members), "checks": 25, "preserved_execution_errors": 1,
                      "combined_uncompressed_object_bytes": combined_uncompressed}))


if __name__ == "__main__":
    main()
