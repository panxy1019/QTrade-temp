# Q — A-share and ETF quantitative trading platform

Q is a safety-first personal quantitative trading platform for Shanghai and
Shenzhen A-shares, exchange-traded funds, and index benchmarks.

The repository contains a runnable local PAPER/SHADOW platform checkpoint with
versioned data, research, event-driven backtesting, portfolio construction,
risk/OMS/accounting, recovery, monitoring, and a control plane. Real-money
trading is not enabled.

## Safety invariant

`LIVE_TRADING_ENABLED` is false by default and cannot be enabled by the current
software. Strategies never call a broker directly: every order follows
portfolio → risk → OMS → execution → gateway.

## Development

Requires Python 3.11 or newer. From the repository root:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python scripts/run_control_plane.py
```

Build and verify the Simplified-Chinese React dashboard separately:

```powershell
npm --prefix web install
npm --prefix web test -- --run
npm --prefix web run lint
npm --prefix web run build
```

The local dashboard is then available at `http://127.0.0.1:8000`. It boots
fail-closed (gateway disconnected and reconciliation unknown); the page is an
operational status surface, not authorization for external order transmission.
Its overview presents the workflow as ordered gates: verified data and factor
definition, factor effectiveness/correlation/robustness, strategy construction,
causal backtest, PAPER/SHADOW, then separately authorized LIVE. Diagnostic pages
remain navigable after a failure, but the overview keeps every downstream stage
blocked and never treats visibility as promotion. The six-stage path switches
to a vertical layout below the large desktop breakpoint so stage labels and
gate reasons remain readable without horizontal scrolling.

Control endpoints are disabled unless `QTRADE_CONTROL_TOKEN` is supplied in the
process environment. Send it as `Authorization: Bearer <token>`. Read endpoints
do not accept a caller-provided role escalation. Bind the development server to
loopback unless a separately reviewed TLS/authentication proxy is deployed.

Automatic PAPER end-of-day reports are available only when the local process is
started with trusted runtime identities and an explicit, operator-verified SSE
open-session horizon. The HTTP API never accepts data or strategy versions for
report generation. For example, after those identities have been verified by
the PAPER operator:

```bash
python scripts/run_control_plane.py \
  --report-data-version authorized-paper-session-v1 \
  --report-strategy-version minute-risk@1.0.0 \
  --report-open-day 2026-08-26 \
  --report-cutoff 15:30
```

The worker polls locally and creates at most one immutable report per configured
open day after the Asia/Shanghai cutoff. Missing calendar coverage, an existing
report with a different identity, or write/hash verification failure stops that
day's generation. `/reporting-status` exposes this state read-only; the public
Vercel deployment remains `UNCONFIGURED` and cannot write reports.

After a restart the PAPER runtime is HALTED. `POST /system/recover` (authenticated)
must complete local reconciliation and issue a fresh readiness attestation before
`POST /system/start` can succeed. Pending/failed fills or uncertain submissions
make recovery fail closed.

Run the complete synthetic provenance workflow with:

```powershell
python scripts/run_baseline_workflow.py
```

It writes ignored runtime artifacts under `runtime/sample`: checksum-verified
Parquet bars and an immutable experiment record containing `git_commit` and
`data_version`.

`qtrade.runtime.PaperBarRunner` consumes strictly ordered historical/replay bars, executes a
strategy's previous target through the durable PAPER pipeline at the next bar
open, and checkpoints the pending target and last consumed event. Dated fills
persist acquisition lots and enforce date-effective T+N sellability across
restart. The runner is intentionally single-process; licensed realtime feeds
belong behind the read-only SHADOW adapter after a provider is selected.

Completed realtime bars use `qtrade.runtime.RealDataPaperRunner`: a finalized bar
may decide a target, but execution can occur only on a later received tick under
a fresh provider/session/data-version readiness attestation. This prevents using
an already historical bar open as a PAPER fill.

Historical vendor exports can enter through `qtrade.data.CsvBarImporter`. The
canonical UTF-8 CSV schema is exactly `symbol,exchange,timestamp,trading_day,open,high,low,close,volume`;
timestamps must include an offset. Imports are checksum-versioned and must pass
the grouped quality gate before Parquet storage. Vendor-specific column mapping
belongs in a separate licensed-data adapter.

Project status and exact continuation steps are maintained in
[`CURRENT_STATE.md`](CURRENT_STATE.md), [`plans/MASTER_PLAN.md`](plans/MASTER_PLAN.md),
and [`plans/V2_MASTER_PLAN.md`](plans/V2_MASTER_PLAN.md).

## Causal minute risk overlay

`qtrade.runtime.MinuteRiskOverlayStrategy` combines an immutable daily target
with finalized one-minute bars. It fails closed while minute history is warming,
then may preserve or reduce the daily target in whole lots when short-term trend,
minute volatility or intraday drawdown limits are breached. It cannot introduce
a symbol, exceed the daily quantity or change the daily strategy version.
Recovery cooldown prevents immediate re-risking.

Daily targets and overlay histories can use the SQLite operational journal as a
state store, so restarts retain the publication timestamp, rolling closes,
session peak, active multiplier and cooldown. `MultiAssetRecordedPaperSession`
serializes a shared tick stream and routes each configured symbol through the
existing raw-recording, quality, one-minute aggregation and causal PAPER chain.
The resulting target still executes only on a tick received after the finalized
bar. `/minute-overlay` exposes fail-closed readiness to the Chinese monitor page.

No authorized realtime provider is configured by default. The endpoint therefore
reports `UNCONFIGURED`, `NOT_ELIGIBLE` and `external_order_transmission=false`;
AKShare daily history is not relabelled as realtime. A licensed RQData or XtData
read-only bridge must be selected and validated before forward SHADOW/PAPER use.

Recent historical minute research uses a separate bounded Sina HTTP reader. It
keeps only complete provider-native sessions and never invents the source's
omitted 14:58/14:59 timestamps. The frozen universe combines 30 ETF candidates
with five current CSI 300 stocks from each of 15 sectors; incomplete ETFs are
excluded with their missing days retained in the signed manifest.

```bash
.venv/bin/python scripts/build_akshare_sector_stock_universe.py

QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true .venv/bin/python \
  scripts/sina_minute_history_ingest.py \
  --stock-universe runtime/universes/akshare-csi300-sw15x5-current-20260731-v1.json \
  --data-version sina-minute-multiasset-sw15x5-20260825-v3

.venv/bin/python scripts/run_sina_minute_overlay_backtest.py \
  --data-version sina-minute-multiasset-sw15x5-20260825-v3 \
  --output runtime/research/sina-minute-overlay-sw15x5-v2
```

The minute diagnostic observes each signal at bar close and changes exposure
only at the next bar open. Seven policies are ranked on the first three sessions;
the chosen policy is then read once on the final two sessions, with 0/5/10/20 bps
cost stress, ETF/stock subgroups and 15 whole-sector leave-one-out checks. Five recent
sessions are far too short for promotion, so the report is always
`NOT_ELIGIBLE` and cannot authorize PAPER or live market access.

