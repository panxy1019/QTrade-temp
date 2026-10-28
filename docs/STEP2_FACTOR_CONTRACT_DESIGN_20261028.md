# STEP2 因子登记与独立评价：接口审计及非生产准备

日期：2026-10-28；状态：**设计与现有接口准备检查，未实现 Factor Registry，未上线独立因子评价任务**。
以上是本文件初始审计时点。12:52更新：后续授权下已实现四个已有因子的非生产登记
与独立评价，并完成固定版本891股×5日实际运行；生产入口仍未接线。当前实现分别见
[登记器](STEP2_FACTOR_REGISTRY_PROGRESS_20261028.md)与
[评价](STEP2_DAILY_FACTOR_EVALUATION_PROGRESS_20261028.md)，保留下面的原接口缺口证据。
本文件依据本轮用户允许与 STEP1 并行开展设计、接口审计、非生产测试的指令；不改变
STEP1 真实收盘、PAPER/SHADOW、公开交付及浏览器验收的完成条件。

原指南已保存在 [plans/STEP2_USER_GUIDE.txt](../plans/STEP2_USER_GUIDE.txt)，
SHA256 `b6b18b8c1c24840d30bc74e94ebc32157cf589e4a81eb577e0e3039b6d07c6b6`。
无需依赖已缺失的 Desktop/step2 路径。最新范围见
[STEP1_STEP2_EXECUTION.md](../plans/STEP1_STEP2_EXECUTION.md)。本次审计起点 Git HEAD
为 `916930884ee26e78c14c167d07fdad747df25660`，不把工作树内其他并行修改当作该提交内容。

## 1. 复用与来源缺口审计

| 已读来源 / 接口 | 已有能力 | STEP2 复用和仍缺能力 |
| --- | --- | --- |
| `requirements-qlib.txt`；`scripts/run_qlib_baseline.py:389` | 固定 pyqlib 0.9.7；Alpha158、DatasetH、处理器及训练切分 | 复用表达式计算和 DataHandler；当前没有逐因子公式/依赖清单和登记服务 |
| `docs/evidence/qlib-online-source-audit-20261026.json` | 2026-10-26 安装环境 Qlib 0.9.7/Python 3.11.17 的接口和模块源码哈希；含 DatasetH、DataHandlerLP | 作为已记录版本证据；该清单没有 Alpha158 loader、表达式算子、`calc_ic`、`SigAnaRecord` 源码，不能宣称这些已在本轮逐项原生验收 |
| `docs/evidence/step2-native-interface-20261028/`（本轮补充） | 实际固定 Qlib 0.9.7 的 loader/handler/ops/eva/record/provider 源码哈希及签名；158 对默认公式；微型合成 IC 检查 | 已补只读源码/描述符及纯数值接口核验；没有 Provider/数据计算、Recorder 写入或独立任务实现，详见第 5.1 节 |
| `src/qtrade/research/factors.py:13`、`:66` | Decimal 动量、跳过期动量、反转、实现波动率、换手；PriceFactorSpec 的名称/窗口/计算 | 复用计算函数；`factor_id` 仅由名称/窗口构成，没有公式、源版本、时点、依赖、登记日志 |
| `src/qtrade/research/signal_baselines.py` | 三个价量评分的别名、截面平均秩、等秩多因子、等权控制；严格共同 21 会话输入 | 将已有信号映射到明确因子版本；等权控制不是可宣称有效性的价格因子 |
| `src/qtrade/research/metrics.py:180`、`:232` | IC、RankIC、ICIR、分组标签均值、最高组换手、组间差；按证券身份对齐期间换手 | 复用并做原生指标一致性验证；没有通用因子间相关性、RankICIR、版本化指标政策、独立评价归档任务 |
| `src/qtrade/research/factor_search.py:21` | 基础因子多期诊断，标签须成熟于指定结束日内 | 复用诊断而非另写引擎；缺少全局交易日历/逐证券共同网格、双信息时钟、版本清单及缺失原因账本 |
| `scripts/run_qlib_baseline.py:472` | 按日对模型预测与 raw label 做 Pearson/Spearman、保存 signal-diagnostics | 这是模型分数评价，不是对 Alpha158 每一列的独立因子评价；当前直接计算，没有通用 SignalRecord/SigAnaRecord 接线 |
| `src/qtrade/research/intraday_factor.py:1554`、`:2825`；`robust_factor_search.py` | 专用盘中稳定性、规则相关性、时间窗/子样本/重采样诊断 | 可借鉴输出与统计政策；盘中规则、阈值、标签不适用于日频 Alpha158，不能改名复用旧结论 |
| `src/qtrade/research/data_versions.py:39`、`:106`；`strategy_contract.py:106` | 完整文件复验、内容寻址版本、READY 准入事件、历史版本及冻结研究数据绑定 | 复用原目录和验证器；不新建平行数据版本体系；必须补独立评价的绑定适配 |
| `src/qtrade/research/experiment.py`；`artifacts.py:13`；`workflow.py:31` | 时间切分、实验记录、规范化 hash、顺序写入禁止覆盖 | 复用身份和归档原则；现有记录没有一等公民的因子定义引用、独立报告清单、复验父引用/指标政策 |
| `src/qtrade/research/pit_repair.py:66` | `known_state` 同时检查 available_at、observed_at 与带时区 decision_at | 复用双时钟判定；现有 FactorObservation 只有 known_at 和日期校验，不能代替逐依赖、精确决策时刻检查 |

