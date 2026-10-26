# Qlib 前瞻 PAPER / SHADOW 研究账本

此模块将已经提前冻结的策略权重接到现有模拟交易管线。它不接券商，
`LIVE_TRADING_ENABLED=false`；不是历史回测结果的改名展示。

## 复用审计与职责

复用 `runtime/paper.py` 的 `build_paper_runtime`、`TradingService`、
`RiskEngine`、`OrderManager`、`PaperGateway`、`Ledger`、`SellabilityLedger`
及 `SQLiteOperationalJournal`。既有来源审计见 `BROKER_GATEWAY_REPORT.md`：
vn.py 原生网关不能补齐这里需要的前瞻研究收据，也不应为本阶段引入真实传输。
保留 portfolio → risk → OMS → execution → simulation gateway 管线。

不直接复用 `PaperBarRunner`：它按单证券下一根 bar 调度，并不提供整组合权重、
信号在实际开盘前冻结的证据、未知 ST/停牌/涨跌停/公司行动的 admission gate。
复用底层持久化执行栈，在其上增加小范围整组合适配。

## 输入与时序

`ForwardTarget` 包含 strategy/signal/data/source 身份、真实生成时间、下一次
执行时间、证券权重和研究限制。权重是有限非负数，合计最多 95%。同一账户同一次
执行时间只能冻结一个目标，冻结后不可覆盖。模型分数是信号来源，不冒充持仓。

**必须在开盘前调用 `freeze` 并实际写入 SQLite**。只有 JSON 里的旧时间戳
不够；收盘后导入昨天的目标会被拒绝。冻结时刻单独记录。生产调用使用真实时钟；
测试提供 `now` 仅用于确定性测试，调度器不得使用回拨时间绕过冻结要求。

`ObservedSession` 要求已完成、已观察到的交易日、前后交易日与日历证据、市场证据。
证券输入使用原始未复权 open/close、成交量、手数、价位、明确的 ST/停牌状态、
涨跌停价、公司行动已核对标记及证据 ID。接入器应先核验来源哈希和有效日期；本核心
不替调用方核验外部数据源真伪。未知状态使用 null，不能用 false 填补。

已完成日线使用公开开盘价进行**收盘后计算的模拟开盘成交**；成交记录写真实处理时间，
单独保留市场开盘时间和观察时间，绝不声称开盘时真实成交。成交量研究上限只使用
**本次开盘前已经取得原文收据的上一交易日成交量**，限制为 1%；当日收盘后才知道的
总成交量不能控制本日开盘数量。`previous_session_volume=null` 时禁止调仓；这仍不是
成交队列/集合竞价流动性证明。没有滑点模型，不声称现实可成交收益。

## 账户、约束与收益

- 独立 `root` 对应唯一 strategy、mode、初始资金和费用配置，配置改变需新账户。
- 先卖后买，按手数向下取整。原有引擎落实现金、最大持仓、总敞口、手续费与 T+1。
- 买入涨停、卖出跌停禁止；未知约束、ST、停牌、未知/无上一交易日成交量禁止调仓。
- 现有持仓缺失价格或公司行动未核对则 HALTED，估值和收益为 null，不能新增风险。
- PAPER 分开保存目标权重、目标数量、订单、模拟成交、实际模拟持仓、现金、净资产。
- 日收益为本日收盘模拟净资产 / 上一完整交易日收盘模拟净资产 − 1；首日基准为初始
  资金。手续费包含在现金账本，换手为成交绝对金额 / 本次开盘模拟净资产。
- SHADOW 只保留信号/目标与市场观察：没有订单、成交、持仓或组合收益。
- 不跨过缺失交易日计算连续收益；每个结果保留幸存者偏差、PIT、成交假设限制。

## 持久化与恢复

`forward.sqlite` 在本地磁盘使用 WAL + FULL：不可变账户配置、冻结目标与真实冻结时间、
完整市场输入和输入哈希、PENDING → COMPLETE 收据及分层结果。
`operations.sqlite` 是复用的订单、成交、资金和 T+1 acquisition lot 持久化账本。

先持久化 PENDING 再进入执行栈。已完成相同输入重放返回原收据；改写市场、目标或
跳日会拒绝。执行后中断保留 PENDING 并 HALT，不能再成交或自动清理标记。
这种不确定状态必须人工核对操作日志与收据，当前适配器不提供自动账本修复。

进程重启恢复 HALTED。显式 `recover(actor=...)` 需要人工操作及成功对账；有 PENDING
收据时拒绝恢复。正常运行的长驻进程可以续期健康对账凭据，但不能从 HALTED/PAUSED
自动恢复。自动任务不能在每次运行时传固定的 recover-actor 冒充人工恢复。

因此无人值守 PAPER 应由长驻服务持有 `ForwardPaperBook`，重启后展示待人工恢复。
CLI 的 PAPER observe 仅适合已明确批准的人工作业；SHADOW 不需要启用执行管线。

## 运行接口

配置 JSON 明确列出 `strategy_id`, `mode` (`PAPER` / `SHADOW`), `opening_cash`，以及
`fees.version/commission_rate/sell_tax_rate/minimum_commission/source_url`。
目标/行情 JSON 字段对应 dataclass；Decimal 用字符串，时间必须有时区。

```powershell
$env:PYTHONPATH='E:/QTrade-git/src'
python -m scripts.run_qlib_forward_paper freeze --root ACCOUNT --config config.json --target target.json
python -m scripts.run_qlib_forward_paper observe --root ACCOUNT --config config.json --target target.json --session session.json
python -m scripts.run_qlib_forward_paper status --root ACCOUNT --config config.json
python -m pytest tests/test_forward_paper.py tests/test_paper_orchestrator.py -q
```

