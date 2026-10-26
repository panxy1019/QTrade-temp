# 独立退市历史队列与收据覆盖审计

本交付提供独立退市队列和只读覆盖审计，供**同一个既有有界历史执行器**接入。
没有新增来源请求入口、服务或当前证券池成员。保持 `LIVE_TRADING_ENABLED=false`。

前置输入是 `PIT_DELISTED_HISTORY_PLAN.md` 中已完整重放原 SSE/SZSE、冻结 master/dataset 的
编译 bundle。本轮开始时，主代理已在集群 `f8ccac3` 成功生成该 bundle；这里不把编译成功或
计划数量当作任何价格覆盖，也不代替后续真实来源验收。此前 552/990 状态和 336 个退市 A 股
缺价格仍是输入事实；本分支没有 SSH、登录供应商、发请求、改 Supervisor 或部署。

## 独立 scope 合同

新增 `scripts.delisted_history_queue.DelistedHistoryQueue(root, bundle, *, now=None,
read_only=False)`。它先调用原编译器的 `verify_bundle`，再次核验所有文件、原文、逐行对账和
具体请求，再将原 plan 与来源 bundle 身份以不可变文件固定在独立执行 root。

退市 scope 固定：

| 字段 | 语义 |
| --- | --- |
| `source_kind=OFFICIAL_DELISTED_UNCERTIFIED` | 官方对账衍生的退市来源范围，未认证历史资格 |
| `security_status=INACTIVE_SOURCE_CLAIM_UNCERTIFIED` | 来源报告非活跃；不会改成 ACTIVE_OBSERVED |
| `scope_version` | 独立任务范围身份；不存在当前股票池 `universe_version` |
| `source_bundle_sha256` / `source_record_sha256` | 原编译包及具体官方对账行身份 |
| `master_sha256` / `source_dataset_sha256` | 原主表与冻结样本身份；不会换成最新默认值 |
| `frequency=day` / 原始 adjustflag=3 | 继续使用 HistoryStore 的已验收日线请求与价格基准 |
| `historical_available_at=null` / `historical_pit_certified=false` | 内容或日期相符不会产生历史可得时间 |

请求的上市/退市日期仍仅是声明与探测锚，不是历史成员区间或最后交易日。代码一致也不能证明
历史主体从未变更。队列只接受原 plan 的唯一证券和月窗口；更改来源、增加当前 universe 字段、
将状态重签为 ACTIVE、伪造历史可得时间都会拒绝。执行 root 不能写入不可变 bundle 内部，
已存在执行状态但丢失冻结输入时不会偷偷重建。当前活跃 1500 证券的 master、计划、root、
coverage 和发布指针均不修改。

## 复用已有恢复与存储行为

`claim`、`finish`、`recover`、`snapshot` 与原 HistoryQueue 方法签名一致，`.meta/.tasks/
.definitions/.identity` 供单一 runner 使用。每个 claim 先持久化，再交给既有 HistoryStore；
不会让策略或请求直接访问网关。月切分、原始流、Parquet、质量 gate、不可变 receipt、
交叠变更隔离、已接纳旧来源保留、指数重试及游标推进复用既有逻辑。

共享 `HistoryQueue.finish` 负责 receipt 与 source-failure 提交；独立 claim/recover 只适配
退市身份，不放宽原 `ACTIVE_OBSERVED` 计划校验。让行后由 retry 完成的当前 frontier 会推进，
旧窗口重试不会倒退游标。封存后未提交的 receipt 在恢复前是 pending，不能计入已接纳覆盖；
未封存捕获保留为 INTERRUPTED，价格计数为零。空响应保持 `EMPTY_WINDOW_UNEXPLAINED`，不
推断停牌、未上市或来源永久不可用。新旧交叠价格不同则保留旧 accepted receipt，拒绝重复计数。

队列对象本身不获取 vendor lease、不登录、不授权下载，也不能单独替代门禁。实际 writer
必须由同一有界 runner 持有原 history worker lease，使用同一 BaoStock session lease，并在
统一跨 root 的历史源失败退避、PIT 真实失败退避、当前采集优先、存储储备和硬截止后才能请求。
这些执行接入由历史执行器代理在其共享文件中实现，本提交只新增独立文件，避免冲突。