vn.py 的复用决策沿用 [VNPY_GAP_ANALYSIS.md](VNPY_GAP_ANALYSIS.md)（2026-08-12 的研究
pin，不是本轮已安装证明）、[QLIB_BASELINE.md](QLIB_BASELINE.md) 的来源审计及
[QLIB_WORKFLOW_INTEGRATION.md](../plans/QLIB_WORKFLOW_INTEGRATION.md) 的复用边界。
vnpy.alpha 的因子/数据集思路可参考，但不能因名称同为 Alpha158 就认定公式、处理器、
数值或版本相同。当前已运行 Qlib 链路应继续复用；vn.py gateway、事件和账户组件
不提供本任务缺少的历史可得时间、因子身份或实验谱系，无须在此引入网关或另建框架。

本机默认 Python 3.14 有 pytest/ruff，无 qlib/vnpy 模块。本轮没有安装或升级环境。
后续明确授权的只读 SSH 调用复用集群已安装 Qlib 0.9.7，仅检查公开包源码/描述符和
微型合成 IC；没有访问数据/生产服务、训练模型、写生产注册器或变更采集/执行门禁。

## 2. Factor Registry 的拟定合同（尚未编码）

登记对象是“如何计算”的不可变定义，不是每天的评分，也不是策略、模型或执行授权。
建议扩展现有研究包和不可变 JSON/队列模式；不要建立新的表达式引擎或复制全部计算代码。

对外稳定引用：

```text
factor_ref = {factor_id, factor_version, definition_sha256}
feature_set_ref = {feature_set_id, feature_set_version, ordered_factor_refs_sha256}
```

`factor_id` 使用有语义的命名空间，例如 `qtrade.price.momentum20`；`display_name`
只作展示。`factor_version` 是规范化完整定义的内容摘要，`definition_sha256` 校验归档
文档。名字、公式、参数、时点、依赖或实现任一变化必须产生新版本；同一 id/version
只允许逐字相同的幂等登记，差异必须拒绝。旧引用与旧实验始终可解析，不能将“latest”
指针解析结果写回历史。重命名别名只追加别名事件，不篡改旧定义。

完整定义需要冻结以下字段；具体 schema 名称建议 `qtrade.factor-definition.v1`：

| 字段 | 合同 |
| --- | --- |
| 身份 | schema、factor_id、factor_version、display_name、definition_sha256、实际 registered_at、source_revision |
| 计算 | engine（QLIB_EXPRESSION / QTRADE_PRICE_FUNCTION）、引擎包版本、模块源码 SHA256、可信计算入口、精确公式或函数+参数、计算精度/单位 |
| 输入 | frequency、security_type、价格基础/复权版本、必需字段、所需历史会话数、交易日历 hash、缺失/异常值政策；不允许用 observed rows 偷换交易会话 |
| 依赖 | 有序 dependency refs（原始字段及其数据合同、其他 factor_ref、处理器配置/拟合产物引用）；拒绝未知引用、循环和跨频率/价格基础隐式混合 |
| 信息时钟 | 每个输入的 available_at/observed_at 政策、有效日/as_of 规则、决策 barrier；未知时钟仍为空，不能用 15:00 或今日采集时间补造历史发布时刻 |
| 数据处理 | raw/processed 明确区分；变换顺序、截尾/标准化参数、拟合范围和产物哈希；训练后拟合状态不能反向进入训练截止前的前瞻声明 |
| 权限及限制 | 研究用途、历史 PIT 认证状态/限制、live_trading_enabled=false、external_order_transmission=false；登记没有晋级/交易权限 |

