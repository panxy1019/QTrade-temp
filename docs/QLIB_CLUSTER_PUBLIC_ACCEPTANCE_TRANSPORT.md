# Cluster public acceptance transport

On 2026-10-27, the cluster's direct Vercel HTTPS attempt failed with OS error 99.
No HTTPS proxy was configured there. The local workstation's existing,
credential-free loopback system proxy could reach the production site.

A one-shot SSH reverse-forward probe successfully ran the existing c5c280f
public-delivery GET implementation on the cluster. It requested an ephemeral
`127.0.0.1` listener and inspected `/proc/net/tcp` and `/proc/net/tcp6` to reject
any non-loopback bind. The local relay accepted only HTTP CONNECT for
`q-trade-temp.vercel.app:443`; other destinations and credential headers were
rejected. TLS validation remained enabled inside the cluster HTTPS client.

The probe fetched deployment, catalog, data versions, signals and catalog again.
Both current catalogs validated and retained the same identity; production
revision was `34c5118bfd73c0aa62c869f109cdf92719e5cb62`. The remote immutable report
contains actual HTTP bytes and hashes. Five relay connections succeeded, with
zero rejected connections. The forward and SSH client were closed in `finally`.
No global proxy, Supervisor service, model, data head, or book was changed.

Evidence: `evidence/qlib-cluster-public-tunnel-c5c280f.json`. The complete remote
capture location is recorded in that receipt. It observes the current complete
day, October 26; it is not the October 27 private-to-public transition acceptance.

## Reproduction and scope

The exact one-shot operator script is retained at
`E:/QTrade-temp/probe-cluster-public-tunnel.py`. Run it with the workstation's
existing credential-free loopback HTTP proxy configured in system settings:

```powershell
python E:/QTrade-temp/probe-cluster-public-tunnel.py
```

SSH credentials are entered through an un-echoed prompt, remain in memory, and
are not saved. The script verifies the pre-existing SSH host key. It contains no
password or proxy address. Captures are immutable: choose fresh evidence paths
inside the script for another observation rather than overwriting this receipt.
The initial listener-inspection attempt failed because a non-login SSH shell
did not resolve `python`; using the explicit cluster virtualenv interpreter
resolved that issue. The first forward was also closed on failure.

For actual C acceptance, use the same temporary loopback transport with
`HTTPS_PROXY` and `https_proxy` scoped to the one audit command, then invoke the
paired production flags documented in `QLIB_PUBLIC_DELIVERY_ACCEPTANCE.md`.
Private raw archive checks must still succeed first. A successful transport probe
alone cannot set `production_session_transition_verified=true`.

This transport requires the local workstation and its SSH connection during
acceptance. It does not provide unattended cluster egress. The data publisher
continues using its existing independent route and was not reconfigured.
No broad or permanent proxy service has been introduced. Whole STEP1 and browser
acceptance remain false; `LIVE_TRADING_ENABLED=false`.
