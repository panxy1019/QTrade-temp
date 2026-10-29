# Handoff — 2026-10-29：以真实因子和策略研究为主线

## Goal：新窗口的目标

先复核并复用已有七策略历史归档。在已经准入的冻结日频数据上，完成891股研究范围的已登记因子计算、独立评价、
真实 Qlib 组合回测和统一基准对照，回答“结果在时间隔离和交易成本下能否重复”。
每日数据快照维护、历史治理、公网发布和前向账本分别验收，不把全部运维缺项串成
离线研究的统一前置门槛。

这是同一项目的上下文交接，**不是重新搭平台、换仓库或重启集群**。
本文件对应用户2026-10-29最新方向，优先于旧状态记录中“全部STEP1结束后才做STEP2”
的工作顺序；旧失败结果、数据准入和交易安全规则保持不变。

## Current State：本轮实际核查了什么

本轮只读审计加文档交接，没有开发运行时代码、训练、回测提交、部署、控制进程、
模型激活或恢复账本。以下“已实现”与“实际验证”分开记录。

| 证据 | 时间/范围 | 可证明 | 不能证明 |
| --- | --- | --- | --- |
| 本地 Git/source 审计 | 2026-10-29上午 | 代码基线be4faa79、已有接口、限额、原报告 | 新实验已执行 |
| 集群只读 SQL/文件摘要 | 上海09:32:23、09:33:32 | 正式日head、版本元数据、独立证券数、ACTIVE与旧owner身份 | 全量19,816文件重验、实时服务健康或十年完整性 |
| Vercel真实GET | 上海09:33:34附近 | production源码ae33a7c8、行情来源5a0925f4、API仍报STALE_PUBLICATION | DOM/截图、完整C/D/E或全部行情接口通过 |
| 既有因子实际实验 | 10月28日，891股/16个决策日 | IC/RankIC/分组/稳定性等真实诊断已生成 | 多年净收益、未见前瞻或可投资性 |
| 旧统一七策略归档 | 原2026-10-27原生验收；本轮09:49只读确认原件仍可读 | 678会话、601,013共同样本行、净收益/回撤/换手已有 | 新FactorRegistry链已经接通、当前891范围完全等价或新盲测 |
| 旧对照输出完整校验 | 本轮上海09:52:52，既有verify_comparison | 原输出28文件、61,877,590字节及结构校验通过 | 两份训练输入档案重新全验、回测重算或新因子来源接线 |
| 新登记型原生组合适配器验证 | 10月29日凌晨，Qlib0.9.7/Linux33项 | 两个原生合成行情执行例及登记/归档合同 | 新登记型891股真实组合已跑完 |

原始只读观察保存在 `E:/QTrade-temp/cluster-evidence/handoff-readonly-20261029T013221Z`。
仓库可携带的事实投影、证券名单、复现SQL和摘要见本目录的
[`evidence-20261029/manifest.json`](evidence-20261029/manifest.json)。
其中初始facts的 `supported_instruments` 是**区间行数**，应使用后续独立去重结果；
见下文，不能误报为1,725/1,726只股票。

## Decisions：资源和完成定义收敛

### 开发精力目标

| 工作线 | 目标比例 | 应做 | 不作为当前主线 |
| --- | ---: | --- | --- |
| STEP2 | 75% | 四因子、长窗口评价、真实组合、样本外和基准、复现、必要网页接线 | 上百新因子、复杂模型竞赛、LLM自动挖掘 |
| STEP1每日数据 | 20% | 自动日版本、来源/完整性/Qlib读取检查、失败保留旧head、必要缺口处理 | 等到所有证券/分钟/十年PIT都完整 |
| 高级发布预留 | 5% | 保留现有成果；只处理确实阻断数据安全或必要交付的问题 | 新增MinIO/R2/Publisher故障状态机研究 |

这是计划精力，不是已测工时、CPU/内存配额或已完成百分比。
不为执行该比例强制停止现有采集、发布、PAPER/SHADOW服务。

### STEP1每日数据的最小完成定义

一份当日预期的日频快照应满足：

1. 自动任务在约定交易日/收盘后时段生成；真实日历区分休市和数据迟到。
2. 冻结证券范围、实际日期、原始来源、转换口径、缺失/隔离原因；范围内来源关系一致。
3. 文件/manifest哈希、日期唯一性、价格/量额字段与必要交易约束检查通过。
4. Qlib能重载provider，模型/因子的必要字段和历史窗口可读；占位日历没有被填成行情。
5. 新版本目录不可变，明确唯一准入记录；失败时head保留上一合格版本，记录原因和年龄。
6. 数据质量按证券×日期×频率×用途隔离；研究可剔除不合格样本，交易未知则阻断对应交易。

