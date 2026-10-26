# 活跃与退市历史共用来源调度

本交付在既有 `run_history_backfill.run_pass` 中接入官方退市 day probe queue。
原活跃计划和 3000 scopes 的身份、原始价格基础、当前 1500 覆盖口径均保留。
执行固定选择 `HistoryQueue` 或 `DelistedHistoryQueue`，不加载任意队列插件。
没有 SSH、真实供应商请求、部署、旧档案修改或 PAPER/LIVE 晋级。

## 来源与复用审计

| 已有组件 | 本次复用与缺口处理 |
| --- | --- |
| 官方退市 compiler/bundle | 重放原 SSE/SZSE 文本、主表、dataset 和 adapter；不把来源报告日期当历史可得时间或最后交易日 |
| `DelistedHistoryQueue` | 独立 source scope/持久任务，不引入 `universe_version` 别名；仅走 day/adjustflag=3 |
| `HistoryStore` | 同一不可变 request、原始捕获、parquet、receipt 和重放质量验收；空月仍为未知缺口 |
| `HistoryQueue.finish/recover` | 保留月游标、独立窗口重试与 crash 恢复；增加统一来源状态提交回调 |
| 原 `scheduler.json` | 原来各 root 独立的来源失败计数不足以阻止另一 root；迁移为共享来源失败钟的本地副本 |
| 既有历史 runner/service | 同一 worker lease、有界 capture、vendor lease、当前采集优先、PIT 失败退避、100 GiB 磁盘储备及停止回收 |
| vn.py 执行/网关 | 延续既有缺口审计，无新增使用；不能提供缺失历史来源、PIT 可得时间或官方原文 |

前置提交为原历史执行器/收据审计及退市 compiler、queue/audit 合同。
专属定义见 `PIT_DELISTED_HISTORY_PLAN.md`、`PIT_DELISTED_EXECUTION_CONTRACT.md`、
`UNIFIED_HISTORY_RECEIPT_AUDIT.md`。以下调度机制补齐原合同尚未实现的跨 root 门禁。

## 一个共享失败钟与请求意图

`<base>/unified-data/backfill/source-backoff.json` 绑定规范化 base、原活跃 root 和原计划哈希。
文件含校验和、连续失败数、下次来源尝试时刻、最近收据身份、单个未完成请求意图及恢复记录。
所有写入都必须持同一个 `history-backfill.lock`；生产 BaoStock session lock 保持原固定位置。
原本已迁移的 root 缺失共享状态、状态损坏/迁移、运行期间被改写或丢失都会停止请求，不能通过
新建退市 root、删除共享状态或换一个活跃 root 重置失败钟。

顺序为：保存 claim → 不可变 request → 原子共享 intent → 有界捕获 → 不可变 receipt →
重放 receipt → 先提交共享来源结果及本地来源副本 → 提交任务游标。
intent 含实际 root 和完整 claim；下一次任一 cohort 获得 writer lease 时先核验 request、
原文及 receipt，恢复该 intent，然后才能发新请求。receipt 已封存但游标未提交的失败不会
被另一 root 绕过，重放也不会重复加失败数或扩展原截止时间。

`LOGIN_FAILED`、`SOURCE_QUERY_FAILED`、`QUERY_TIMEOUT` 连续失败退避 30/60/120/240 分钟，
上限 6 小时。成功返回的空窗口或内容拒绝可以说明端点返回了响应并重置来源故障计数，但仍
保留各窗口原有未知缺口/重试，覆盖不会因此增加。合作让行、正常停止、磁盘暂停不重置失败数。
无终态捕获的崩溃保留真实 `INTERRUPTED` 收据，并在共享调度中按不可信来源结果保守退避。
未纳入共享 intent 的旧版未封存尝试同样保守处理一次，不把旧的不明结果当健康依据。

升级时先重放原活跃 root 的旧失败收据，保留其真实截止及失败数；再处理旧 active claim。
退市直接 CLI 必须显式提供已经冻结的 `--observed-root`。原 root 的本地 scheduler 在以后每轮
都从共享状态同步，因此另一 cohort 的成功不会被旧本地截止重新覆盖。

