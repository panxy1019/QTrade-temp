"""Synthetic public JSON only; every remote is an isolated local bare fixture."""
import dataclasses
import hashlib
import importlib.util
import json
import os
import random
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('object_stage_nonproduction', HERE / 'publisher_object_stage_nonproduction_20261028.py')
h = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = h
spec.loader.exec_module(h)
POSIX = pytest.mark.skipif(os.name != 'posix', reason='actual protocol requires POSIX owned session cleanup; Linux must run all')


def run(repo, args, data=None, ok=True):
    result = subprocess.run(['git', '-C', str(repo), *args], input=data, capture_output=True,
        env=h.environment(), timeout=30)
    if ok:
        assert result.returncode == 0, result.stderr
    return result.stdout if ok else result


def commit(repo, label):
    run(repo, ['add', '--all'])
    run(repo, ['commit', '-m', label])
    return run(repo, ['rev-parse', 'HEAD']).decode().strip()


def certificate(repo, target):
    files = {}
    for line in run(repo, ['ls-tree', '-rz', target]).split(b'\0'):
        if line:
            metadata, name = line.split(b'\t', 1)
            oid = metadata.decode().split()[2]
            files[name.decode()] = hashlib.sha256(run(repo, ['cat-file', 'blob', oid])).hexdigest()
    return {'schema_version': h.SCHEMA, 'source_revision': 'a' * 40,
        'source_inventory_sha256': 'b' * 64, 'target_commit': target, 'public_files': files}


@pytest.fixture
def fixture(tmp_path):
    source = tmp_path / 'ordinary'
    source.mkdir(); run(source, ['init', '--template=', '-b', 'main'])
    (source / 'latest.json').write_bytes(b'{"fixture":"old-public"}\n')
    old = commit(source, 'fixture old public main')
    remote = tmp_path / 'remote.git'; remote.mkdir(); run(remote, ['init', '--bare', '--template='])
    run(remote, ['config', 'receive.unpackLimit', '0'])
    (remote / 'qtrade-nonproduction-object-stage.json').write_bytes(h.canonical({
        'schema_version': 'qtrade.local-bare-fixture.v1', 'production': False}))
    run(source, ['push', str(remote), 'HEAD:refs/heads/main'])
    rng = random.Random(17)
    for _ in range(4):
        raw = h.canonical({'schema': 'fixture.public.v1', 'payload': rng.randbytes(2048).hex(),
            'live_trading_enabled': False, 'external_order_transmission': False})
        p = source / 'unified-market/objects' / (h.sha(raw) + '.json')
        p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
    (source / 'unified-market.json').write_bytes(b'{"fixture":"complete-new-index"}\n')
    new = commit(source, 'fixture complete new public index')
    isolated = h.create_isolated_repository(tmp_path / 'isolated.git', (source / '.git/objects').resolve())
    cert = certificate(source, new)
    target = h.CertifiedTarget(isolated, cert)
    transport = h.LocalBareTransport(remote.resolve(), tmp_path.resolve())
    return source, remote, isolated, old, new, cert, target, transport


def test_exact_pack_is_prebuilt_bounded_and_has_no_target_commit(fixture):
    _, _, _, _, new, _, target, transport = fixture
    plan = h.plan_batch(target, target.blobs, transport.refs(), 1)
    assert len(plan.pack) <= h.MAX_PACK and h.sha(plan.pack) == plan.pack_sha256
    assert new not in plan.objects and plan.commit in plan.objects
    assert plan.ref.startswith('refs/heads/qtrade-object-stage/')


def test_source_refs_index_and_objects_are_not_changed_by_planning(fixture):
    source, _, _, _, _, _, target, transport = fixture
    paths = list((source / '.git').rglob('*'))
    before = {p.relative_to(source).as_posix(): h.sha(p.read_bytes()) for p in paths if p.is_file()}
    h.plan_batch(target, target.blobs, transport.refs(), 1)
    after = {p.relative_to(source).as_posix(): h.sha(p.read_bytes()) for p in paths if p.is_file()}
    assert after == before