这里的“来源一致”指研究数据快照自身及其实际输入，**不能因为公网行情包的catalog/index
不一致，就认定一个独立、通过READY合同的Qlib版本也不可用**。反过来，也不能忽略
Qlib版本自身的真实来源缺口。首次研究前仍须重验所选版本，而不是仅信任数据库状态。

这项定义是新的验收组织方式，尚未通过本轮故障注入或连续多日SLA重新认证。
下一窗口可补3—5个自然收盘日的观察，不必让离线研究等待这些日期。

### 四条状态分开报告

| 状态维度 | 当前判断/原则 |
| --- | --- |
| 日频研究数据 | 已有正式READY版本，可选择冻结版后复核并研究 |
| 离线研究交付 | 旧七策略678日对照、四因子短窗口评价已有；新登记链统一接线/长窗独立评价和复验待做 |
| 公网交付 | 独立未通过；不阻断已有冻结版上的离线研究 |
| PAPER/SHADOW安全和原STEP1 C/D/E | 原证据/未通过状态保留；不因离线任务完成而自动通过 |

历史98d Proof、D门槛、公开交付和完整集群证据继续如实保留未通过。
不得重写原audit、移动时钟或伪造历史来“收口”。旧C时限已到，也不能用后来证据
宣称按时通过。此后补证应产生新的实际时点记录。

## 项目背景和架构

QTrade从日频/分钟行情采集和早期ETF工具，逐步接入Qlib研究与受保护的研究任务。
现在应保留已有采集/版本/注册器/队列/网页，以Qlib承担特征、训练和组合执行。
不是完整复刻Qlib的所有模块，也不是网页上已经能够操作任意模型和策略。

```text
公开行情源 → 原始响应/Parquet/质量记录
                       ↓
                不可变Qlib日版本
                       ↓
          FactorRegistry → 独立评价
                       ↓
                 StrategyRegistry
                       ↓
             Qlib组合回测 → 研究归档
                       ↓
              后续受保护队列/网页

公网行情发布：独立交付支线
PAPER/SHADOW：独立准入、持仓、会计和前向观察支线
实盘：关闭
```

Vercel运行网页和受保护API；集群运行采集、版本构建、研究worker和已有前向服务。
Git代码分支更新不等于集群运行时更新，也不等于生产网页更新。
向GitHub增加一个因子文件不会自动生成因子版本、运行实验或激活策略。

## Files And Artifacts：仓库、环境与配置

### 本机与代码版本

| 项目 | 位置/值 |
| --- | --- |
| **实际工作仓库** | `E:/QTrade-git` |
| 当前环境默认cwd | `E:/Q`；是容易混淆的目录，不能直接假定它是本项目最新仓库 |
| GitHub | https://github.com/panxy1019/QTrade-temp |
| 代码基线 | `be4faa79dd29d145ed6fb490777a9a91f8d302ca` |
| 分支 | `codex/qlib-handover-production-acceptance` |
| PR | https://github.com/panxy1019/QTrade-temp/pull/35 ，OPEN/DRAFT |
| 生产网页 | https://q-trade-temp.vercel.app/ |
| Vercel项目 | https://vercel.com/richman3/q-trade-temp |
| 本轮观察的生产网页源码 | `ae33a7c8c27ea721eb3be1e4430b562a44b67f91` |
| 本轮观察的公网行情来源 | `5a0925f47e541726210a8879e2f2bed4ae2a62fd` |
| 本机辅助材料 | `E:/QTrade-temp`；不是Git工作仓库，不应整体加入Git |
| 原用户STEP2附件 | Desktop/step2.txt本轮不存在，但原指南已归档为 `plans/STEP2_USER_GUIDE.txt`；旧“必须全面完成STEP1”顺序已被本次用户指令取代 |

交接文档提交会在代码基线之后增加一个文档commit。新窗口先检查实际HEAD；
本文件中的be4指运行时代码基线，不要求把工作树reset回它。
本轮开始仅有未跟踪 `node_modules/`，未修改/删除/加入Git。新窗口仍须重新检查。

### 集群路径（不含认证材料）

连接目标沿用用户既有授权：`ssh -p 20115 root@10.10.164.243`。
本文件不保存密码、SSH私钥、账户cookie或访问token。

