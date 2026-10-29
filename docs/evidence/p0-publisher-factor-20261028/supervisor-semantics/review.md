# Same Go Supervisor isolated semantic evidence, 2026-10-28

PASS for the separately reviewed named-program RPC fixture, not a production handover.
Binary SHA256 c0e12d2e498d00a2285161372cbb0a46b795b94e9a42511351eba7980f408569;
the explicit isolated-config `version` command reports v0.6.8. Two new managers had
independent Unix sockets, no owned INET sockets, and two unrelated sentinel groups each.

Normal exit0 restarts exactly once: controlled516820→516839. Named RPC
`supervisor.signalProcess('controlled','TERM')` was invoked once after native RUNNING,
PID/start_ticks/PPID/cwd/command hash checks and a flushed/fsynced intent. Old516855
and its same-PGID helper remain live/native RUNNING at110.660s. The child handler only
sets Event; actual drain is112.1004277s. It then cleans its new-session worker and
same-PGID helper, exits0, and existing autorestart starts516992 with fixture wrapperv2.
Both sentinel identities remain unchanged. No stopProcess call occurs in the semantic
window; separate final cleanup uses only each new manager's private shutdown RPC.

All fixture processes are cleaned: remaining=[], cleanup errors=[], SIGKILL0. Production
publisher294214 has identical start_ticks1012537254, PPID, cwd and command hash before/
after. No production signal/control/config/wrapper operation, NFS input access or
production environment/configuration/credential capture was performed.

The original pidfd fixture FAILED: os.pidfd_open exists in Python but returns ENOSYS38.
Its first child fails cleaning its owned worker; inherited pipes may explain native
RPC retaining Running for a no-longer-present PID (inference, not a certified root cause).
It proves native status alone is insufficient. The first cleanup introspection attempt
also failed with HTTP500 on system.listMethods before any shutdown; v2 cleanup succeeds
through only the new manager's Unix shutdown and one recorded orphan-fixture worker TERM.
The PID/start/cwd/argv checks for that named worker and production identity equality are
retained in a sanitized cleanup receipt. Raw receipts with full production argv are omitted.
The still earlier operator.py import-shadow failure is separately preserved, source
renamed .py.txt to avoid repeating the stdlib-shadowing fault.

There are49 original regular files in the RPC capture: internal45 + summary + local SSH
stdout/stderr + transport receipt. This whitelist packages the45 internal exact SHA-bound
artifacts and summary; the duplicate local transport files remain outside the pack, with
their receipt hash/exitcode recorded in payload-manifest.json. The failed-pidfd and import
failures are separate groups, never relabelled as passing runs. Only explicitly selected
files are included; no credential helper, production private config or environment dump.

The upstream v0.6.8 reference handler routes SignalProcess through Signal(sig,false),
then a positive-PID syscall; it does not call Stop. Its success boolean ignores delivery
errors, and the RPC has no expectedPID/CAS parameter. This fixture's real child event,
exit0 logs, /proc identities and new RUNNING evidence supply acceptance; the reply alone
does not. The upstream source is a reference, not an attestation of the binary build:
[tagged handler](https://github.com/ochinchina/supervisord/blob/v0.6.8/supervisor.go#L357),
[process signal](https://github.com/ochinchina/supervisord/blob/v0.6.8/process/process.go#L644),
[signal syscall](https://github.com/ochinchina/supervisord/blob/v0.6.8/signals/signal.go#L38).

Production pidfd handover remains blocked by ENOSYS. The named-RPC interface has a
check-to-call replacement race and is not atomic PID/CAS. The fixture does not certify
the real wrapper/release, current cold/warm performance, complete oracle/lineage,
ordinary export/Git/public delivery, real-close/PAPER or deployment approval.
