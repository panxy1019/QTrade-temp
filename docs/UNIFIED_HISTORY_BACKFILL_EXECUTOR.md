# 持久、有界的长期历史执行器

状态：2026-10-26，本地实现与专项验证完成，集群真实来源及 Linux 运行验收待执行。
这次交付执行下载、质量校验、断点与缺口记录；不把现有计划或来源探测写成已完成覆盖。

## 审计与复用选择

审计输入：`plans/UNIFIED_DATA_ONLINE_GUIDE.md`、`docs/UNIFIED_DATA_ONLINE.md`、
`docs/UNIFIED_INCREMENTAL_DESIGN.md`、`docs/evidence/unified-history-probes-20261026.json`、
`docs/evidence/unified-history-final-acceptance-20261026.json`。

已冻结的范围为 1500 证券 × day/5min = 3000 scopes，计划哈希
`649872fb2eef3ad84614ab0c11635e56517bdcda13fde7fe76d3a9d63bef3945`，主表哈希
`aea0fe389a3cb016f020b68fe327e9e9c910e53a90ff61fff2dc81a96adc6fa5`。
三个 2016 窗口空响应仅证明那三个请求没有行；三个较新窗口登录失败没有建立任何价格范围。
旧长期分钟 172 份中只有 171 份通过，002384 原件继续隔离保留。本执行器不改写或复用它们。

| 现有组件 | 本次复用 | 原有缺口 |
|---|---|---|
| `backfill_plan.py` | 完整重建并验证原计划及主表，复用月份切分 | 原实现只封存计划，不执行 |
| `probe_unified_history.py` / 生产 BaoStock parser | 重放已保存的原始 5min 行、身份和时钟验收 | 没有长期游标、断点队列或规范化产物 |
| `vendor_session.py` | 同一个 `/root/qtrade-local/baostock-session.lock`，非阻塞申请 | 需要在每个有界请求后释放给日常/PIT |
| `UnifiedBatchStore` | 内容寻址、不可变对象、大小与 checksum 验证 | 现有 capture allowlist 只允许 Sina，故独立封装 BaoStock 捕获 |
| `BarQualityGate` / `IncrementalBar` | OHLCV、时序、金额及规范化行合同 | 历史网格、原始价格基准及缺口必须独立表达 |
| vn.py 既有执行适配 | 延续已有审计结论，不引入 gateway | 不能提供缺失公共历史、PIT 或供应商响应谱系 |

