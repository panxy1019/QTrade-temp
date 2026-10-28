from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
GIT = shutil.which("git")
assert GIT
COMMANDS: list[dict] = []
CHECKS: list[dict] = []
RESULT: dict = {"schema": "qtrade.local-bounded-git-fixture.v1", "fixture_only": True,
                "production_access": False, "network_transport": False, "push": False,
                "live_trading_enabled": False, "root": str(ROOT), "git_binary": GIT}
BASE_CAP = 2_000_000
BUNDLE_CAP = 2_000_000
OBJECT_CAP = 64
TARGET_BLOB_CAP = 1_000_000
FROZEN_REF = "refs/heads/frozen-head"
ENV = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
ENV.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(ROOT / "empty.gitconfig"),
           GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_PROTOCOL_FROM_USER="0",
           GIT_ALLOW_PROTOCOL="file", LIVE_TRADING_ENABLED="false")
OFFLINE = False


def within(path: Path) -> Path:
    p = path.resolve()
    assert p == ROOT or ROOT in p.parents, p
    return p


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(path: Path, value: object) -> None:
    within(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def run(args: list[str], *, cwd: Path = ROOT, data: bytes | None = None,
        extra: dict[str, str] | None = None, timeout: float = 30) -> subprocess.CompletedProcess:
    global COMMANDS
    within(cwd)
    idx = len(COMMANDS) + 1
    env = dict(ENV)
    if OFFLINE:
        env["GIT_ALLOW_PROTOCOL"] = "disabled-fixture-only"
    if extra:
        env.update(extra)
    cfg = ["-c", f"core.hooksPath={ROOT / 'no-hooks'}", "-c", "protocol.allow=never",
           "-c", f"protocol.file.allow={'never' if OFFLINE else 'always'}",
           "-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]
    argv = [GIT, *cfg, *args]
    start = time.monotonic()
    cp = subprocess.run(argv, cwd=cwd, input=data, capture_output=True, env=env, timeout=timeout)
    out = ROOT / "commands" / f"{idx:03d}.stdout.bin"
    err = ROOT / "commands" / f"{idx:03d}.stderr.bin"
    out.write_bytes(cp.stdout)
    err.write_bytes(cp.stderr)
    COMMANDS.append({"id": idx, "argv": argv, "cwd": str(cwd), "offline_phase": OFFLINE,
                     "environment_policy": "all inherited GIT_* removed; global/system config disabled; hooks disabled; file only before gate, every transport disabled after gate; lazy fetch disabled",
                     "stdin": None if data is None else {"bytes": len(data), "sha256": sha(data)},
                     "returncode": cp.returncode, "seconds": time.monotonic() - start,
                     "stdout": {"path": str(out.relative_to(ROOT)), "bytes": len(cp.stdout), "sha256": sha(cp.stdout)},
                     "stderr": {"path": str(err.relative_to(ROOT)), "bytes": len(cp.stderr), "sha256": sha(cp.stderr)},
                     "stdout_text": cp.stdout.decode("utf-8", "replace") if len(cp.stdout) < 20000 and b"\0" not in cp.stdout else None,
                     "stderr_text": cp.stderr.decode("utf-8", "replace") if len(cp.stderr) < 20000 and b"\0" not in cp.stderr else None})
    save(ROOT / "commands.json", COMMANDS)
    return cp


def git(repo: Path, *args: str, data: bytes | None = None, ok: bool = True,
        extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    cp = run(["-C", str(within(repo)), *args], data=data, extra=extra)
    if ok:
        assert cp.returncode == 0, (args, cp.returncode, cp.stderr.decode("utf-8", "replace"))
    return cp


def text(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.decode().strip()


def check(name: str, condition: bool, details: object = None) -> None:
    CHECKS.append({"name": name, "passed": bool(condition), "details": details})
    save(ROOT / "checks.json", CHECKS)
    assert condition, (name, details)


def inventory(repo: Path) -> dict[str, dict]:
    out = git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype) %(objectsize)").stdout.decode()
    found = {}
    for line in out.splitlines():
        oid, kind, size = line.split()
        found[oid] = {"type": kind, "bytes": int(size)}
    return found


def tree(repo: Path, rev: str) -> list[dict]:
    records = []
    for row in git(repo, "ls-tree", "-r", "-z", "--full-tree", rev).stdout.split(b"\0"):
        if not row:
            continue
        meta, path = row.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        records.append({"path": path.decode(), "mode": mode, "type": kind, "oid": oid})
    return records


def blob(seed: str, count: int) -> bytes:
    return hashlib.shake_256(seed.encode("ascii")).digest(count)


def commit(repo: Path, message: str, stamp: str) -> str:
    git(repo, "add", "--all")
    git(repo, "-c", "user.name=Synthetic Local Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--no-verify", "-m", message,
        extra={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
    return text(repo, "rev-parse", "HEAD")


def init_bare(name: str) -> Path:
    repo = within(ROOT / name)
    run_ok = run(["init", "--bare", "--object-format=sha1", str(repo)])
    assert run_ok.returncode == 0, run_ok.stderr
    return repo


def header(bundle: Path, *, expected_base: str, expected_target: str,
           byte_cap: int = BUNDLE_CAP) -> dict:
    raw = bundle.read_bytes()
    if len(raw) > byte_cap:
        raise ValueError("BUNDLE_BYTE_BOUND")
    end = raw.find(b"\n\n")
    if not 0 < end <= 65536:
        raise ValueError("HEADER_BOUND")
    lines = raw[:end].decode("ascii").splitlines()
    if lines[0] != "# v2 git bundle":
        raise ValueError("BUNDLE_VERSION")
    prereq = []
    refs = []
    for line in lines[1:]:
        if line.startswith("-"):
            oid, _, message = line[1:].partition(" ")
            prereq.append({"oid": oid, "message": message})
        else:
            oid, sep, name = line.partition(" ")
            if not sep:
                raise ValueError("REF_FORMAT")
            refs.append({"oid": oid, "name": name})
    if len(prereq) != 1 or prereq[0]["oid"] != expected_base:
        raise ValueError("EXACT_UNIQUE_PREREQUISITE")
    if refs != [{"oid": expected_target, "name": FROZEN_REF}]:
        raise ValueError("EXACT_UNIQUE_TARGET_REF")
    pack = raw[end + 2:]
    if pack[:4] != b"PACK" or len(pack) < 32:
        raise ValueError("PACK_HEADER")
    count = int.from_bytes(pack[8:12], "big")
    if not 1 <= count <= OBJECT_CAP:
        raise ValueError("PACK_OBJECT_BOUND")
    return {"prerequisites": prereq, "advertised_refs": refs, "bundle_bytes": len(raw),
            "bundle_sha256": sha(raw), "pack_offset": end + 2, "pack_objects": count,
            "pack_version": int.from_bytes(pack[4:8], "big"), "header_bytes": end + 2}


def expect_header_reject(name: str, raw: bytes, base: str, target: str, code: str,
                         byte_cap: int = BUNDLE_CAP) -> None:
    path = ROOT / "malformed" / f"{name}.bundle"
    path.write_bytes(raw)
    try:
        header(path, expected_base=base, expected_target=target, byte_cap=byte_cap)
    except ValueError as exc:
        check(name, str(exc) == code, {"actual": str(exc), "expected": code,
                                     "path": str(path.relative_to(ROOT)), "bytes": len(raw), "sha256": sha(raw)})
    else:
        check(name, False, "accepted malformed bundle header")


def package(repo: Path, path: Path) -> dict:
    files = {}
    directories = []
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as tar:
        for f in sorted(repo.rglob("*")):
            assert not f.is_symlink()
            rel = f.relative_to(repo).as_posix()
            if f.is_dir():
                directories.append(rel)
                info = tarfile.TarInfo("base.git/" + rel + "/")
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
                continue
            assert f.is_file()
            raw = f.read_bytes()
            files[rel] = {"bytes": len(raw), "sha256": sha(raw)}
            info = tarfile.TarInfo("base.git/" + rel)
            info.size = len(raw)
            info.mode = 0o644
            import io
            tar.addfile(info, io.BytesIO(raw))
    return {"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
            "sha256": sha(path.read_bytes()), "files": files, "directories": directories}


def run_experiment() -> None:
    global OFFLINE
    version = run(["--version"])
    assert version.returncode == 0
    RESULT["git_version"] = version.stdout.decode().strip()
    RESULT["python_version"] = sys.version
    RESULT["bounds"] = {"base_tar_bytes": BASE_CAP, "increment_bundle_bytes": BUNDLE_CAP,
                         "increment_pack_objects": OBJECT_CAP, "target_blob_bytes": TARGET_BLOB_CAP,
                         "advertised_refs": 1, "prerequisites": 1, "header_bytes": 65536}
    source = ROOT / "source"
    assert run(["init", "--initial-branch=fixture-work", "--object-format=sha1", str(source)]).returncode == 0
    (source / "historical-only.bin").write_bytes(blob("historical-only", 131072))
    ancestor = commit(source, "historical ancestor outside shallow base", "2026-10-01T01:00:00+00:00")
    historical_oid = text(source, "rev-parse", "HEAD:historical-only.bin")
    (source / "historical-only.bin").unlink()
    expected_old = {
        "README.txt": b"Synthetic local-only Git cold-reconstruction fixture.\n",
        "reuse/reuse-a.bin": blob("reuse-a", 131072),
        "reuse/reuse-b.bin": blob("reuse-b", 262144),
        "reuse/reuse-c.bin": blob("reuse-c", 32768),
        "public/changed.bin": blob("changed-old", 196608),
        "public/deleted-from-base.bin": blob("deleted-from-base", 65536),
    }
    for rel, value in expected_old.items():
        f = source / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(value)
    old = commit(source, "complete shallow base", "2026-10-02T01:00:00+00:00")
    git(source, "branch", "base", old)
    middle_blob = blob("deleted-intermediate-public", 98304)
    (source / "public/intermediate-deleted.bin").write_bytes(middle_blob)
    changed = bytearray(expected_old["public/changed.bin"])
    changed[90000:90016] = b"TARGET-CHANGED-01"
    (source / "public/changed.bin").write_bytes(bytes(changed))
    middle = commit(source, "intermediate contains later-deleted public blob", "2026-10-03T01:00:00+00:00")
    middle_oid = text(source, "rev-parse", "HEAD:public/intermediate-deleted.bin")
    (source / "public/intermediate-deleted.bin").unlink()
    (source / "public/deleted-from-base.bin").unlink()
    new_blob = blob("new-target", 131072)
    (source / "public/new.bin").write_bytes(new_blob)
    target = commit(source, "frozen current target", "2026-10-04T01:00:00+00:00")
    git(source, "branch", "frozen-head", target)
    expected_target = dict(expected_old)
    del expected_target["public/deleted-from-base.bin"]
    expected_target["public/changed.bin"] = bytes(changed)
    expected_target["public/new.bin"] = new_blob
    rows = tree(source, target)
    for row in rows:
        value = expected_target[row["path"]]
        row.update(bytes=len(value), sha256=sha(value))
    old_rows = tree(source, old)
    RESULT["frozen"] = {"ancestor": ancestor, "base": old, "middle": middle, "target": target,
                        "target_ref": FROZEN_REF, "target_tree": text(source, "rev-parse", target + "^{tree}"),
                        "target_files": rows, "base_files": old_rows, "historical_only_oid": historical_oid,
                        "intermediate_deleted_public_oid": middle_oid,
                        "intermediate_deleted_public_sha256": sha(middle_blob)}
    check("fixture_has_three_reused_blobs_changed_new_and_deleted_history",
          len(rows) == 6 and all(expected_old[p] == expected_target[p] for p in expected_old if p.startswith("reuse/"))
          and expected_old["public/changed.bin"] != expected_target["public/changed.bin"]
          and "public/intermediate-deleted.bin" not in expected_target)
    bundle = ROOT / "increment.bundle"
    git(source, "-c", "pack.window=0", "-c", "pack.reuseDeltas=false", "-c", "pack.reuseObjects=false",
        "bundle", "create", "--version=2", str(bundle), f"{old}..{FROZEN_REF}")
    parsed = header(bundle, expected_base=old, expected_target=target)
    listed = git(source, "bundle", "list-heads", str(bundle)).stdout.decode().splitlines()
    check("increment_has_exact_single_base_and_exact_single_target_ref", listed == [target + " " + FROZEN_REF], parsed)
    RESULT["increment_bundle"] = parsed
    raw_bundle = bundle.read_bytes()
    inspector = init_bare("pack-inspector.git")
    idx = git(inspector, "index-pack", "--stdin", "--fix-thin", data=raw_bundle[parsed["pack_offset"]:]).stdout.decode().strip().split()[-1]
    verify_pack = git(inspector, "verify-pack", "-v", str(inspector / "objects" / "pack" / f"pack-{idx}.idx"))
    pack_rows = [line.split() for line in verify_pack.stdout.decode().splitlines() if len(line.split()) >= 5 and len(line.split()[0]) == 40]
    check("increment_pack_has_no_deltas_and_contains_deleted_intermediate_public_blob",
          len(pack_rows) == parsed["pack_objects"] and all(len(row) == 5 for row in pack_rows)
          and middle_oid in {row[0] for row in pack_rows}, {"pack_rows": pack_rows, "config_is_not_evidence_without_pack_check": True})
    reusable_oids = {row["oid"] for row in old_rows if row["path"].startswith("reuse/")}
    check("delta_free_increment_still_omits_reused_base_blobs",
          reusable_oids.isdisjoint({row[0] for row in pack_rows}), sorted(reusable_oids))
    RESULT["pack_objects"] = pack_rows
    filtered = ROOT / "file-filter-control.git"
    filter_cp = run(["clone", "--bare", "--no-hardlinks", "--depth=1", "--single-branch", "--branch", "base",
                     "--no-tags", "--filter=blob:none", source.as_uri(), str(filtered)])
    assert filter_cp.returncode == 0, filter_cp.stderr
    filter_objects = inventory(filtered)
    RESULT["file_filter_control"] = {"stderr": filter_cp.stderr.decode("utf-8", "replace"),
                                     "all_base_blobs_physically_present": all(row["oid"] in filter_objects for row in old_rows),
                                     "physical_objects": filter_objects,
                                     "not_used_as_metadata_only_fixture": True}
    check("file_filter_control_is_explicitly_classified",
          (b"filtering not recognized by server, ignoring" in filter_cp.stderr
           and all(row["oid"] in filter_objects for row in old_rows))
          or not all(row["oid"] in filter_objects for row in old_rows), RESULT["file_filter_control"])
    metadata = init_bare("metadata-only.git")
    tree_ids = {text(source, "rev-parse", old + "^{tree}")}
    for line in git(source, "ls-tree", "-r", "-t", old).stdout.decode().splitlines():
        fields = line.split()
        if fields[1] == "tree":
            tree_ids.add(fields[2])
    for oid, kind in [(old, "commit"), *[(oid, "tree") for oid in sorted(tree_ids)]]:
        value = git(source, "cat-file", kind, oid).stdout
        copied = git(metadata, "hash-object", "-w", "-t", kind, "--stdin", data=value).stdout.decode().strip()
        assert copied == oid
    (metadata / "shallow").write_text(old + "\n", encoding="ascii")
    git(metadata, "update-ref", "refs/heads/base", old)
    before_meta = inventory(metadata)
    check("manual_metadata_receiver_has_only_base_commit_and_trees_no_blobs",
          set(before_meta) == {old, *tree_ids} and all(x["type"] != "blob" for x in before_meta.values()), before_meta)
    RESULT["metadata_only_fixture"] = {"method": "manual copy of exact base commit/tree raw bytes plus explicit shallow boundary; NOT genuine partial-clone certification",
                                       "before_objects": before_meta, "missing_base_blobs": old_rows}
    base_image = ROOT / "full-base.git"
    cp = run(["clone", "--bare", "--no-hardlinks", "--depth=1", "--single-branch", "--branch", "base", "--no-tags",
              source.as_uri(), str(base_image)])
    assert cp.returncode == 0, cp.stderr
    git(base_image, "remote", "remove", "origin")
    base_objects = inventory(base_image)
    check("finite_full_shallow_base_is_complete_and_excludes_ancestor",
          text(base_image, "rev-parse", "--is-shallow-repository") == "true"
          and text(base_image, "rev-list", "--count", "--all") == "1"
          and ancestor not in base_objects and historical_oid not in base_objects
          and all(row["oid"] in base_objects for row in old_rows)
          and not (base_image / "objects/info/alternates").exists(), base_objects)
    git(base_image, "fsck", "--full", "--no-reflogs")
    base_tar = ROOT / "base.tar"
    RESULT["base_package"] = package(base_image, base_tar)
    check("full_shallow_base_package_meets_byte_and_object_bounds",
          base_tar.stat().st_size <= BASE_CAP and len(base_objects) <= OBJECT_CAP, RESULT["base_package"])
    cold_dir = ROOT / "cold-receiver"
    cold_dir.mkdir()
    with tarfile.open(base_tar, "r") as archive:
        for member in archive.getmembers():
            destination = within(cold_dir / member.name)
            assert cold_dir in destination.parents and (member.isfile() or member.isdir())
        archive.extractall(cold_dir, filter="data")
    positive = cold_dir / "base.git"
    extracted_files = {f.relative_to(positive).as_posix(): {"bytes": f.stat().st_size, "sha256": sha(f.read_bytes())}
                       for f in positive.rglob("*") if f.is_file()}
    extracted_dirs = sorted(f.relative_to(positive).as_posix() for f in positive.rglob("*") if f.is_dir())
    check("cold_extracted_base_matches_every_packaged_byte_and_empty_directory",
          extracted_files == RESULT["base_package"]["files"] and extracted_dirs == RESULT["base_package"]["directories"])
    # Freeze source paths away, disable all Git transports, and use only the two delivered artifacts from here on.
    source.rename(ROOT / "source-unavailable-offline")
    base_image.rename(ROOT / "full-base-unavailable-offline.git")
    filtered.rename(ROOT / "file-filter-control-unavailable-offline.git")
    OFFLINE = True
    RESULT["offline_gate"] = {"source_path_absent": not source.exists(), "base_image_path_absent": not base_image.exists(),
                              "all_git_transports_disabled_after_gate": True,
                              "os_network_firewall_claim": False,
                              "no_alternates_no_promisor_no_remote_on_receiver": True}
    meta_verify = git(metadata, "bundle", "verify", str(bundle), ok=False)
    meta_unbundle = git(metadata, "bundle", "unbundle", str(bundle), ok=False)
    meta_objects = inventory(metadata)
    if target in meta_objects:
        git(metadata, "update-ref", FROZEN_REF, target)
    missing = []
    present = []
    for row in rows:
        cp = git(metadata, "cat-file", "blob", row["oid"], ok=False)
        if cp.returncode != 0:
            missing.append(row)
        else:
            assert len(cp.stdout) == row["bytes"] and sha(cp.stdout) == row["sha256"]
            present.append(row)
    meta_fsck = git(metadata, "fsck", "--full", "--no-reflogs", ok=False)
    RESULT["metadata_only_result"] = {"bundle_verify_returncode": meta_verify.returncode,
                                      "unbundle_returncode": meta_unbundle.returncode,
                                      "target_blob_missing": missing, "target_blob_present": present,
                                      "fsck_returncode": meta_fsck.returncode,
                                      "publication_gate": "REJECT_INCOMPLETE_BASE_BLOBS"}
    check("metadata_receiver_is_explicitly_rejected_by_all_target_blob_gate",
          reusable_oids <= {row["oid"] for row in missing} and len(missing) > 0, RESULT["metadata_only_result"])
    check("metadata_receiver_is_explicitly_rejected_by_full_fsck", meta_fsck.returncode != 0, meta_fsck.stderr.decode("utf-8", "replace"))
    # Native Git bundle verification is a prerequisite-commit check, not proof of all base blobs.
    check("metadata_bundle_verification_and_import_do_not_prove_closure",
          meta_verify.returncode == 0 and meta_unbundle.returncode == 0
          and target in meta_objects and len(missing) > 0, RESULT["metadata_only_result"])
    git(positive, "bundle", "verify", str(bundle))
    git(positive, "bundle", "unbundle", str(bundle))
    git(positive, "update-ref", FROZEN_REF, target)
    positive_rows = tree(positive, FROZEN_REF)
    check("offline_positive_target_exact_tree_paths_modes_oids",
          positive_rows == [{k: row[k] for k in ("path", "mode", "type", "oid")} for row in rows]
          and text(positive, "rev-parse", FROZEN_REF + "^{tree}") == RESULT["frozen"]["target_tree"])
    total = 0
    verified = []
    for row in rows:
        cp = git(positive, "cat-file", "blob", row["oid"])
        assert len(cp.stdout) == row["bytes"] and sha(cp.stdout) == row["sha256"]
        total += len(cp.stdout)
        verified.append({**row, "verified_sha256": sha(cp.stdout)})
    check("offline_positive_every_current_blob_exact_and_bounded", total <= TARGET_BLOB_CAP, {"files": verified, "total_blob_bytes": total})
    fsck = git(positive, "fsck", "--full", "--no-reflogs", ok=False)
    check("offline_positive_full_fsck_clean", fsck.returncode == 0, {"stdout": fsck.stdout.decode(), "stderr": fsck.stderr.decode()})
    current = ROOT / "cold-current-tree"
    current.mkdir()
    cp = run(["--git-dir", str(positive), "--work-tree", str(current), "checkout", "--force", "frozen-head"])
    assert cp.returncode == 0, cp.stderr
    current_files = {f.relative_to(current).as_posix(): sha(f.read_bytes()) for f in current.rglob("*") if f.is_file()}
    check("offline_checkout_all_current_file_bytes_exact", current_files == {r["path"]: r["sha256"] for r in rows}, current_files)
    RESULT["positive_result"] = {"state": "PASS_EXACT_CURRENT_TREE_AND_BLOBS", "target": target,
                                 "target_tree": RESULT["frozen"]["target_tree"], "verified_files": verified,
                                 "blob_bytes": total, "fsck_returncode": fsck.returncode,
                                 "physical_objects": inventory(positive), "shallow_boundary": (positive / "shallow").read_text().strip(),
                                 "full_ancestor_history_claim": False}
    raw_header, raw_pack = raw_bundle.split(b"\n\n", 1)
    base_line = next(line for line in raw_header.splitlines() if line.startswith(b"-"))
    target_line = (target + " " + FROZEN_REF).encode()
    expect_header_reject("duplicate_target_ref_rejected", raw_header + b"\n" + target_line + b"\n\n" + raw_pack,
                         old, target, "EXACT_UNIQUE_TARGET_REF")
    expect_header_reject("duplicate_prerequisite_rejected", raw_header + b"\n" + base_line + b"\n\n" + raw_pack,
                         old, target, "EXACT_UNIQUE_PREREQUISITE")
    expect_header_reject("wrong_frozen_target_rejected", raw_bundle, old, middle, "EXACT_UNIQUE_TARGET_REF")
    expect_header_reject("wrong_base_identity_rejected", raw_bundle, ancestor, target, "EXACT_UNIQUE_PREREQUISITE")
    expect_header_reject("bundle_byte_bound_rejected", raw_bundle, old, target, "BUNDLE_BYTE_BOUND", len(raw_bundle) - 1)
    bad_count_pack = raw_pack[:8] + (OBJECT_CAP + 1).to_bytes(4, "big") + raw_pack[12:]
    expect_header_reject("pack_object_count_bound_rejected", raw_header + b"\n\n" + bad_count_pack,
                         old, target, "PACK_OBJECT_BOUND")
    bad_base = init_bare("wrong-prerequisite-receiver.git")
    wrong_verify = git(bad_base, "bundle", "verify", str(bundle), ok=False)
    wrong_unbundle = git(bad_base, "bundle", "unbundle", str(bundle), ok=False)
    check("native_git_rejects_absent_base_commit", wrong_verify.returncode != 0 and wrong_unbundle.returncode != 0,
          {"verify_returncode": wrong_verify.returncode, "unbundle_returncode": wrong_unbundle.returncode})
    # Test the positive input's provenance/size gate separately, before any promotion could occur.
    changed_base = dict(RESULT["base_package"])
    changed_base["sha256"] = "0" * 64
    check("wrong_base_package_sha_rejected_before_import", sha(base_tar.read_bytes()) != changed_base["sha256"])
    check("no_remotes_alternates_or_promisor_on_positive_receiver",
          text(positive, "remote") == "" and not (positive / "objects/info/alternates").exists()
          and git(positive, "config", "--get-regexp", "promisor|partialclone", ok=False).returncode == 1)
    RESULT["state"] = "PASS_FIXTURE_METADATA_SCHEME_DISPROVED_FULL_SHALLOW_BASE_POSITIVE"


def finalize(error: str | None, elapsed: float) -> None:
    RESULT["elapsed_seconds"] = elapsed
    RESULT["command_count"] = len(COMMANDS)
    RESULT["checks"] = len(CHECKS)
    RESULT["check_failures"] = sum(not c["passed"] for c in CHECKS)
    RESULT["execution_error"] = error
    if error:
        RESULT["state"] = "ERROR_EVIDENCE_RETAINED"
    save(ROOT / "result.json", RESULT)
    suite = ET.Element("testsuite", name="local_bounded_git_fixture", tests=str(len(CHECKS) + bool(error)),
                       failures=str(sum(not c["passed"] for c in CHECKS)), errors=str(int(bool(error))),
                       skipped="0", time=f"{elapsed:.6f}")
    for item in CHECKS:
        case = ET.SubElement(suite, "testcase", name=item["name"], classname="isolated_git_protocol")
        if not item["passed"]:
            ET.SubElement(case, "failure", message="fixture acceptance check failed").text = json.dumps(item["details"], ensure_ascii=False)
        ET.SubElement(case, "system-out").text = json.dumps(item["details"], ensure_ascii=False)
    if error:
        ET.SubElement(ET.SubElement(suite, "testcase", name="execution", classname="isolated_git_protocol"), "error").text = error
    ET.ElementTree(suite).write(ROOT / "results.xml", encoding="utf-8", xml_declaration=True)
    files = {}
    for f in sorted(ROOT.rglob("*")):
        if f.is_file() and ".git" not in f.parts and not any(p.endswith(".git") for p in f.parts) and f.name != "manifest.json":
            raw = f.read_bytes()
            files[f.relative_to(ROOT).as_posix()] = {"bytes": len(raw), "sha256": sha(raw)}
    save(ROOT / "manifest.json", {"schema": "qtrade.local-bounded-git-evidence.v1", "files": files,
                                  "production_access": False, "network_transport": False, "push": False})
    print(json.dumps({"root": str(ROOT), "state": RESULT["state"], "checks": len(CHECKS),
                      "failures": RESULT["check_failures"], "commands": len(COMMANDS),
                      "error": error, "seconds": elapsed}, ensure_ascii=False))


if __name__ == "__main__":
    assert len(sys.argv) == 1, "Use a new unique directory for each execution; no resume/replay over evidence."
    (ROOT / "commands").mkdir()
    (ROOT / "malformed").mkdir()
    (ROOT / "no-hooks").mkdir()
    (ROOT / "empty.gitconfig").write_bytes(b"")
    started = time.monotonic()
    execution_error = None
    try:
        run_experiment()
    except BaseException:
        execution_error = traceback.format_exc()
    finally:
        finalize(execution_error, time.monotonic() - started)
    if execution_error or RESULT["check_failures"]:
        raise SystemExit(1)