| 用途 | 路径 |
| --- | --- |
| NFS数据根，以下称QT_BASE | `/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade` |
| 本地运行状态根，以下称QT_LOCAL | `/root/qtrade-local` |
| 正式不可变日版本 | `$QT_BASE/unified-data/qlib-versions/versions/<version_id>` |
| 日版本准入DB | `$QT_LOCAL/qlib-versions.sqlite` |
| 模型DB | `$QT_LOCAL/online-models.sqlite` |
| 研究任务DB | `$QT_LOCAL/workflow.sqlite` |
| 现有策略DB（正式输入只读） | `$QT_LOCAL/strategies.sqlite` |
| 前向账户/账本 | `$QT_LOCAL/forward-research` |
| 非生产证据/小型输入 | `$QT_LOCAL/operator-evidence/<新的独立运行目录>` |
| 采集/API Python | `$QT_BASE/venv/bin/python`，既有Python3.14环境 |
| 原生研究 Python | `$QT_BASE/qlib-env/bin/python3.11`，Qlib0.9.7 |

研究与API环境分离。`requirements-qlib.txt`固定：pyqlib0.9.7、numpy1.26.4、
pandas2.2.3、lightgbm4.6.0、scikit-learn1.6.1、mlflow2.22.2、pyarrow19.0.1。
不能把API的Arrow依赖直接安装进研究环境，也不要为跑测试升级现有环境。
此前研究环境无pytest，已通过独立test-vendor镜像验证；不是生产环境已经装好pytest。

09:32可用空间：NFS279,243,128,832 bytes（约260.1GiB），local13,556,867,072 bytes
（约12.6GiB）。继续保留NFS100GiB、本地2GiB。大数据/报告镜像应放独立NFS研究目录，
本地盘主要用于运行状态和小型索引；不能将本地12.6GiB假装成100GiB通过检查。
这些是观察值，不是新运行前的空间保证。

同机新窗口可复用已授权连接入口：

```python
import importlib.util
spec = importlib.util.spec_from_file_location(
    "authorized_cluster_read", "E:/QTrade-temp/authorized_cluster_read.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
client = module.connect()
try:
    # 先执行短、有界、只读检查。
    pass
finally:
    client.close()
```

不读取/复制该辅助脚本正文，不打印凭据或环境；脚本不进入Git/交接包。
若换机器/账户后入口失效，重新建立安全认证，不从旧聊天或文件搜集密码。
10.10地址需要原有内网/VPN可达性；另一云端执行环境不一定能连接。
聊天历史、Python变量、SSH client和浏览器会话不会随Markdown自动迁移，必须重新建立；
磁盘上的不可变归档可继续引用，已消费恢复授权不能因此重新使用。

Vercel已有配置名包括 `QTRADE_RESEARCH_PASSWORD_HASH`、
`QTRADE_RESEARCH_SESSION_KEY`、`QTRADE_RESEARCH_QUEUE_KEY`、
`QTRADE_RESEARCH_QUEUE_SECRET`。只交接名称，不导出其值。本轮未核对账户设置。
用户此前允许暂留Config类型；不要重复把改Secret当作离线研究前置条件。

## 数据现状：891究竟代表什么

| 范围 | 本轮/原证据 | 含义 |
| --- | --- | --- |
| 统一采集目标池 | 1,500 = 1,470股票+30ETF | 不是训练时每天都可用的证券数 |
| Qlib历史数据集 | 990只 | 版本中保留的历史证券，不保证同起止日/完整矩形 |
| Oct28日版本当前合格 | 988只 | manifest当前范围；002827、605258被阻断 |
| Oct27日版本当前合格 | 987只 | 该版还阻断600621；不得仅凭Oct28名单变化认定复牌事实 |
| workflow支持范围 | **891个去重证券** | 当前支持交易规则范围；不等于每一天891条有效因子/成交 |
| 支持区间记录 | Oct27为1,725行，Oct28为1,726行 | 同一股票可有多段区间，不能把行数当股票数 |
| 既有五日评价 | 每日部分因子885—891有效对 | 由历史、标签、资格区间等决定 |
| 既有16日共同历史 | 884/891该短段日期格齐全，7只有缺口 | 保留891候选及拒绝ledger，不填价补足 |

正式日head：

```text
b82baaac956745f4e02a520a35b2aa277b0037510a4d164d798db803ce77e0d5
actual_end = 2026-10-28
state = RESEARCH_READY_QUALIFIED_SUBSET
admitted_at = 2026-10-28T11:57:40.694469+00:00
manifest文件数 = 19,816
```

已有四因子16日实验绑定的旧版本：

```text
09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407
actual_end = 2026-10-27
manifest文件数 = 18,828（不含version.json自身）
```

日历从2016-10-24开始，分别有2,431/2,432个真实历史会话；末尾还有仅日历占位边界，
**不代表2026-10-30/11-02已有行情**。990股票的历史起始日不统一，最晚起始到2021-11-10。
因此只能说“有跨约十年的研究日历与不平衡证券面板”，不能说“891股十年全完整”。
09:32尚未到10月29日收盘，正式日版截至28日本身并不异常；公网显示陈旧是另一条事实。