第一批映射应保持既有数学语义，不为对齐名字修改现有函数：

| 当前信号或函数 | 拟定语义与依赖 |
| --- | --- |
| momentum20 → PriceFactorSpec(momentum,20) | `close[t]/close[t-20]-1`；21 个合格共同日频 close |
| reversal5 → PriceFactorSpec(reversal,5) | `-(close[t]/close[t-5]-1)`；6 个 close |
| lowvol20 → PriceFactorSpec(low_volatility,20) | 最近 20 个简单收益的负总体标准差，ddof=0；21 个 close；不年化 |
| skipped_momentum | `close[t-skip]/close[t-lookback-skip]-1`；窗口与 skip 都进入版本 |
| multifactor_equal_rank | 对上述三个确切版本的截面平均秩取等权平均；平均秩和 ties/证券排序政策、共同证券集合进入定义 |
| equal_weight_universe | 共同资格证券上的控制信号，不伪造市场预测因子或独立 alpha |
| liquidity_turnover | 平均 volume / free_float_units；流通股数必须有独立版本、单位和双时钟。当前缺少合格历史输入时保持不可评价，不使用今日股本回填 |
| Alpha158 | 从固定 Qlib 0.9.7 loader 生成实际使用的 158 对 name/formula 与顺序；分别登记并组成不可变 feature_set_ref，不能只登记字符串 Alpha158 |

Alpha158 首个实现检查点必须在隔离的固定 Qlib 0.9.7 环境记录 loader/表达式算子源码
SHA256、有效配置、输出名称和公式一一对应、精确依赖/最大回看窗口，并验证结果顺序。
本轮已完成实际默认描述符、顺序及相关源码/接口核验；完整递归依赖窗口与真实数据上的
数值/缺失政策仍未认证，不能把最大直接 Ref 偏移 60 当作所有因子的完整 warmup。
已有 `FEATURE_SET_VERSION = qlib-0.9.7-Alpha158-v1` 只说明旧策略合同的特征集身份；
它不证明已经存在 158 条独立注册记录。Float64Features 只是将已算特征转成 float64，
不能宣称修复上游计算精度或创建新因子。

首批网页/任务只接受已审核 factor_ref，不接受任意 Python、module_path、文件路径、
eval 或用户任意表达式。后续公式扩展需审计 Qlib 原生解析/算子允许列表；特征中的
未来引用必须拒绝，标签的未来引用只在独立标签合同中出现。

## 3. 信息时点与当前合格日频范围

每次运行冻结 `knowledge_mode`。`OBSERVED_SYSTEM` 要求每个依赖的 available_at 和
observed_at 均已知且不晚于带时区 decision_at；日期相同也必须比较精确时间。派生
评分的最早可用时刻不得早于所有依赖和必要拟合产物的可用时刻；实际 computed_at
另存，不能把事后计算写成当年已经运行。全部比较 aware instants，不比较 ISO 字符串。

`RETROSPECTIVE_RECONSTRUCTION` 可以对当前合格历史日频范围开展诊断；来源时钟未知
就披露未知，并禁止由该结果推导系统当时已经知道、历史完全 PIT 或实际前向收益。
available_at、observed_at、historical_available_at、actual registered_at 不相互替代。
新取回的旧公告即使有更早出版日，也不回写系统旧 observed_at。

没有必要等待全市场、所有频率或完整十年 PIT。每次评价必须读取指定 READY 数据
版本，完整复验目录，不从变化中的湖目录取输入；冻结共同研究证券和按日资格/剔除
原因清单，保存每个候选的选择依据、缺行情、warmup 不足、价格基础、状态未知等原因。
当前 990 来源样本、合格子集、1500 观察池不能互换。实际数量由该实验归档得出，
不能抄用本文件或旧检查点数量。使用既有 causal_intervals 也仍是 biased sample。