The same frozen cross-section also has a separate provider-native 5-minute
falsification study. It is not a resample of the five-day 1-minute file and is
not evidence of 1-minute realtime readiness:

```bash
QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true .venv/bin/python \
  scripts/sina_minute_history_ingest.py \
  --interval-minutes 5 \
  --sessions 40 \
  --stock-universe runtime/universes/akshare-csi300-sw15x5-current-20260731-v1.json \
  --data-version sina-five-minute-multiasset-sw15x5-20260825-v1

.venv/bin/python scripts/run_sina_five_minute_walk_forward.py \
  --data-version sina-five-minute-multiasset-sw15x5-20260825-v1 \
  --output runtime/research/sina-five-minute-walk-forward-v1
```

The signed dataset contains 193,920 bars for 26 ETFs and 75 stocks across 40
sessions. Four expanding folds start with 16 training sessions and each consume
six disjoint test sessions; every candidate is causally shadow-warmed and the
selected exposure changes only at the next 5-minute open. At 5 bps the stitched
24-session baseline returned `-1.9249%`, while the overlay returned `-4.6234%`
(`-2.6985%` relative), with only one of four folds positive in absolute terms.
Even the 0 bps overlay trailed the baseline. This failure is retained as a
cross-time-scale robustness veto rather than tuned away. `/five-minute-research`
and `/five-minute-bars/{symbol}` expose only hash-verified, historical, read-only
evidence, and the market page provides a separate native-5-minute symbol browser
instead of relabeling the 1-minute tab. Promotion, realtime/PAPER readiness and
external orders remain disabled.

For a longer provider cross-check, the optional BaoStock 0.9.3 path downloads
provider-native 5-minute A-share history. It deliberately excludes ETFs because
the bounded provider probe returned materially shorter ETF coverage, uses only
the previously frozen 75-stock/15-sector universe, omits all-zero suspension
placeholders without forward-filling, and records `qfq`/`adjustflag=2` as a
non-PIT future-adjustment-knowledge risk:

```bash
QTRADE_BAOSTOCK_FREE_DATA_ACK=true uv run --extra baostock python \
  scripts/baostock_five_minute_history_ingest.py \
  --data-version baostock-five-minute-stock-sw15x5-qfq-20250102-20260825-v1

uv run python scripts/run_sina_five_minute_walk_forward.py \
  --profile baostock-long \
  --data-version baostock-five-minute-stock-sw15x5-qfq-20250102-20260825-v1
```

The signed dataset contains 1,324,800 bars for 75 stocks across 368 common
complete sessions. The predeclared study reads the first 360 sessions only:
120 initial training sessions followed by six disjoint 40-session test folds;
the final eight sessions are excluded before records enter the strategy engine.
At 5 bps, the stitched 240-session
baseline returned `+40.8285%`, while the overlay returned `+1.0438%`
(`-39.7847%` relative). The overlay reduced maximum drawdown from `-15.1086%`
to `-12.1806%`, but even at 0 bps it lagged the baseline by `-10.0946%`.
The causally aligned execution diagnostics cover all 864,000 symbol-bars in the
six test folds: mean exposure was only `63.0175%`, `80.0331%` of observations
were below full exposure, mean cumulative turnover was `511.89` per symbol, and
the 5 bps equity deduction averaged `26.8744%` of one initial-equity unit per
symbol. Weak minute trend was the most frequent overlapping trigger. This
explains the veto as persistent under-exposure plus turnover drag; it is not a
new parameter search.
`/long-five-minute-research` exposes the checksum- and artifact-verified result
as a second, stronger robustness veto. The read-only
`/long-five-minute-bars/{symbol}?trading_day=YYYY-MM-DD` endpoint verifies the
same Parquet checksum and returns exactly the selected session's 48 native bars;
the market page provides stock and date selectors. The final eight sessions did
not enter that six-fold study. They were later read exactly once by a separately
committed preregistered diagnostic: the candidate policy was fixed before the
read, all prior 360 sessions were used only for causal state warmup, and only the
eight reserved sessions were scored.

At 5 bps the reserved-tail baseline, high-exposure candidate and prior
low-exposure comparator returned `-3.0960%`, `-3.0655%` and `-3.0157%`.
The candidate improved drawdown versus the baseline and cut turnover below half
the comparator, but missed the 85% exposure floor, lost to the comparator, stayed
negative at 0/5 bps, and won only 6 of 15 leave-one-sector-out comparisons. It
therefore passed only 2 of 6 locked gates and remains `NOT_ELIGIBLE`; the same
tail must not be used for a second tuning iteration. The tail was partially
viewed during frontend chart QA, so this is a diagnostic rather than a blind
independent confirmation. `/reserved-tail-diagnostic` recomputes the gates and
verifies the preregistration, dataset, prior report, curve and artifact hashes
before returning the evidence. None of these endpoints authorizes realtime data,
PAPER, strategy promotion or external orders.