首个新实验应固定一个版本；最省改动的第一步是用09a67复用原登记和已看16日窗口，
验证真实组合执行。新的长窗口/Oct28再验证另建实验，不能把旧报告换绑到b82。
需要长期测试时先产出逐证券/逐年的日期覆盖、可用字段、资格区间、缺失原因和实际有效数。

## 当前因子、策略和研究结果

### 已登记四因子

| alias | 数学定义 | 最短历史 |
| --- | --- | ---: |
| momentum20 | `close[t]/close[t-20]-1` | 21个close |
| reversal5 | `-(close[t]/close[t-5]-1)` | 6个close |
| lowvol20 | 20个简单收益的负总体标准差，ddof=0 | 21个close |
| multifactor_equal_rank | 上述三项确切版本的截面平均百分位秩等权平均 | 21个close |

FactorRegistry冻结公式、输入时点、依赖、价格口径和来源摘要。不要用重新生成的refs
替换旧refs，也不要把Qlib同名近似表达式当成完全相同的数学版本。
Alpha158在已有模型研究中使用，但其158项尚非全部登记为独立评价因子。

16日真实诊断，09-28至10-26（真实会话）：

| 因子 | 平均IC | 平均RankIC |
| --- | ---: | ---: |
| momentum20 | 0.038961 | 0.049934 |
| reversal5 | -0.082741 | -0.095554 |
| lowvol20 | 0.115293 | 0.187130 |
| multifactor_equal_rank | 0.068722 | 0.110079 |

这些是close到下一会话close的毛标签诊断；不是组合净收益、年化投资能力或显著性证明。
16个日期不能因为有约14,000个instrument-day就当成14,000个独立时间样本。
未知历史available_at/observed_at仍大量存在，模式为RETROSPECTIVE_RECONSTRUCTION。
不要因为反转在这段为负就根据测试结果自动翻转符号，也不要把低波的短期正IC直接用于晋级。

16日实验ID：`factor-eval-dd6a5c6d61a64044975646668eef4486`；说明和原报告在
[`../STEP2_LONGER_FACTOR_EVALUATION_20261028.md`](../STEP2_LONGER_FACTOR_EVALUATION_20261028.md)。
原报告191,719,859字节；重复保存逐样本依赖会使多年JSON很大，这是扩展时的实际工程问题。

### 当前策略执行能力的准确边界

- 已有Alpha158＋Ridge/LightGBM的配置化模型、预测和TopkDropout研究路径。
- **已有旧流程的七策略真实历史对照。** 不得把“新登记型适配器尚未完成真实891验收”
  泛化为“系统从未跑过真实组合”。详情见下一节；优先复用，不从头重做。
- 因子策略可登记到现有StrategyRegistry，保留evaluation/data/factor/source引用。
- 受保护Builder/任务队列已有**登记型**隔离闭环，不代表任意因子已能网页提交组合回测。
- 新 `FACTOR_STRATEGY_BACKTEST_V1` 是独立CLI适配器；Linux33通过含两个真实Qlib
  合成行情例，**没有完成891股真实全窗口组合**，也没有接共享worker/API的新job kind。
- 原登记合同中的 `adapter_implemented=false` 是历史字段；不能回写成true。
  新执行结果独立引用旧合同并证明实际执行来源。
- `hold_thresh` 是最少持有步，不是任意换仓周期或固定到期日。
- 当前独立适配器使用明确零收益现金参考；没有自动完成“与可交易等权基准比较”。
- 已有Signal Baselines可复用动量/反转/低波/等秩/等权信号，不恢复已退役的旧五模板页面。

### 必须先复用的旧七策略真实对照

本轮09:49在集群只读确认以下归档、manifest摘要及七组曲线/指标仍存在；09:52又以
既有 `verify_comparison()` 重验原输出28文件、61,877,590字节与结构并通过。
没有重新训练、重新执行回测或全量重验两份原训练输入，不能把输出校验称为新实验。
原生历史执行证据在 `docs/evidence/qlib-step1-initial-native-20261027.json`；
旧公开捕获在 `docs/evidence/qlib-overall-public-20261027-1929/comparison.body.json`。

```text
comparison_id = f6b9e52b1c30c4f46e0733d02bb4e800450a9acbb67a8954d63a2bb857dde943
root = $QT_BASE/online-research/strategy-comparisons/<comparison_id>
signal_start/end = 2024-01-02 / 2026-10-23
每组曲线 = 678会话；共同signal行数 = 601,013
comparison.json SHA256 = 5831eb47bcce4bfe6e429aa0f49113f11d44392ea26041fcac129427597d89ce
```