必须随报告保留 CURRENT_LIQUIDITY_UNIVERSE_SURVIVORSHIP_AND_SELECTION_BIAS、
POST_HOC_VIEWED_HISTORY_NOT_UNSEEN_FORWARD_EVIDENCE，以及状态/精确涨跌停、复权、
历史主表和退市覆盖等实际限制。缺数据不填价格、零收益、停牌或已认证状态。

## 4. 独立 Factor Evaluation 的拟定合同（尚未编码）

任务独立于模型训练/策略回测：只计算已登记特征及冻结未来标签，形成独立评价产物。
不因为 IC 较高而修改 ACTIVE、StrategyRegistry 生命周期、目标权重或 PAPER 账户。

```text
evaluation_ref = {evaluation_id, specification_sha256, report_manifest_sha256}
evaluation_spec = {
  ordered_factor_refs, feature_set_ref?, data_binding,
  universe_manifest_sha256, eligibility_ledger_sha256,
  label_definition_sha256, split_sha256, knowledge_mode,
  metric_policy_version, source_revision, package_versions,
  revalidates_evaluation_id?
}
```

`data_binding` 复用现有研究合同：data_version_id、manifest_sha256、dataset_sha256、
source_snapshot_sha256、ready_event_id、supported_universe_sha256 等；另存证券名单
及日期区间，不能只有数量。按源版本和观察时点固定实际输入，不在执行时自动改到最新。
specification_sha256 覆盖上述全部内容；evaluation_id 每次实际尝试独立生成，失败、
取消、中断和重试身份可审计。相同 specification 的不同尝试可以并存，不能覆盖失败。

标签明确公式、价格基础、频率、进入/退出会话偏移和成熟时刻。旧模型合同标签为
`Ref($open,-2)/Ref($open,-1)-1`，不能把 close-to-close 标签改名当成同协议。
训练/验证/测试切分和 purge 按交易日历及标签最远未来依赖检查，不能仅靠 TimeSplit
的日期不重叠。训练只拟合训练段，独立测试段不参与参数选择；已看过窗口明确披露。
raw 标签和已标准化模型训练标签必须分开。评价时刻未成熟的标签保留缺失原因。

拟定 `qtrade.factor-evaluation-policy.v1` 至少固定：

| 输出 | 定义及边界 |
| --- | --- |
| IC / RankIC | 每日横截面 Pearson / Spearman（ties 平均秩），保存每日有效 pairs、缺失/常量原因；日均等权，不把合并所有日期的相关性当每日 IC |
| ICIR / RankICIR | 日相关均值 / 样本标准差 ddof=1，默认不年化；小于两日或零方差输出 null+reason；这是新政策，不重写旧 pstdev(ddof=0) 或旧 zero 结果 |
| 分组与组间差 | 预先固定 quantiles、方向、同分时证券 id 排序、各日组人数/平均标签、最高组减最低组；组收益是标签诊断，不是已经实际成交的组合净收益 |
| 换手 | 最高组证券集合按 instrument 对齐；固定变动分母、组边界和样本进出政策；不能按数组位置比较两日 |
| 稳定性 | 固定连续会话/月度或季度 blocks，分别报告有效天数、IC/RankIC、方向一致率、覆盖与组间差；不在观察测试结果后改变分段选择最优 |
| 冗余 | 同日、同证券、同版本 basis 的因子值 Pearson/Spearman 矩阵，保存每对有效数、缺失政策、常量原因及每日/汇总方法；不是模型预测与标签相关性的别名 |
| 样本及失败 | 每日候选/资格/因子/标签有效数，剔除原因、未成熟标签、无足够截面/天数；无效结果不能显示为零或成功 |

优先审计并复用固定版本 `qlib.contrib.eva.alpha.calc_ic`、原生 signal analysis 和
Recorder/记录模板。本轮已经补 calc_ic 的 ties/NaN/常量/索引对齐微型检查，以及
SigAnaRecord 源码、签名和统计分母审计；原生分组/报告模板的隔离接线与实际数据验收
仍待实施。容差和缺失政策经明确比较后再做小适配。
无需另造统计框架。当前自有 evaluate_factor 在常量相关性时返回 0，
evaluate_factor_periods 使用总体 IC 标准差；模型 runner 使用 pandas 样本标准差。
因此旧结果不能不经政策标记就并入新报告，不能因两个函数都叫 ICIR 就视为可比。