`POST /single-stock-five-minute-backtest` provides a separate ad-hoc individual
stock preview over the same signed 75-stock dataset. The request must select one
verified stock, two to 120 consecutive listed trading sessions, bounded cost and
bounded reduction-only overlay parameters. All earlier sessions through the
selected start are replayed only as causal warmup; selected metrics, next-Bar
equity curves, execution diagnostics and daily rows are returned with a
deterministic result hash. The response and Chinese page also expose cumulative
and annualized return, annualized volatility, zero-rate Sharpe, maximum drawdown,
matched Buy-and-Hold excess return and information ratio. Annualization is fixed
at 48 native five-minute Bars per session and 252 sessions per year; it is not
silently inferred by the browser. The page exposes the complete form and JSON
export. Before a run, the form expands its current parameters into a readable
reduction-only policy draft: the constant full-exposure base target, trend and
volatility reductions, drawdown exit, cooldown recovery, required warmup,
next-Bar execution and cost. This browser draft is neither a stock-selection
entry signal nor a server-validated strategy/evidence identity. During one open
page session it also retains the latest fifteen distinct
result hashes for side-by-side parameter and metric comparison; loading an old
row only refills the form and requires a fresh run. Any manual parameter change
also clears the current result while retaining the session comparison, so an old
hash cannot remain presented as the output of new inputs. Each retained row shows
its stock sector and short result hash and can export the complete original response even after
the main result has been cleared. A bounded batch action can deterministically
choose the first one or two stocks by sector code and symbol in each signed sector
and run the existing stateless endpoint sequentially with one shared
date/cost/policy form snapshot. The two-stock setting covers 30 stocks across the
signed 15-sector universe, with stock-request and successful-sector counts shown
separately. Partial
failures remain missing instead of being synthesized, the visible coverage uses
successful rows only, and the complete session comparison can be exported as one
JSON document. The batch does not tune per stock and is not evidence for every
stock in a sector. For the successful batch rows, a compact cross-sectional
summary reports finite-metric coverage, positive strategy and excess counts,
the median cumulative/annualized return, Sharpe, maximum drawdown, active excess
and information ratio, plus the worst-return stock and the deepest-drawdown
stock. Failed
requests never enter those denominators. This is a browser-session fragility
diagnostic over at most two representatives per sector, not a full 75-stock
statistical robustness gate or independent sample-out result. The batch workflow
also retains the latest five batch summaries in the current page,
including exact shared parameters, real stock/sector coverage and failures, all
six common-metric medians and every admitted preview hash. Operators may export
the comparison or load an older parameter set for a required fresh run. The
sixth run evicts the oldest; refresh clears the history, and the browser never
auto-ranks or selects a winner. This is not server persistence, an immutable
experiment, sample-out evidence or promotion authority. The two-stock
mode additionally forms a within-sector pair only when both
requested responses pass identity binding and all key metrics are finite. It
reports complete-pair coverage, return/excess sign agreement, median absolute
return spread and the worst-divergence sector; single-stock sectors and missing,
rejected or non-finite partners are excluded without imputation. Pair details are
preserved in the browser-session export and remain diagnostic rather than a
formal robustness or promotion gate. Every complete pair is also visible in a
bounded sector-sorted table with both stock codes, both strategy/excess returns,
both annualized returns, Sharpes, maximum drawdowns and information ratios, the
two sign-agreement decisions and absolute return spread. A pair requires every
one of these common metrics to be finite for both stocks. The table reads the
same admitted pair objects as the export and never renders or imputes an
incomplete/non-finite pair.
Before any response enters the session,
the browser also requires its stock/sector identity, complete normalized request
parameters, signed data version/checksum, fixed `NOT_ELIGIBLE` classification and
all realtime/PAPER/state/order safety flags to match the batch contract. A drifted
response is counted as identity-rejected and omitted rather than mixed into the
same-parameter claim. While that batch identity remains active, the session export
is an explicit browser-only diagnostic envelope rather than a bare success array:
it preserves the signed dataset identity, deterministic representative roster,
every expected normalized request, completed/failed/identity-rejected counts,
successful results, finite-metric summary and worst tail. It explicitly carries
no-persistence, no-registry, `NOT_ELIGIBLE`, no-state and no-order boundaries.
The page and export also retain one ordered attempt row per representative with
only `COMPLETED`, `REQUEST_FAILED` or `IDENTITY_REJECTED`; a completed row binds
its accepted result hash, while failed/rejected rows expose neither exception text
nor a rejected response payload and never enter the metric denominator.
Up to 30 accepted hashes remain in the session table, paginated after 15 rows.
The comparison is cleared on
refresh and is neither persisted evidence nor an experiment record. Every result
remains current-universe, QFQ/non-PIT,
`NOT_ELIGIBLE`, stateless and unable to transmit an external order.

`POST /intraday-factor-backtest` and the research-page minute factor workbench
implement the explicit factor-to-strategy path for the opening three-ten-minute-
bullish rule. Each request rereads the checksum- and schema-verified 75-stock,
15-sector native five-minute dataset, pairs the first six provider Bars into the
09:30-09:40, 09:40-09:50 and 09:50-10:00 intervals, and knows the factor only at
the third interval close. BaoStock timestamps label interval ends: the entry
price is the open of the next Bar labelled `10:05` (interval start `10:00`), and
the A-share T+1 exit is the open of the next session's Bar labelled `09:35`
(interval start `09:30`). The response includes IC/Rank IC/ICIR, related-feature
correlations, conditional event returns, standard performance metrics, an
equal-weight same-horizon benchmark, 0/5/10/20 bps cost stress, daily curves and
all 15 sector/75 stock breakdowns. The current-result stock tab can filter those
same-run rows by six-digit code or name and keeps signal count, conditional mean,
hit rate, strategy return, active excess and drawdown together; filtering never
reruns the test or selects a new stock/parameter. Editing any factor/backtest
input clears the current result and save action; changing the discovery end date
or cost also clears its separately bound 104-candidate result. Its robustness section fixes candidate body
thresholds at `0/5/10/20 bps`, uses the first half as the initial training
window, selects a threshold from prior sessions only, and evaluates disjoint
expanding-window test folds. The training day's T+1 exit is known at the test
day's `09:35` open before that day's `10:00` signal; test returns never enter the
same fold's selection.

Once discovery returns, “载入训练锁定开仓因子” copies only the selected training
candidate's three directions, fixed `ALL` combination and body threshold into
the custom entry-factor form. Current dates, cost and exit rule are intentionally
left untouched; any prior strategy/correlation response is cleared and no API
run starts. The page keeps the discovery evidence visible and warns that its
test window is already viewed, so the operator must separately choose/review an
exit and run a diagnostic or genuinely new preregistered window. The handoff
copies no test conclusion and creates no experiment, registry or runtime state.

The workbench also separates entry-factor drafts from tested strategies. “保存
当前开仓因子候选（不回测）” stores only the bounded three-window directions,
`ALL`/`ANY` combination and body threshold in the current browser. Two to eight
such drafts can be checked on one 75-stock window for clustered effectiveness,
Holm-Bonferroni adjusted significance and continuous-score correlation before
an exit rule or strategy result exists. These dataset-bound drafts are always
`NOT_RUN`: they contain no dates, cost, exit, strategy metrics or backtest
conclusion and cannot write either registry, promote, start PAPER/LIVE or send
an order. Completed entry/exit backtests remain in a separate local strategy
experiment table.

Before a checked factor can enter strategy construction, the browser verifies
that the correlation response exactly binds the current data version/checksum,
date window, ordered rules, rule/effectiveness/matrix IDs, complete pair family,
response hash and fail-closed safety fields. An effective factor is eligible for
a manual handoff only when it belongs to no redundant pair in that response.
“进入策略构造” copies only the bound opening directions, combination and body
threshold; dates, cost and exit stay untouched, and no backtest starts. A drifted
response is rejected, while ineffective, redundant and unbound rules expose no
handoff action. This is still viewed-history diagnosis, not automatic selection,
registration, PAPER/LIVE admission or order authority.

That admission now verifies the response semantics rather than trusting those
top-level identities alone. The selected trading-day sequence, required next
session for T+1 exits, native 5-minute Bar count, 75-stock/15-sector scope and
stock-day observation count must all agree with the verified dataset. Each
effectiveness row must reproduce the fixed Newey-West/Holm thresholds and the
Holm adjustment for the submitted family. The Pearson matrix must be finite,
bounded, symmetric and unit-diagonal; every pair must equal the corresponding
matrix cell and derive its redundancy flag, maximum absolute correlation and
overall result from the fixed threshold. A scope, p-value or matrix arithmetic
drift rejects the whole response before a factor handoff is shown.

