"""Execute only the fixed synthetic package; no production adapter construction."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_PARENT = Path("/root/qtrade-local/operator-evidence")
DATA_ROOT = Path("/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade")
EXPECTED_PYTHON = DATA_ROOT / "venv/bin/python"
MIN_LOCAL_FREE = 2 * 1024**3
MIN_DATA_FREE = 100 * 1024**3
TESTS = ["test_publisher_handover_v4_20261028.py", "test_publisher_handover_v5_20261028.py",
         "test_publisher_incremental_gates_v4_20261028.py"]


def require(value, code):
    if not value:
        raise ValueError("HANDOVER_LINUX_" + code)


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def durable_write(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main():
    root = Path(__file__).absolute().parent
    require(root.parent == EXPECTED_PARENT and root.name.startswith("handover-v5-linux-"),
            "ISOLATED_ROOT_REQUIRED")
    require(not any(path.is_symlink() for path in (root, *root.parents)), "ROOT_ALIAS")
    require(stat.S_IMODE(root.stat().st_mode) == 0o700, "PRIVATE_ISOLATED_ROOT_REQUIRED")
    require(Path(sys.executable) == EXPECTED_PYTHON and os.environ.get("LIVE_TRADING_ENABLED")
            == "false", "FIXED_PYTHON_LIVE_OFF_REQUIRED")
    require(os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"
            and os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == "1", "NO_HIDDEN_TEST_WRITES")
    local_free, data_free = shutil.disk_usage(root).free, shutil.disk_usage(DATA_ROOT).free
    require(local_free >= MIN_LOCAL_FREE and data_free >= MIN_DATA_FREE, "SPACE_RESERVE")
    package_raw = (root / "package.sha256.json").read_bytes()
    package = json.loads(package_raw)
    require(package["schema_version"] == "qtrade.handover-v5-isolated-package.v1", "PACKAGE_SCHEMA")
    inventory = package["files"]
    require(type(inventory) is dict and inventory and all("/" not in name and "\\" not in name
            and name.endswith(".py") for name in inventory), "EXACT_PACKAGE_FILES")

    def source_snapshot():
        result = {}
        for name, expected in inventory.items():
            path = root / name
            require(not path.is_symlink() and path.stat().st_nlink == 1, "SOURCE_ALIAS")
            require(path.stat().st_size == expected["bytes"], "SOURCE_SIZE_CHANGED")
            actual = file_sha(path)
            require(actual == expected["sha256"], "SOURCE_SHA_CHANGED")
            result[name] = actual
        require(file_sha(root / "package.sha256.json") == hashlib.sha256(package_raw).hexdigest(),
                "PACKAGE_MANIFEST_CHANGED")
        return result

    before = source_snapshot()
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONPATH"] = ""
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["LIVE_TRADING_ENABLED"] = "false"
    command = [str(EXPECTED_PYTHON), "-B", "-m", "pytest", "-q", "-c", "/dev/null",
        "-p", "no:cacheprovider", "--noconftest", "--import-mode=importlib",
        *[str(root / name) for name in TESTS], "--junitxml=" + str(root / "pytest.xml")]
    started = datetime.now(timezone.utc).isoformat()
    begin = time.monotonic()
    completed = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=60,
                               check=False)
    elapsed = time.monotonic() - begin
    durable_write(root / "pytest.stdout.txt", completed.stdout)
    durable_write(root / "pytest.stderr.txt", completed.stderr)
    after = source_snapshot()
    suites = ET.parse(root / "pytest.xml").getroot()
    suites = [suites] if suites.tag == "testsuite" else list(suites.iter("testsuite"))
    counts = {key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    require(completed.returncode == 0 and counts == {"tests": 102, "failures": 0,
            "errors": 0, "skipped": 0}, "EXACT_102_ZERO_SKIP_REQUIRED")
    require(before == after, "SOURCE_CHANGED_DURING_TEST")
    forbidden = [str(path.relative_to(root)) for path in root.rglob("*")
                 if path.name in {"__pycache__", ".pytest_cache"} or path.suffix == ".pyc"]
    require(not forbidden, "TEST_CACHE_CREATED")
    require(shutil.disk_usage(root).free >= MIN_LOCAL_FREE
            and shutil.disk_usage(DATA_ROOT).free >= MIN_DATA_FREE, "AFTER_SPACE_RESERVE")
    result = {"schema_version": "qtrade.handover-v5-isolated-linux-result.v1", "state": "PASS",
        "scope": "SYNTHETIC_ONLY_NO_REAL_RPC_PRIVATE_WRAPPER_OR_PRODUCTION_CONTROL",
        "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed, "remote_root": str(root), "python": sys.version,
        "python_executable": sys.executable, "command": command, "counts": counts,
        "live_trading_enabled": False, "private_wrapper_read": False,
        "production_service_control": False, "real_rpc_connected": False,
        "package_sha256": hashlib.sha256(package_raw).hexdigest(), "before_sha256": before,
        "after_sha256": after, "source_unchanged": before == after, "cache_created": forbidden,
        "initial_free_bytes": {"local": local_free, "data": data_free},
        "retained_reserve_bytes": {"local": MIN_LOCAL_FREE, "data": MIN_DATA_FREE},
        "outputs": {name: {"bytes": (root / name).stat().st_size,
                            "sha256": file_sha(root / name)}
                    for name in ["pytest.xml", "pytest.stdout.txt", "pytest.stderr.txt"]}}
    durable_write(root / "result.json", (json.dumps(result, sort_keys=True, indent=2) + "\n").encode())
    print(json.dumps({"state": result["state"], "counts": counts, "remote_root": str(root),
                      "source_unchanged": before == after, "result_sha256": file_sha(root / "result.json")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
