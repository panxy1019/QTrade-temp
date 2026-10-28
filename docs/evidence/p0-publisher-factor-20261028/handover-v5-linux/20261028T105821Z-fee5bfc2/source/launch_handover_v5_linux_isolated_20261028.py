"""Upload only fixed public operator/test bytes and run the isolated synthetic package."""
from __future__ import annotations

import hashlib
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path("E:/QTrade-temp")
EVIDENCE = Path("E:/QTrade-git/docs/evidence/p0-publisher-factor-20261028/handover-v5-linux")
PARENT = "/root/qtrade-local/operator-evidence"
PYTHON = "/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python"
FILES = ["publisher_handover_draft_20261028.py", "publisher_handover_bridge_safe_module_v3_20261028.py",
    "publisher_handover_v4_20261028.py", "publisher_handover_bridge_v4_20261028.py",
    "publisher_handover_v5_20261028.py", "publisher_handover_bridge_v5_20261028.py",
    "publisher_incremental_gates_v4_20261028.py", "test_publisher_handover_draft_20261028.py",
    "test_publisher_handover_bridge_safe_module_v3_20261028.py", "test_publisher_handover_v4_20261028.py",
    "test_publisher_handover_v5_20261028.py", "test_publisher_incremental_gates_v4_20261028.py",
    "run_handover_v5_linux_isolated_20261028.py"]


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    name = "handover-v5-linux-" + stamp
    local = ROOT / name
    archive = EVIDENCE / stamp
    local.mkdir(exist_ok=False)
    archive.mkdir(parents=True, exist_ok=False)
    payload = {}
    for filename in FILES:
        raw = (ROOT / filename).read_bytes()
        (local / filename).write_bytes(raw)
        payload[filename] = raw
    manifest = {"schema_version": "qtrade.handover-v5-isolated-package.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "SYNTHETIC_TESTS_ONLY", "files": {filename: {"bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest()} for filename, raw in payload.items()}}
    manifest_raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (local / "package.sha256.json").write_bytes(manifest_raw)
    (archive / "package.sha256.json").write_bytes(manifest_raw)
    sys.path.insert(0, str(ROOT))
    # Import only the authorized callable. Never read its source/config/credentials.
    from authorized_cluster_read import connect

    client = connect()
    remote = PARENT + "/" + name
    try:
        sftp = client.open_sftp()
        try:
            sftp.mkdir(remote, mode=0o700)  # Unique path; never replace an old result.
            for filename, raw in [*payload.items(), ("package.sha256.json", manifest_raw)]:
                with sftp.open(remote + "/" + filename, "wx") as handle:
                    handle.write(raw)
                sftp.chmod(remote + "/" + filename, 0o600)
            command = ("env LIVE_TRADING_ENABLED=false PYTHONDONTWRITEBYTECODE=1 "
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 " + shlex.quote(PYTHON) + " -B -")
            stdin, stdout, stderr = client.exec_command(command, timeout=100, get_pty=False)
            code = "import runpy\nrunpy.run_path(" + repr(remote + "/run_handover_v5_linux_isolated_20261028.py") + ", run_name='__main__')\n"
            stdin.write(code)
            stdin.channel.shutdown_write()
            out, error = stdout.read(), stderr.read()
            status = stdout.channel.recv_exit_status()
            (local / "launcher.stdout.txt").write_bytes(out)
            (local / "launcher.stderr.txt").write_bytes(error)
            (archive / "launcher.stdout.txt").write_bytes(out)
            (archive / "launcher.stderr.txt").write_bytes(error)
            if status != 0:
                print(json.dumps({"state": "FAILED_NO_RETRY", "remote_root": remote,
                    "local_evidence": str(archive), "exit_code": status}))
                return 1
            for filename in ["result.json", "pytest.xml", "pytest.stdout.txt", "pytest.stderr.txt"]:
                with sftp.open(remote + "/" + filename, "rb") as handle:
                    raw = handle.read()
                (local / filename).write_bytes(raw)
                (archive / filename).write_bytes(raw)
            result = json.loads((archive / "result.json").read_bytes())
            for filename, expected in result["outputs"].items():
                assert hashlib.sha256((archive / filename).read_bytes()).hexdigest() == expected["sha256"]
            assert result["package_sha256"] == hashlib.sha256(manifest_raw).hexdigest()
            for filename, raw in payload.items():
                assert result["before_sha256"][filename] == result["after_sha256"][filename] == hashlib.sha256(raw).hexdigest()
            # Sources are public operator/mock bytes, not private wrapper/config.
            sources = archive / "source"
            sources.mkdir()
            for filename, raw in payload.items():
                (sources / filename).write_bytes(raw)
            (sources / "launch_handover_v5_linux_isolated_20261028.py").write_bytes(Path(__file__).read_bytes())
            print(json.dumps({"state": result["state"], "counts": result["counts"],
                "source_unchanged": result["source_unchanged"], "remote_root": remote,
                "local_evidence": str(archive), "result_sha256": hashlib.sha256((archive / "result.json").read_bytes()).hexdigest()}, sort_keys=True))
        finally:
            sftp.close()
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