The subsequent strategy backtest is also admitted to the page only after a
fail-closed identity check. The response must reproduce the normalized request,
BaoStock data version/checksum, 5-minute 75-stock/15-sector scope, opening and
exit behavior, next-bar/T+1 contract, read-only strategy ID/spec hash,
preregistration binding, blocked registration/PAPER workflow and all disabled
state/order fields. Any missing or drifted value is rejected before metrics or
curves render; a valid result displays “策略回测响应身份已绑定”. This verifies the
returned preview identity only and does not recalculate it, register a strategy,
promote it or start PAPER/LIVE.

When that accepted preview follows the identity-bound manual factor handoff in
the same browser session, the page also builds one deterministic read-only
lineage export. It binds the local candidate key/name/expression, `Rxx` rule,
effective/non-redundant decision, Holm-adjusted evidence and correlation result
hash to the exact later strategy request, preview hash and read-only behavior
hash. Any form, candidate selection or evidence change clears the binding. The
package explicitly says that the exit rule and strategy returns were not
validated by the factor-correlation diagnostic and that the shared viewed window
is not independent evidence. Direct backtests remain valid diagnostics but show
that no joint handoff lineage exists; neither path writes a registry, starts
PAPER/LIVE, mutates state or sends an order.

The research page begins with one evidence-derived, read-only factor catalog.
It combines only definitions actually present in the signed daily-factor search
and intraday long-window report, then displays each factor's selection/entry/
exit/L2-hypothesis role, exact formula, required data, causal known/execution
clock, validation status and evidence binding. Optional exit and L2 rows are not
invented when their signed contracts are absent, and an unknown daily formula is
marked non-executable. This is an exportable evidence index, not a writable
`FactorRegistry`; it cannot create a strategy or experiment, authorize PAPER or
LIVE, mutate state or transmit an order.

The workbench also exposes a bounded opening-direction rule builder over those
same three completed intervals. Each slot can be `BULLISH`, `BEARISH` or
`IGNORE`, the non-ignored predicates can be joined with `ALL` or `ANY`, and one
nonnegative body threshold applies to every active slot. This is a typed rule
contract rather than an arbitrary expression evaluator: requests cannot select
other fields, execute code, change the three time windows or move the decision,
entry and T+1 exit timestamps. The original three-bullish factor remains an
immutable preset. Before running, the page renders the current entry predicates
in body-return/bps notation, the `10:00 → 10:05` causal clock and the complete
T+1 exit branch. This is a readable form draft only; it has no server identity or
evidentiary meaning until the API validates and hashes a run. Custom runs reuse
the same effectiveness, correlation,
walk-forward, cost, sector, stock and promotion gates; they are stateless
research previews and cannot create a PAPER candidate or external order.

Each completed intraday-factor preview can be saved into a browser-local
comparison notebook. A bounded record keeps the exact dataset version/checksum,
result hash, typed rule parameters, read-only strategy ID/spec hash, signal counts, standard metrics, stitched
walk-forward return and both gate outcomes. Records with invalid structure,
another dataset identity or relaxed safety flags are ignored; duplicate result
hashes replace the older label and at most 30 summaries are retained. Loading a
record only repopulates the form and clears the old result, so the user must run
the current verified dataset again. The notebook is not server-synchronized,
does not create a registered experiment and cannot feed PAPER or LIVE.

New records retain `AD_HOC_READ_ONLY_STRATEGY_IDENTITY` only when the strategy ID
matches the SHA-256 prefix and every unregistered/no-authority flag remains
strict. Older schema-v1 browser rows load with an explicit missing identity
rather than inventing one. The table label stays `AD_HOC_UNREGISTERED`; preserving
the factor-to-entry/exit behavior hash is lineage for replay, not registration.

That notebook now exposes the complete saved comparison surface rather than only
headline PnL: cumulative and annualized return, active excess, Sharpe, maximum
drawdown, Rank IC, maximum absolute factor correlation, stitched out-of-sample
return/excess, Newey-West t and its 95% interval appear on each row. These are the
original checksum- and preview-hash-bound response values; the table does not
recompute, backfill or reinterpret them as independent evidence.

The full current-dataset notebook can be downloaded as deterministic JSON with
provider/version, checksum, evidence/PIT identity, typed entry/exit rules,
factor definitions, metrics, result hashes and read-only strategy identities.
No export timestamp is introduced, and an empty notebook disables the action.
This is portable review data only: there is no import, automatic synchronization
or winner selection, immutable experiment or registry write, promotion,
PAPER/LIVE transition, state mutation or external order transmission.

The selected 2–8 rule same-window check also exposes its complete server result
as a `preview_hash`-named JSON download after success. It retains per-rule saved
thresholds, session-clustered effectiveness, Newey-West statistics, raw and
Holm-adjusted p-values, the continuous-score Pearson matrix, redundant pairs,
dataset/PIT identity and every read-only safety flag. No export appears before a
result exists; form or selection changes invalidate the old result. The file is
not recalculated browser evidence, persistence, automatic selection, registry or
PAPER/LIVE authority.

Factor effectiveness no longer treats every same-day stock observation as an
independent significance sample. For each session containing at least one
signal, the engine first computes the triggered-stock mean forward return minus
that session's full-universe mean. It then reports the daily-series mean, an
automatically banded Newey-West standard error and t-statistic, and a normal 95%
interval. The research gate additionally requires at least ten clustered signal
sessions and `t > 1.96`; insufficient or zero-variance samples return a
conservative zero statistic. This remains a retrospective effectiveness
diagnostic and cannot bypass the forward PAPER gate.

