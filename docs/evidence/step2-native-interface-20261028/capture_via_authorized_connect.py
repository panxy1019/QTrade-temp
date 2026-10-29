"""Capture a new isolated native audit using the existing authorized connector.

Connection credentials stay in connect(); this script never reads or prints them.
"""
from __future__ import annotations

import json
import shlex
import sys
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, "E:/QTrade-temp")
from authorized_cluster_read import connect  # noqa: E402

root = Path(__file__).resolve().parent
source = (root / "native_audit_operator.py").read_bytes()
attempt = root / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
attempt.mkdir(exist_ok=False)
(attempt / "operator.py").write_bytes(source)
python = "/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/qlib-env/bin/python"
client = connect()
try:
    stdin, stdout, stderr = client.exec_command(shlex.quote(python) + " -B -", timeout=60)
    stdin.write(source.decode())
    stdin.flush()
    stdin.channel.shutdown_write()
    out, err = stdout.read(), stderr.read()
    code = stdout.channel.recv_exit_status()
    (attempt / "stdout.json").write_bytes(out)
    (attempt / "stderr.txt").write_bytes(err)
    (attempt / "transport.json").write_text(json.dumps({
        "exit_code": code, "executed_at": datetime.now(UTC).isoformat(), "python": python,
        "operator_sha256": sha256(source).hexdigest(),
    }, indent=2), encoding="utf-8")
    print(json.dumps({"attempt": str(attempt), "exit_code": code}))
    if code:
        raise SystemExit(code)
finally:
    client.close()