归档中已扣既定Qlib成本的结果如下；是**旧事后研究结果**，不是本轮新算或实盘业绩：

| 家族 | 累计净收益 | 最大回撤 | 平均日换手 | RankIC |
| --- | ---: | ---: | ---: | ---: |
| momentum20 | 2.33% | -46.17% | 16.29% | -0.02749 |
| reversal5 | 46.99% | -40.23% | 19.85% | 0.01734 |
| lowvol20 | 53.01% | -12.33% | 14.26% | 0.03480 |
| multifactor_equal_rank | 65.12% | -14.53% | 19.05% | 0.02613 |
| equal_weight_universe | 102.16% | -21.75% | 0.71% | null（恒定分数） |
| Ridge | 87.47% | -32.18% | 17.53% | 0.04674 |
| LightGBM | 126.71% | -32.17% | 17.43% | 0.05176 |

这份报告中四个简单因子的累计净收益都低于全池等权；不能仅凭16日IC宣称其有增量价值。
低波/组合的回撤较小，是值得核查的风险特征，不自动等于更优投资策略。
等权使用WeightStrategyBase，其他六项使用TopkDropout，组合约束/换手结构并非完全相同，
所以差异不能全归因于信号；LightGBM高于等权也不证明未见前向alpha。
历史当前池筛选、PIT、复权、约束近似和已看样本等限制都保留。

原comparison使用旧Ridge/LightGBM档案的**共同合格面板**，还要求两模型都有分数和21日历史。
必须核查其证券名单、原provider及数据摘要与当前891/09a67/b82的关系；不得默认是同一数据版本。
旧归档缺新FactorRegistry/evaluation_ref接线，不能事后改写它来补这些字段。
新增映射/兼容证明单独归档；能复用旧引擎和输出时不必为新界面重算整段。

现有入口 `scripts.run_qlib_strategy_comparison`、`verify_comparison()` 和
`docs/QLIB_UNIFIED_STRATEGY_COMPARISON.md` 是M0优先读取项。当前source支持旧政策的
显式识别，不把旧研究约束升级成已认证交易规则。

### 长窗口的两个实际代码门槛

1. `factor_evaluation.py`：证券数×决策日数×各因子required_closes之和≤1,000,000。
   四因子总和69，891×16×69=983,664；17日超限。这就是当前真实评价只有16日的原因之一。
2. `factor_strategy_backtest.py`：证券数×决策日数≤100,000；891股约只能112个决策日。

这些上限属于**新独立评价/登记型适配器**，不是旧七策略脚本的限额；不能因此推迟
已有多年结果的解读。长窗口优先复用旧比较引擎和已有Parquet/指标函数，不另建研究框架。

不能只把常量改成极大值。应复用现有评价器，设计有界分批/缓存的新版长实验规格：
每批保留warmup和边界、样本/标签/拒绝原因与校验；父实验明确全部块和聚合来源。
逐日IC可聚合，跨块稳定性/换手须衔接，组合净值/现金/持仓不能每块重新初始化后拼接。
同时冻结内存/输出/时间预算、失败保留及独立数值对照；旧v1报告和限额不被静默改写。

## 下一阶段交付计划

### M0：锁定可研究数据和实验规格

**首要动作，不运行Publisher或MinIO。**

- 先复核并解释上述旧七策略归档，核对其来源、真实成本/切分/持仓和原provider；
  交付已有结果的清楚对照，不把已完成的678会话研究说成不存在。
- 复用指定READY的完整哈希/唯一准入校验和Qlib重载，输出891证券名单摘要及覆盖矩阵。
- 区分raw、adjusted、amount/VWAP、资格区间、buy_block/sell_block/capacity、缺价与原因。
- 固定因子refs、标签、组合、成本、基准、切分、随机种子、源码和运行环境。
- 明确最初16日真实组合只是已看历史的工程验收，不给OOS有效性结论。

验收：数据/规格/来源manifest可重读；每个不能用的证券日都有原因；没有为凑891填价。

### M1：先跑现有891股真实短窗口原生组合

- 本项专门验收**新登记型因子执行链**，不是重做已经存在的旧七策略回测；
  M0若已证明等价部分可复用，只补来源映射和必要的最小差异重放。
- 复用09a67和现有因子登记，先跑一个固定因子，再按同规则跑其余三项。
- 使用真实Qlib TopkDropout/Exchange；核第一笔执行日、100股单位、方向/容量限制、费用、
  现金/持仓/NAV恒等关系。保留独立结果，不改原登记或评价。
- 四项全部同一数据、执行区间、资金和成本；输出每日净值、费用、换手、持仓和成交。

