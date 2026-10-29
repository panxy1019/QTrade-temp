# S2-D 最小接线方案：受保护因子策略登记

日期：2026-10-28。本文件是基于实际源码边界的设计，没有改 API、GitQueue、Worker
或网页，也没有提交任务。S2-C 目前只实现严格合同及 Registry 原子关联；S2-B 新的
16 日报告仍是诊断档案，未自动登记为策略或取得执行权限。

## 已核对的复用点及缺口

| 现有文件/能力 | 最小接线 |
| --- | --- |
| `src/qtrade/api/research_workflow.py` | 复用 actor/session、same-origin、JSON 16,000-byte 上限、worker freshness、三任务容量和幂等语义 |
| `src/qtrade/research/workflow_queue.py` | 原样复用固定 SSH 目的地/host key、HMAC、append-only、非 force Git CAS、requests/cancellations；不加第二队列 |
| `scripts/run_qlib_worker.py` | 复用 jobs/events、容量、取消、owned-child 身份恢复、超时和 predictor 优先；增加明确 kind 分支，不进入模型 control/promotion/rolling 分支 |
| `scripts/qlib_research_contracts.py` | 当前仅发布 QLIB_ALPHA158、解析 Qlib spec，不能直接拿它登记 factor；新增受限 factor adapter 并使用同一 StrategyRegistry |
| `src/qtrade/research/factor_strategy_contract.py` | 复用 freeze/validate/verify_bound/register；注册前重读真实 FactorRegistry、EvaluationStore、READY 字节，EXP/NONE 上限不变 |
| `web/src/pages/QlibWorkflowPages.tsx` | 复用现有登录/预览/提交/失效机制；增加单独因子登记面板，不把新 refs 塞进旧 model spec 或 parameters |

当前 Worker.ingest 只执行已知 QLIB_RESEARCH_JOB（及已有独立控制 kind）。当前
workflow.validate_spec 固定 Alpha158/linear/lightgbm，拒绝因子字段。现有拒绝测试
必须保留。当前 execute 并未实现 factor backtest；新登记不能调用旧模型 runner。

## 第一步只开放 REGISTER，不暗示执行

建议新增两个同登录/同 origin 的路径：

- `POST /research/factor-strategies/specification`：body 恰好 `{spec}`，输入为 S2-C
  的八字段因子 spec；返回独立的 `qtrade.factor-strategy-preview.v1`，含冻结定义、
  expected_strategy_version、worker source/catalog refs，no-store，不入队。
- `POST /research/factor-strategies/jobs`：body 恰好
  `{spec, request_id, expected_strategy_version, expected_catalog_sha256}`，返回 202/QUEUED。
  actor 从 session 得到；不能由客户端覆盖。409 表示预览版本/目录/源已变，需重预览。

路由复用同一鉴权 helper、固定 GitQueue 和共享容量。旧 `/specification`、`/jobs`
及 Qlib envelope 的 schema 完全保留，不给旧 model spec 加未知字段。通用取消路径
继续按同一 job_id 工作。

新的 HMAC envelope 使用固定 kind `FACTOR_STRATEGY_REGISTER_V1`，闭合字段包括
job_id、actor、factor_spec、factor_spec_sha256、expected_strategy_version、
expected_catalog_sha256、source_revision、data_version_id、live_trading_enabled=false。
所有字段进入 HMAC；source/data 必须等于预览绑定和 worker 重建值。不得接收文件路径、
模块、表达式、训练命令、网关、promotion、activation 或 PAPER 字段。

REGISTER 的结果只证明不可变关联已保存，状态为 EXPERIMENT/NONE，并明确没有
模型、信号执行或组合回测结果。未来实际回测需另外的受限 kind、实现版本、原生
Qlib signal/Exchange adapter 和验证门槛；不能让当前 backtest_implemented=false
的合同悄悄变成执行合同。

## 预览数据来源与体积：不能在 API 端加载整份报告

现有 API 从固定 observations Git public descriptor 读取数据，不拥有集群本地
FactorRegistry/EvaluationStore。S2-B raw 报告 191,719,859 bytes，gzip 4,172,334 bytes，
均不能放进 16,000-byte 请求；gzip 也已超过当前 read_public 默认 3,000,000-byte
上限。不要扩大这些限制来传输完整报告或改由浏览器提交自签事实。

先由实际 worker/批准的归档发布器生成有界 `factor-catalog.v1`：

1. 按精确 evaluation_ref 从本地完整存储核验报告和 READY；发布 compact descriptor，
   含 factor definition/ref、S2-C evaluation_context、源文件绑定、实际状态、限制、
   指标摘要和完整 report manifest ref。必须是真实本地核验后的对象。
