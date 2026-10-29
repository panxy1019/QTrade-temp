# STEP2 Strategy Registry / Builder 接口审计与非生产验证

日期：2026-10-28。范围：设计、现有接口审计、临时目录中的就绪测试。
依据是本次读取的仓库源码、`plans/STEP2_USER_GUIDE.txt`、
`plans/STEP1_STEP2_EXECUTION.md` 与 ADR-193/195/198/200。
归档指南已在仓库找到，不依赖原桌面文件。最新阶段授权允许本准备工作与 STEP1
并行；本文不宣告 STEP1 或 STEP2 生产验收完成。

## 1. 结论与复用边界

已有完整的 Alpha158 + Ridge/LightGBM 研究合同、StrategyRegistry 登记、受限网页、
签名 Git 队列、SQLite 工作器和不可变实验归档。应扩展这些接口，不建第二套策略库、
任务框架、组合引擎或模拟账本。

主要缺口是将独立 Factor Registry / Evaluation 的身份和结果绑定进正式策略合同。
网页不能选择单个注册因子、任意权重法或再平衡周期；这些输入当前会被拒绝。
独立因子报告也不能因其有 checksum 就自动变成 StrategySpec、PAPER 或模型审批。

`docs/VNPY_GAP_ANALYSIS.md` 已记录 vn.py 复用选择及 paperaccount/会计缺口。
本次复核研究边界：vn.py 的网关、事件、订单对象不提供因子公式版本、数据 READY
事件、评价来源、策略内容身份及历史信息时点合同。这里复用现有 QTrade 注册/归档适配；
Qlib 原生计算/回测继续复用。本文核对了项目实际调用点，未重新联网审计 vn.py
上游，也未在本机安装或执行 Qlib 原生引擎。

| 能力 | 状态 | 源码及实际范围 |
| --- | --- | --- |
| 完整策略身份与生命周期 | 已实现 | `src/qtrade/strategies/registry.py`：不可变规格、家族/版本唯一性、血缘、状态和角色分离；Qlib 研究合同/任务原子关联 |
| READY 数据研究选择 | 已实现 | `strategy_contract.ready_dataset/verify_bound_spec`：全档案字节、唯一 READY 准入事件、manifest/source/dataset/universe 核对；允许旧 READY，不要求它是当前 serving head |
| 正式 Qlib 策略版本 | 已实现 | `freeze_strategy/validate_strategy`：内容地址身份，冻结特征、模型/处理器、标签/分段、组合、执行/成本与完整代码 revision |
| 当前网页 Builder 子集 | 已实现 | `web/src/pages/QlibWorkflowPages.tsx`：READY 数据、已发布策略起点、固定 Alpha158、Ridge/LightGBM、Top-K、淘汰数、最少持有步、仓位、成本、资金、种子、切分；编辑使预览失效 |
| 单因子/多因子共同历史基线 | 已实现固定协议 | `signal_baselines.py`、`run_qlib_strategy_comparison.py`：三种价量因子、等秩多因子、等权控制、Ridge、LightGBM 共用数据/标签/证券资格及原生 Exchange；等权全池是单独分配控制 |
| Qlib TopkDropout / Exchange | 已实现调用 | `run_qlib_baseline.py` 实际构造 `TopkDropoutStrategy` 并调用 `backtest_daily`；方向限制和容量字段来自冻结 Provider，成本由规格映射 |
| Qlib WeightStrategyBase | 已实现固定适配 | 比较脚本内 `EqualUniverse` 子类实现全池等权；未开放通用网页权重配置 |
| Factor Registry / Evaluation 到 StrategySpec | 待适配 | 当前 `StrategySpec.factor_set` 仅记录完整 Alpha158 特征版本；缺独立因子/评价引用和验证器 |
| 注册因子选择、再平衡周期、权重方法 | 新增受限合同后适配 | 当前只支持固定日频、TopkDropout；不得把 `hold_thresh` 当成可配置固定平仓期或再平衡周期 |
| FactorEvidenceCatalog / 分钟临时结果 | 已有只读证据 | `FactorEvidenceCatalog.tsx`、`strategyRegistrationPreflight.ts` 明示非 FactorRegistry、非完整 StrategySpec，不可绕过正式登记 |
| XGBoost / Alpha360 | 未开放 | 当前 allowlist 明确拒绝；需要新的版本化能力、处理器恢复、共同比较及资源验证 |

