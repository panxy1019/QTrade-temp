# Qlib官方模块与当前项目接入情况

核对日期：2026-10-26，北京时间。依据为用户提供的
[官方文档首页](https://qlib.readthedocs.io/en/latest/)、本地源码及集群现场环境。
这是集成盘点与后续建议，不表示下列待办已经实施。

结论：当前为Qlib核心日频研究链路的选择性集成，尚非全部官方模块的网页化平台。
底层框架可以支持某项能力、项目实际调用它、网页允许配置它、生产通过验收，是四种
不同状态。后台持续采集也不等于已实现Qlib Online Serving或每日自动重训。

## 1. 版本与当前完整链路

集群现场：Qlib **0.9.7**，Python **3.11.17**，MLflow **2.22.2**。
当前 `/en/latest/` 文档标题为 **0.9.8.dev11**。因此不能默认最新版文档的所有
接口都在现有固定环境中完成兼容性验收；版本升级应单独处理，保留旧实验环境身份。

当前实际执行：

研究登录 → 允许列表规格 → 私有Git队列 → SQLite持久化工作器 →
冻结数据和状态来源 → Qlib本地Provider → Alpha158/DataHandler/DatasetH →
Ridge或LightGBM → TopkDropout日频回测 → Recorder/模型及结果归档 → 网页结果与对照。

这条链路已实际在生产浏览器提交并于集群执行，能够复用归档规格。
已有结果不是手工填写的展示数据，但支持范围受已适配的合同约束。

## 2. 按官方目录逐项对照

图例：**已接通**表示当前生产链路有实际调用或验收；**部分**表示仅接入固定子集；
**自有实现**表示业务功能存在但没有采用该官方模块；**未接入**不表示依赖包一定
不含相应类，而是本项目尚未接通并验收。

| 官方模块/能力 | 当前状态 | 实际实现及边界 |
| --- | --- | --- |
| Initialization | 已接通 | 实际调用qlib.init，使用中国市场、本地Provider和MLflow实验后端 |
| Data Preparation / Qlib Format | 已接通固定版本 | 生成.bin、日历与证券区间，适配复权价格、量、成交额/VWAP等；当前训练输入冻结至2026-10-23 |
| Automatic daily update | 部分 | 原始行情维护在运行；每天新行情到新Qlib版本、验收和自动发布尚未完整串联 |
| Data API / Data Loader | 底层已使用 | D.calendar、D.features及Alpha158内部数据加载已调用；没有面向网页的通用数据查询/算子工作台 |
| Data Handler / Processor / Dataset | 已接通固定合同 | Alpha158、DatasetH、训练期RobustZScoreNorm、Fillna、标签处理、固定三段切分；网页不能任意更换处理器和标签 |
| Cache | 基础使用/共享未验收 | 当前本地数据计算沿用库行为；没有专门接入和验收远程共享缓存管理或Qlib-Server |
| Building Formulaic Alphas | 部分 | Alpha158底层表达式在计算；自定义公式登记、因子版本和逐因子评价入口尚未接通 |
| Forecast Model | 部分 | 正式允许Ridge和LightGBM；其他模型尚未逐个适配、测试和开放 |
| Custom Model Integration | 未开放通用入口 | 开发者可按合同修改代码适配；网页不能提交任意module_path、Python类或命令 |
| Workflow / qrun | 业务闭环已接通，运行器自定义 | 用自己的run_qlib_baseline串联Qlib组件；不是原样开放任意qrun YAML工作流 |
| Portfolio / TopkDropoutStrategy | 已接通 | 可配置TopK、淘汰数、最少持有步、仓位及成本，当前主要组合规则仍为这一类 |
| EnhancedIndexingStrategy / WeightStrategyBase自定义策略 | 未接入网页正式链路 | 风险模型、行业/风格时点数据、优化合同及对应结果比较还需建设 |
| Backtest / Exchange | 已接通日频子集 | 实际调用backtest_daily和方向限制检查，使用开盘价、容量与成本近似及来源对账后的附加限制 |
| Nested Decision Execution / High-Frequency Trading | 未接入 | 现有75股观察和分钟历史工具，不是Qlib日频组合与盘中执行的嵌套回测 |
| Qlib Recorder / Experiment / MLflow | 已使用核心 | R.start、log_params、log_metrics、save_objects、recorder_id均在使用；网页展示自有统一实验目录，不是完整原生实验管理界面 |
| SignalRecord / PortAnaRecord等Record Template | 未按通用模板接入 | 当前直接计算预测IC并调用回测、保存结果；不能称所有官方Record模板已被使用 |
| Analysis / Graphical Reports | 部分 | 主指标、净值、回撤、换手及受控模型对照已展示；官方全部分析图、月度分布、收益归因和持仓穿透未全部接入 |
| Serialization | 部分 | model.pkl、positions.pkl等已归档并校验；完整DataHandler/处理器恢复及每日恢复推理流程尚无统一服务验收 |
| Task Management / TaskManager | 自有实现 | 登录提交、排队、取消、状态、时限和归档已有；使用私有Git＋SQLite，不是官方MongoDB TaskManager |
| RollingGen / Trainer滚动任务 | 未接入新网页流程 | 旧代码有walk-forward基础；新Qlib链路仍为单次固定切分，没有网页配置滚动窗口、批次和汇总 |
| Online Serving / OnlineManager / Updater | 未接入 | 未建立每天更新预测、轮换在线模型、记录每日模型版本和形成前向信号的完整运行闭环 |
| Point-In-Time Database | 未接入原生模块 | 当前自有主表/ST/交易状态回填与原文冻结，不是原生财务PIT文件数据库；完整历史可得性仍未认证 |
| Online & Offline mode / Qlib-Server | 当前本地模式 | 集群读取本地Provider；Vercel展示网页不等于部署Qlib-Server远程数据服务 |
| Meta Controller | 未接入 | MetaTask、MetaDataset、MetaModel等元学习流程尚未适配 |
| Reinforcement Learning | 未接入 | 没有接入强化学习环境、策略训练和执行优化的正式实验 |

上述状态不适合相加得出一个“集成百分比”：数据API、研究模型、服务和强化学习的
工作量、依赖和必要性不同。应按研究目标验收能力，不以目录条目数衡量成熟度。

## 3. 几个容易混淆的地方

### 网页已上线与Online Serving

Vercel是我们自己的研究控制台和结果展示。官方
[Online Serving](https://qlib.readthedocs.io/en/latest/component/online.html)
关注模型、预测、信号和在线模型历史的持续管理。两者不是同一个模块。
已有网页和持续采集，不代表每天已自动生成新Qlib信号。

### 任务中心与官方TaskManager

官方[Task Management](https://qlib.readthedocs.io/en/latest/advanced/task_management.html)
包含任务生成、存储、训练和收集，原生TaskManager文档要求MongoDB。
我们的研究任务功能已实现，只是采用当前规模适用的Git队列与SQLite。
后续可复用RollingGen的任务生成思想或接口，继续交给现有受保护队列执行；
是否采用完整原生TaskManager取决于并发与运营需求，无需为了凑齐模块重做队列。

### 状态回填与原生PIT Database

官方[PIT数据库](https://qlib.readthedocs.io/en/latest/advanced/PIT.html)主要处理
财务数据的发布日期、报告期以及后续修订版本；其文档也说明当前针对季度/年度因子。
它不会自动补齐我们缺少的退市股行情、历史成员、ST/停复牌和精确交易规则。
现有180股状态来源回填，也不能解释为财务PIT数据库已经集成。

### 模型记录与模型持久化

本次对真实生产归档 `archived-compare-lightgbm-20261026-1003990bba3effdc` 复查：
`model.pkl`、`positions.pkl`、`predictions.parquet`、`labels.parquet`、
`signal-diagnostics.parquet`、`portfolio-report.parquet` 全部存在于校验清单，
实际字节SHA256均匹配。该实验也具有MLflow recorder_id。

因此模型文件确实已保存；后续仍需补“恢复模型及其预处理→对新数据预测→验证与
原推理一致→每天封存信号”的服务合同。模型归档与每日在线推理要分别验收。

### Alpha158与自有因子库

已有Alpha158计算不等于已开放[自定义公式因子](https://qlib.readthedocs.io/en/latest/advanced/alpha.html)。
后续还需因子ID、版本、公式、输入与时点、独立评价、关联实验和策略引用关系。
模型整体IC也不等于158个输入因子各自的独立IC。

## 4. 接下来的接入顺序

1. **P0数据可信度与P0独立因子评价并行。** 先修复旧分钟检查点的覆盖验收、推进
   状态与退市来源核验，同时接通因子登记、表达式、固定数据与单因子报告。接通
   新交易日适配/验收/发布到Qlib版本。详见[平台能力与路线图](PLATFORM_CAPABILITY_AUDIT_20261026.md)。
2. **P1统一策略与滚动研究。** 增加简单因子/多因子/模型的同协议基线，接入有界
   RollingGen/Trainer式任务生成、滚动结果汇总、成本和容量压力、持仓及风险归因。
   保持模型对照与组合规则对照的变化轴明确。
3. **P1前向信号。** 在数据版本更新可验收后，再接OnlineManager/Updater或等价
   服务，先做每日封存预测和前向观察；不自动授予模拟盘或实盘资格。
4. **按输入条件扩展。** 有时点行业/市值/风险模型后再接指数增强或约束优化；有
   真实财务披露及修订数据后再接原生财务PIT。不是先打开一个菜单再假定数据已有。
5. **P2专项能力。** LLM候选生成、公告事件结构化进入统一评价；元学习、强化学习、
   嵌套执行与Qlib-Server在研究问题和数据需要明确时单独验收。

当前不建议为了覆盖官网所有条目而重建项目。先完成上面前三项，才更接近日常可用
的研究平台；更多模型和高级算法应有明确研究假设及合格输入。

## 5. 如何判断以后某个模块真的接通

- 有对应实际数据和允许列表配置，缺失与时点规则明确。
- 集群能执行，模型/输入/源码/环境身份能够固定。
- 网页能配置有界参数、提交、跟踪、查看结果和限制。
- 产物可恢复、可复现、可对照；故障和版本变更有清晰状态。
- 有相应测试、集群与生产验收，记录已知限制。

安装依赖或新增一个按钮只能完成其中一部分。
`LIVE_TRADING_ENABLED=false`，研究结果仍保持NOT_ELIGIBLE。

## 6. 官方资料与本地代码

- [官方模块总目录](https://qlib.readthedocs.io/en/latest/)
- [Data Layer](https://qlib.readthedocs.io/en/latest/component/data.html)
- [Workflow](https://qlib.readthedocs.io/en/latest/component/workflow.html)
- [Recorder](https://qlib.readthedocs.io/en/latest/component/recorder.html)
- [Portfolio Strategy](https://qlib.readthedocs.io/en/latest/component/strategy.html)
- [Analysis](https://qlib.readthedocs.io/en/latest/component/report.html)
- [High Frequency](https://qlib.readthedocs.io/en/latest/component/highfreq.html)
- [Meta Controller](https://qlib.readthedocs.io/en/latest/component/meta.html)
- [Reinforcement Learning](https://qlib.readthedocs.io/en/latest/component/rl/toctree.html)
- [Qlib-Server模式](https://qlib.readthedocs.io/en/latest/advanced/server.html)
- [实际训练、回测及保存模型代码](../scripts/run_qlib_baseline.py)
- [网页任务允许列表与规格](../src/qtrade/research/workflow.py)
- [集群工作器](../scripts/run_qlib_worker.py)
- [正式网页入口](../web/src/pages/QlibWorkflowPages.tsx)

本次为环境、源码和已有归档的核验，没有执行新训练或修改运行代码。
