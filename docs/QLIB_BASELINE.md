# Qlib 日频基线

## 首次运行结果（2026-10-25）

990/1000股、2,157,805根日线通过数据适配。排除的10股为000560、002640、002297、
002129、300243、300088、002584、002025、002440、300496；原因均为原有数据中的
部分日期在供应商原始日线中不存在。保留旧数据原样，不以重新下载覆盖差异。

运行ID：`alpha158-lightgbm-daily-v1-beb391a43169a5f0`，实际研究源码提交
`207a6b7a4eb324956139a98529110f166039095f`。
样本外组合678个会话，2024-01-02至2026-10-23；IC标签截至2026-10-21。

| 指标 | 本次结果 |
|---|---:|
| IC | 0.02341358 |
| RankIC | 0.04940501 |
| 成本后累计收益 | 111.2896% |
| 成本后年化收益 | 32.0539% |
| 成本后Sharpe | 1.211655 |
| 最大回撤 | -33.5016% |
| 日均双边换手 | 17.3714% |
| 同期沪深300收益 | 19.6503% |

该股票池在测试末期筛选，结果有明显的幸存者/筛选偏差，且历史交易状态未全部
认证；这些数字是迁移基线诊断，不是无偏收益预测、前瞻验证或交易许可。

生产验收：`https://q-trade-temp.vercel.app/#/research` 和 `#/backtest` 展示独立Qlib面板；
`/api/qlib/baseline` 返回200、COMPLETE，结果/产物哈希与集群一致。
原有观测通道healthy=true，采集、维护和发布服务均运行。验收记录为
`deployment/acceptance/qlib-production-20261025.json`。
Linux全量516项通过，后续新增日历边界的24项相关检查通过；前端全量196项通过；
Ruff、mypy和Vercel构建通过。原有889KB入口分块告警也存在于迁移前构建，本次未改动分包。

该容器Supervisor的配置重读RPC返回值与Python客户端不兼容；当前使用显式启动器
将原发布进程exec到已核验的新源码目录，PID受原Supervisor管理。磁盘上的Supervisor
配置也已指向新目录，重启后可直接使用；未重载整个守护进程或其他用户服务。

这是对现有研究平台的增量迁移：采集、质量核对、归档和网页继续复用 QTrade，
特征、训练、信号与组合回测使用 Qlib。没有交易网关或订单权限。

## 数据合同

从 `expansion/universe.json` 读取已签名的当前流动性样本。逐股重新读取 Sina
原始压缩日线，补回成交额，并与已有 Parquet 的哈希、日期、OHLCV 核对。
无法解释的差异排除整只股票，清单随数据版本保存，不静默覆盖已有数据。
原始供应商响应和每股转换结果均有 SHA256。

不直接使用 AKShare 合并流通股本后前向填充的结果，以避免非交易日期出现
填充价格。供应商原始记录缺失不等于停牌原因已认证：缺行情时不成交，不能
声称已经拥有权威停复牌表。

价格采用供应商按生效日提供的后复权乘数 H。首日归一化常量 C=首日收盘价×H：
`factor=H/C`，`adjusted_price=raw_price×factor`，
`adjusted_volume=raw_volume/factor`，`vwap=amount/raw_volume×factor`。
因此 `close/factor` 恢复原始价格，`vwap×volume` 等于成交额。
只向后使用已生效的因子，不把未来因子回填到更早日期。
供应商历史修订和公司行为仍未独立核验，不能宣称历史 PIT 认证。

Alpha158 使用完整 158 个默认特征，包含真实成交额/成交量得到的 VWAP。
缺失交易日保持 NaN，不创造 OHLCV。基准为 Sina 沪深300指数日线，其日期构成
本版本的历史交易日历；这不是未来交易日历服务。
Qlib回测步长需要下一个会话边界；使用现有已核验的2026年交易日历追加3个
仅日历日期，价格和成交量保持NaN，证券有效区间和实际数据截止日不延长。

当前 1000 股是在 2026-10-23 样本末期按流动性筛选，存在幸存者及初筛偏差。
适配失败的股票不进入本次训练；最终实际数量和排除原因见数据 manifest。

## 固定实验

配置：`configs/qlib_daily_baseline.json`。随机种子 20261025，Alpha158 + LightGBM。

| 分区 | 区间 |
|---|---|
| 训练 | 2017-04-05 至 2021-12-28 |
| 验证 | 2022-01-04 至 2023-12-26 |
| 历史样本外测试 | 2024-01-02 至 2026-10-23 |

训练/验证结束处保留交易日隔离，使未来两步标签不跨入下一分区。
标签为 `Ref($open,-2)/Ref($open,-1)-1`；末尾未成熟标签不进入 IC/RankIC，
但已成熟行情可继续进入组合净值。树数上限300，验证集早停30；不搜索测试集参数。
这是迁移后建立的事后固定基线，不宣称预注册盲测或未见前瞻证据。