## 2. 当前链路与准确的输入/输出

```text
已准入不可变日频数据
  → 受限 spec + 当前已发布 worker revision
  → 冻结 strategy_definition / strategy_version
  → 同一签名 requests/<job_id>.json
  → Worker.ingest 的本地全量核验
  → StrategyRegistry: EXPERIMENT / role NONE + 原子 job link
  → runner 再核验 + Provider 重建一致性 + 拟合/恢复证明 + 原生回测
  → 校验归档 → COMPLETE 研究结果 + 独立 TRAINED model artifact
```

`StrategySpec` 的 15 个字段为：strategy_id、name、family、version、
parent_strategy_id、git_commit、data_version、universe、factor_set、model、
portfolio_rule、parameters、split、created_at、updated_at。TimeSplit 只证明区间
有序不重叠，不能单独证明未来标签隔离。Qlib adapter 的额外校验不能省略。

当前 `strategy_spec()` 固定 family=QLIB_ALPHA158、parent_strategy_id=None、
factor_set=(qlib-0.9.7-Alpha158-v1,)，parameters 引用完整冻结合同的 checksum。
完成训练产生独立 model artifact；冻结信号中的 model_artifact_id 仍为 null，
不能把训练后对象写回原来未拟合的策略定义。

| 接口 | 输入 | 输出 / 副作用 |
| --- | --- | --- |
| `GET /research/catalog` | 公共只读目录 | datasets/data_binding、strategy_versions、strategies/job/model links、worker revision/heartbeat；描述缓存不等于每次重读所有 Provider 字节 |
| `POST /research/specification` | 登录同源请求，恰好 `{spec}` | 签名 `qtrade.qlib-specification-preview.v1`：完整 definition、source_revision、expected_strategy_version；no-store；不入队 |
| `POST /research/jobs` | `{spec, request_id, expected_strategy_version}`；version-bound spec 必须有预览版本；request_id 为 32 位小写 hex | 202/QUEUED、job_id、spec_sha256、strategy_version；向既有 Git 队列 append HMAC envelope，不表示已通过工作器或训练完成 |
| `GitQueue.append` | requests/cancellations、32 位 job_id、确定性 envelope | 同 job 相同字节幂等，不同字节冲突；固定 SSH 地址/host key、非 force push 的 CAS |
| `Worker.ingest` / `ResearchContracts.verify(register=True)` | 合法 HMAC、spec digest、expected version、当地 READY 档案 | durable QUEUED 或 REJECTED；新接受研究原子登记 EXPERIMENT/NONE；重放不重复生命周期，不自动训练/批准/恢复 |
| `bound_training_definition` / `frozen_result_binding` | 实际 revision、完整 spec、数据数据库、expected version、restore proof | runner 独立再核验；封存前重新读取 strategy-definition.json，并将其与配置/版本/revision 匹配 |
| `GET /research/experiments/{id}` / `.../{id}/template` | 已发布实验身份 | 不可变结果或可复用原规格；模板不从今天默认值补缺失字段，也不静默迁移执行器版本 |

现有 spec 限制：Alpha158；linear(Ridge alpha=1)/lightgbm；supported_a_share；
topk 10–100、n_drop 1–20 且不超过 topk、hold_thresh 2–20、risk_degree 0.1–0.95、
买卖成本各 1–100 bp、min_cost 0–100 元、初始资金 10 万–1 亿元。
日频/下一交易日开盘/100 股单位/前日量容量约束均固定；非线性冲击成本为 0。
这些是研究近似，`rules_certified=false`，不是逐日交易规则或真实竞价成交认证。

## 3. Factor Registry / Evaluation 到正式策略的最小接线

以下是与 Factor 设计子任务对齐的**拟议合同**，当前 API/注册器不能接收；新测试
验证它们当前失败关闭。正式实现时需要明确 schema 版本和服务端验证，不可把未知
字段塞进旧 parameters 或改客户端 TypeScript 类型即称完成。