验收：真实891候选、实际有效数/成交阻断可追溯；原始portfolio/positions/metrics归档。
有成交不能成为强制要求：若约束拒绝全部成交，应说明原因，不放宽规则造收益。

### M2：支持多年窗口并冻结严格时间切分

- 完成上述有界长窗口方案，先做短窗口完全一致性对照，再扩大日期；不是一次性去掉保护。
- 固定因子无模型拟合，train段用于规则/方向/组合参数选择；valid用于有限选择；test仅评价。
- 若比较Ridge/LightGBM，处理器/缺失拟合/标准化只用train，早停只用valid，冻结后进入test。
- 不得拿2026年训练的当前ACTIVE模型直接回测2024年并称其未见样本；离线重新训练或核实
  原冻结模型训练截止及处理器，不激活/替换现有模型。

可复用旧基线的时间框架（先按实际交易日、标签跨度重新核对，不自动视为适用于新规格）：

| 分区 | 现有配置 |
| --- | --- |
| train | 2017-04-05—2021-12-28 |
| valid | 2022-01-04—2023-12-26 |
| 历史test | 2024-01-02—2026-10-23 |

原ML标签为 `Ref($open,-2)/Ref($open,-1)-1`；现有因子诊断是close-to-close。
两者不是同一收益区间；报告必须同时标明信息截止、信号时点、首个可成交时点和标签端点。
Qlib TopkDropout内部取前会话信号，不要再把score整体平移一次导致双重滞后。
边界按标签终点/持有期purge，必要时按预先规则embargo；不能随机划分证券日。
warmup可读过去，不得让test信息参与train拟合。按日聚合不能使用未来截面/未来成员。

该历史test已经在以往讨论/基线中被看过，只能叫“训练时间隔离的事后样本外检验”，
不是预注册盲测。另行冻结未来尚未出现的交易日，作为独立真正前向验证；不等待它才
交付历史研究结果。

### M3：统一评价和基准，形成可复现研究报告

至少输出：

| 维度 | 要求 |
| --- | --- |
| 预测信息 | 每日IC/RankIC、均值/离散度、有效天数和配对数；无效为null+原因 |
| 分组/稳定性 | 分组成员及收益、预设分段/年份、因子相关；边界样本不足显式披露 |
| 真实组合 | 毛/净收益、净值、最大回撤、换手、现金/持仓/成交、费用总额及逐日明细 |
| 基准 | 同范围/时段/约束/成本的简单等权策略；现金；数据满足时另列沪深300参考 |
| 成本 | 冻结买卖费率/最低费/滑点，禁止重复扣费；预设敏感性而非测试后挑最好值 |
| 复现 | 规格、data/factor/strategy/source版本、拟合产物、环境、种子、日志及文件清单 |
| 限制 | 当前池选择/幸存者偏差、非认证历史PIT与复权、日线成交/容量近似、已看历史 |

已有基线成本买8bp/卖13bp、最低5元、资金1,000万元，可作明确研究假设的起点，
不是券商报价或跨历史税费真实性证明。新策略若使用不同成本，必须进入规格和对照。
Qlib `return` 与 `cost` 分开，现有净收益用 `return-cost` 后复利，不能又从已净收益扣一次。
分组“新增Top成员比例”不等于执行组合的双边成交额/NAV换手，分别命名。

同规格重跑应形成新run_id，保留旧结果；对确定性输出约定数值容差/运行环境，不强求
计算时间或整个bundle字节相同。负收益/无稳定IC也可以是合格交付，不能把盈利作为
工程通过条件。统计证据以日期维度为主，短样本/多重尝试不作显著性结论。

### M4：把已经跑通的研究接回网页

先结果归档/API只读展示，再增加受保护的任务提交；不让网页接线阻挡首个离线结果。
复用现有预览、身份校验、HMAC队列、容量/空间门槛、取消和不可变完成记录。
页面明确：数据版本/日期/覆盖、研究或前向、任务状态、gross/net、基准和限制。

**兼容性注意：**既有登记合同冻结20文件来源，其中包括 `scripts/run_qlib_worker.py`。
直接改worker会使旧合同runtime source检查失败。先设计显式执行release兼容策略；
旧登记仍按旧来源可重放，新执行记录绑定新来源。不能删除旧检查、重写source标签或
把“兼容旧定义”说成“旧字节仍相同”。

### M5：独立Paper Trading路径

研究结果与前向准入分开。先冻结策略/模型/数据/执行规则，影子观察，再核账户、
持仓/现金/费用/阻断和前向估值。未认证交易状态继续拒绝对应交易。
研究成功不是恢复账本或连接网关的授权；恢复仍需明确人类动作和成功对账。
本阶段不重启旧账户、不回填历史会话、不覆盖冻结signals，不启用实盘。