PIT 的 `next-pass.json` 仍独立读取：真实失败、不可信回执、非零 returncode、连续失败会阻断；
可信正常 `SUCCEEDED`/`PARTIAL` 的队列间隔不阻断。`RUNNING` 仍服从固定共享 vendor lease。
PIT 与共享历史门禁在请求前、持 session lease 登录前、捕获运行中均重查。运行中丢失共享
文件会停止并回收子进程，拒绝用内存副本静默覆盖异常状态。

## 单服务交替调度，两个覆盖出口

原服务不带新参数时仍只调度活跃队列。显式同时提供 bundle/root 后，同一个 Supervisor
进程按 300 秒 start-to-start 周期交替活跃、退市；每轮总预算仍最多 240 秒、同样的窗口预算，
不会每周期分别启动两套来源 worker。初始轮从活跃开始；重启会保留两个队列游标及共享失败钟。
独立 heartbeat 记录 `running_cohort`、`last_pass_cohort` 和各 cohort 最近收据摘要哈希。

| 产物 | 含义 |
| --- | --- |
| 原活跃 `<root>/progress.json` / `backfill/execution-latest.json` | 活跃计划自身的收据统计；退市运行不覆写它 |
| 退市 `<probe-root>/progress.json` / `backfill/delisted-execution-latest.json` | 独立退市统计；`current_universe_coverage_delta=0`，无 `universe_version` |
| `backfill/service-state.json` | 15 秒独立服务心跳与调度状态，不是覆盖凭证 |
| 各 cohort 原始 lake/receipt | 唯一可重放内容凭证；计划数、请求数、已检查月份均不是合格覆盖 |

## 复现与父代理验收

先运行离线回归，不调用任何真实来源：

```powershell
$env:PYTHONPATH='E:/QTrade-history-agent/src'
$env:MYPYPATH='E:/QTrade-history-agent/src'
$env:PYTHONUTF8='1'
$env:LIVE_TRADING_ENABLED='false'
python -m pytest tests/test_history_shared_execution.py tests/test_history_backfill.py tests/test_delisted_history_queue.py tests/test_public_delisted_plan.py
python -m ruff check scripts/history_source_gate.py scripts/history_backfill_queue.py scripts/run_history_backfill.py scripts/run_history_backfill_service.py tests/test_history_shared_execution.py tests/test_history_backfill.py
python -m mypy --explicit-package-bases --platform linux --follow-imports=silent scripts/history_source_gate.py scripts/history_backfill_queue.py scripts/run_history_backfill.py scripts/run_history_backfill_service.py
```

本地新增 29 项共享执行检查通过；四组联合 168 项通过，2 项真实 Linux flock/SIGALRM 检查
在 Windows 跳过。Linux 子进程硬截止检查现用仅存在于该测试子进程 PYTHONPATH 的
`sitecustomize.py` 切换到 tmp 目录真实 flock，验证 fake login 确实开始、退出 124、两个锁释放；
生产 session lock 没有添加可绕过的环境变量，也不需要停止真实采集来让测试通过。

父代理在已核验 release 上执行上述四组及全套 pytest，验证真实目录 fsync/flock/子进程。
然后分别运行两个只读审计，保留实际输出，不将两个 coverage 相加计入当前1500：

```bash
export LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src" MYPYPATH="$PWD/src"
python -m scripts.audit_history_backfill --root "$OBSERVED_ROOT" \
  --source-revision "$VERIFIED_SOURCE_REVISION" --seconds 60 --maximum-receipts 10000
python -m scripts.audit_delisted_history --root "$PROBE_ROOT" --bundle "$BUNDLE" \
  --source-revision "$VERIFIED_SOURCE_REVISION" --seconds 60 --maximum-receipts 10000
```

审计 `VERIFIED` 表示实际原文/收据/任务一致，不表示完整历史或 PIT 认证。首次执行前没有
退市状态也可以审计为计划零覆盖；冻结输入丢失/现有执行状态不完整、预算不足或活动 writer
均须读取具体非完成状态，不能当作采集完成。