def test_uncertified_source_path_is_rejected(fixture):
    source, _, isolated, _, _, cert, _, _ = fixture
    (source / 'src').mkdir(); (source / 'src/private.py').write_bytes(b'PRIVATE_FIXTURE_NOT_A_SECRET=1\n')
    new = commit(source, 'fixture disallowed source')
    cert = certificate(source, new)
    with pytest.raises(ValueError, match='PUBLIC_PATH_ALLOWLIST'):
        h.CertifiedTarget(isolated, cert)


def test_wrong_content_certificate_rejected(fixture):
    _, _, isolated, _, _, cert, _, _ = fixture
    cert['public_files']['latest.json'] = '0' * 64
    with pytest.raises(ValueError, match='PUBLIC_BYTES_MISMATCH'):
        h.CertifiedTarget(isolated, cert)


def test_private_json_field_and_live_authority_rejected():
    for data in ({'password': 'synthetic-fixture-value'}, {'live_trading_enabled': True},
                 {'nested': {'external_order_transmission': True}}):
        with pytest.raises(ValueError):
            h.public_json(data)


def test_unknown_fields_and_incomplete_target_certificate_rejected(fixture):
    _, _, isolated, _, _, cert, _, _ = fixture
    unknown = {**cert, 'self_claimed_approved': True}
    with pytest.raises(ValueError, match='CERTIFICATE_KEYS'):
        h.CertifiedTarget(isolated, unknown)
    cert['public_files'].pop('latest.json')
    with pytest.raises(ValueError, match='TARGET_PRIVATE_OR_SPECIAL_ENTRY'):
        h.CertifiedTarget(isolated, cert)


def test_oversized_exact_pack_rejected_before_transport(fixture):
    _, _, _, _, _, _, target, transport = fixture
    with pytest.raises(ValueError, match='EXACT_PACK_BYTES_EXCEEDED'):
        h.plan_batch(target, target.blobs, transport.refs(), 1, maximum_pack_bytes=256)


def test_unreachable_object_and_out_of_namespace_parameters_rejected(fixture):
    _, _, _, old, _, _, target, transport = fixture
    with pytest.raises(ValueError, match='CERTIFIED_TARGET_OBJECTS_ONLY'):
        h.plan_batch(target, [old], transport.refs(), 1)
    for sequence in (0, 10000, True):
        with pytest.raises(ValueError, match='SEQUENCE_BOUND'):
            h.plan_batch(target, target.blobs, transport.refs(), sequence)


def test_url_and_unsentinel_endpoint_rejected(tmp_path):
    with pytest.raises(ValueError, match='LOCAL_FIXTURE_ENDPOINT'):
        h.LocalBareTransport('ssh://fixture.invalid/repo', tmp_path.resolve())
    endpoint = tmp_path / 'unmarked.git'; endpoint.mkdir()
    with pytest.raises(ValueError, match='FIXTURE_SENTINEL_REQUIRED'):
        h.LocalBareTransport(endpoint.resolve(), tmp_path.resolve())


def test_json_duplicate_and_nan_rejected():
    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
        with pytest.raises(ValueError):
            h.checked_json(raw)


