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

按该固定批量作线性估算，首轮可能到 19:50 左右结束，之后还需 provider 构建、恢复验证、预测、候选验证和发布。网络失败、重试或新门禁会延长时间，因此不承诺完成时刻。正式日版本仍截至 2026-10-26，网页必须显示版本日期及迟滞状态。

[股票前向本地检查](evidence/qlib-stock-forward-local-blocked-20261027.json)中 12 个账本各自通过，但全局仍因正式版本落后而 BLOCKED；PAPER 的 1,135 次阻断、零成交不能解释为收益验证通过。[ETF 前向检查](evidence/qlib-etf-forward-local-blocked-20261027.json)也仍阻断。独立的[ETF 中断调查](evidence/qlib-etf-partial-attempt-audit-20261027.json)证明首个当日响应完整，随后因当前采集优先级退出：原尝试只完成 1/30。既有服务刻意没有同日自动重试，需保留原标记和响应，另经显式可审计修复；不可删文件触发重采或把半成品作为当前诊断。

ETF 显式修复先经独立 204 项回归、Ruff/mypy，再部署不可变源码。第一轮 Linux 为 204 通过、1 个测试 fixture 清理错误，[失败 XML](evidence/qlib-etf-repair-linux-64cec48-failed.xml)保留；仅修复该测试 mock 的作用域后，真实 Linux [204 项全部通过、无跳过](evidence/qlib-etf-repair-linux-56b8a34.xml)。生产逻辑没有为测试错误放宽。

[实际只读修复计划](evidence/qlib-etf-repair-plan-56b8a34-20261027.json)绑定真实 Git tree 的 245 个 Python/日历源码文件及传输字节哈希，明确复用 1、最多追加 29。17:21 实际源门禁为空，随后显式应用计划。17:22:46 的[真实结果](evidence/qlib-etf-repair-result-56b8a34-20261027.json)是 `STOPPED_ONCE_ATTEMPTED`：当前采集重新获得优先级，新增的 23 次请求均有完整 reservation、response/raw，剩余 6 次没有发送。旧请求、失败记录、主表及旧 raw 字节完全不变；公开 latest 仍是 10 月 26 日，且没有最终 resolution。此结果不是修复成功，也没有自动重跑。任何后续只可在核验完整已封存前缀、无不确定请求、同一累计 30 次预算和真实源码后，以新的显式 plan/apply 执行；默认服务禁止自动同日重试的政策不变。

## 18:04 的真实恢复结果

[一次性追赶结果](evidence/qlib-close-catchup-result-20261027.json)在 17:28:21 确认远端精确等于计划 tip，耗时 225.07 秒；[发布器恢复凭证](evidence/qlib-close-catchup-publisher-resume-20261027.json)核验原包装器与 `b2386c3` 源码，PID 改为 `294214`。普通 Git 仍为 90 秒，未强推、重置、改树或重写索引时间。[17:37 实际状态](evidence/qlib-close-live-services-1738-20261027.json)记录普通导出与发布已成功，PAPER 仍为 `175982`。

第一次[17:31 公网核验](evidence/qlib-publication-live-probe-f3c55cb-20261027-1731.json)已通过，但[17:36 核验](evidence/qlib-publication-live-probe-f3c55cb-20261027-1736.json)仍过期：固定 commit 的 8 MB 目录以 identity 传输耗时 95 秒，检查结束索引年龄 198.15 秒。保留这项失败，不将检查开始时新鲜等同结束时通过。

只读探针现对固定不可变 Git 目录、索引、碎片支持有界 gzip，API 仍用 identity。压缩传输量和解压后原文件量分别受原上限约束；拒绝炸弹、截断、CRC 错误、尾随、多 member、未知编码及 Content-Length 不符。所有签名、Git blob、原字节 SHA、截止时间与 180/1,200 秒新鲜度门禁保持不变。原始 Git 字节不因传输编码变化。

| 真实完整公网检查 | 公开 commit | 完整耗时 | 索引 / 目录年龄 | 结果 |
| --- | --- | --- | --- | --- |
| [17:47](evidence/qlib-publication-gzip-probe-f3c55cb-20261027-1747.json) | `d1f28e07933f9516cc86ce7dcc7973a3250f7643` | 24.85 秒 | 145.20 / 106.84 秒 | FRESH_TRANSPORT |
| [17:49](evidence/qlib-publication-gzip-probe-f3c55cb-20261027-1749.json) | `7968f1d0e4e7e4ae2dfc769f5b75ccdf2c40dbba` | 18.12 秒 | 91.92 / 68.32 秒 | FRESH_TRANSPORT |