1. Registry 输出 `factor_ref={factor_id, factor_version, definition_sha256}`。
   factor_id 为语义命名空间 ID；factor_version 为完整不可变定义的内容摘要。
   定义冻结公式/参数、Qlib 或自有函数实现/包版本与源文件摘要、依赖引用、频率、
   价格基准、缺失/预热政策、输入时点。Alpha158 集合引用还冻结有序的 158 项
   名称/公式/definition refs，不能仅以字符串 Alpha158 代替逐项来源。
   集合引用为 `feature_set_ref={feature_set_id, feature_set_version,
   ordered_factor_refs_sha256}`，与 `STEP2_FACTOR_CONTRACT_DESIGN_20261028.md`
   的 Registry/Evaluation 设计一致。
2. Evaluation 输出
   `evaluation_ref={evaluation_id, specification_sha256, report_manifest_sha256}`。
   评价规格绑定有序 factor_refs、现有完整 READY data_binding、确切证券名单、逐日
   资格/剔除 ledger hash、label_definition_sha256、split_sha256、指标政策版本和
   实际 source_revision。报告保存独立因子 IC/RankIC、分组/稳定性、相关性、有效样本
   与拒绝/失败范围；模型整体 IC 不能替代逐因子评价。
3. 受限 Builder 载入注册因子版本/已核验评价作为明确起点，显式选择 signal_kind
   （固定单因子/固定合成/受支持模型）、模型、Top-K、持有/淘汰规则、日频再平衡、
   允许的权重法和成本。第一版可继续只开放 day/TopkDropout；“固定”也要进入合同。
   对照应声明仅改变信号/模型还是改变组合规则，不能把不同组合当成纯模型比较。
4. 服务端确认 factor/evaluation 真实存档、数据/证券/标签/切分身份匹配后，生成
   新版 strategy_definition；把全部引用及组合/执行配置计入 strategy_version。
   StrategySpec.factor_set 引用完整不可变因子版本集，合同和侧档保存完整对象。
   复用现有 registry 事务和 job links，不由评价成功自动授予 CANDIDATE/PAPER。
5. 继续使用相同 `/specification → /jobs → GitQueue → Worker → archive`。
   新 Factor Evaluation kind 如需入队，必须在既有 worker 中新增明确的有界分支，
   沿用签名、capacity、取消、时限、资源优先级和归档校验。现有 worker 只接受已知
   kind，不能把新评价内容伪装成当前 QLIB_RESEARCH_JOB。
6. 单因子/合成信号复用 `PriceFactorSpec`、`signal_family` 和 Qlib 表达式/DataHandler；
   排名组合复用 TopkDropout；全池等权复用已存在 WeightStrategyBase 适配；全部交给
   同一原生 Exchange 和成本/资格合同。不重新实现表达式、排序成交或会计框架。

下一轮非生产 Builder 草案可采用以下闭合集合；这是开发输入合同，当前 endpoint
没有接线，不是已开放能力：

| signal_kind | 允许输入 | 固定/拒绝边界 |
| --- | --- | --- |
| FACTOR_SINGLE | 一个完整 factor_ref、方向/标准化政策、匹配 evaluation_ref | factor_id/version 必须真实注册；没有模型、不能把未来标签当特征；所有资格/缺失政策冻结 |
| FACTOR_COMPOSITE | 有序 factor_refs、已声明等秩合成政策、匹配批量评价/相关性 refs | 首版复用现有 multifactor_equal_rank；不同样本不能拼接，不做看过测试后自动选权重；其他权重待单独允许列表及测试 |
| MODEL_SCORE | 完整 feature_set_ref、有序依赖摘要、linear/lightgbm、冻结处理器/超参、匹配评价 refs | 首版仍完整 Alpha158；XGBoost/Alpha360、自定义模块或训练命令拒绝；TRAINED artifact 独立保存 |

三种输入都必须显式绑定同一 READY 数据、标签、时间切分、证券与资格 ledger、
TopkDropout/固定 day 再平衡、holding/drop/risk/cost 合同及实际 revision。
下一版预览、入队、worker、runner、报告均验证相同新增 schema；不允许客户端
声明新 signal_kind 而后台悄悄使用旧 Alpha158 默认值。更换因子/方向/合成/模型/
组合/成本的任意一项都生成新身份。

已有 `ExperimentRecord`/不可变文件与 Qlib run 归档可复用；前者本身只验证部分字段，
不能充当以上完整跨档案核验器。追加引用必须由 ingest、runner、结果封存和公共读取
四处一致核验，避免只检验网页预览。