组合 Top50，每天最多替换5只，95%风险仓位，初始资金1000万元。
前一交易日信号在下一交易日开盘成交，至少持有2个交易步；100股整手。
科创板可参与特征训练，但不买入，以避免用100股规则错误替代其交易数量约束。
历史 ST 标记和逐日精确涨跌停价格未认证，使用保守的4.5%方向性复权开盘
跳空限制，可能拒绝实际可成交的机会。它不能替代权威历史交易状态。
容量为前一会话原始成交量的1%，转换到当日复权股数；不使用当天完整成交量
提前决定开盘容量。缺失前一日成交量时不成交。

买入8bp、卖出13bp、最低5元：近似3bp佣金+5bp滑点，卖出再计5bp印花税；
这些是研究假设，并非券商报价，没有独立的非线性冲击或开盘集合竞价撮合。
换手遵循 Qlib 的双边成交额/账户净值口径。组合收益减去 `report.cost` 后复利；
Sharpe用252个会话年化，ICIR为日IC均值/日IC标准差，不乘年化系数。

## 集群运行

独立 Conda Python3.11 环境；`requirements-qlib.txt`固定核心依赖，实际完整依赖
另存 `pip-freeze.txt`。MLflow2.22要求 Arrow<20，因此研究环境用 Arrow19；
现有采集/API环境不变，不在研究环境中安装项目的API依赖组合。

```bash
export LIVE_TRADING_ENABLED=false
export BASE=/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade
export PYTHONPATH="$PWD/src"
# 现有采集环境，Sina请求不占用BaoStock匿名会话。
"$BASE/venv/bin/python" -m scripts.prepare_qlib_data \
  --expansion "$BASE/expansion" --root "$BASE/qlib-data-20261023" --workers 3
# 固定的源码提交必须是本次实际运行的提交。
"$BASE/qlib-env/bin/python" -m scripts.run_qlib_baseline \
  --data-root "$BASE/qlib-data-20261023" --output "$BASE/qlib" \
  --tracking /root/qtrade-local/qlib-mlruns --source-revision ACTUAL_FULL_COMMIT
```

数据准备可以按每股已验证 checkpoint 重试；已经完成的 dataset 根目录不可
覆盖，变更源数据或口径必须换目录。不要并发启动同一批次。
失败训练的 `.partial` 保留供审计，下次运行使用新 attempt 目录。
MLflow活动记录在本地盘；NFS保存完整不可变运行，包括配置、代码副本、环境、
数据清单、Qlib二进制输入、模型、预测、标签、IC明细、每日组合报告、持仓和哈希清单。
仅完整归档才能生成 COMPLETE 状态。

## 发布与后续操作

发布器读取 `qlib/status.json`，完成后重新验证归档文件哈希，仅将有界摘要和
每日净值发布为独立 `qlib.json`。已有行情 JSON、历史报告与实验不被改写。
网页策略研究/回测分析展示该结果；`GET /api/qlib/baseline` 只读，无启动实验API。
每60秒刷新；发布年龄和结果数据截止日分开判断，旧实验不会随心跳变为最新结果。

新增因子需定义公式、所需字段和可用时点，注册进独立处理器/配置并建立新实验，
通过时间切分和成本后评估后发布结果。GitHub代码提交触发Vercel代码部署；
模型计算与数据更新继续在集群，不能靠上传Python文件自动得到实验结论。
本版本是一次固定基线，不自动修改配置、重训或晋级；新增交易日需创建新数据/运行版本。

## 源码复用审计

- QTrade：复用原始数据版本、Bar/Parquet校验、签名JSON、原子写入和只读发布通道。
- Qlib：复用 Alpha158、LGBModel、DatasetH、MLflow Recorder、TopkDropoutStrategy、
  Exchange、SimulatorExecutor；不另写训练/撮合引擎。
- vn.py：当前 vnpy.alpha同样有Alpha158与ML流程；本次目标主要是Qlib模型与后续
  RD-Agent集成，替换为vn.py并不补齐本次数据缺口。执行接口保留未来独立适配边界。

验证命令：

```bash
PYTHONPATH=src python -m pytest tests/test_qlib_adapter.py tests/test_qlib_publication.py
python -m ruff check .
python -m mypy src
npm --prefix web run test -- src/components/QlibBaseline.test.tsx
npm --prefix web run build:vercel
```

官方接口依据：[数据层](https://qlib.readthedocs.io/en/latest/component/data.html)、
[工作流](https://qlib.readthedocs.io/en/latest/component/workflow.html)、
[Qlib v0.9.7 Exchange](https://github.com/microsoft/qlib/blob/v0.9.7/qlib/backtest/exchange.py)、
[信号策略](https://github.com/microsoft/qlib/blob/v0.9.7/qlib/contrib/strategy/signal_strategy.py)。