两次均核验 1,500 个目标身份、3,000 个索引条目和五组固定 API/公开碎片逐行一致，生产页面版本当时均为 `f3c55cb`。这证明两个不同普通发布轮次的实际交付，仍不是全天 SLA 或全量 1,500 证券字节复验；日线 1,497/1,500、五分钟 1,496/1,500 仍为 PARTIAL。

ETF 的[唯一续补计划](evidence/qlib-etf-continuation-plan-0a545a8-20261027.json)仅接受实际已封存的 1+23 前缀、两类已知采集优先级停止码和剩余六个符号。两个完整源码 manifest 的核心来源/存储/门禁/日历 blob 必须相同，续补记录实际操作员 `0a545a8`，保留原 `56b8a34` 冻结修复身份。不得重新发送已完成请求或再次续补。[真实结果](evidence/qlib-etf-continuation-result-0a545a8-20261027.json)在 17:51:40 核验 30/30、最终 resolution 和 latest；29 个追加 reservation/response 完整，原始失败、请求、主表与响应字节不变。

`0a545a8` 的[真实 Linux 测试](evidence/qlib-close-operator-linux-0a545a8-20261027.xml)为 446 项全部通过、无跳过，包含 ETF 六组与公网探针/交付两组测试，另经独立审查及 Ruff/mypy。[ETF SHADOW 部署](evidence/qlib-etf-shadow-deploy-0a545a8-20261027.json)只重启该无成交 SHADOW 服务为 `299932`，核验实际 cwd/PYTHONPATH/实盘关闭，其余 PID 完全不变。初次管理接口返回形状检查在任何停进程前拒绝，修正后仅对指定 ETF 进程正常 stop/start；没有重启其它进程或 PAPER。

[18:00 本地审计](evidence/qlib-etf-forward-local-audit-1801-20261027.json)通过后，[18:02 独立公网捕获](evidence/qlib-etf-public-capture-20261027-1802/report.json)验证当日诊断 30/30、SHADOW RUNNING/无 errors。将其中[公开响应原始字节](evidence/qlib-etf-public-capture-20261027-1802/forward.body.json)按 SHA256 原样送入集群，[18:04 独立审计](evidence/qlib-etf-forward-public-audit-1805-20261027.json)再回放实际原始来源、事前冻结/准入、观察与公开条目：`PASSED`，三个目标收盘输入均存在。审计的 `publication_file_sha256` 与 HTTP 捕获均为 `1b138a966ff48bcedd63da0d8bed73e4992297d61ecc7316d01edbb5304e6c47`，因此没有把本地输出冒充公网交付。

这只接受旧目标 `463cff…` 在 10 月 27 日的 ETF SHADOW 观察；目标及新 10 月 28 日目标已由 `59dc24cd` 实现冻结，历史实现身份如实保留，当前 `0a545a8` 服务不改写。新目标仍 prospective，没有观察。订单、成交、持仓为空，现金/净值/收益为空；native close、分红拆分、复权、PIT 和 ETF 交易规则限制继续显示。

[18:00 Qlib 只读检查](evidence/qlib-close-preparation-progress-1801-20261027.json)为 425/987 已准备，562 未尝试，无待重试；正式日版本仍止于 10 月 26 日，ACTIVE 激活次数仍为 1。首轮准备估计 19:50—20:00，之后还需真实 provider/预测/signals/候选验证和 C/D/股票前向验收，不是整个链路 ETA。[后台回补/PIT 快照](evidence/qlib-close-history-pit-services-1753-20261027.json)记录历史队列 49,032 合格行（日线 1,224、五分钟 47,808）、独立退市队列 47 行，低优先级任务仍按政策让位。PIT 当前 990 范围刷新不等于历史 PIT，以上队列数也不等于整个数据湖。空闲 275.56 GiB，保留 100 GiB 下限。

## 18:27 主分支正式交付与剩余队列