The first disclosed 20-session run from 2026-07-28 through 2026-08-24 produced
192 signals across 1,500 stock-days. At 5 bps per side it returned `-4.0840%`,
annualized `-40.8673%`, Sharpe `-3.3347` and maximum drawdown `-5.1038%`.
It exceeded the falling same-horizon benchmark by `0.2544` percentage points,
but mean signal return, conditional excess, Rank IC, sector breadth and absolute
return gates failed. The workbench therefore reports the result as a rejected
ad-hoc hypothesis, never as a strategy eligible for PAPER. The factor-to-
strategy handoff is nevertheless reproducible as a read-only specification:
entry factor, expression, threshold, exit rule, position sizing, benchmark,
cost and T+1 execution fields share one deterministic SHA-256 and derived
`adhoc-intraday-*` ID. Observed exit-trigger counts are excluded from the spec
hash. The identity is bound into the PAPER registration blocker but is not a
registered strategy version and grants no promotion authority. The proposed
sequential-advancement contract separately prevents that diagnostic artifact
from looking like workflow approval. Factor effectiveness, correlation and
rolling robustness must all pass before strategy construction is research-
admitted. This run passes only one of those three stages and first blocks at
factor validation; its strategy and causal backtest remain diagnostic previews.
The viewed window cannot be used for reselection. PAPER admission consumes the
same next-action value, so this rejected run consistently instructs the user to
predeclare a new hypothesis before new evidence; it no longer suggests strategy
registration. Before the dossier renders, the browser now requires the exact
ordered eight-gate family, dataset/strategy-bound evidence strings, dynamic
research/backtest gate values and count arithmetic. A duplicated or missing
gate, forged `passed_gate_count`, evidence drift or extra authority field rejects
the complete response instead of displaying a misleading PAPER boundary. That
instruction now expands into a read-only preregistration
preflight bound to the rejected strategy ID/hash, dataset checksum, viewed date
range and first failed stage. It mirrors the required `ResearchHypothesis` and
`ExperimentRunSpec` fields, requires a new disjoint evidence period, independent
leakage review, bounded candidate/robustness budgets, a fixed cost model, a
pre-data immutable specification hash and contamination disclosure. Completing
that document is not a registry write: only afterward may a separately reviewed
immutable strategy enter `StrategyRegistry` as `IDEA`, while `APPROVED` and
`CHAMPION` still require human authorization. The browser now admits that
preflight only when its binding and sequentially derived status, ordered 7
hypothesis fields, 13 experiment locks, eight governance requirements,
disjoint-window policy, lifecycle and human-authorization list match exactly.
Evidence reuse, field/gate drift or any write authority rejects the full result.
A separate registration preflight
now proves that the current behavior-only hash is not the complete
`StrategySpec` hash. It enumerates all 15 registry fields and nine admission
gates; this rejected run can source only 6/15 fields and passes only the behavior
hash plus dataset-identity gates (`2/9`). Research, backtest, signed
preregistration, leakage review, full registry hash, frozen disjoint split and
lineage declaration remain blocked. No registration operation is exposed, and
`IDEA → EXPERIMENT` still requires a complete experiment spec, a quality-passed
manifest and an independent leakage-review PASS. The browser now admits that
preflight only when the ordered 15-field inventory, 6/15 readiness split, nine
gate identities/evidence, research/backtest-derived pass count and fixed
transition evidence match exactly. A forged count, duplicate gate, field-list
drift or exposed registration authority rejects the complete backtest response.
The earlier sequential-advancement envelope is now equally strict: the browser
reconstructs the ordered factor/correlation/robustness rows, count, earliest
blocker, diagnostic/admitted status, backtest branch and next action from the
validated stage booleans. Reordered stages, inconsistent arithmetic or blocker,
extra fields and any registration/state/order authority reject the whole result;
the check itself exposes no advancement or execution operation.
The displayed eight-stage workflow is also reconstructed exactly from the same
validated factor, correlation, robustness and causal-backtest facts. Its factor
detail switches only when a conditional exit factor was actually evaluated;
strategy construction becomes a research-admitted preview only after all three
research stages pass. Stage order, status or explanation drift—including a
forged PASS—rejects the complete result while PAPER stays ineligible and LIVE
stays disabled.

For researchers who want to begin with existing strategy structures, the
strategy center now loads a deterministic, read-only catalog of the five
implemented baselines: an equal-weight buy-and-hold benchmark, single-asset
moving-average trend, ETF momentum rotation, ETF momentum with trend/volatility
constraints, and cross-sectional factor ranking. Each row exposes the actual
implementation reference, data interval, universe scope, entry/exit rules,
unfrozen defaults, required inputs and known limitations. Four entries are
active research baselines and one is comparison-only. The catalog does not
claim profitability, create an experiment, register a strategy, execute
arbitrary code or start PAPER/LIVE; the next legal action is still to freeze an
`ExperimentRunSpec` before a causal backtest.

Three catalog entries now connect directly to the bounded causal backtest lab:
ETF momentum rotation, ETF momentum with trend/volatility controls, and
cross-sectional reversal or low-volatility ranking. Loading a template only
prefills the matching implemented parameters; it never freezes them. The lab can
construct and export all 13 `ExperimentRunSpec` fields, binding the exact
dataset version/checksum, point-in-time universe, implementation reference,
chronological train/validation/test split, costs and seed to a deterministic
hash. This is deliberately a stateless draft rather than an immutable
preregistration. Because its historical test dates are already visible, the
draft is not fresh held-out evidence and cannot register a strategy, start
PAPER/LIVE, mutate runtime state or transmit an order.

The draft can now be executed through one hash-bound read-only request. The
server recomputes the spec, reopens the checksum-verified dataset, runs the
causal preview from the same request and hashes the resulting `spec_hash`,
`preview_hash`, data version/checksum and template identity together. Any form
change invalidates the browser's prior draft and binding immediately. This
proves which reproducible preview belongs to which draft, but it still writes no
immutable artifact and cannot convert an already viewed window into independent
confirmation evidence or authorize PAPER/LIVE.

The browser-local comparison table now preserves that lineage when a bound
preview is saved: template ID, spec hash and binding hash remain visible beside
the metrics, while an ordinary adjustable run is labelled as an immediate
preview. Save-time validation fails closed if the current result hash, dataset
version/checksum, read-only gates or no-order flags do not match the bound
response. Legacy browser records remain readable as unbound previews. These
records are removable local summaries, not immutable experiments or registry
entries, and they confer no promotion or runtime authority.

Saved rows now expose the same common comparison surface needed for repeated
tuning: total and annualized return, matched benchmark return, active excess,
Sharpe, information ratio and maximum drawdown. Missing metrics in older rows
render as unavailable rather than being inferred. The table keeps the matched
benchmark's current-universe, non-PIT survivorship warning adjacent to the
numbers, so a compact A/B view cannot be mistaken for promotion evidence.

Loading a saved bound row now restores its code-template identity and exact
backtest parameters instead of silently degrading it to an unbound preview. It
clears every prior result/draft/binding and initializes a fresh run-spec form;
the saved spec and binding hashes are shown only as historical identifiers.
Because the local summary never stored the original experiment ID, commit,
split or seed, the UI explicitly requires a newly completed draft and hash.
Unbound rows remain unbound, and a template/parameter mismatch fails closed by
loading parameters without restoring the template.

The complete browser-local comparison can also be exported as one deterministic
JSON document. It binds the provider/version/data checksum and PIT flag to the
ordered saved rows, including parameters, common metrics, preview hashes and any
template/spec/binding identities. The document deliberately has no generated-at
field, so unchanged browser state produces unchanged JSON. It is a portable
read-only checkpoint, not automatic device synchronization, immutable experiment
storage, registry evidence or PAPER/LIVE authority.

Every adjustable preview now also runs an equal-weight Buy-and-Hold comparison
through the same causal engine. The benchmark selects only symbols known at the
first decision close, enters at the next open, and shares the strategy's date
window, initial cash, reserve, transaction cost and slippage. The response and
chart include aligned benchmark equity/drawdown, cumulative excess return,
benchmark return and information ratio alongside annualized return, Sharpe and
maximum drawdown. This is a matched current-universe diagnostic; the universe
is still not PIT and retains survivorship bias.