独立归档至少包括 specification、定义/依赖快照、数据绑定与完整文件清单、证券和
资格账本、标签/切分/指标政策、实际执行代码及包版本、每日因子/标签有效样本索引、
每日指标/分组/稳定性/冗余、失败原因、summary 和报告 manifest。大矩阵用现有
Parquet 归档；网页只读摘要必须校验 manifest，不能凭缓存显示成功。

生产级写入应复用已验证的 immutable/atomic writer 与队列独占执行模式。现有
ExperimentArtifactStore 的 exists 后 write_text 只足以证明顺序禁止覆盖，不构成
并发写入/中途断电验收；frozen dataclass 内嵌 dict 也不是递归不可变。正式 adapter
必须规范化复制、校验精确字段、以内容身份封存，并完成并发/中断验证。

PIT、退市历史或状态覆盖改善时：产生新数据版本 → 新 specification/evaluation_id
→ 新独立报告，`revalidates_evaluation_id` 指向旧实验。若因子公式未变，保留原
factor_ref；只改变数据/资格政策时更新相应版本。旧输入、指标、失败和结论均不覆盖，
不“升级”旧认证标签。比较要同时展示原实验、新实验和变化维度；重跑不是新前瞻日。

## 5. 本轮可执行准备检查及结果

新增 [test_step2_factor_contract_readiness.py](../tests/test_step2_factor_contract_readiness.py)
只测试已存在接口，使用明确的合成索引日期、内存数据和 pytest tmp_path：

- 已有每日别名确实计算 PriceFactorSpec，并以三组平均秩构成多因子。
- 只修改或移除评价结束日后的价格，不改变该窗口的已成熟标签诊断。
- 含 ties 的正仿射特征变换保持 RankIC/分组，数值 IC 在容差内一致。
- 两日证券行顺序改变不影响指标，最高组换手按证券身份得到 0.5。
- 任一信息时钟比决策晚 1 微秒均拒绝；缺任一时钟拒绝；不同时区同一瞬间允许。
- 同价格文件但新 PIT 来源 hash 形成新 data identity；追加新实验后旧字节不变，
  相同 experiment_id 的覆盖被拒绝。

Windows 实际执行：**10 passed，0 failed/errors/skipped（0.32s）**；新文件 Ruff
**All checks passed**。连同既有 factor_search、signal_baselines、qlib_adapter、
data_versions 的定向回归为 **43 passed，0 failed/errors/skipped（1.17s）**。
这不是拟定 Factor Registry、双时钟聚合器、原生 Qlib 或独立
评价任务已经实现的证明。合成 fixture 不宣称是交易所日历或真实历史 PIT。

复现（PowerShell，从仓库根目录运行）：

```powershell
$env:PYTHONPATH = 'src'
$env:LIVE_TRADING_ENABLED = 'false'
python -m pytest tests/test_step2_factor_contract_readiness.py
python -m ruff check tests/test_step2_factor_contract_readiness.py
python -m pytest tests/test_step2_factor_contract_readiness.py tests/test_factor_search.py tests/test_signal_baselines.py tests/test_qlib_adapter.py tests/test_data_versions.py
```

### 5.1 固定 Qlib 原生接口的实际只读核验

实际最终取证时刻 **2026-10-28 12:04:37 上海**；环境 Python **3.11.17**、pyqlib
**0.9.7**、pandas **2.2.3**、numpy **1.26.4**、scipy **1.17.1**。任务卷可用
**271.0449 GiB**，超过 100 GiB 保留门槛。仅通过现有授权连接器的 connect() 发送
静态 stdin operator；没有读取、打印或复制连接凭据，也没有在远端保存 operator 文件。

证据入口：[evidence-summary.json](evidence/step2-native-interface-20261028/evidence-summary.json)、
[package-interface.json](evidence/step2-native-interface-20261028/package-interface.json)、
[source-excerpts.json](evidence/step2-native-interface-20261028/source-excerpts.json)、
[42 文件校验清单](evidence/step2-native-interface-20261028/file-manifest.json)。
42 个证据载荷共 514,488 字节逐个 SHA256 校验通过；清单不包含它自身。