PR31 已合并为 `b64b5f9e9a77a59a140d366fc1cf9a0186a90cfa`。不能仅凭 Vercel 构建成功判定正式别名已切换：合并后的首次 API 检查仍返回旧 `f3c55cb`，18:24 的实际默认生产端点才确认新 `main`。随后[完整实际公网核验](evidence/qlib-publication-probe-b64b5f9-20261027-1825.json)在 18:24:46 完成，耗时 18.48 秒，公开提交固定为 `be60c348bd317c7a52924d7033b6ba148826a845`，索引/目录年龄 83.54/68.31 秒，`FRESH_TRANSPORT`。

这次再次核验 1,500 个身份、3,000 个条目、目录/索引 Git blob 与原始字节哈希、五组 API 与引用分片的逐行一致。日线 1,497/1,500、五分钟 1,496/1,500 仍为 `PARTIAL`；不能据此接受所有证券原始来源、整个交易日 SLA、网页渲染或 C/D/E。

[18:27:31 的只读准备快照](evidence/qlib-close-preparation-progress-1828-20261027.json)为 524/987，尚有 462 只未尝试和 1 只等待重试。自动完成文件不存在，正式日版本仍止于 10 月 26 日；ACTIVE 激活次数为 1。受监管服务全部运行，原 PAPER `175982` 和其它 PID 不变。

实际调度日志还记录一个边界缺陷：上一子周期开始于 UTC `10:13:45.739667`，写入冷却时间 `10:18:45.739667`；下次子进程在 `10:18:45.723310` 开始，提前约 16 毫秒，返回 `WAITING_FOR_RETRY`，父进程再等待一整轮五分钟。`10:23:45.723720` 才继续到 524。这是少一次准备轮次，不是采集器停止，也不能通过改时钟或增大每轮 25 个新证券额度掩盖。

窄范围调度修复正在测试。当前 `request.json` 明确冻结 `55b5b0044649a28fd84c77364ca9908df0cee7a4`：直接换新 `--source-revision` 会触发 `BLOCKED_DAILY_INPUT_CHANGED_REVIEW_REQUIRED`，用新代码谎报旧 revision 也会破坏证据。因此当前自动 owner 保持运行，修复只在冻结请求完成或另行审查解决后部署，不修改既有请求。剩余首轮按原节奏估计约 20:00，重试、源失败、后续 provider 构建、预测、signals、候选验证和独立 C/D/股票前向验收仍会增加时间。

[剩余 C/D/股票前向清单](QLIB_CLOSE_REMAINING_GATES_20261027.md)补充精确只读查询、归档定位、真实公开响应提取和失败状态处理。滚动 OFF 不阻止独立候选后续版本验证；但精确 prediction binding 的终态失败不会自动重排。股票 API 外层包装需要与其内层签名 publication 分别保存，不能把 ETF CLI 接受包装对象的行为套用到股票审计器。

## 18:49 持续交付与调度修复验证

新一轮[实际完整公网检查](evidence/qlib-publication-probe-b64b5f9-20261027-1850.json)在 18:49:04 完成，仍为正式 `main b64b5f9`，但公开提交已前进到 `2dffe7dd1e460695d0d421839f2f5db458281428`。耗时 17.56 秒，索引/目录年龄 118.62/106.92 秒，全部目录和五组引用抽查通过 `FRESH_TRANSPORT`。这与 18:24 是不同实际交付帧，覆盖仍为 PARTIAL。

中间一次完整探针返回 `UNIFIED_PROBE_HTTP_FETCH_FAILED`，没有验收报告。保留[失败后的操作员观察](evidence/qlib-publication-probe-failure-b64b5f9-20261027-1847.json)：它记录实际 CLI 返回码和工具耗时，未捕获具体 HTTP 失败阶段，也没有声称源已过期或后台停机。后一次检查的[独立调用凭证](evidence/qlib-publication-probe-attempt-b64b5f9-20261027-1850.json)保留真正开始/结束时钟与 CLI 输出。后续成功不删除失败，更不构成连续可用性 SLA。

[18:46:32 只读检查](evidence/qlib-close-preparation-progress-1846-20261027.json)为 600/987，387 未尝试，没有待重试。自动完成标记仍不存在，正式 provider 截至 10 月 26 日；全部受监管 PID 未变，原 PAPER `175982`、ACTIVE 激活次数 1 保持。磁盘空闲 275.49 GiB。剩余首遍至少还需 16 个有效批次，按五分钟节奏约 80 分钟；已有边界空转、后续网络失败仍可能延长，当前规划估计 20:10—20:30，再加 provider、预测、signals、候选验证与独立验收，不能当作全部 STEP1 的 ETA。

