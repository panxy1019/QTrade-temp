"""Capture actual stock HTTP delivery and run the existing immutable-day audit read-only.

No cluster output directory, service constructor, control action or production write.
Every run creates new desktop evidence files. Real UTC drives the existing audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from authorized_cluster_read import connect

REPO = Path("E:/QTrade-git")
sys.path[:0] = [str(REPO / "src"), str(REPO)]
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.forward_publication import validate_forward_publication

BASE = "/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade"
REVISION = "9846a7f30774d6ae4b4d41f02b493c5246be87d9"
SOURCE = BASE + "/releases/" + REVISION
MODEL = "29589b4486a09159eb3a07153c54fba609fbc80b52c168dd665cd46660be8487"
PRODUCTION = "ae33a7c8c27ea721eb3be1e4430b562a44b67f91"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    with path.open("xb") as stream:
        stream.write((json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--capture", type=Path, help="Previously captured raw stock HTTP directory")
args = parser.parse_args()
if args.capture is None:
    result = subprocess.run([sys.executable, "-B", "E:/QTrade-temp/capture-stock-delivery-20261027.py"],
        capture_output=True, check=True, text=True, timeout=95)
    capture_result = json.loads(result.stdout)
    capture = Path(capture_result["root"])
else:
    capture = args.capture

meta = json.loads((capture / "capture.json").read_bytes())
assert signed({k: v for k, v in meta.items() if k != "evidence_sha256"}) == meta
assert len(meta["captures"]) == 3
for row in meta["captures"]:
    assert row["state"] == "CAPTURED" and row["status_code"] == 200
    raw = (capture / row["body_file"]).read_bytes()
    assert digest(raw) == row["body_sha256"] and len(raw) == row["body_bytes"]
before = json.loads((capture / "deployment-before.json").read_bytes())
after = json.loads((capture / "deployment-after.json").read_bytes())
assert before == after
wrapper = json.loads((capture / "stock-forward.json").read_bytes())
publication = json.loads((capture / "publication.json").read_bytes())
assert wrapper["publication"] == publication
assert signed({k: v for k, v in publication.items() if k != "evidence_sha256"}) == publication
validate_forward_publication(publication, now=datetime.now(UTC))
expected_source = subprocess.check_output(["git", "show", REVISION + ":scripts/audit_qlib_forward_transition.py"], cwd=REPO)
code = r'''
import json
from pathlib import Path
from datetime import UTC,date,datetime
from hashlib import sha256
from qtrade.monitoring.data_freshness import AUTHORITY,Calendar,signed
from scripts.audit_qlib_forward_transition import audit_transition
source=Path.cwd();publication=PUBLICATION
result=audit_transition(Path(BASE),Path('/root/qtrade-local/forward-research'),publication,
 date(2026,10,28),MODEL,Calendar(source/'configs/observer_calendar_2026.json'),
 now=datetime.now(UTC),model_database=Path('/root/qtrade-local/online-models.sqlite'))
print(json.dumps(signed({**AUTHORITY,'audit':result,'auditor_source_sha256':sha256((source/'scripts/audit_qlib_forward_transition.py').read_bytes()).hexdigest(),'auditor_source_root':str(source),'production_mutations':False}),sort_keys=True))
'''.replace("PUBLICATION", repr(publication)).replace("Path(BASE)", "Path(" + repr(BASE) + ")").replace("MODEL", repr(MODEL))
client = connect()
try:
    command = "cd " + shlex.quote(SOURCE) + " && env LIVE_TRADING_ENABLED=false PYTHONPATH=src:. " + shlex.quote(BASE + "/venv/bin/python") + " -B - <<'PY'\n" + code + "\nPY"
    _, stdout, stderr = client.exec_command(command, timeout=180)
    raw, errors = stdout.read(), stderr.read()
    rc = stdout.channel.recv_exit_status()
finally:
    client.close()
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
audit_path = capture / ("E-private-" + stamp + ".json")
if rc:
    with audit_path.with_suffix(".error").open("xb") as stream:
        stream.write(errors)
    print(json.dumps({"exit": rc, "capture": str(capture), "error_file": str(audit_path.with_suffix('.error'))}))
    raise SystemExit(rc)
value = json.loads(raw)
assert signed({k: v for k, v in value.items() if k != "evidence_sha256"}) == value
assert value["auditor_source_sha256"] == digest(expected_source)
audit = value["audit"]
assert signed({k: v for k, v in audit.items() if k != "evidence_sha256"}) == audit
write(audit_path, value)
accepted = audit["state"] == "PASSED" and wrapper["publication_available"] is True and wrapper["service_fresh"] is True and before.get("git_commit_sha") == PRODUCTION
binding = signed({**AUTHORITY, "kind": "ACTUAL_OCT28_STOCK_E_HTTP_AND_READ_ONLY_AUDIT_BINDING",
    "recorded_at": datetime.now(UTC).isoformat(), "state": "PASSED_FIXED_DAY_SCOPE" if accepted else "BLOCKED",
    "day": "2026-10-28", "approved_model_id": MODEL,
    "capture_directory": str(capture), "capture_evidence_sha256": meta["evidence_sha256"],
    "publication_sha256": publication["evidence_sha256"], "inner_file_sha256": digest((capture / "publication.json").read_bytes()),
    "production_revision": before.get("git_commit_sha"), "expected_production_revision": PRODUCTION,
    "deployment_matches": before.get("git_commit_sha") == PRODUCTION,
    "publication_available": wrapper["publication_available"], "service_fresh": wrapper["service_fresh"],
    "publication_state": publication["state"], "publication_errors": publication["errors"],
    "audit_file": audit_path.name, "audit_sha256": audit["evidence_sha256"], "auditor_source_sha256": value["auditor_source_sha256"],
    "audit_source_revision": REVISION, "audit_state": audit["state"], "audit_failures": audit["failures"],
    "expected_book_count": audit["expected_book_count"], "current_book_count": audit["current_book_count"],
    "retired_book_count": audit["retired_book_count"],
    "accepted_current_observations": sum(b["accepted"] for b in audit["books"] if b.get("scope") != "READ_ONLY_RETIRED_BOOK_NOT_A_NEW_DAY_OBSERVATION"),
    "accepted_retired_proofs": sum(b["accepted"] for b in audit["books"] if b.get("scope") == "READ_ONLY_RETIRED_BOOK_NOT_A_NEW_DAY_OBSERVATION"),
    "whole_step1_accepted": False, "browser_verified": False, "production_mutations": False})
binding_path = capture / ("E-binding-" + stamp + ".json")
write(binding_path, binding)
print(json.dumps({"capture": str(capture), "audit": str(audit_path), "binding": str(binding_path),
    "state": binding["state"], "audit_failures": audit["failures"],
    "accepted_current_observations": binding["accepted_current_observations"],
    "accepted_retired_proofs": binding["accepted_retired_proofs"],
    "book_failures": [{"strategy_id": b["strategy_id"], "mode": b["mode"], "failures": b["failures"]} for b in audit["books"] if not b["accepted"]]}, ensure_ascii=False))
