# C/D/E 本轮上线与验收记录

截至北京时间 2026-10-27 凌晨，PR20 已合并，生产网页版本为 `7c7d98a`，
集群研究 worker、只读发布与单一历史回补服务运行源 `a69eb70`。
当下最近完整交易日仍为 2026-10-26；页面不是盘中实时交易终端。

## 已真实完成的内容

| 模块 | 已核验结果 | 仍未完成 |
|---|---|---|
| C 持续预测 | 用户批准的 LightGBM 保持 ACTIVE；新代码生成 888 条预测，生产 API 判为可用；旧预测不覆盖 | 下一完整交易日的新数据版本与预测需跨日实测；数据版本回退控制面与完整版本目录 |
| D 滚动训练 | 原生 RollingGen/TrainerR 实际训练完成，候选 TRAINED；独立进程恢复 17,813 行、158 特征，分数差 0、未重拟合；政策队列及网页已部署 | 自动训练仍为 OFF；下一合格版本验证及实际周期执行待实测；新候选激活须另行人工批准 |
| E 每日信号 | 动量、反转、低波、多因子排名、共同股票池等权五组信号；每组 885 只；持久任务、不可变档案、分页 API、独立页面已上线 | ETF Provider、同口径历史策略对照、持仓/换手/执行约束与 PAPER/SHADOW |
| 长期回补 | 单服务交替处理当前观察池与退市探测，共享来源失败退避、来源会话锁与断点恢复 | 当前只有首批有界窗口，不能称为长期历史回补完成 |
| PIT | 状态内容校验覆盖 817/990 只、1,861,256 行；退市源身份/日期对账和独立探测链路已接入 | 历史 available_at、完整历史股票池、退市行情、ST/停牌原因、公司行为和精确交易约束仍未认证 |

在线入口：[在线模型](https://q-trade-temp.vercel.app/#/models)、
[每日研究信号](https://q-trade-temp.vercel.app/#/signals)。
在线模型可审阅当前模型、候选和训练政策；默认关闭自动重训。保存训练政策需要登录，
保存并不激活候选。此次用户授权只适用于原 LightGBM，不扩展到后续候选或实盘。

信号页明确区分信号分数与指示权重，后者尚未经过真实持仓、换手和执行约束。
不得把指示权重当作实际持仓、委托或成交。没有前向收益时保持空值，不能用回测收益填充。
发布读取时重核 worker、数据版本、代码及预定研究执行时间，旧档案保留但不标为当前。

## 本轮真实数据证据

回补首轮 5 个请求经过独立原始响应、收据和 Parquet 重放审计：4 只证券共
48 行日线，1 只证券共 576 行五分钟行情；覆盖窗口 2026-10-08 至 10-23。
退市探测首轮 4 个请求收到 21 行响应，独立审计仅确认 1 只证券的 3 行合格日线。
其余响应没有当作合格覆盖。退市数据与当前 1500 证券池分开，不增加当前池统计，
也没有自动写入已封存的 Qlib 版本或赋予历史 PIT 认证。

磁盘剩余约 283.3 GiB；继续保留 100 GiB 下限。采集持续执行但来源限速、可得性、
日常采集让行及质量隔离都可能限制吞吐，目前不能给出可靠的完整历史完成日期。

## 验收与复现

- 最终源：1,127 项 Linux 检查全部通过，无跳过；227 项前端检查全部通过。
- 构建、ESLint、Ruff、mypy 通过。前端全套使用两个 worker，避免并发环境下既有用例超时。
- 生产 API 验证新代码版本、当前预测可用、TRAINED 候选、OFF 政策；匿名政策写入返回 401。
- 五组信号 API 分别实测，均有 885 分数，限制分页，执行准入为 false、前向收益为空。
- 模型/工作流 SQLite 一致性备份通过；仅重启目标服务，其他服务 PID 保持不变。
- 浏览器自动化仍超时，因此没有宣称视觉验收或真实浏览器保存训练政策通过。

```powershell
$env:PYTHONPATH='E:/QTrade-git/src'
$env:MYPYPATH='E:/QTrade-git/src'
python -m pytest tests/test_rolling_policy.py tests/test_rolling_control.py tests/test_qlib_worker.py tests/test_signal_publication.py
python -m ruff check .
python -m mypy src --platform linux
npm --prefix web test -- --maxWorkers=2
npm --prefix web run build
```

真实 Linux 持久化与进程锁验收在已核验 release 上运行 `python -m pytest -q`。
实际训练和信号重放命令见 `QLIB_ROLLING_TRAINING.md`、`QLIB_SIGNAL_BASELINES.md`；
历史共享调度与两个独立审计命令见 `UNIFIED_HISTORY_SHARED_SOURCE_SCHEDULING.md`、
`PIT_DELISTED_EXECUTION_CONTRACT.md`。

证据文件：

- `evidence/qlib-serving-cluster-20261026.json`：实际账本、预测、信号、两类回补审计和 PIT 状态。
- `evidence/qlib-serving-production-20261026.json`：生产版本、五组 API 与当前预测验收。
- `evidence/qlib-rolling-signal-acceptance-20261026.json`：原生滚动训练、成熟切分与恢复一致性。

下一步先完成同一成熟评价区间、数据/来源版本、证券资格和成本下的统一历史策略对照；
随后将信号接入真实持仓核对、换手和交易约束，再接不可变 PAPER/SHADOW 前向记录。
长期回补与公共源 PIT 修复继续并行。C/D/E 全阶段仍未标记完成。