最终执行源码和结果保存在 `20261028T040432Z-c51fea0d/`。首次包源码读取与最终
接口核验的六个共同模块哈希完全一致；执行的 operator 字节与保留的主 operator 相同。

| 固定模块 | 本轮 SHA256 |
| --- | --- |
| `qlib/contrib/data/handler.py` | `b621481c6009c39066c67c71390fd2bea635f56daf9f2c4e38817eff268e3232` |
| `qlib/contrib/data/loader.py` | `814b7f7ab3d418ae3c87ce352220080b239eba2670eac9e38376b794be4075cb` |
| `qlib/data/ops.py` | `6f648355725a85a9f17528d864281fc065f8a4f887261a9909f7495d5db42760` |
| `qlib/contrib/eva/alpha.py` | `f8e6eb2a4647cb7b2f6ce316fec36f875dcf9125d21515790a145435f5d26229` |
| `qlib/workflow/record_temp.py` | `ef07266b594e9b7e80cce0ed11c9663ab67c2828f20306371f7de5b71e226fb7` |
| `qlib/data/data.py` | `9156bfef585b9e2d22d4d1ba5831efdb63b419d24846ee738eee237090ff6a24` |

另外保存 DataHandlerLP/QlibDataLoader 两个模块哈希，以及 Alpha158、其方法、loader
静态方法、DataHandlerLP、QlibDataLoader、Ref/窗口方法、Rolling、Std、Mean、Corr、
calc_ic、SigAnaRecord/_generate、ExpressionProvider 方法的实际签名。

正确的无数据入口是 `Alpha158DL.get_feature_config()`；对照调用
`Alpha158.get_feature_config(None)`，因其方法仅构造配置并委托 loader，不构造 handler。
两者产生完全相同的 **158 个唯一名称和有序公式**。完整清单见
[alpha158-default-descriptors.json](evidence/step2-native-interface-20261028/alpha158-default-descriptors.json)，
规范化摘要为 `23badba179ce07b691a394f60844b85b6401a5c238d4d3a9d3338ab94760828d`。
输入字段为 close/high/low/open/volume/vwap；直接 Ref 偏移集合为 1/5/10/20/30/60，
未发现负直接偏移；这次简单扫描不是完整递归依赖/最大窗口认证。

名称相似不能直接合并版本：原生 `ROC20` 是 `Ref($close,20)/$close`，与 QTrade
momentum20 的 `close[t]/close[t-20]-1` 不同；原生 `STD20` 是价格标准差/当前价格，
也不是 QTrade 简单收益的负总体波动率。原生 handler 默认标签是未来 close 比值，
项目冻结策略标签为未来 open 比值；此次仅提取特征描述符，没有默用原生标签替换项目标签。

合成数值检查只有 4 个虚构日期 × 6 个证券评分/标签，包含 ties、负相关、常量和
成对缺失。`calc_ic(pred,label,date_col='datetime',dropna=False)` 按索引对齐；反转评分
行顺序保持结果不变。三个非退化日期与本地既有 evaluate_factor 比较，IC/RankIC
最大绝对差 **2e-16**，低于 1e-12；缺失日实际只有 4 个成对样本。
常量日原生 IC/RankIC 为 NaN（归档为 null），自有接口返回 0；`dropna=True` 删除该日，
仍须在新报告中保留剔除原因，不能静默缩小天数。见
[accepted-local-replay.json](evidence/step2-native-interface-20261028/accepted-local-replay.json)。

SigAnaRecord 签名为 `(recorder, ana_long_short=False, ann_scaler=252, label_col=0,
skip_existing=False)`；源码计算 ICIR/RankICIR 为日均值除 pandas 默认样本标准差
（ddof=1），未对这两个比率年化。两日合成 ICIR：自有总体分母为
**-0.03127445677793965**，原生记录模板的样本分母政策为 **-0.022114380465606685**。
本轮没有构造/运行 SigAnaRecord 或写 Recorder；这些模板公式来自所捕获源码，
不是已经执行模板归档的声明。