## 4. 因果输入、研究有效性与新增 PIT 的追加复验

当前合格日频子集可以开展明确限定的因子评价，无须等待完整十年或全频率数据。
每个实验必须冻结实际证券名单、动态资格/剔除原因、数据版本、标签、分段及来源；
明确披露当前流动性样本的幸存者/选择偏差。causal_intervals 的因果资格只在已选偏样本
内成立，不能消除源证券池偏差，不能把 990 股票样本写成 1500 证券全市场。

- 信号价格/量输入只能来自决策时已完整的会话；`signal_family` 要求确切共同 21
  会话，不前向填补缺失；`causal_capacity` 使用已完成前一会话量。
- 特征依赖不能使用未来 Ref/居中窗口；未来收益只能存在于明确隔离的标签部分。
  train/valid/test 的日历日期间隔校验与 runner 的实际交易日 +2 标签隔离均保留；
  未成熟末端标签不能变成 0 或有效评价样本。所有拟合处理器仅用训练区间。
- `pit_repair.known_state` 同时校验 available_at 与 observed_at 均不晚于 decision_at。
  今天观测到的旧公告或供应商状态不能回填成系统当年已知；缺失历史可得时间保持未知。
  此参考适配与实际执行来源的准入是不同事项，不能仅因函数存在就称执行已接新状态。
- 新的历史成员、退市股、状态或公司行为证据形成新数据版本、独立评价和新实验。
  拟议追加链接 `revalidates_evaluation_id` 指向原评价，并保存旧/新 data_binding、
  factor refs、标签/切分/组合/成本及差异原因；原报告、输入、失败和结论均保留。
- 当前系统已保证不同 READY 绑定生成不同策略版本、独立 job；但尚无完整的
  evaluation revalidation link。不可把本次两个临时数据版本测试写成 PIT 改善已完成。
  `strategy_spec()` 当前也不会自动生成血缘；后续需适配新的同家族 StrategyLineage
  或不可变复验侧档引用，不能修改已注册原规格。

## 5. 失败合同

| 边界 | 当前失败 / 拟议规则 | 后果 |
| --- | --- | --- |
| 未登录 / 跨源 | HTTP 401 / 403 | 不预览、不入队 |
| 未开放模型/特征/字段、错误日期、重叠或 purge 不足、非法数值 | HTTP 422；底层 validate_spec 的显式 ValueError | 不补默认值、不执行调用者代码/路径 |
| READY 绑定不在目录 | HTTP 422 | 不切换到 latest 或 serving head |
| worker 过期、目录不可读、API 与 worker 冻结器不一致 | HTTP 503 | 无可确认的预览或提交 |
| 提交预览版本不一致 / 同请求 ID 不同内容 | HTTP 409 | 重新预览 / 保留原 job；不得重写原请求 |
| 当前非终态任务达到 3 | HTTP 429；worker 独立超过容量时 durable REJECTED | 不扩大并发绕过日常采集/推理优先级 |
| Git CAS/传输失败 | HTTP 503，提交未获确认 | 内容不变时同 ID 重试；不能把 202 等同于 worker 接受 |
| HMAC、kind、job ID 或 digest 不匹配 | worker 忽略不可信条目 | 不执行、不公布敏感条目正文；这不是带有效签名的 durable REJECTED |
| 当地 Provider/READY/source/revision/expected version 漂移 | 已知研究请求写为 REJECTED / IMMUTABLE_RESEARCH_INPUT_REJECTED | 不注册新策略；runner 仍独立再验 |
| 未确认旧运行 / 子进程失败 | INTERRUPTED / FAILED；原事件和部分归档保留 | 不自动恢复旧任务或覆盖成成功 |
| 拟议 factor/evaluation 丢失/篡改/跨数据或 split 引用 | 新验证器须明确拒绝；目前尚未实现 | 不把评价缺失当成空列表/零指标/自动选优 |
| LIVE 环境非 false | 当前研究登记/runner 明确拒绝 | 不开启真实交易；无策略→gateway 路径 |

当前代码的 terminal 状态历史、独立模型审批与 PAPER recovery/reconciliation 保持各自
合同。StrategyRegistry 的 post-SHADOW 人工授权及现有 promotion gates 不因本设计改变。
halt 后新风险仍受原风险/OMS/执行边界约束；本文没有放宽任何生产门禁。