2. 每个 data binding 的真实交易日历只发布一份，带 calendar_sha256。因子面板据此
   校验 split 边界、真实会话 purge 和标签终点，不用自然日假设。
3. descriptor/目录数有显式上限、版本索引和分页；目录保持现有读取预算。明细成员、
   dependency ledger、源快照和完整报告留在不可变归档，由 worker 本地独立验证。
4. 当前 S2-B/C 文件仍在 operator evidence。接线前须把精确字节导入批准的不可变
   research archive/catalog 路径，校验 manifest、冲突拒绝、持久化和恢复；不能让新
   队列依赖唯一临时文件。复制不改变原 evaluation_id，不补造旧计算时点。

需要为 S2-C 模块新增一个公开、纯预览适配器：从已核验的 published template /
descriptor 构建允许的 spec 变体，复用私有合同构造，不让 API 直接调用 `_compose`。
它校验原 template、固定 factor/evaluation/data/source、目录日历摘要，只允许已开放
name/segments/portfolio/execution/parent 变化。不同因子/评价必须选择其对应 descriptor。
这会改变模块的实际源 hash，新预览身份必须绑定新的发布源，不静默迁移旧定义。

目录的 checksum 只证明内容一致性，不独立证明历史事实或客户端可信度。API 只从
固定可信发布渠道取 descriptor；worker 仍要重读实际存储。缺少真实归档或当前 worker
不支持该 source 时，503/拒绝，不能用自签 binding 代替。

## Worker 执行边界、幂等和崩溃

ingest 先校验 HMAC、已知 kind、精确 fields/size、false 权限、job_id/spec digest 及
共享容量，durable 保存同一 jobs/events。相同 job 不同 envelope 必须冲突。
不要在每轮 ingest/predictor tick 中同步解析 190 MB 报告；完整验证进入一个有界
owned research child，在现有 predictor 优先规则下调度，复用取消/超时/进程身份恢复。

child 必须：

1. 核对实际 source_revision 和完整固定源字节；按精确 refs 查批准的本地归档，不能
   follow latest；完整重验评价、日历、双时钟、资格/源清单和 READY。
2. 调用 freeze/verify_bound，要求输出 version 等于 expected_strategy_version；
   若 refs/data/成本/父/source 任一不同则 durable REJECTED，不回退 Alpha158。
3. 用同一个 StrategyRegistry 调用完整 register_strategy 适配器，原子写 spec、
   contract、parent lineage、job link 和唯一初始 transition。只允许 EXPERIMENT/NONE。
4. 输出有界、不可变 registration receipt；worker 核对退出码、ref、合同 hash 和
   Registry job link 后才报告 COMPLETE。不得创建模型、训练日志或执行收益伪结果。

jobs 与 StrategyRegistry 是两个现有 SQLite 数据库；S2-C 只保证 Registry 内关联的
原子性，不是跨数据库事务。先持久化请求再开始 child；若 Registry 已提交而 worker
尚未记录结果便崩溃，保留该事实和 INTERRUPTED，按原恢复/显式 resubmit 规则处理。
重验和登记必须幂等，不能因重启创建第二 transition、回滚/打开生命周期或自动执行。
正式集成要有跨此断点的注入测试和可审计关联查询，不能把两个 commit 宣称为一个。

## Builder 表现与最小验收

单独展示精确注册因子版本、对应评价/数据日期与状态、实际样本/缺失、IC/RankIC/
稳定性/相关性摘要，以及未知历史时钟、已看历史、毛标签等限制。只允许单因子与现有
等秩组合、日频 TopkDropout/当前成本边界；所有改动使预览失效。页面行动名应明确为
研究策略登记；未接入的模型、再平衡、权重法、非零滑点和执行功能不能伪装成可选项。

集成验收必须包括：

- 新 kind 的 auth/origin/HMAC/16KB/容量/取消，以及旧 Qlib 全部现有拒绝仍成立。
- 预览到 ingest/child 间的 factor/evaluation/source/READY/parent 漂移均拒绝；未知
  refs、不成熟标签、坏报告/目录、假时钟和重复请求不能产生部分登记。
- 同 job 幂等、不同 payload 冲突、Registry/worker 两次 commit 间故障和重启证据；
  三任务共享上限、collector/predictor 优先、不抄近路到模型/ACTIVE/PAPER。
- 原生 factor signal/portfolio/backtest 属后续独立能力：同数据、资格、执行成本、
  容量的净值 oracle 和归档验收完成前，不发布回测完成或交易准入。

本轮只提出这些接口和验收点；当前 API/Worker/Web 仍按旧 allowlist 拒绝新字段。