源码发现 `ExpressionProvider.get_expression_instance` 内部使用
`eval(parse_field(field))`。因此不能把“复用原生表达式”当作安全接收任意用户公式的
依据。此次描述符仅组装可信包中的字符串，不求值：descriptor/合成 IC 阶段的
`builtins.eval`、ExpressionProvider、BaseProvider.features、QlibDataLoader.load、
DataHandlerLP 构造及 qlib.init 均有拒绝探针，最终调用计数全部为 **0**。
Python 审计钩子阻断持久文件写入、目录/文件变更、生产输入读取、网络和子进程。
包导入时一次 subprocess.Popen 和一次 socket 构造探测被阻断，原样保留；dill
检查文件类型的 4 次 `/dev/null` 打开单独记录，未放开持久写入。后续纯描述符/数值
阶段没有阻断事件，也没有 Provider、数据集、真实行情或训练访问。

所有早期尝试均独立保留：首次 reserve 检查错用了 `/root` 系统卷而安全停止；随后
严格写入拦截遇到 dill 的 `/dev/null` 与 tempfile 探测；改为仅允许 null sink、预设
tempfile 的 `/tmp` 字符串而仍禁止持久写入。另一次因将已阻断的导入期探测计入
“零尝试”断言而失败。最终分别披露导入期已阻断尝试和操作阶段零调用，不删除失败
或将失败改成成功。导入时的 Gym 维护提示、常量相关性 warning 保存在 stderr。

本地离线复验（不连接集群、不需要本机安装 Qlib）：

```powershell
$env:PYTHONPATH = 'src'
python docs/evidence/step2-native-interface-20261028/verify_capture.py docs/evidence/step2-native-interface-20261028/20261028T040432Z-c51fea0d/stdout.json
python -m ruff check docs/evidence/step2-native-interface-20261028/native_audit_operator.py docs/evidence/step2-native-interface-20261028/verify_capture.py docs/evidence/step2-native-interface-20261028/capture_via_authorized_connect.py
```

在本轮相同只读范围内重新捕获会新建唯一目录，不覆盖原证据；使用已有授权连接器：

```powershell
python docs/evidence/step2-native-interface-20261028/capture_via_authorized_connect.py
```

三个当前维护的证据脚本 Ruff 通过。上述核验关闭固定包源码、默认公式描述符及微型
IC 接口的准备缺口；不证明 Alpha158 真值矩阵、完整窗口、独立评价任务、Factor
Registry、真实日频实验或生产网页已经完成。原 10 项准备检查与 43 项回归结果不变。

## 6. 下一实现和验收边界

1. 本轮已补固定 Qlib 0.9.7 源码/签名、真实 158 name/formula 和微型 IC 审计；
   下一步补完整递归依赖/窗口、真实特征数值、分组及记录模板隔离接线，不扩大本轮结论。
2. 在现有研究包增加小型只读定义校验/登记适配和独立评价 spec；测试精确字段、
   hash、依赖循环、未来特征引用、时区/盘后输入、缺会话、跨切分标签与同名冲突。
3. 隔离队列/数据库/输出目录中验证新任务；注入源文件变化、身份变化、重试和中断，
   证明失败不污染旧实验。新报告追加/PIT 复验引用必须成为正式合同，不只约定文件名。
4. 选定一份当前合格 READY 日频版本做有界集成：冻结完整证券与资格原因，分别输出
   基础因子和 Alpha158 的独立结果，人工复核原生数值和归档。保留样本/幸存者偏差。
5. 审核后再逐项接正式队列/API/只读网页，复核浏览器展示的指标政策、缺失原因和限制。
   策略 Builder 未来只消费精确 factor_ref/evaluation_ref，不接受 latest 漂移。

上述 1 的准备部分已完成，完整功能与 2–5 仍未完成。本轮没有新增因子生产注册器、
评价后台/API/UI、新研究模型或 PAPER/SHADOW 账本；Linux 仅有只读源码/描述符和微型
合成接口核验，没有真实数据端到端集成或生产验收。STEP1 的真实证据继续独立获取。
LIVE_TRADING_ENABLED=false；登记和报告不授予交易权限；任何策略后续进入执行都
继续经过 portfolio → risk → OMS → execution → gateway，halt/reconciliation 门禁不变。