独立 progress 使用 `qtrade.delisted-history-progress.v1`，标明
`SEALED_RECEIPT_ACCOUNTING_NOT_A_NEW_BULK_REVALIDATION`。它是队列记账视图；正式覆盖必须读下述
独立审计，而不能根据存活进程、成功退出、计划数或缓存计数判断。

## 覆盖审计

`scripts.audit_delisted_history` 获取同一个 history writer lease 后，以 read-only 模式打开
退市队列，并通过 `HistoryStore.load` 逐个重放原始捕获、规范化 Parquet、质量报告、行数、
价格基准、请求范围和来源身份。审计不恢复游标、不提交未完成 receipt、不修改检查点。

只有 checkpoint 当前 `accepted_attempt_id` 指向、内容完整重放且与窗口及计划一致的 receipt
才增加 `verified_accepted_rows`。其他状态分别报告：计划但未尝试、当前 active、未封存捕获、
尚未提交的封存结果、历史 superseded receipt、空月、冲突和来源错误。已接受行数不会因重观测
重复增加。损坏原始字节或重签后虚增 checkpoint 计数会成为 `INVALID_EVIDENCE`，其他独立通过
的行仍只作已核验下界。时间或 receipt 预算耗尽时为 `PARTIAL_AUDIT_LIMIT`，不宣称全量通过。

无论取得多少内容合格日线，两个报告均保持：

- `current_universe_coverage_delta=0`、`published_qlib_versions=0`；
- `complete_historical_pit_symbols=0`、`historical_pit_certified=false`；
- `historical_available_at=null`、`independent_source_reconciled=false`；
- 不认证十年完整、ST、精确涨跌停、公司行为、集合竞价或交收。

## 验证与集群复现

本地新增 17 项检查通过：来源/状态与窗口合同、重启退避、让行重试游标、封存与未封存恢复、
scope 防篡改、零计划覆盖、缺失冻结输入、空月未知状态、交叠来源保留、审计损坏检测、审计
预算下界和全输入字节不变。与 compiler、历史队列、官方对账及 PIT 审计联合为 160 项通过、
1 项既有 Linux-only 检查跳过；Ruff 全仓、125 个 source 模块及两脚本 Linux 严格 mypy 通过。
Windows 仅替换既有目录 fsync 与 flock，真实 Linux 持久化/互斥
由主代理验收。本地合成行是接口验证，不是新增市场价格覆盖。

```powershell
$env:PYTHONPATH='E:/QTrade-pit-agent/src'
$env:MYPYPATH='E:/QTrade-pit-agent/src'
$env:LIVE_TRADING_ENABLED='false'
python -m pytest tests/test_delisted_history_queue.py tests/test_public_delisted_plan.py tests/test_history_backfill.py
python -m ruff check .
python -m mypy --explicit-package-bases --platform linux --follow-imports=silent scripts/delisted_history_queue.py scripts/audit_delisted_history.py
```

集群从核验 release 运行。执行接入仍由单一 runner 统一控制；以下命令仅做覆盖审计：

```bash
export LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src" MYPYPATH="$PWD/src"
python -m pytest tests/test_delisted_history_queue.py tests/test_public_delisted_plan.py \
  tests/test_history_backfill.py
python -m scripts.audit_delisted_history --root "$DELISTED_EXECUTION_ROOT" \
  --bundle "$VERIFIED_DELISTED_PLAN_BUNDLE" --source-revision "$VERIFIED_SOURCE_REVISION" \
  --seconds 60 --maximum-receipts 10000 --maximum-details 50 \
  --output "$BASE/pit-repair/audits/delisted-acceptance-<unique-run-id>.json"
```

正在运行的单一 writer 会使审计让行并退出 2；不抢锁或创建第二个写入者。输出文件使用排他
不可变写入，文件名需唯一。审计完整、无待恢复或损坏时退出 0；受限、pending 或坏证据退出
2，并保留明确状态。真实来源尚未取得时，正确验收结果就是零已核验价格行。
