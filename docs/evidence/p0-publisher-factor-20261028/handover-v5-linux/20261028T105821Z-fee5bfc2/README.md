# Isolated Linux handover-tool validation

Result: **102 passed, 0 failures, 0 errors, 0 skipped** on Linux Python 3.14.6.
Test subprocess elapsed 0.608100 seconds; pytest reports 0.31 seconds.
Actual UTC run: 2026-10-28 10:58:23.639889 through 10:58:24.250840
(Shanghai 18:58:23.639889 through 18:58:24.250840).

This is synthetic operator/bridge/schema validation, not actual publisher
handover, real gate acceptance, private-wrapper compatibility or production
delivery. No private wrapper body or authentication configuration was read or
retained. No real RPC, production service control, wrapper install or TERM was
performed. Existing production services and source files were not changed.

## Fixed package and execution

Remote unique retained directory:
`/root/qtrade-local/operator-evidence/handover-v5-linux-20261028T105821Z-fee5bfc2`.

The existing QTrade venv Python ran with `-B`, `LIVE_TRADING_ENABLED=false`,
`PYTHONDONTWRITEBYTECODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, empty test
`PYTHONPATH`, pytest `-c /dev/null -p no:cacheprovider --noconftest
--import-mode=importlib`. No pytest cache or Python bytecode was created.
The launcher imported only the previously authorized `connect()` callable;
its source, configuration and credentials are excluded from the package.

`package.sha256.json` fixes all 13 uploaded source files. `result.json` records
identical per-file SHA256 before and after execution, the precise command,
resource reserves and output hashes. Initial local free space was
22,619,246,592 bytes and data-volume free space was 285,396,172,800 bytes;
the required local 2 GiB/data 100 GiB reserves were also checked after the run.

- v5 operator SHA256:
  `980d288602f35160f9904a164893b40ef147b2ee882e2a91a0f973043824ebb4`.
- v5 bridge SHA256:
  `f60b83490b7ef6ba6d46b4ba728120a847e38b6a40aca0ff136e2f8409ddb1fb`.
- Multi-mode gate SHA256:
  `4f05e880c19096ea78d2a8b392d463348d9b955d39f8a7a8b75ee5dd344d2954`.
- Package manifest SHA256:
  `78bc4a0e5f5fa8d28ba94a8b479ddb131fa2ed8fcecf4323803a6b6d6ceafe53`.
- Result SHA256:
  `ae2fb82988b6b3f3f88591aa61a88bf58f194b1376da93776f15b05f0dd266da`.

The synthetic suite consists of 46 v4 transformation/RPC/lifecycle cases,
22 v5 exact-old-hash/expression-preservation cases and 34 separate mode-gate
boundary cases. Source copies and launcher/runner are retained under `source/`;
the XML and stdout/stderr are exact downloaded bytes. No older evidence was
overwritten.

## Reproduction

In a new isolated directory containing the fixed source files, use the same
existing venv and environment above, then run:

```sh
python -B -m pytest -q -c /dev/null -p no:cacheprovider --noconftest --import-mode=importlib test_publisher_handover_v4_20261028.py test_publisher_handover_v5_20261028.py test_publisher_incremental_gates_v4_20261028.py --junitxml=pytest.xml
```

Use a new result directory on every run. The remote runner additionally enforces
the unique operator-evidence parent, fixed interpreter, free-space reserves,
source hashes, exact 102/0-skip result and absence of caches. Production export
remains 45 seconds; the 120-second bootstrap mode is diagnostic only. The mode
gate is not wired to a production mutation path by this validation.