## 6. 已执行验证与后续非生产集成清单

新增 `tests/test_step2_strategy_readiness.py`，9 项通过、0 failure、0 skip。
测试调用真实 FastAPI 登录/预览/提交、HMAC、Worker.ingest、READY 核验和
StrategyRegistry；仅 Git 传输由内存队列替代，所有数据库/Provider fixture 位于 tmp_path。
没有调用 Worker.execute，不训练模型、不启动进程、不请求远端队列。

| 检查 | 证据 |
| --- | --- |
| Ridge / LightGBM 各自完整 Builder 接线 | 非默认 Top-K/淘汰/持有/仓位/成本/资金/种子到冻结合同、HMAC 请求、worker 和 EXPERIMENT；模型数据库逻辑 dump 完全相同 |
| 幂等 | HTTP 两次提交相同 response；worker 两次 ingest 一份 job、一条初始事件、一次登记、一个 link |
| 未开放能力 | factor_refs、evaluation_ref、weekly rebalance、risk_parity、module_path 共 5 例均 422，无入队/登记 |
| 新数据追加 | 第二 admitted fixture 形成第二策略/job；原 Provider 字节、请求 envelope、合同均相同，偏差/非 PIT 限制仍在 |
| 预览与 ingest 间代码变化 | HTTP 202 后模拟不同实际 worker revision，durable REJECTED；重放仍拒绝且无登记 |

PowerShell 复现（从仓库根目录）：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m pytest tests/test_step2_strategy_readiness.py
python -m ruff check tests/test_step2_strategy_readiness.py
```

现有相关检查已一起回归：122 passed、1 skipped、0 failure；跳过的是
`tests/test_qlib_worker.py:149` 的 Linux process identity check，在 Windows
不适用。专用新增 9 项无跳过；此 Windows 回归不能替代后续 Linux 检查。

```powershell
python -m pytest tests/test_step2_strategy_readiness.py tests/test_qlib_bound_research_integration.py tests/test_qlib_strategy_contract.py tests/test_qlib_worker.py tests/test_qlib_workflow.py tests/test_strategy_registry.py tests/test_strategy_comparison.py
```

测试开发中的首次运行未设置 src 导入路径，collection 失败；修正命令后，最初两项
比较含实时 observed_at 的 model snapshot 而失败。最终改为比较数据库逻辑 dump，
直接证明模型状态/审批事件没有写入；没有冻结或改写时钟。最终 9 项及 Ruff 通过。
本机为 Python 3.14.6、pytest 9.1.1；现有 FastAPI TestClient 发出一条 httpx 弃用警告。

待合同实现后的隔离集成门槛：

1. 小型日频 Provider：包含稳定样本、缺一会话、IPO/退市区间、STAR、两种时点、
   不成熟标签和零方差因子；冻结名单/资格 ledger，独立重算报告和相关性。
2. 注册依赖 DAG 与公式白名单：未知表达式/未来依赖/循环/改包源码/标签进入特征
   必须拒绝；相同语义 ID 的新定义形成新版本，旧定义只读。
3. Factor Evaluation 实际队列分支：HMAC、三任务容量、重复/取消/重启/超时、
   采集和预测优先级；失败留存；不得以 viewed test 再调参后宣称未见样本。
4. factor/evaluation refs→strategy version→同队列→原生 Qlib TopkDropout/
   WeightStrategyBase/Exchange→不可变归档的端到端验证；更改任何单个引用/成本/
   持有/再平衡规则都改变身份，跨数据/label/split 报告不能拼接。
5. 新 PIT 数据版本复验：明确链接原评价与实验，旧报告/失败字节保持不变，
   输出样本/来源差异与指标变化；不自动升级历史认证或晋级。

限制：本轮完成接口审计和既有链路的非生产就绪验证。尚无新的 Factor Registry、
独立 Evaluation、生产 STEP2 API、原生 Qlib 全计算测试、Linux 集成、Git 网络往返、
集群训练/新 PIT 复验、生产网页视觉或真实下一交易日验收。本轮只修改本文及专用测试。
LIVE_TRADING_ENABLED=false；既有 ACTIVE、信号、账本、服务和生产文件未用作测试写入。
