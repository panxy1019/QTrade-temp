# 11｜组件覆盖索引与术语表

[返回学习目录](README.md)

索引按网站实际区域组织。相似名称的组件可能属于不同历史实验，先匹配区域，再找解释。

## 1. 策略中心索引

| 组件/字段 | 解释位置 |
|---|---|
| 页面标题、4主动＋1基准、已实现未生产验证 | [02 第1—2节](02-strategy-center.md) |
| 等权买入持有 | [02 第3.1节](02-strategy-center.md) |
| 移动平均趋势、默认window/quantity | [02 第3.2节](02-strategy-center.md) |
| ETF动量轮动、horizons/TopK/调仓/预留现金 | [02 第3.3节](02-strategy-center.md) |
| 趋势过滤、波动窗口、目标风险 | [02 第3.4节](02-strategy-center.md) |
| 横截面排序、多因子权重 | [02 第3.5节](02-strategy-center.md) |
| 模板范围、开仓、平仓、展开实现/输入/限制 | [02 第2节](02-strategy-center.md) |
| 载入因果回测、尚未接入 | [02 第2节](02-strategy-center.md)、[10 第6节](10-walkthrough-and-exercises.md) |
| 候选名称、Family、状态、角色、证据 | [02 第4节](02-strategy-center.md) |
| Sharpe、最大回撤小数格式 | [07](07-performance-metrics.md) |
| 策略ID、数据版本、Commit、Record哈希、场景、谱系 | [02 第4—7节](02-strategy-center.md) |
| 搜索、场景筛选、分页、导出 | [02 第6节](02-strategy-center.md) |

## 2. 策略研究主区域索引

| 组件/字段 | 解释位置 |
|---|---|
| 当前数据横幅、符合交易日历 | [09 第8—9节](09-data-and-evidence.md) |
| Qlib运行状态、刷新、进度与排除数 | [05 第2、10节](05-qlib-baseline.md) |
| 数据行数、158特征、训练验证测试、标签截止 | [05 第2—5节](05-qlib-baseline.md) |
| Top50、替换5、仓位、持有期、成交限制、成本 | [05 第6—7节](05-qlib-baseline.md) |
| IC、RankIC、累计收益、Sharpe、回撤、双边换手 | [05 第8节](05-qlib-baseline.md)、[07](07-performance-metrics.md) |
| Qlib净值/沪深300/右轴回撤 | [01 第6节](01-platform-map.md) |
| 实验ID、提交、清单/结果哈希、复制、导出 | [09 第10—12节](09-data-and-evidence.md) |
| 因子目录七列、角色、known_at、earliest_execution | [03 第2节](03-research-page.md) |
| 五个日频公式、三连阳、L2假设 | [04 第4—6节](04-factors.md) |
| 120日固定报告全部摘要指标 | [03 第3节](03-research-page.md) |
| 长期曲线/六折/阈值/时间块/行业/个股/研究硬门 | [03 第3节](03-research-page.md)、[08](08-statistics-and-robustness.md) |
| 全局晋级结论和原因码、全部研究导出 | [03 第1、5节](03-research-page.md) |

## 3. 分钟工作台索引

| 组件/字段 | 解释位置 |
|---|---|
| 因子、日期、成本、实体阈值、退出参数 | [06 第2节](06-intraday-lab.md) |
| 三段看涨/看跌/忽略、ALL/ANY、草案 | [06 第3节](06-intraday-lab.md) |
| 10:00确认、10:05标签、T+1、14:55时间退出 | [06 第4—5节](06-intraday-lab.md) |
| 运行75股因子检验与回测 | [06 第1、6—8节](06-intraday-lab.md) |
| 身份已绑定、手工交接血缘/未交接 | [06 第6、10节](06-intraday-lab.md) |
| 研究硬门、顺序停点、流程步骤图 | [06 第6节](06-intraday-lab.md) |
| 下一假设预注册及八类要求 | [06 第6节](06-intraday-lab.md)、[09](09-data-and-evidence.md) |
| 注册前审查、2/9、6/15、行为规格/完整注册规格 | [06 第6节](06-intraday-lab.md)、[09 第10节](09-data-and-evidence.md) |
| PAPER准入、1/8、运行证明、对账 | [06 第6节](06-intraday-lab.md) |
| 本次20日默认预览所有指标 | [06 第7节](06-intraday-lab.md)、[07](07-performance-metrics.md)、[08](08-statistics-and-robustness.md) |
| 因子与时序、只读策略ID、注册状态 | [06 第8节](06-intraday-lab.md) |
| 有效性与相关性、平仓因子有效性 | [06 第5、8节](06-intraday-lab.md) |
| 滚动样本外与阈值、时间稳定性、成本压力 | [08 第8—10节](08-statistics-and-robustness.md) |
| 15行业、75个股、分组过滤 | [08 第11节](08-statistics-and-robustness.md) |
| L2状态、20字段、零分母、六项就绪门 | [06 第12节](06-intraday-lab.md) |
| 本地开仓候选名称、保存、勾选2—8、删除/载入 | [06 第10节](06-intraday-lab.md) |
| 规则有效性、原始p/Holm p、相关矩阵、冗余对 | [08 第5—7节](08-statistics-and-robustness.md) |
| 进入策略构造、联合身份包导出 | [06 第10节](06-intraday-lab.md) |
| 本地因子实验名称、保存、载入、删除、导出 | [06 第11节](06-intraday-lab.md) |