## 推荐读取和复用文件

| 文件 | 用途 |
| --- | --- |
| `AGENTS.md` / `CURRENT_STATE.md` / `plans/MASTER_PLAN.md` / `DECISIONS.md` | 安全规则、最新方向、历史证据 |
| `src/qtrade/research/data_versions.py` / `strategy_contract.py` | READY完整校验、准入、支持证券与绑定 |
| `scripts/prepare_qlib_data.py` / `run_qlib_baseline.py` | 原数据适配、Alpha158及训练/组合基础 |
| `configs/qlib_daily_baseline.json` | 现有切分、模型、Topk和成本；不是自动给新实验背书 |
| `src/qtrade/research/factor_registry.py` / `factor_evaluation.py` | 因子身份、双时钟、IC/分组与当前限额 |
| `src/qtrade/research/factor_strategy_contract.py` | 评价→策略、切分/标签与运行来源 |
| `src/qtrade/strategies/registry.py` | 复用现有StrategyRegistry，不再建一套注册库 |
| `src/qtrade/research/factor_strategy_backtest.py` | 原生组合适配与100,000行上限 |
| `scripts/run_daily_factor_evaluation.py` / `run_factor_strategy_backtest.py` | 真实独立CLI入口 |
| `scripts/run_qlib_strategy_comparison.py` | 现有净收益/回撤/换手/IC计算基础 |
| `scripts/factor_research_contracts.py` / `run_factor_strategy_registration.py` | 只读批准归档和登记执行 |
| `scripts/run_qlib_worker.py` / `src/qtrade/api/research_workflow.py` / `online_research.py` | 已有任务和保护入口；修改前审来源兼容 |
| `web/src/pages/QlibWorkflowPages.tsx` | 研究/Builder相关网页 |
| `web/src/pages/ForwardResearchPage.tsx` / `OnlineModelsPage.tsx` | 前向账本与模型页面，避免混成历史回测 |
| `api/index.py` / `web/package.json` | Vercel API入口与前端脚本 |

历史证据导航：

- `docs/STEP2_LONGER_FACTOR_EVALUATION_20261028.md`：891股16日真实评价及独立数值核验。
- `docs/STEP2_FACTOR_CONTRACT_ACCEPTANCE_20261028.md`：时间语义修复；Linux后续证据见相应归档。
- `docs/STEP2_FACTOR_LOCAL_QUEUE_E2E_20261028.md`：只认证登记型隔离队列闭环。
- `docs/evidence/object-publication-minio-20261029/factor-native/sealed-review/README.md`：原生适配器33项、真实891待做。
- `docs/QLIB_BASELINE.md`：已有成本/交易近似与PIT限制。
- `docs/QLIB_UNIFIED_STRATEGY_COMPARISON.md`及 `docs/evidence/qlib-step1-initial-native-20261027.json`：已有七策略真实执行资产，优先复用。
- `plans/STEP2_USER_GUIDE.txt`：原用户指南，保留原文；阶段顺序以本次最新指令为准。
- `docs/OBJECT_PUBLICATION_MINIO_20261029.md`：发布支线成果和未通过项，按需查，不作为启动主阅读任务。

不要先通读几十万行 `docs/evidence`，先看上面摘要，需要准确重现时定位指定manifest。

## Commands And Checks：新窗口怎么开始

### 第一次只读检查

```powershell
Set-Location -LiteralPath E:/QTrade-git
git status --short
git branch --show-current
git rev-parse HEAD
Get-Content AGENTS.md
Get-Content CURRENT_STATE.md -TotalCount 70
Get-Content plans/MASTER_PLAN.md -TotalCount 60
Get-Content DECISIONS.md -TotalCount 70
Get-Content docs/handoffs/2026-10-29-step2-research-priority.md
```

不要 `git reset --hard`、清理node_modules、直接checkout旧commit或强制pull覆盖其他窗口工作。
同一机器直接打开已有仓库即可；不同机器应获取该分支及文档，再安全配置集群访问。

### 第一项数据复核与短窗口组合入口

优先调用 `ready_dataset(version_root, data_database)`，它会走完整文件验证和唯一准入
检查。不要为读取生产状态构造有写入/owner claim的DataVersionCatalog或StrategyRegistry。
生产SQLite用URI `mode=ro` + `PRAGMA query_only=ON`；需要可写Registry时先做一致性
backup到自己的新目录，不能直接复制活跃WAL主文件假装已冻结。

下列为已存在的CLI形状，**只有替换为核实过的登记、archive和请求后才能执行**：