The proposed
five-level order-book buying-intensity factor remains `UNCONFIGURED`: its read-only
contract requires five bid/ask prices plus sizes normalized to shares, provider
event/receive clocks and sequence, continuous-auction validity, certified
freshness and recorded-replay reconciliation. The proposed `intensity > 1`
entry is explicitly untested, its exit is not yet defined, and authorized L2
depth is never synthesized from OHLCV. The added
browser admission now independently requires the exact 20-field schema, five
identity fields, rejection policy, causal clock and ordered six-gate 0/6
contract before rendering any strategy response. A missing level, duplicate or
unknown field, forged gate/count, future-book permission or OHLCV substitution
rejects the complete response instead of degrading to a partial L2 card. This
does not make L2 configured; an authorized provider and recorded replay remain
external requirements. The added
rolling diagnostic also rejected the rule: both five-session folds selected a
5 bps body threshold and returned `-2.5834%` and `-2.1394%`; their stitched return
was `-4.6675%`, and every fixed threshold returned between `-4.4507%` and
`-5.7885%`. Because these dates were already available, this is labelled reused
historical evidence rather than a blind confirmation.

The same rule now has a separately generated immutable 120-session robustness
report. `scripts/build_intraday_factor_long_window_evidence.py` fixes the latest
120 eligible sessions (2026-03-03 through 2026-08-24), an initial 60-session
training prefix, six disjoint ten-session test folds and the same four body
thresholds. It binds the BaoStock dataset identity, factor/API source hashes and
the complete deterministic result hash. Across 9,000 stock-days it triggered
1,001 signals; the full-window strategy returned `-27.1715%` with Rank IC
`-0.0121`. Only one of six test folds was positive and the stitched sample-out
return was `-28.3060%`, with `-28.7245%` maximum drawdown and `-12.4906%`
excess return. Every fixed threshold also lost money. The read-only
`/intraday-factor-robustness` endpoint and research/data-center pages expose this
signed veto, while any digest, source, dataset, fold or fixed-result mismatch
fails closed. This is reused viewed history, not independent confirmation, and
it remains `NOT_ELIGIBLE` for PAPER or LIVE.
The long-window card exposes the same signed result as a 120-session strategy/
benchmark/equity/drawdown curve, all six fold rows, all 15 independent sector
breakdowns, a searchable 75-stock table, threshold pressure and both research
gate matrices. These partitions are diagnostic views of the fixed result; the
browser cannot remove a weak sector or stock and feed the filtered set back into
selection.
All 120 sessions contained at least one signal, but their session-clustered
conditional excess averaged `-0.0574%`; the Newey-West t-statistic was `-0.4800`
and its 95% interval was `[-0.2916%, 0.1769%]`. Thus the enlarged cross-section
does not manufacture statistical significance.

The public deployment can be audited independently across one stock from every
sector and three widely separated sessions. The script runs the same 45 requests
twice, verifies the dataset checksum, 48 unique bars, trading day, observation
type and China-market `09:35`–`15:00` clock, then signs the result:

```bash
PYTHONPATH=. .venv/bin/python scripts/audit_public_minute_bars.py \
  --cache-key <unique-audit-id> \
  --output deployment/evidence/public-minute-audit/report.json
```

The 2026-08-26 audit passed all 90 historical-bar requests. The
forced-cache-miss round had `4.669s` median and `6.248s` P95 latency; the
repeated CDN-hit round had `0.179s` median and `0.274s` P95 latency, with all 45
responses reported as `HIT`. The same run also confirmed that all seven public
control/cancel writes returned `503`.
`/public-minute-audit` exposes this point-in-time, checksum-bound availability
evidence. It demonstrates public historical-bar access and caching, not an
authorized realtime feed or a performance SLA.

The retrospective development-only policy search is reproducible with:

```bash
PYTHONPATH=. .venv/bin/python \
  scripts/run_baostock_development_policy_search.py \
  --output deployment/evidence/development-policy-search/report.json
```

It evaluates four fixed high-exposure, slower 5-minute reduction overlays on
the first 360 sessions only; the final eight sessions do not enter the strategy
engine or candidate metrics. The observed development leader retains `91.32%`
mean exposure and reduces mean turnover to `50.52` per symbol, but still trails
the base return by `2.21` percentage points. Prior reserved-tail evidence was
already visible before this post-hoc search, so the signed report remains
`NOT_ELIGIBLE`. It locks that candidate before the 2026-08-26 open for a future
minimum of 20 complete sessions; partial same-day observations never enter its
metrics.

That lock now has a code-bound forward-observation preregistration. It was
created after the first eligible session opened but before this project fetched
that target session, so the artifact explicitly does not claim a fully blind
preregistration. It binds the development report, the 368-session warmup
dataset, the exact policy, the collector/evaluator/engine hashes and five fixed
20-session gates. The research page and `/forward-observation` show the verified
`0/20` starting state. The first immutable 75-stock × 48-bar session was later
collected for 2026-08-26 and independently promoted, so the current verified
state is `1/20`, `INCOMPLETE` and `NOT_ELIGIBLE`; its five provisional cumulative
gate values do not become a conclusion before 20 complete sessions.

After 15:10 China time, one complete future session can be collected with:

```bash
QTRADE_BAOSTOCK_FREE_DATA_ACK=true PYTHONPATH=. .venv/bin/python \
  scripts/probe_baostock_forward_session.py \
  --anchor-day 2026-08-25 --target-day 2026-08-26 \
  --output-root deployment/evidence/forward-session-data
```

The collector requires the exact frozen 75-stock identity, 48 provider-native
5-minute bars per stock and a byte-equivalent anchor-day OHLCV grid. A partial
session, suspended zero placeholder, missing symbol or QFQ anchor revision
rejects the entire target day. Accepted snapshots are immutable. The locked
observer is then reproducible with:

```bash
PYTHONPATH=. .venv/bin/python \
  scripts/run_baostock_locked_forward_observation.py \
  --forward-root deployment/evidence/forward-session-data \
  --output runtime/research/baostock-locked-forward-observation-v1/report.next.json
PYTHONPATH=. .venv/bin/python \
  scripts/promote_baostock_forward_observation.py \
  --candidate runtime/research/baostock-locked-forward-observation-v1/report.next.json
```

All 368 warmup sessions enter strategy state but not future metrics. Only future
complete sessions are scored at 0/5 bps and under whole-sector exclusions. No
conclusion is complete before 20 sessions, and even a pass cannot authorize
PAPER, LIVE or external order transmission.

The candidate is never copied directly over the public baseline. The promotion
step reruns the same dataset, development-lock, preregistration and report
validators used by the public API; requires at least one newly appended complete
session; rejects any rewritten historical session, lock change or non-monotonic
generation time; then uses an atomic file replacement and verifies the published
digest again. It also rereads `deployment/evidence/forward-session-data`, verifies
each signed manifest and checksum-bound Parquet, and requires the candidate's
count/date/hash/3,600-Bar/anchor list to match those source sessions exactly. The
validator requires the report's warmup data version, checksum,
session count and metric-exclusion flag to equal the preregistered identity, and
requires its generation time to follow the lock. It rejects booleans in every
integer session-count field even though Python normally treats them as integers,
and rejects a target day that had not reached the locked 15:10 China-time
collection boundary when the report was generated. Cost-stress execution rows
must carry finite `[0,1]` exposure and finite non-negative total/per-symbol
turnover whose arithmetic agrees. Leave-one-sector-out rows must exactly match
the 15 sector code/name identities derived from the verified frozen dataset.
Both cost rows must share an identical Buy-and-Hold return/drawdown/volatility/
point-count baseline. Their complete execution diagnostics are validated for
field identity, genuine non-boolean bounded counts/ratios and exposure/turnover/
cost arithmetic.