## 4. 104候选设计与当前可用性

当前按钮实测被响应契约拒绝，以下组件为源码中存在的条件结果区，未在本次线上成功展示。

| 条件结果组件 | 原理位置 |
|---|---|
| 104候选、训练锁定、测试切分、候选表 | [06 第9节](06-intraday-lab.md) |
| 锁定规则载入、导出发现证据 | [06 第9节](06-intraday-lab.md) |
| 四档成本及固定测试门 | [08 第10节](08-statistics-and-robustness.md) |
| 行业内、整行业剔除、正收益占比 | [08 第11节](08-statistics-and-robustness.md) |
| 个股中位数、前五绝对收益集中度 | [08 第11节](08-statistics-and-robustness.md) |
| 信号前市场状态、状态已知时间 | [06 第9节](06-intraday-lab.md) |
| 会话内置换、p值、95分位 | [08 第13节](08-statistics-and-robustness.md) |
| 1/2/3根Bar延迟 | [06 第4节](06-intraday-lab.md)、[08 第15节](08-statistics-and-robustness.md) |
| 1%/5%/10%参与率、一手覆盖、股数/人民币容量 | [08 第15节](08-statistics-and-robustness.md) |
| CSCV块、70组合、排名logit、PBO、赢家频次 | [08 第14节](08-statistics-and-robustness.md) |
| 完整发现门、局部门与最终NOT_ELIGIBLE | [06 第9节](06-intraday-lab.md) |

## 5. 底部十个页签索引

| 页签 | 原理位置 |
|---|---|
| 实验候选 | [03 第4.1节](03-research-page.md) |
| 因子搜索 | [03 第4.2节](03-research-page.md) |
| 因子鲁棒性 | [03 第4.3节](03-research-page.md) |
| 多板块个股迁移 | [03 第4.4节](03-research-page.md) |
| 开发区分钟策略 | [03 第4.5节](03-research-page.md) |
| 未来完整日观测、来源覆盖、检查点、1/20 | [03 第4.6节](03-research-page.md)、[10 第7节](10-walkthrough-and-exercises.md) |
| 滚动样本外 | [03 第4.7节](03-research-page.md) |
| 稳健性诊断、离散度 | [03 第4.8节](03-research-page.md) |
| 演化树 | [03 第4.9节](03-research-page.md) |
| 未晋级 | [03 第4.10节](03-research-page.md) |

## 6. 名词速查