@POSIX
def test_actual_aux_protocol_keeps_main_complete_and_final_ordinary_push_excludes_blobs(fixture):
    source, remote, _, old, new, _, target, transport = fixture
    blob_ids = sorted(target.blobs)
    for sequence, selected in enumerate((blob_ids[:3], blob_ids[3:]), 1):
        plan = h.plan_batch(target, selected, transport.refs(), sequence)
        receipt = transport.send(target, plan)
        assert receipt['state'] == 'STAGED_LOCAL_FIXTURE_ONLY' and receipt['main_unchanged']
        assert receipt['pack_bytes'] == len(plan.pack) <= h.MAX_PACK
        assert run(remote, ['rev-parse', 'refs/heads/main']).decode().strip() == old
        assert run(remote, ['show', 'refs/heads/main:unified-market.json'], ok=False).returncode != 0
        names = run(remote, ['ls-tree', '-r', '--name-only', plan.commit]).decode().splitlines()
        assert names and all(x.startswith('public-') for x in names)
        assert run(remote, ['fsck', '--strict', '--no-reflogs'], ok=False).returncode == 0
    packs_before = set((remote / 'objects/pack').glob('*.idx'))
    # This is the ordinary publisher command shape, in a local fixture only.
    run(source, ['-c', 'pack.useSparse=false', 'push', str(remote), 'HEAD:refs/heads/main'])
    assert run(remote, ['rev-parse', 'refs/heads/main']).decode().strip() == new
    assert run(remote, ['show', 'refs/heads/main:unified-market.json']) == b'{"fixture":"complete-new-index"}\n'
    final_indices = set((remote / 'objects/pack').glob('*.idx')) - packs_before
    assert final_indices
    transmitted = set()
    for index in final_indices:
        for line in run(remote, ['verify-pack', '-v', str(index)]).splitlines():
            if len(line.split()) >= 5 and h.identity(line.split()[0].decode()):
                transmitted.add(line.split()[0].decode())
    assert transmitted and not (transmitted & target.blobs)
    assert run(remote, ['fsck', '--strict', '--no-reflogs'], ok=False).returncode == 0


@POSIX
def test_original_complete_tree_can_stage_after_its_blobs(fixture):
    _, remote, _, old, _, _, target, transport = fixture
    first = h.plan_batch(target, target.blobs, transport.refs(), 1)
    transport.send(target, first)
    second = h.plan_batch(target, target.trees, transport.refs(), 2)
    assert not (set(second.objects) & target.blobs)
    transport.send(target, second)
    assert run(remote, ['rev-parse', 'refs/heads/main']).decode().strip() == old
    assert run(remote, ['fsck', '--strict', '--no-reflogs'], ok=False).returncode == 0


@POSIX
def test_interrupted_pack_never_claims_success_or_exposes_partial_ref(fixture):
    _, remote, _, old, _, _, target, transport = fixture
    plan = h.plan_batch(target, target.blobs, transport.refs(), 1)
    with pytest.raises(ValueError, match='ACK_NOT_CONFIRMED_NO_RETRY'):
        transport.send(target, plan, truncate_pack_at=len(plan.pack) // 2)
    assert plan.ref not in transport.refs()
    assert transport.refs()['refs/heads/main'] == old
    assert run(remote, ['fsck', '--strict', '--no-reflogs'], ok=False).returncode == 0


@POSIX
def test_ack_loss_is_uncertain_and_same_aux_ref_is_never_retried(fixture):
    _, _, _, _, _, _, target, transport = fixture
    plan = h.plan_batch(target, target.blobs, transport.refs(), 1)
    with pytest.raises(ValueError, match='ACK_NOT_CONFIRMED_NO_RETRY'):
        transport.send(target, plan, suppress_ack=True)
    assert transport.refs()[plan.ref] == plan.commit
    with pytest.raises(ValueError, match='ADVERTISEMENT_CHANGED_NO_RETRY'):
        transport.send(target, plan)


@POSIX
def test_non_fast_forward_or_existing_aux_ref_is_refused_without_force(fixture):
    _, remote, _, old, _, _, target, transport = fixture
    plan = h.plan_batch(target, target.blobs, transport.refs(), 1)
    run(remote, ['update-ref', plan.ref, old])  # Only the isolated adversarial fixture.
    with pytest.raises(ValueError, match='ADVERTISEMENT_CHANGED_NO_RETRY'):
        transport.send(target, plan)
    assert transport.refs()[plan.ref] == old


@POSIX
def test_forged_main_destination_or_changed_pack_is_rejected_before_io(fixture):
    _, _, _, _, _, _, target, transport = fixture
    plan = h.plan_batch(target, target.blobs, transport.refs(), 1)
    for change in ({'ref': 'refs/heads/main'}, {'pack': plan.pack + b'x'},
                   {'selected': (plan.commit,)}, {'target_commit': '0' * 40}):
        with pytest.raises(ValueError):
            transport.send(target, dataclasses.replace(plan, **change))
