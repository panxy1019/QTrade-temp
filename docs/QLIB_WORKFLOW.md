# Qlib 统一研究工作流

当前实现进入集群联调阶段，不能仅凭代码合并就视为生产提交已开放。
最终验收记录见本文末尾及 `plans/QLIB_WORKFLOW_INTEGRATION.md`。

## 页面职责

| 页面 | 当前职责 |
| --- | --- |
| 策略中心 | 列出真正接通的模型、Alpha158 特征和 TopkDropout 组合协议 |
| 策略研究 | 研究账户登录，冻结数据、时间切分、模型、成本和组合参数并提交 |
| 实验中心 | 查询统一任务状态、阶段时间线、归档身份、限制，申请取消任务 |
| 回测分析 | 查看已完成实验的 IC/RankIC、净值、成本后收益、换手与回撤；同协议模型对照 |
| 历史工作区 | 保留旧 ETF、75 股分钟研究、固定因子稳健性报告与旧实验 |

“登录”指本平台独立研究账户，不是登录 Vercel 管理控制台。
公开访客可以看结果；提交与取消必须通过服务器会话验证。

## 能运行什么

首批开放 Ridge（alpha=1）及 LightGBM，均使用 Alpha158 和训练期拟合的
RobustZScoreNorm/Fillna。组合为 TopkDropout；Top K、每日淘汰数、最少持有步数、
仓位、买卖成本、最低成本、初始研究资金、随机种子和时间切分在限定范围可配置。
默认 Top50、淘汰5、持有至少2步、95%股票仓位、买8bp/卖13bp、最低5元。
预测标签使用未来第1至第2日开盘收益，分区必须留出标签隔离区间。

990 只输入库存、2,157,805 根日线、2016-10-24 至 2026-10-23 为冻结研究版本。
训练和排名排除科创板，以完成历史至少120个有效交易日及过去20日成交额判断资格。
这只修正现有样本内部的因果资格，不能消除2026年流动性筛选形成的幸存者偏差。
每次实验冻结实际入选区间审计、输入清单、源码、包版本及 PIT 修复覆盖。
下一开盘价、保守方向限制和前日原始量1%容量依旧属于近似执行协议。

原固定 LightGBM 基线保持原身份，不会被新模型覆盖。其预处理与训练/投资范围
和新协议不同，网页禁止把二者直接解释为模型增量。新实验只有在数据、源码、
时间、成本、组合、种子和协议一致时才允许模型归因对照。

## 通道与时延

浏览器 → 同源 `/api/research` → 私有 Git 队列 → 集群串行执行器 →
不可变归档 → 现有公开观察发布通道 → 回测网页。
不开放集群公网端口，不传券商凭据，不连接真实交易网关。
执行器约15秒轮询，公开发布约5分钟，网页15秒核对公开目录。
提交确认不表示集群已开始执行；界面会单独标出等待发布的本地导航记录。
同一份规格重试沿用请求 ID；修改规格后使用新的请求 ID。

最多保留3个非终态任务，集群并发1、单任务60分钟，持久盘须保留100 GiB。
取消是意图：等执行器确认后才成为已取消。执行器重启时，未完成任务会标为中断，
不自动重放；其旧模型进程须经进程身份核对后回收。
队列不可用不会启动新任务，已启动任务仍受时限约束。

## 运维与复现

服务器环境变量名称（只保存名称，不在仓库保存值）：

```text
QTRADE_RESEARCH_USER
QTRADE_RESEARCH_PASSWORD_HASH
QTRADE_RESEARCH_SESSION_KEY
QTRADE_RESEARCH_QUEUE_KEY
QTRADE_RESEARCH_QUEUE_SECRET
QTRADE_RESEARCH_GITHUB_HOST_KEY
QTRADE_RESEARCH_ORIGIN
```

Vercel 仅 Production 环境配置这些值；预览部署默认不具备写入凭据。
私钥仅限专用队列仓库，不使用本地 `gh` 的账户级令牌。
活跃 SQLite 在容器本地 `/root/qtrade-local`；一致性备份及实验归档在持久盘。
研究模型子进程不继承研究账户或队列密钥。

```bash
PYTHONPATH=src python -m pytest tests/test_qlib_workflow.py tests/test_qlib_worker.py
python -m ruff check .
python -m mypy src
npm --prefix web test
npm --prefix web run lint
npm --prefix web run build:vercel

# 集群运行，BASE 与 REVISION 取真实部署目录/完整源码提交；密钥由私有启动器加载
PYTHONPATH=src python -m scripts.run_qlib_worker --base "$BASE" \
  --database /root/qtrade-local/workflow.sqlite \
  --python "$BASE/qlib-env/bin/python" --source-revision "$REVISION"

# 共享匿名数据源会话锁，分批补历史状态；不是历史可得性认证
PYTHONPATH=src python -m scripts.collect_pit_evidence --base "$BASE" --maximum 20
```

新增模型需要在服务器允许列表、配置构造、适配测试、能力目录和复现文档中登记，
然后提交 GitHub 并经过 CI。修改网页不会自动让集群获得模型运行代码；必须发布
同一经过验证的集群版本。新增因子也需明确公式、数据依赖、可得时间、缺失处理和
独立评价，再加入特征合同，不能从网页提交任意 Python 模块或命令。

## PIT 与交易约束并行工程

`collect_pit_evidence` 保存 BaoStock 股票主表及原始日线 `tradestatus/isST/preclose`
来源快照、接收时间和校验和。现在接收的回填不会伪造历史 available_at。
`known_state` 拒绝未来才可得状态，`historical_blocks` 在状态或精确限制未知时拒绝
将其认定为已认证可交易。这两个严格检查是后续认证执行适配的入口；当前诊断回测
继续使用明确标注的近似协议，不能把“采到了状态”解释为“回测已完整采用精确规则”。

后续 P0：独立全市场及退市股历史对账、上市/退市/板块规则的历史可得性、
公司行为账本、官方原始涨跌停与上市初期例外、竞价/停牌恢复和最小单位规则。
任何实验完成均不会自动获得 PIT、PAPER 或实盘晋级资格。

## 验收记录

- 本地：18项配置/认证/因果性测试通过，worker补充测试3通过、1项Linux专用待集群；
  新前端5项交互/比较测试通过，原前端196项通过，构建和lint通过。
- Windows完整后端测试触发既有目录fsync/只读文件fsync与封存源校验差异；
  应在目标Linux运行完整套件，不能将该本地结果记为全绿。
- 集群真实模型任务、来源回填、Vercel配置和生产端到端验收：待补充。