| 名词 | 一句话解释 |
|---|---|
| Alpha | 相对基准或风险暴露之后的预测/收益优势；应说明具体定义 |
| Beta | 对市场或某个共同因子的敏感度 |
| Alpha158 | Qlib价量特征集合，不是158条成熟策略 |
| LightGBM / GBDT | 梯度提升树模型 |
| Feature / X | 决策时可知的模型输入 |
| Label / y | 后来才实现的训练/评价答案 |
| Score | 预测排序分数，不必是收益率或概率 |
| Universe | 候选标的集合 |
| PIT | 历史时点可获得的信息与状态 |
| OHLCV | 开高低收及成交量 |
| Amount | 成交额，要核对币种和单位 |
| VWAP | 以成交量加权的平均成交价，通常由金额/股数得到 |
| Raw / QFQ / HFQ | 原始/前复权/后复权价格口径 |
| Corporate action | 分红、送转、配股等公司行为 |
| Bar | 某时间区间的行情聚合 |
| Known_at / Available_at | 信息可以用于决策的时间 |
| Earliest execution | 最早允许模拟执行的时点 |
| Session | 交易会话，本项目多指一个交易日 |
| Warm-up | 计算首个信号所需的先前历史 |
| Top K | 排名后持仓的目标名额 |
| Rebalance | 重新计算和调整目标仓位 |
| Reserve | 现金预留比例 |
| Risk degree | 风险资产资金目标比例 |
| Turnover | 换手，必须说明单/双边、日均/累计、金额/成员 |
| Exposure | 风险暴露；具体模块可能采用活跃日近似 |
| bp / bps | 万分之一/基点，100bp=1% |
| Slippage | 执行价与参考价之间的偏差 |
| Impact | 订单自身影响价格的成本 |
| Capacity | 在流动性与规则下可以交易的规模 |
| Round lot | 整手交易单位，须依证券与时期确定 |
| Equity / NAV | 账户权益或归一化净值 |
| CAGR | 复合年化增长率 |
| Volatility | 收益波动标准差 |
| Drawdown / MDD | 距历史峰值的损失/最大损失 |
| Sharpe | 超过无风险收益的均值相对总波动 |
| Sortino | 均值相对下行偏差 |
| Calmar | 复合年化收益相对最大回撤幅度 |
| Benchmark | 比较基准 |
| Excess | 相对基准的差异，需明确算术差或财富比 |
| Tracking error | 主动收益波动 |
| Information ratio | 主动收益均值相对跟踪误差 |
| IC | 因子/预测与未来收益的相关 |
| RankIC | 排名与未来收益排名的相关 |
| ICIR | 日IC均值相对日IC波动 |
| Quantile | 按分数切出的分组 |
| Long-short spread | 高低分组收益差，不必是可执行做空 |
| Hit rate | 某种观察为正的比例，要确认事件单位 |
| Conditional excess | 条件触发后的收益相对无条件样本 |
| Cluster | 把共同相关的观察按日期等组聚合 |
| Standard error | 估计量的不确定性尺度 |
| HAC / Newey–West | 考虑异方差与滞后相关的误差估计 |
| p-value | 零假设成立时观察到极端统计量的可能性 |
| Confidence interval | 在给定方法与重复抽样解释下的参数区间 |
| Holm | 控制一组多次检验错误率的方法 |
| Correlation rho | 线性或排名联系，不等于因果 |
| Multicollinearity | 输入之间高度相关 |
| Redundancy | 新输入与已有输入的信息重复 |
| Ablation | 在固定协议下去掉一组输入观察变化 |
| Train / Valid / Test | 拟合/选择/评价的不同时间分区 |
| Holdout | 留出的测试数据，需保持信息隔离 |
| Purge / Embargo | 清除跨分区标签或设置隔离间隔 |
| Walk-forward | 按历史向未来滚动的训练测试流程 |
| OOS | 样本外；必须说明相对哪次拟合/选择 |
| Bootstrap | 从已有样本重采样评价不确定性 |
| Placebo | 构造随机/无效对照来检查观察优势 |
| CSCV | 组合式对称交叉验证，用于选择偏差诊断 |
| PBO | 训练赢家在互补测试落到较差位置的比例估计 |
| Regime | 市场状态，预测用途要求事前可知 |
| Leakage | 把当时不可知信息带入决策或选择 |
| Survivorship bias | 当前存续样本回投历史造成的选择偏差 |
| Multiple testing | 同时试很多假设带来的偶然显著问题 |
| Artifact | 模型、预测、曲线等实验文件 |
| Manifest | 输入/产物清单与校验信息 |
| SHA256 / Checksum | 内容摘要，用于一致性核对 |
| Spec / Spec hash | 行为或运行规格及其摘要 |
| Record | 绑定身份与结果的记录 |
| Lineage | 明确的实验或策略父子血缘 |
| Registry | 存储正式定义/版本/状态的注册表 |
| Preregistration | 在新证据前固定假设与评价协议 |
| Champion / Challenger | 已按治理指定的主策略/挑战者角色 |
| AD_HOC_UNREGISTERED | 临时研究身份，尚未注册 |
| COMPLETE | 工程运行完成，不是科学有效性结论 |
| NOT_ELIGIBLE / BLOCKED | 不满足晋级/下一动作条件 |
| PAPER | 无真实资金的订单与账户模拟 |
| SHADOW | 观察信号和假想执行的前向运行；本项目具体准入合同另定 |
| LIVE | 真实交易模式，当前禁用 |
| Reconciliation | 订单、成交、持仓和资金对账 |
| OMS | 订单管理系统 |
| Gateway | 外部执行/行情适配边界 |
| Freshness | 数据或发布的新鲜程度，不是研究有效性 |
| Vercel | 当前网页和只读接口的部署服务 |
| MLflow Recorder | 保存运行参数、指标和产物的实验记录组件 |
| Parquet | 列式数据文件格式，适合历史行情与预测 |
| LocalStorage | 浏览器本地持久存储，不能当集群实验仓库 |

“独立因子评价”指单独和条件增量检验；不是强制每个因子与其他因子都完全统计独立。“统一策略对照”指把可比较条件冻结，再隔离变化来源。