调度修复 `c3e08c205c032c85aae6682449106898e67fae5e` 只在子进程首次获得合法签名 `WAITING_FOR_RETRY` 时，按真实 aware deadline 至多等待一次、最多 300 秒，然后用新的实际 UTC 重走所有 cycle 门禁。第二次仍 WAITING 就返回，不循环，不提前覆盖 attempt，不重复准备。原单个 900 秒 alarm 覆盖两次检查和等待，源/锁/25+5/采集优先级/存储/head/epoch 检查不变，另经独立源码复核。

Windows [56 项最终测试](evidence/qlib-scheduler-windows-short-20261027.xml)、实际 Linux [56 项隔离测试](evidence/qlib-scheduler-linux-c3e08c2-20261027.xml)均无失败、错误或跳过，另有 Ruff/mypy；这是相同测试跨平台通过，不能相加为 112 个不同用例。[Linux 凭证](evidence/qlib-scheduler-linux-c3e08c2-20261027.json)绑定 c3e08c2 和 250 个源码/测试/配置文件，逐项核对 Git blob、SHA256、大小，在独立目录验证，没有切换任何生产进程。

[Windows 报告](evidence/qlib-scheduler-windows-report-20261027.json)如实保留最初三个 FileNotFoundError 的工具观察、原 HEAD 复现及短临时路径全部通过的结果；路径长度是证据支持的推断，不冒充保存了初次原始日志。扩大测试还发现固定 10 月 26 日 fixture 使用真实当前日默认值，[55 通过/1 失败原始 XML](evidence/qlib-scheduler-windows-before-fixture-fix-20261027.xml)保留；只给该测试的两次调用补上 fixture 日期，生产默认时钟和准备逻辑未改。

原始 XML 与 JSON 都通过 Git `-text` 属性保留字节。失败 XML 的原始断言空白和 Windows 换行也是凭证内容，不能为了通用空白检查而重排；归档后再次比较 Git blob 与捕获 SHA256，防止换行归一化破坏报告引用。

**修复没有部署到当前 data owner。** 今日冻结请求必须继续用 `55b5b004`；等其完成或有另行审查的解决后，才使用新真实 revision 部署。当前 Linux 目录只是隔离测试产物，不能当作新服务来源或今天 C/D/E 已通过。

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

调度修复的四组针对性回归如下。Windows 使用经过确认的短临时目录，避免路径长度问题；目录必须全新，执行前确认其位于预期临时根下。普通 Linux 不需要该变通。

```bash
PYTHONPATH="$PWD/src:$PWD" LIVE_TRADING_ENABLED=false python -m pytest \
  tests/test_qlib_data_service.py tests/test_daily_collection_binding.py \
  tests/test_prepare_qlib_incremental.py tests/test_pit_collection_queue.py \
  -q -o addopts=''
```

ETF 独立审计使用新捕获的真实公开响应，输出必须与来源、local root 和捕获目录分离。保留真实时钟，旧捕获过期时应如实拒绝，不修改日期；已成功的续补不得再次 apply。

```bash
cd "$B/releases/0a545a8a6741c9e210d3077c877d8912170fc9e9"
PYTHONPATH="$PWD/src:$PWD" LIVE_TRADING_ENABLED=false "$B/venv/bin/python" -B \
  -m scripts.audit_etf_forward_transition \
  --archives "$B/online-research/etf-trend" \
  --local-root /root/qtrade-local/etf-shadow-service \
  --published-forward FRESH_PUBLIC_ETF_FORWARD_CAPTURE.json \
  --target-id 463cff4d89878ef3c29c785d97e86835d9c71fc19d77102433b1171a1385d299 \
  --day 2026-10-27 --revision 0a545a8a6741c9e210d3077c877d8912170fc9e9 \
  --output "$B/online-research/unified-etf-forward-audit-NEW.json"
```

真实收盘 C/D/E 另等交易日 16:30 门禁及自动版本、预测、候选验证、股票/ETF 前向产物完成，分别运行既有独立审计。任何单项通过都不接受整个 STEP1；历史 PIT、复权、多年完整性和浏览器渲染仍各有独立限制。没有启动实盘、重启 PAPER、重复消费恢复授权或激活候选。STEP2 继续受 STEP1 完整验收门禁约束。
