# v6 invocation API — preparation only

Only attempt:
`/root/qtrade-local/operator-evidence/publisher-handover-fba51aad-20261028T113923Z`.
The failed113853 preparation remains retained and is never an apply retry target.

The62 original roles belong at `<attempt>/gate-evidence/<role>.raw`. Keep the raw
mount/report evidence private. All v6 Python files must be siblings under the
uploaded tool directory because their exact-hash imports use `with_name()`.

## Actual read-only file-backed check

Assuming tools are staged at `<attempt>/tools/`, write the exact prepared JSONs
under new names `manifest.v6.json` and `gates.v6.json`, then:

```sh
env LIVE_TRADING_ENABLED=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH= \
  /root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -B \
  /root/qtrade-local/operator-evidence/publisher-handover-fba51aad-20261028T113923Z/tools/publisher_handover_v6_preparation_20261028.py \
  --check \
  --manifest /root/qtrade-local/operator-evidence/publisher-handover-fba51aad-20261028T113923Z/manifest.v6.json \
  --gates /root/qtrade-local/operator-evidence/publisher-handover-fba51aad-20261028T113923Z/gates.v6.json
```

This reads only fixed gate evidence names and public tool source. It does not
read the private wrapper, create phase files, install anything or signal a
process. The operator CLI has no apply option.

Optional bridge check, using the same exact operator:

```sh
python -B publisher_handover_bridge_v6_20261028.py --check \
  --operator publisher_handover_v6_preparation_20261028.py \
  --manifest /absolute/path/manifest.v6.json --gates /absolute/path/gates.v6.json
```

## Read-only adapter and preparation wiring

```python
socket_identity = {key: fresh_foundation["socket"][key]
                   for key in ("device", "inode", "uid")}
rpc = bridge.UnixPublisherRPC(socket_identity)  # No TERM permission.
adapter = operator.PublisherOnlyAdapter(
    manifest,
    bridge.readonly_reader(),
    rpc,
    running_probe=lambda pid: bridge.runtime_probe(
        operator, pid, manifest["new_revision"], manifest["runtime_files"]),
    mutation_stage_approved=False,
    actual_gate_bundle=gates,
)
prepared = bridge.prepare_snapshot(operator, manifest, gates, adapter)
```

Do not call `advance()` merely to prepare: it creates durable intent records.
Use the bridge preparation function above, which creates no phase journal.
Pass only device/inode/uid to the RPC identity constructor; extra socket fields
are not accepted.

## Later approved mutation API shape — not invoked here

The existing API is exactly:

```python
operator.advance(manifest, gates, adapter, journal,
                 backup=Path(manifest["attempt_root"]) / "private/wrapper.old.py")
```

For a separately executed approved mutation path, the adapter must retain the
same full `actual_gate_bundle=gates`, have `mutation_stage_approved=True`, and
use one `UnixPublisherRPC(socket_identity, allow_one_term=True)` instance. The
`JsonPhaseJournal` root must equal `Path(manifest["attempt_root"])` exactly.
The guard reloads and revalidates the same real gate bundle before every
wrapper/TERM mutation; the source/owner/config/control guards still run.

`advance()` may be called again only to continue its existing journal state.
After TERM_INTENT or ambiguous RPC/install, it never retries the mutation or
redirects TERM to another PID. Do not start a new attempt, stop/kill a process,
restore old controls or change runtime budgets to bypass that outcome.
The named supervisor method still has no atomic expected-PID CAS.

The v6 read-only positive check is not apply authorization. No example in this
document was executed against a production mutation path.