PAPER 的人工 observe 可显式加 `--recover-actor <真实操作人>`。生产数据源适配、调度、
网页发布和下一真实交易日验收由集成阶段完成。单元测试的未来日期只是合成测试，不能
当成生产前瞻收益证据。ETF 特殊手数/T+0/税费目前未接入，不能用 STOCK 规则替代。

## 生产来源适配器

`scripts/qlib_forward_paper_adapter.py` 提供以下接口，供主工作进程调用：

- `signal_targets(root, data_version=..., source_revision=...)`：复用原有信号归档验证器，
  逐文件验证内容、重算权重，绑定当前数据/代码；返回五类目标与归档引用。
- `prediction_target(root, active_model_id=..., data_version=..., source_revision=...)`：
  复用批准预测验证器，核对 APPLIED 批准、模型身份、计算证明和所有文件哈希，再读取
  实际 scores.parquet，生成 Top50 / 95% 的独立目标。策略 ID 包含 model ID，模型换代
  不串入旧账户。调用方提供实际 ACTIVE 身份，不把历史批准等同当前 ACTIVE。
- `observed_session(store_root, batch_ids, day=..., instruments=..., calendar=...)`：
  只读取既有统一采集 `objects/` 和 `batches/`，验证收据、压缩原文、Parquet、完整日历，
  并用实际 collector 的 `completed_klines/completed_quotes/reconcile_day` 重新解析原文，
  与归档行逐项核对。返回 session 及有哈希的来源证据。缺失证券明确列出，冲突日线拒绝。
- 可选 `previous_batch_ids` 只接受上一交易日日线收据，并以本次实际开盘时刻为严格
  上界复核完成/取得时间；重放原文后才填入 `previous_session_volume`。缺少该参数或
  对应证券时保持 null。当前日成交量不替代缺失的历史收据。
- `public_book_summary(book, archive_link=..., limit<=30)`：发布有界的日收据、模拟成交/
  持仓/阻断数量、收益、限制及原归档身份。继续保留公共研究非交易授权标记。

统一采集目前的价格口径是 `SINA_NATIVE_ADJUSTMENT_UNCERTIFIED`，同一供应商 K 线与
报价的核对不是独立复权认证。适配器严格保留这一限制，不因为列名 raw_open 就赋予
独立原始价认证。其 day Parquet 不包含可证明交易当时有效的 ST、停牌、精确涨跌停价
和公司行动清算。`source_constraints.py` 审计表明 BaoStock 回补状态只允许额外阻断，
历史 available_at 未知，不能当作解除阻断的正向授权。因此这五类状态保持 null，
PAPER 会阻断调仓，而 SHADOW 可以记录真正观察到的后续价格。STAR 最小订单规则未
接入，lot_size=0 明确不准交易；ETF 不准混入当前 STOCK 适配器。

建议长驻服务顺序：核对当前数据/模型/源码 → 验证归档 → 开盘前 freeze → 收盘后取得
既有采集 batch IDs → 原文重放验证 → observe → 发布 summary。不能用人手编造的 JSON
代替生产证据。任何重启 HALT 或 PENDING 由人工明确恢复；任务轮询不带 recover-actor。

适配器测试命令：

```powershell
python -m pytest tests/test_forward_paper_adapter.py tests/test_forward_paper.py -q
```

## 公共发布与页面合同

`forward.json` 使用 `qtrade.forward-service.v1`，包含真实 `generated_at/heartbeat_at`、
源码 revision、服务状态、独立 `books`、仅含安全代码的 `errors` 和研究限制。每个账本
使用 `qtrade.forward-book-summary.v1`。外层和账本均重算 SHA256 摘要与既有公共研究
authority 字段；摘要是完整性绑定，不赋予交易授权。`forward_publication.py` 严格核对
字段、类型、有限十进制字符串、时序、状态、数量和 null 语义，任何失败不发布。

上限为 2 MB、32 个账本、每账本 30 个日收据/10 个冻结目标；只有最近日保留最多
50 条持仓、订单、成交、阻断明细，最近冻结目标最多 50 个权重。每类都有完整总数和
`*_truncated`；旧日只提供总数及结果。归档只发布 kind/身份/摘要/model ID，绝不发布
服务路径、凭据、异常正文或任意文件下载 URL。

公共只读 `GET /api/research/online/forward-books` 返回
`{publication_available, service_fresh, publication}`。校验失败或不存在时 publication 为
null；心跳过期时可显示最后发布并明确警告。页面不生成示例持仓，不用历史回测替代
前向结果；SHADOW/PENDING/无法估值的收益维持 null。`ForwardResearchPage` 的 overview、
holdings、fills、risk 分别对应账本概览、模拟持仓、模拟订单/成交、阻断与人工恢复状态。
公开页面没有下单或恢复操作。

验证命令（Windows 可加短路径 `--basetemp=E:/fp-verify` 避免既有归档测试长路径限制）：

```powershell
python -m pytest tests/test_forward_paper.py tests/test_forward_paper_adapter.py tests/test_forward_publication.py -q
python -m ruff check src/qtrade/research/forward_paper.py src/qtrade/research/forward_publication.py scripts/qlib_forward_paper_adapter.py scripts/run_qlib_forward_paper.py
python -m mypy src/qtrade/research/forward_paper.py src/qtrade/research/forward_publication.py
cd web
npm test -- --run src/pages/ForwardResearchPage.test.tsx
npm run build
```

目前测试为合成可重复证据。实际下一交易日的目标冻结、行情观察、约束阻断、模拟收据、
发布和网页仍需主集成阶段在真实时钟下完成；未发生的成交或收益不能由测试结果认证。