以下是供父代理统一部署的服务参数，本文没有执行。必须替换成已核验原活跃 root、原 plan/master、
完整 bundle 和新独立 probe root；将新参数加入**既有一个** Supervisor program，不开第二服务：

```bash
python -m scripts.run_history_backfill_service --base "$BASE" --root "$OBSERVED_ROOT" \
  --plan "$ORIGINAL_PLAN" --master "$ORIGINAL_MASTER" \
  --delisted-probe-bundle "$BUNDLE" --delisted-probe-root "$PROBE_ROOT" \
  --source-revision "$VERIFIED_SOURCE_REVISION" --seconds 240 \
  --new-windows 4 --retry-windows 1 --query-seconds 25
```

来源执行仍需既有 `QTRADE_BAOSTOCK_FREE_DATA_ACK=true`，且 `LIVE_TRADING_ENABLED=false`。
可在真实失败退避中启动服务检查两类摘要的零请求状态；不得修改截止来制造成功。恢复后的
首轮真实请求、空月、合格 rows、退出状态、无遗留子进程和旧原始字节不变由父代理验收。
历史可得时间、独立来源对账、完整交易状态、公司行为及完整 PIT 认证仍保持未知/未认证。

### 当前部署路径交接

主代理提供的实际 base 为
`/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade`；子代理未登录集群。
既有 program 为 `qtrade-history-backfill`，配置为
`/etc/supervisor/conf.d/qtrade-history-backfill.conf`。保持其原 `--root` 为
`$B/unified-data/backfill/execution-v1`。它读取自己的冻结 plan/master 和任务状态；当前采集
优先标记则仍读取 `$B/unified-data/service-state.json`，不要把后者目录误作 `--observed-root`。

先在已核验 release 离线打印真实 bundle、原 root 与追加参数。此命令不会发来源请求，也不写状态：

```bash
B=/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade
export LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src"
"$B/venv/bin/python" - "$B" <<'PY'
import hashlib, json, shlex, sys
from datetime import UTC, datetime
from pathlib import Path
from scripts.history_backfill_queue import read_signed
from scripts.plan_public_delisted_history import verify_bundle
base = Path(sys.argv[1])
observed = base / "unified-data/backfill/execution-v1"
plan = read_signed(observed / "plan.json")
master = read_signed(observed / "master.json")
assert plan["master_sha256"] == master["evidence_sha256"]
bundles = sorted((base / "pit-repair/delisted-plans").glob("*/manifest.json"))
assert len(bundles) == 1, "Select an explicit verified bundle; do not guess among plans"
bundle = bundles[0].parent
manifest = verify_bundle(bundle, now=datetime.now(UTC))
probe = base / "pit-repair/delisted-execution" / manifest["plan_sha256"]
print(json.dumps({"observed_root": str(observed), "observed_plan": plan["evidence_sha256"],
    "observed_master": master["evidence_sha256"], "bundle": str(bundle),
    "probe_root": str(probe), "manifest_evidence_sha256": manifest["evidence_sha256"],
    "manifest_bytes_sha256": hashlib.sha256(bundles[0].read_bytes()).hexdigest(),
    "append_to_existing_service_command": shlex.join([
        "--delisted-probe-bundle", str(bundle), "--delisted-probe-root", str(probe)])}, indent=2))
PY
```

父代理核对输出（逻辑 evidence hash 与文件字节 hash 不混用），将打印的新参数追加到既有一个
program，并按统一 release 更新其 directory、Python 源路径及 `--source-revision`。
随后使用已有部署方式重读/更新该 program；无需新增第二份 Supervisor 配置：

```bash
supervisorctl reread
supervisorctl update qtrade-history-backfill
supervisorctl status qtrade-history-backfill
```

如果 bundle 多于一个，必须明确选择并单独 `verify_bundle`，不取目录字典序最新项。
这一步只生成/核验具体参数；服务更新与真实采集由主代理负责。