For a bounded same-day observation, the following probe selects one stock from
each of the 15 frozen sectors and keeps only bars older than a conservative
60-second finalization guard:

```bash
QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true PYTHONPATH=. .venv/bin/python \
  scripts/probe_sina_intraday_observation.py \
  --output deployment/evidence/intraday-observation/report.json
```

`/intraday-observation` and the market-page “当日分钟观测” tab expose the signed
point-in-time result. A pre-open run correctly contained zero same-day bars; the
09:33:16 China-time refresh then verified two finalized bars (09:31 and 09:32)
for all 15 sector representatives. The 12:07:48 midday refresh verified all 120
morning-session common bars through 11:30, with a `1.057s` request window. Its
larger latest-bar age is retained because the market was in the lunch break;
provider rows timestamped after the causal cutoff were excluded. The
probe is intentionally a free historical-source observation, not a streaming
subscription: it never sets realtime/PAPER readiness, mutates strategy state or
sends an external order. The report can be refreshed after the market opens to
show only finalized same-day bars while preserving that boundary. Its signed
evidence also records the 15-symbol request window and the maximum age of each
symbol's latest same-day finalized bar so source delay stays visible.

A separate breadth probe checks all five frozen stocks in each of the 15 sectors
without duplicating their full OHLCV payload in the dashboard snapshot:

```bash
QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true PYTHONPATH=. .venv/bin/python \
  scripts/probe_sina_intraday_breadth.py \
  --output deployment/evidence/intraday-breadth/report.json
```

The signed artifact retains every stock's finalized same-day timestamp set plus
its first open, previous close and latest close. The API independently recomputes
the 75-stock intersection, per-sector intersections, latest-bar ages, request
window, relative-open/latest-minute returns and equal-weight directional breadth,
then omits the raw timestamp sets from its response. The market page shows the
resulting 15×5 coverage and sector-direction tables beside the 15 representative
stock charts. At the fourth 12:35 China-time checkpoint, all 75 stocks shared 120
finalized morning minutes through 11:30; 59/75 were above their first open and
12/75 rose in the latest finalized minute, while the equal-weight session and
latest-minute returns were `+1.0826%` and `-0.0709%`. This is observable source
breadth only; it does not create realtime entitlement, strategy input, PAPER
readiness or order authority.

Multiple detail/breadth refreshes can be chained into a bounded continuity
artifact:

```bash
PYTHONPATH=. .venv/bin/python scripts/update_intraday_continuity.py \
  --output deployment/evidence/intraday-continuity/report.json
```

Each checkpoint binds both source-report hashes, completion times, coverage,
common finalized-minute counts and lag measurements to the prior checkpoint.
Starting at an explicit immutable sequence, it also retains the verified
relative-open/latest-minute breadth and equal-weight returns. Earlier checkpoint
hashes are not rewritten during this schema migration. `/intraday-continuity`
recomputes the chain, monotonicity and latest-report
binding before the market page renders the timeline. A source regression remains
visible instead of being repaired or dropped. The chain is deployment evidence,
not a realtime subscription, strategy state or PAPER authorization.
The current ninth checkpoint binds 15/15 detail and 75/75 breadth coverage at
the source-defined complete 238-minute session through 15:00 and is monotonic
versus the previous 210-minute checkpoint. Sina omits 14:58/14:59 and publishes
the closing auction at 15:00; QTrade does not synthesize the two absent timestamps.
The signed direction timeline starts at checkpoint five; checkpoint nine has
55/75 stocks above open, 13/75 rising in the latest minute, `+0.8777%`
equal-weight session return and `-2.88 bp` equal-weight latest-minute return.
Versus checkpoint eight, participation rose by one stock and the equal-weight
session return fell `3.79 bp`. Its 75-stock request window was `2.802s` and
remains visible rather than being represented as realtime. All three mutable report
writers publish through a flushed, `fsync`-backed same-directory temporary file
and atomic replacement, so an
interrupted refresh cannot expose a half-written JSON document.

The dashboard reads the long 5-minute research, reserved-tail diagnostic,
development lock, future observer, public audit and both intraday reports through
one `/minute-research-snapshot` request. The server opens and SHA-256 verifies the
24 MB / 1,324,800-row long-history Parquet once, then passes the same verified
identity to all eight independent report validators. Standalone endpoints remain
available for inspection. A missing or invalid report stays `null` in the bundle
and every browser GET uses `cache: no-store`, so a refresh cannot reuse an older
signed checkpoint after a deployment.
and cannot be replaced by an unverified value; the aggregation changes read
amplification only, not evidence rules or runtime authority.
The browser also caps concurrent GETs at six, preventing a fresh serverless
deployment from cold-starting every remaining partition at once. A request-pool
test fixes that upper bound and confirms that the retired heavy endpoints are no
longer part of the dashboard fan-out.

The optional native RQData bridge is implemented with pinned `rqdatac 3.6.3`.
It imports the SDK lazily, reads `QTRADE_RQDATA_USERNAME` and
`QTRADE_RQDATA_TOKEN` only inside the bridge process, and claims a session only
after both SDK login and realtime WebSocket authorization succeed. Install and
run its data-only probe with:

```bash
python -m pip install -e '.[rqdata]'
python scripts/probe_rqdata_readonly.py
python scripts/probe_rqdata_readonly.py --connect --symbol 510300
```

The first probe prints only SDK/configuration booleans and environment-variable
names. `--connect` performs an authorized heartbeat and snapshot round trip but
never accesses an account or sends an order. Plain six-digit A-share/ETF codes
are mapped by exchange prefix; ambiguous index codes must include an explicit
RQData suffix such as `000300.XSHG`.

For forward PAPER, register the `RqDataProvider.on_tick` callback with the
configured `MultiAssetRecordedPaperSession` before subscribing. The bridge gives
every callback a session-global ingestion ordinal, and the recorder preserves it
across per-symbol canonical files so multi-stock replay retains arrival order.
The local `TickBarAggregator` remains the strategy clock. RQData's pushed minute
bar is a secondary comparison input because the vendor documents that late ticks
can make a live minute bar differ from its cleaned end-of-day history. A fresh,
short-lived market-data readiness attestation is still required before the PAPER
runner accepts any tick.

## Real historical data and adjustable preview

AKShare is an optional, noncommercial research source. After installing
`.[akshare]`, ingest a bounded daily dataset with an explicit acknowledgement:

```bash
QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true python scripts/akshare_history_ingest.py \
  --start 2024-01-01 --end 2026-08-21 \
  --data-version akshare-etf30-local-v1
```

