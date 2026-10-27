# 收盘阶段行情导出故障与恢复验证（2026-10-27）

## 已确认的故障

16:05 的[实际生产完整抽查](evidence/qlib-publication-live-probe-f3c55cb-20261027-1605.json)为 `STALE_TRANSPORT`。外层发布服务仍成功推送，但行情索引最后成功时间停在 14:10。这是实际的导出故障，不能用进程 `RUNNING` 或目录更新时间解释为正常。

16:27 的[只读后台快照](evidence/qlib-export-before-fix-services-20261027.json)确认全部受监管服务运行。1,496/1,500 只分钟目标已到 15:00；日线当时应到仍为 10 月 26 日，1,497/1,500 就绪。原 PAPER PID `175982`、ACTIVE LightGBM、滚动 OFF 均未改变。磁盘空闲 276.075 GiB，保留 100 GiB 下限。快照中的 `/proc/meminfo` 是宿主可见值，不能作为容器实际分配内存。缺失行情继续保持未知，不推断停牌。

## 冻结数据定位与失败证据

操作员在 16:09:58 用只读 SQLite backup 冻结 ledger，并复制目录到独立检查目录。原始 lake 只读复用，所有导出写入隔离目录；受控快照时钟只用于可重复性能比较，不是生产验收时间。

该快照包含 66,782 条合法 chunk、2,694 个批次和 4,488 个输出分片。旧 `MAX_CHUNKS=60000` 在任何原始数据加载前便失败。[旧上限失败凭证](evidence/qlib-export-old-cap-failure-20261027.json)记录冷/暖两次 `UNIFIED_MARKET_CHUNK_BUDGET`，而不是推断耗时超限。

已有协议允许 12,000 个批次、每批最多 50 只证券，ledger 主键为 `(batch_id, symbol)`，故元数据上限可推导为 600,000。查询按跨频率累计剩余额度加一并立即拒绝超界；不去重、截断或缩短窗口。该上限不承诺这些数据均能在计算预算内完成。

| 隔离对照 | 冷运行 | 暖运行 | 结论 |
| --- | --- | --- | --- |
| 原计算路径，仅受控提高元数据上限 | 71.47 秒 | 60.02 秒 | [诊断](evidence/qlib-export-cap-only-benchmark-20261027.json)使用隔离的 120 秒窗口，生产 45 秒不变 |
| `8ef491d`：固定上下文、按证券分组、单次对象编码 | 45.15 秒失败 | 53.96 秒失败 | [真实失败凭证](evidence/qlib-export-first-optimization-failed-20261027.json)，未据此部署 |
| `34e8662`：同批次只读材料校验与重放共用 | 50.81 秒失败 | 45.16 秒失败 | [失败凭证](evidence/qlib-export-material-optimization-failed-20261027.json)，未据此部署 |
| `b2386c3`：保留行在完整分片边界统一验证一次 | 45.22 秒失败 | 41.14 秒通过 | [完整对照](evidence/qlib-export-single-validation-benchmark-20261027.json)，全部 4,488 分片字节和来源引用完全相同 |

第一版优化在真实 Linux 的[相关测试](evidence/qlib-export-linux-8ef491d.xml)为 367 通过、无跳过。测试通过不等于性能通过。profile 为包含式计时，项目之间重叠，不能相加；报告中的进程 RSS 是高水位，也不是缓存上限。

最终实现复用同一批次已验证的原始材料，保留每轮真实字节哈希、完整 schema/行数/准入内容检查和首次 raw replay。保留行在最终完整分片边界验证一次；越窗行、冲突、原始重放和索引验证仍保留。畸形缓存行不能进入 AVAILABLE。最终代码在[真实 Linux 的 428 项相关测试](evidence/qlib-export-linux-b2386c3.xml)中全部通过，无跳过，并经独立复核。

## 当前收盘数据的实际时钟试运行

[隔离试运行](evidence/qlib-export-live-pilot-b2386c3-20261027.json)复制实际目录，复用原始 lake 和已有索引引用的对象，只读打开当前 ledger，写入独立目录；没有覆盖生产索引，也没有传入受控时间或延长预算。当前数据选择 68,279 个 chunk、2,755 个批次，输出 4,488 个分片、102,968,844 字节。实际源读取为 119,843,988 字节；估计缓存 127,999,623 字节。

首次冷运行在 45.21 秒处拒绝推进，原索引字节保持不变。后续两轮真实时钟导出分别在 40.01 秒和 38.26 秒完成，均无隔离条目。这个结果证明了当前暖运行可完成，**没有证明首次冷运行能通过，也不是全天稳定时效 SLA**。失败后保留旧索引，实际是否恢复必须继续检查普通发布轮次和公网证据，不能把隔离试运行直接视作生产验收。

[部署凭证](evidence/qlib-export-publisher-deployment-b2386c3-20261027.json)记录只切换只读发布进程，PAPER 与其它进程 PID 未变。实际普通导出随后完成于 37.86、38.08、37.65 秒；[17:14 快照](evidence/qlib-export-post-deployment-1715-20261027.json)保留其成功与 Git 推送 `TimeoutExpired` 同时发生的状态。两次额外[公网抽查一](evidence/qlib-publication-live-probe-f3c55cb-20261027-1710.json)、[抽查二](evidence/qlib-publication-live-probe-f3c55cb-20261027-1713.json)仍为 `STALE_TRANSPORT`，不能把本地新索引当作网站已恢复。