```bash
export LIVE_TRADING_ENABLED=false
export QT_BASE=/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade
export QT_SRC=/path/to/verified/research-release
export QT_RUN=/path/to/new/isolated-nonproduction-run
export PYTHONPATH="$QT_SRC/src:$QT_SRC"

"$QT_BASE/qlib-env/bin/python3.11" -B -P -m scripts.run_factor_strategy_backtest \
  --base "$QT_BASE" \
  --data-database /root/qtrade-local/qlib-versions.sqlite \
  --strategy-database "$QT_RUN/input-strategies.sqlite" \
  --archive-root "$QT_RUN/approved-factor-archive" \
  --request "$QT_RUN/request.json" \
  --output "$QT_RUN/new-result" \
  --source-revision ACTUAL_VERIFIED_SOURCE_GIT40
```

所需request包含schema/kind、新job_id、actor、确切strategy_id/version/contract、
registration_job_id、data_version_id、执行source_revision以及live=false。可以查看
`validate_request()`，不要猜字段或使用latest。runner会重验旧登记/报告/READY、
复制provider、执行Qlib和写新结果，不会自动登记/激活/恢复PAPER。

### 定向验证

先按改动选择最小有意义集合，不为了测试数量泛化跑所有证据目录：

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
$env:LIVE_TRADING_ENABLED='false'
python -B -m pytest -q tests/test_factor_registry.py tests/test_daily_factor_evaluation.py tests/test_step2_factor_contract_independent.py tests/test_factor_strategy_contract.py tests/test_factor_strategy_backtest.py
```

Windows缺Qlib时原生例可能跳过，不能用它替代集群原生验收。Linux用既有研究解释器，
精确源码和独立测试依赖目录；不要混装API环境。前端有改动时在 `web/` 运行对应vitest、
`npm run lint` 和 `npm run build:vercel`；本轮文档交接没有运行前端构建或视觉验收。

每个新实验保留：来源/数据核验 → 不可变规格 → 实际执行 → 数值/会计核验 → 报告和复现命令。
原失败只追加新证据，不能改成成功或覆盖旧输出。

## Open Questions：保留的限制和风险

1. 891股逐年实际覆盖、首次可用日和有效样本数需M0量化，不能仅看日历长度。
2. 已有因子历史信息可得时点、历史股票池/退市/ST/精确涨跌停/公司行为复权未认证。
   研究条件性结论应披露；不据此阻止全部离线研发，也不升级成真实交易可行性证明。
3. 长窗口限额、输出大小和旧source指纹兼容需M2处理；不能静默放大限额或删校验。
4. 等权/指数基准、成交费用、组合持仓跨块连续性尚须真实891结果验证。
5. 冻结的abe公网包index/master38f5与catalog/masterc4ed不一致，Git180发送结果不确定；
   不重发旧claim。保留为独立发布任务，不把修它作为M1前提。
6. 网页当前版本不等于开发分支；本轮无DOM/截图验收。需要浏览器时用户指定
   GPT-6.1 Terra high子agent；一般子agent按最近授权GPT-6.1-sol xhigh，避免反复创建失败会话。
7. 旧自动任务仍PAUSED，不恢复带有旧“STEP1全部完成才STEP2”的heartbeat提示词。

## 安全与并行边界

- 始终 `LIVE_TRADING_ENABLED=false`、rolling OFF；不修改当前ACTIVE或旧账本/冻结信号。
  09:32实测ACTIVE仍68d9287e…，但新窗口必须核实时点；站立自动政策的自然事件也要如实保留。
- 旧publisher647839/startticks1022098698和forward383859/startticks1015213725仍存在、cwd对应
  原发布版；这不是可用性认证，更不是重启授权。不得kill/重启owner作为研究准备步骤。
- 已消费的handover/恢复请求不能重放；新账户/异常恢复要独立人类动作与成功对账。
- 策略不访问网关；保留portfolio→risk→OMS→execution→gateway，研究不连接券商。
- 采集owner活跃时，历史缺口治理只做不竞争的读取/计划，不强行apply维护修复。
- 新窗口接管后，本窗口只交接文档；避免两个agent同时改同一branch/文件或启动相同实验。

## Next Step：给新窗口的第一项交付

先提交**旧七策略结果的复核说明、891股冻结版覆盖矩阵与新登记链可运行规格**。
旧对照可复用部分不重做；用最小实际重放闭合新登记型适配器，再扩至四项和长窗评价。
同时提出长窗口有界执行的窄改方案，尽快进入M2/M3。不要先恢复MinIO/R2研究。

最终应给用户的是可重跑的策略研究结果及其限制，而不是更多协议测试数字。