原计划 day 候选为 Sina。其当前已验收通道无法提供同样的任意月份范围、原始 OHLCV+amount
合同。本次明确适配 BaoStock day `adjustflag=3`，保留原 `task_id`、计划哈希和
`original_candidate_source`，另记 `execution_source`，没有改写原计划。
[BaoStock 自维护的 0.9.3 发布页](https://pypi.org/project/baostock/0.9.3/)
给出了日期范围、day 频率、amount、tradestatus 和 adjustment 字段示例；
官方文档页面本次访问不可读，没有据此声称十年源范围已经核实。
day 及 5min 均标记 `BAOSTOCK_RAW_ADJUSTFLAG_3_VENDOR_CLAIM_UNCERTIFIED`，与旧 qfq、
Sina native、当前日常增量和 Qlib Provider 相互独立。

## 执行与恢复合同

入口：`scripts/run_history_backfill.py`；队列与存储分别为
`scripts/history_backfill_queue.py` 和 `scripts/history_backfill_store.py`。

1. 同一主机只允许一个 history writer。运行前取得本地非阻塞进程租约；所有写入者必须
   使用相同 `--lease-dir`。持久队列采用逐 scope 原子 JSON，不把 SQLite WAL 放在 NFS。
2. 原始计划与对应主表冻结到独立 root，恢复时逐字段重新构建原计划验明身份。
   后来的 catalog/latest 不能替代该主表。每 scope 最新月份向前推进，每次最多 31 个自然日。
3. 新窗口和已到期重试分别有预算，默认每 pass 4 个新窗口、1 个重试，240 秒总预算，
   每个来源子进程最多 25 秒；允许配置但有硬上限。单个空窗口不堵塞其他证券与更早窗口。
4. 查询前先保存 active claim 与不可变 request。子进程逐行保存解码 API 行并 fsync，
   再开始下一行读取；保留完整流、SHA256 和被终止的尾部。它不是 TCP 原始报文。
5. 父进程每 0.2 秒检查当前采集、停止信号及磁盘，抢占时 terminate/kill 并 wait 回收。
   子进程继承 writer lease；即使父进程被 SIGKILL，也不会让另一 writer 与其并行。
   子进程独立硬截止会退出并释放 writer/vendor 两个租约。
6. 原始捕获先写成内容寻址 gzip 对象，之后才能解析。规范化 Parquet 与 receipt 均不可变。
   字节、schema、行 hash、原始捕获及生产 parser 可独立重放。相同 attempt 的不同输入拒绝复用。
7. receipt 先封存，后提交队列。封存后崩溃会重放；尚未封存则保留已有流并记录 INTERRUPTED。
   全局源退避先持久化再完成 claim，崩溃重放不重复增加失败次数。
8. 登录、查询失败或超时，对所有历史 day/5min 请求统一退避 30 分钟、1/2/4/6 小时，
   最高 6 小时；每个失败、空或部分窗口也有独立退避。不会在一次 pass 内立即循环重试。
   供应商租约忙或当前采集抢占只让行 60 秒，不推进尚未检查的月份游标。
9. 默认当前采集标记必须存在、时间不超前且 300 秒内，状态必须为空闲；COLLECTING、FAILED、
   不明、缺失或陈旧标记均阻断历史请求。可重复传 `--priority-state` 加入日预测等优先工作标记。
   这些标记也必须有 `state` 和新鲜 `heartbeat_at`/`updated_at`；主代理统一接入调度。
10. 新的大量写入需在 100 GiB reserve 外再有 16 MiB 单次请求余量；不自动删除旧产物。
    请求有行数、字段数、字段长度、流大小及对象大小上限，历史数据不会每日全量复制。

实际全局 source 退避由此历史队列管理；既有 PIT repair 等服务各自的退避政策仍需尊重。
共享 vendor lease 仅支持当前单主机部署，不声称跨容器/NFS 分布式互斥。

## 质量、覆盖与缺口含义

请求包括唯一证券/交易所、日期范围、频率及 `adjustflag=3`。校验严格字段、有限数值、
OHLC、时序/重复、周末、5min 网格与日期、amount/volume 的价格范围和单位一致性。
来源价格、成交额或格点不合格时保留原始对象并隔离，不能晋级为合格覆盖。

5min 只有实际观测到完整 48 点的会话计入 `qualified_rows`，部分会话保存在规范化对象里，
标记 `PARTIAL_SESSION_QUARANTINED`，不填充 K 线。day 只计实际观测、正成交量且内容合格的行。
供应商全零占位、零成交量、声称暂停交易的行保留在 raw，不当作可交易价格或权威停牌证据。

窗口可能为 `EMPTY_WINDOW_UNEXPLAINED`、`LOGIN_FAILED`、`SOURCE_QUERY_FAILED`、
`QUERY_TIMEOUT`、`QUALITY_REJECTED`、`PARTIAL_SESSIONS_QUARANTINED`、
`VENDOR_NONTRADING_OBSERVATIONS_UNCERTIFIED` 或 `CONTENT_QUALIFIED_WITH_UNRESOLVED_GAPS`。
后者证明其中若干观测会话内容合格，不证明整个月、十年历史或真实证券资格完整。

报告列出未观测的工作日候选、部分会话、供应商非交易行、未尝试日期区间、下一重试时间、
最新和已接纳 receipt。由于没有权威历史日历，工作日候选可能是节假日或其他未知原因，
不叫作已认证缺失交易日。上市日期保留供应商声明，不据此自动丢弃上市前空窗口或伪造覆盖。
重试覆盖减少时保留前一个合格对象；交叠价格变化隔离新结果；只有交叠完全一致且覆盖为超集
才切换 accepted receipt，不重复计数、不覆盖旧字节。

`progress.json` 是持久 receipt 记账视图，明确标记并非每次全量重读所有历史数据；
任何 receipt 复用、恢复或 accepted 指针切换都读取实际原始/Parquet 并完整重放验收。
正式使用一个历史对象前必须调用 `HistoryStore.load(attempt_id)`；不得仅依赖进度计数。
每 pass 独立报告请求、下载、解析、合格（含重观测）和新增合格行；对账及 Qlib 发布始终为零。

目录：

```text
<root>/plan.json, master.json              原冻结输入
<root>/scheduler.json                     历史全局源退避
<root>/tasks/<task_id>.json                逐 scope 游标、窗口和重试
<root>/lake/attempts/<attempt_id>/         request、逐行 capture、immutable receipt
<root>/lake/objects/<sha>.jsonl.gz         原始捕获对象
<root>/lake/objects/<sha>.parquet          内容合格/部分会话分标识的观测
<root>/progress.json                      完整覆盖与缺口视图
<base>/unified-data/backfill/execution-latest.json  有界摘要
```

## 复现与集群验收

本地专项 50 项通过、1 项真实 Linux 子进程检查因平台跳过；
连同计划/探测/存储/当前采集队列共 101 项通过、同一项跳过；
三份新脚本的严格 mypy（Linux 目标）与 Ruff 通过。
Windows 测试仅替换不支持的目录 fsync；Linux 应执行全部 102 项且无跳过。
真正的 fsync/flock、子进程信号及来源仍需 Linux 验收。

```sh
export PYTHONPATH="$PWD/src" MYPYPATH="$PWD/src" LIVE_TRADING_ENABLED=false
python -m pytest tests/test_history_backfill.py tests/test_unified_backfill_plan.py \
  tests/test_probe_unified_history.py tests/test_unified_store.py \
  tests/test_collect_unified_data.py tests/test_unified_queue.py
python -m mypy --explicit-package-bases --platform linux --follow-imports=silent \
  scripts/history_backfill_store.py scripts/history_backfill_queue.py \
  scripts/run_history_backfill.py
python -m ruff check scripts/history_backfill_store.py scripts/history_backfill_queue.py \
  scripts/run_history_backfill.py tests/test_history_backfill.py
```

由主代理在已检验 release、原持久卷根 B 中初始化；不用 catalog/latest 替换原冻结主表：

```sh
export QTRADE_BAOSTOCK_FREE_DATA_ACK=true
export PYTHONPATH="$PWD/src" LIVE_TRADING_ENABLED=false
REVISION=$(git rev-parse HEAD)
HISTORY_ROOT="$B/unified-data/backfill/execution-v1"
"$B/venv/bin/python" -m scripts.run_history_backfill --base "$B" --root "$HISTORY_ROOT" \
  --plan "$B/unified-data/backfill/plans/649872fb2eef3ad84614ab0c11635e56517bdcda13fde7fe76d3a9d63bef3945.json" \
  --master "$B/unified-data/catalog/versions/aea0fe389a3cb016f020b68fe327e9e9c910e53a90ff61fff2dc81a96adc6fa5.json" \
  --source-revision "$REVISION" --seconds 60 --new-windows 2 --retry-windows 0 \
  --query-seconds 20
# 后续只读已经封存的 plan/master，恢复同一个 root。
"$B/venv/bin/python" -m scripts.run_history_backfill --base "$B" --root "$HISTORY_ROOT" \
  --source-revision "$REVISION" --seconds 60 --new-windows 2 --retry-windows 1 \
  --query-seconds 20
```

先证明：完整 Linux 回归通过；原 plan/master/旧档哈希稳定；当前采集运行时请求数为零；
共享 vendor lease 被占用时不登录；空闲窗口才实际下载；失败的登录全局等待不会重复轰击源；
停止后没有存活 capture 子进程；原始与规范化对象独立重放一致。源实际返回空/失败也应封存，
不能伪报已获得覆盖。至少再做一次先封存后退出的恢复验收，并记录实际耗时/字节/行数。
当前模块不更改 Supervisor、集群服务、线上模型、预测任务或网页，主代理负责统一集成部署。

## 需合入公共状态的摘要

历史回补从“仅计划及探测”推进至有界持久执行器：原 3000 scopes 身份不变，
独立 raw day/5min 价格口径、单月份请求、逐 scope 游标、独立重试与全局 source backoff、
采集优先和共享 BaoStock 租约、崩溃恢复、不可变原始/Parquet/receipt、逐窗口缺口与覆盖回退保护。
本地实现和专项检查完成；集群真实来源、完整 Linux 集成与生产状态展示由主代理继续验收。
不能据此宣布十年历史、PIT、公司行为、全交易日历、独立来源对账或新 Qlib 版本已经完成。
`LIVE_TRADING_ENABLED=false`，没有 broker/gateway、交易、模型激活或自动预测路径。