为处理积压，先确认发布器当前已进入有界 push 阶段及 Supervisor 的 110 秒停止等待，再只向发布器请求正常退出。等待原 90 秒命令完成/清理后，确认 PID 为 0、没有遗留 Git/SSH/send-pack，再做[只读追赶计划](evidence/qlib-close-catchup-plan-20261027.json)。计划固定远端 `main`、本地 tip、4 个保留提交、107,193,571 字节新增 blob，低于既有 16 提交/256 MB 操作员上限。

已有 alias 迁移器的 `--apply` 不适用：它刻意拒绝已存在 `.delta/` 的仓库。本次只复用该工具已审查的固定 Git 传输和门禁，以独立备份 ref 保留 tip，再对精确 tip 做一次非强制追赶 push；不改 commit/tree、源索引时钟、alias 或任何普通服务时限。操作员单次 600 秒窗口不算普通 90 秒周期通过。超时后仍需检查真实 remote；状态不明不得重复推送或假写普通成功。追赶成功后也必须恢复原发布器并重新取得普通轮次与公网新鲜度证据。

## 收盘 C/D/E 的独立阻断

16:30 日完成门禁之后，日线采集为 1,497/1,500。原 990 股票范围中 987 只当前日线合格，`002827`、`600621`、`605258` 不足条件。准备流程需逐股验证复权来源，按现有每五分钟最多 25 只继续；16:48 已准备 75/987，未尝试 912 只。这不是 987 只全部完成，更不是模型预测股票数。

按该固定批量作线性估算，首轮可能到 19:50 左右结束，之后还需 provider 构建、恢复验证、预测、候选验证和发布。网络失败、重试或新门禁会延长时间，因此不承诺完成时刻。正式版本仍截至 10 月 26 日之前，网页必须显示版本日期及迟滞状态。

[股票前向本地检查](evidence/qlib-stock-forward-local-blocked-20261027.json)中 12 个账本各自通过，但全局仍因正式版本落后而 BLOCKED；PAPER 的 1,135 次阻断、零成交不能解释为收益验证通过。[ETF 前向检查](evidence/qlib-etf-forward-local-blocked-20261027.json)也仍阻断。独立的[ETF 中断调查](evidence/qlib-etf-partial-attempt-audit-20261027.json)证明首个当日响应完整，随后因当前采集优先级退出：原尝试只完成 1/30。既有服务刻意没有同日自动重试，需保留原标记和响应，另经显式可审计修复；不可删文件触发重采或把半成品作为当前诊断。

ETF 显式修复先经独立 204 项回归、Ruff/mypy，再部署不可变源码。第一轮 Linux 为 204 通过、1 个测试 fixture 清理错误，[失败 XML](evidence/qlib-etf-repair-linux-64cec48-failed.xml)保留；仅修复该测试 mock 的作用域后，真实 Linux [204 项全部通过、无跳过](evidence/qlib-etf-repair-linux-56b8a34.xml)。生产逻辑没有为测试错误放宽。

[实际只读修复计划](evidence/qlib-etf-repair-plan-56b8a34-20261027.json)绑定真实 Git tree 的 245 个 Python/日历源码文件及传输字节哈希，明确复用 1、最多追加 29。17:21 实际源门禁为空，随后显式应用计划。17:22:46 的[真实结果](evidence/qlib-etf-repair-result-56b8a34-20261027.json)是 `STOPPED_ONCE_ATTEMPTED`：当前采集重新获得优先级，新增的 23 次请求均有完整 reservation、response/raw，剩余 6 次没有发送。旧请求、失败记录、主表及旧 raw 字节完全不变；公开 latest 仍是 10 月 26 日，且没有最终 resolution。此结果不是修复成功，也没有自动重跑。任何后续只可在核验完整已封存前缀、无不确定请求、同一累计 30 次预算和真实源码后，以新的显式 plan/apply 执行；默认服务禁止自动同日重试的政策不变。

## 复现与验收界限

相关测试：

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
$env:LIVE_TRADING_ENABLED='false'
python -m pytest tests/test_unified_market.py tests/test_unified_source.py tests/test_unified_catalog_api.py tests/test_probe_unified_publication.py tests/test_qlib_public_delivery.py -q -o addopts=''
```

冻结对照目录为集群 `$B/operator-checks/market-export-20261027-1610`，含 `freeze.json`、只读 `frozen.sqlite`、原始隔离输出 `snapshot`。同目录 `candidate-benchmark.py` 是操作员对照驱动；只能在新隔离输出目录执行，不得把受控时间结果用作当前行情或收盘验收。45 秒、512 MB 源读取、128 MB 估计缓存、12,000 批次、192 MB 输出和普通 Git 90 秒上限保持不变。

生产抽查另用实际时钟与已部署的完整 main SHA，报告路径必须全新：

```powershell
python -m scripts.probe_unified_publication --expected-production-revision REVIEWED_MAIN_40_HEX --report NEW_REPORT.json
```

真实收盘 C/D/E 另等交易日 16:30 门禁及自动版本、预测、候选验证、股票/ETF 前向产物完成，分别运行既有独立审计。任何单项通过都不接受整个 STEP1；历史 PIT、复权、多年完整性和浏览器渲染仍各有独立限制。没有启动实盘、重启 PAPER、重复消费恢复授权或激活候选。STEP2 继续受 STEP1 完整验收门禁约束。