Build the web application and start the loopback-only control plane against the
generated, checksum-signed manifest:

```bash
npm --prefix web run build
python scripts/run_control_plane.py \
  --historical-manifest runtime/data/akshare/akshare-etf30-local-v1/manifest.json
```

Open `http://127.0.0.1:8000/web/#/backtest`. The parameterized preview supports
bounded date, momentum/reversal/low-volatility factor family, factor horizons,
Top-K, rebalance, reserve, trend, volatility, capital, transaction-cost and
slippage adjustments. It recomputes from verified Parquet and never mutates the
strategy registry or PAPER runtime. Results can be named and compared in the
current browser, where records remain bound to the exact data version, checksum
and result hash; this local notebook is not server persistence. Every preview is
explicitly non-PIT, ad hoc and `NOT_ELIGIBLE`; it is not promotion evidence or
investment advice.

The same page also exposes the 75-stock native 5-minute preview. Select a stock,
verified trading-day range and minute overlay parameters, then compare the
reduction-only curve with that stock's Buy-and-Hold baseline. A request is
limited to 120 selected sessions and uses prior history only for causal state
warmup; no result is written to the PAPER runtime.

## Bounded factor search

The formal factor search uses a longer checksum-verified dataset and separates
factor discovery, portfolio validation and one locked active confirmation. It
does not require a clean Git tree; instead, the report records a deterministic
hash of every tracked/untracked source file so local research remains auditable.

```bash
QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true .venv/bin/python \
  scripts/akshare_history_ingest.py \
  --start 2022-09-06 --end 2026-08-21 \
  --data-version akshare-etf30-factor-research-20220906-20260821-v1

.venv/bin/python scripts/run_akshare_etf_factor_search.py \
  --data-version akshare-etf30-factor-research-20220906-20260821-v1 \
  --output-dir runtime/research/akshare-factor-search-v3
```

The current run evaluates five factor hypotheses and at most fifteen active
validation candidates with modeled transaction cost and slippage. Buy-and-Hold
is comparison-only. The resulting report and its diagnostics/validation/test
artifacts are ignored runtime evidence, served through `/factor-search` only
after envelope and artifact verification. The current result is negative,
current-universe/non-PIT, based on a previously viewed confirmatory period and
therefore permanently `NOT_ELIGIBLE`.

## Repeated robustness search

The follow-up search freezes 36 factor/parameter candidates before execution and
uses only eight quarterly windows from 2024 through 2025 for selection. A
candidate must first pass the window-level return/median-Sharpe/worst-Sharpe
gate, then survive doubled costs, one-parameter neighbours, six deterministic
80% universe subsets and a 1,000-replication stationary bootstrap. The 2026
period is accessed only after selection and is permanently labelled a polluted
audit rather than new held-out evidence.

```bash
.venv/bin/python scripts/run_akshare_etf_robust_factor_search.py \
  --data-version akshare-etf30-factor-research-20220906-20260821-v1 \
  --parent-report runtime/research/akshare-factor-search-v3/report.json \
  --output-dir runtime/research/akshare-robust-factor-search-v4
```

The current run found one research-robust candidate,
`reversal-10-top3-20`: six of eight positive quarters, median quarterly Sharpe
`0.636225`, worst quarterly Sharpe `-0.900655`, doubled-cost return `30.9870%`,
eight of nine positive one-parameter neighbours, six of six positive universe
subsets, and bootstrap positive-return probability `85.3%`. Its 2026 polluted
audit returned `-0.4437%` with maximum drawdown `-17.8407%`. This is repeated,
post-selection evidence on a current/non-PIT universe—not a new blind test—and
remains `NOT_ELIGIBLE`. `/factor-robustness` exposes it only after independently
checking the report, all five artifact hashes, ranking/window arithmetic, every
gate criterion and the 2026 audit boundary.

## Multi-sector stock transfer

The ETF-selected `reversal-10-top3-20` candidate is also frozen before any stock
outcome is read, then transferred without stock-side parameter tuning to 75 CSI
300 members: 15 current SW level-1 sectors and the five highest current CSI 300
weights in each sector. The universe is signed before price download.

```bash
.venv/bin/python scripts/build_akshare_sector_stock_universe.py

QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true .venv/bin/python \
  scripts/akshare_stock_history_ingest.py \
  --data-version akshare-csi300-sw15x5-hfq-20180101-20260821-v1

.venv/bin/python scripts/run_akshare_stock_sector_transfer.py \
  --data-version akshare-csi300-sw15x5-hfq-20180101-20260821-v1 \
  --output-dir runtime/research/akshare-stock-sector-transfer-v3
```

The ingest is resumable through per-stock signed caches, uses one uniform HFQ
series, removes five initial price-discovery sessions for stocks listed inside
the window, and resets history after the last gap exceeding 90 calendar days.
The resulting PASSED dataset has 149,903 bars from 2018-01-02 through 2026-08-21;
all 75 stocks have at least 961 retained bars.

The formal 2019-2025 transfer passed the predeclared research gate: `7/7`
positive years, `15/15` positive sector-only runs, `15/15` positive
leave-one-sector-out runs, `315.7504%` full return, `256.1430%` doubled-cost
return, `96.9%` bootstrap probability of a positive total return, and
`-34.2044%` maximum drawdown. These unusually strong current-winner results do
not cure survivorship bias: the 2026 constituents are projected backward, HFQ
is not point-in-time, limit-up/down execution is absent, and suspended holdings
may use the last close for up to 40 missing market sessions. The report therefore
remains permanently `NOT_ELIGIBLE`. `/stock-sector-transfer` verifies the report,
six artifact hashes and all gate arithmetic before the Chinese research page
shows it.

## Public read-only web deployment

`vercel.json` builds the React application for `/` and exposes the stateless
FastAPI entrypoint at `/api`. The committed deployment evidence contains the
checksum-bound daily, 1-minute and native 5-minute historical datasets plus the
accepted research, walk-forward, robustness and transfer reports. Packaging
these immutable artifacts does not add realtime identity or data authority. The
cloud context has zero trading capacity and no control or trade token, so
mutation endpoints fail closed with HTTP 503.

Low-cardinality runtime counters and gauges are available in Prometheus 0.0.4
text format at `/metrics/prometheus` locally and `/api/metrics/prometheus` on the
Vercel deployment. The export omits strategy heartbeat identities and contains
no credential, account identifier or order mutation path. The system-monitor
page links to that read-only endpoint and can search, filter and export the
current bounded audit view.

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/test_vercel_entry.py
npm --prefix web run build:vercel
npx --yes vercel@latest deploy --prod
```

The hosted application is a public research dashboard, not the persistent local
PAPER runtime. It has no provider credential, realtime minute feed, SQLite
account state, broker adapter or external order-transmission capability. See
`deployment/README.md` for the packaged-evidence boundary.

Production: <https://qtrade.erapi0neer.xyz>
