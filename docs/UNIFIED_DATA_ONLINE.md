# 统一证券数据与 Qlib 在线研究：实现及维护

状态：2026-10-26，Phase A、分钟 P0 修复及 Phase B 当前增量里程碑已验收。
本页保留 Phase A 首次结果；当前采集见 [增量验收](UNIFIED_INCREMENTAL_ACCEPTANCE_20261026.md)。
完整目标见 [执行计划](../plans/UNIFIED_DATA_ONLINE_PLAN.md)。完整 Online Serving 尚未完成。

## 首次现场结果

| 项目 | 实测结果 |
|---|---|
| 主表身份（含非目标及停用） | 5594 |
| 目标池 | 1500：1470 股票 + 30 ETF |
| 旧清单去重 / 当前候选补足 | 1037 / 463 |
| 日线内容验收 | 1000，其余 500 处于计划状态 |
| 旧长期分钟检查点 | 172：171 内容/覆盖通过，1 隔离 |
| 分钟目标池分布 | 171 通过、1 隔离、1328 尚未取得合格长历史 |
| 长历史数据截止 | 2026-10-23；不能标成当天最新 |
| 历史 PIT 认证 | 0 |

隔离证券为 002384，原文件仍存在，不再能凭正确 checksum 获得覆盖验收。
生产 [数据中心](https://q-trade-temp.vercel.app/#/data) 已验收 1500 目标、
002384 查询和 5min 隔离；API 总量/历史身份查询及只读边界检查通过。
证据：[集群](evidence/unified-phase-a-cluster-20261026.json)、
[生产](evidence/unified-phase-a-production-20261026.json)。
新维护和唯一发布服务使用 release 133b034；原 Qlib worker、PIT repair、observer
保留原代码身份。Linux 全套 637 项、前端 213 项、Ruff、115 文件严格类型检查、
ESLint 与构建通过。Windows 另行旧 Vercel 证据测试有平台相关失败，Linux
两版全套均通过；未把该 Windows 检查写成通过。

## 当前调用关系与复用审计

业务角色为 observer、maintenance、PIT repair、Qlib worker、publisher。
现场实际还发现重复的旧 publisher：两个进程以不同代码写相同 Git 输出；
按 ADR-172 保留备份后停用重复写入，只保留 workflow-aware publication 服务。
observer 写 runtime 数据和新鲜度；maintenance 写 expansion 并刷新小范围当前图表；
PIT repair 用独立持久队列补公共主表/状态；worker 消费 Git + SQLite 任务并冻结实验；
publisher 发布有界观测到独立只读仓库；Vercel 读取结果，受保护入口写私有任务队列。
源数据、模型和完整实验留在持久卷，公开仓库不承载任务密钥。

| 组件 | 本阶段选择 | 缺口与后续 |
|---|---|---|
| InstrumentMaster | 保留严格历史解析，复用 SSE/SZSE 身份 | 观察声明单独建表，不能伪造 known_from |
| ParquetBarStore / BarQualityGate | 复用 schema、字节哈希、OHLCV、时序及不可变写入 | 频率覆盖、金额、公司行为需另验收 |
| Sina / BaoStock 客户端 | 复用取数与会话租约 | 免费分钟真实历史范围必须实测；日常与回补调度待统一 |
| Git / SQLite / Supervisor | 沿用权限、队列、租约、恢复 | 后续增加数据/预测/训练任务类型及优先级 |
| vn.py 已有适配器 | 本阶段不新增执行组件 | 不能提供公共源历史身份、PIT 或 Qlib 处理器归档 |
| Qlib 0.9.7 DatasetH / DataHandlerLP | 计划保存训练拟合处理器并做跨进程对照 | 原有模型归档缺少完整处理器，不能认定可恢复 |
| Qlib TrainerR / RollingGen | 已审计安装接口，后续兼容适配 | 标签成熟、日历边界、时间切分及成本身份需要门禁 |
| OnlineManager / OnlineStrategy / OnlineToolR | 后续按需复用 | 原生在线标签不替代人工激活、原子发布和回滚事务 |
| PredUpdater / RMDLoader | 已核对 params.pkl + dataset 加载合同 | 旧 Recorder 不全，不能直接调用；不改写旧预测 |

官方审计固定在 [Qlib v0.9.7 源码](https://github.com/microsoft/qlib/tree/v0.9.7/qlib)：
`model/trainer.py` 的 TrainerR 会保存 params.pkl 和 dataset；
`workflow/online/update.py` 的 RMDLoader 恢复处理器后按 IT_LS 加载。
`workflow/task/gen.py` 使用 Provider 日历，必须排除未来边界占位日。
本地安装模块源码哈希随集群验收证据保存，不把 latest 文档当作已安装版本。

## 统一观察主表

身份格式 SSE.600036 / SZSE.000001。现有 ETF30、真实长期 75 股票清单和
日线 1000 清单先去重；分钟 400 是目标而非已独立取得的 400 个新证券。
从冻结流动性候选清单补足配置目标，只有当前 master 身份无冲突且声明活跃的
候选可补足。主表保留非目标和历史停用的股票身份；不把全部 vendor type=5
当 ETF，仅保留已有 ETF 清单明确声明的身份。

valid_from/to 是来源声明的上市/退市有效区间，valid_time_basis 标明未认证；
available_at 全部为空。source_known_at 仅代表清单时间，不是每条历史行情当时可知。
历史 PIT 股票池认证数保持 0。新观察表不改变严格 InstrumentMaster 的解析。

目标池日线与 5min 都创建计划。频率状态独立，不因分钟缺失而抛弃日线。
目录时间、vendor 源观测时间、各频率真实数据截止日分别展示。

## 旧分钟复验

每次缓存复用都读取实际 Parquet，检查 schema、checksum、data_version、OHLCV、
证券/交易所、重复与时间顺序，并重验请求起止、至少 650 个观测会话、48 根/会话、
盘中网格及周末。650 是原有三年扩容合同，不能用于全平台新股资格判断。
缺失日期不能推断停牌；尚未完整验证历史休市日历、amount 和复权时点。
旧检查点的 bar_count 不作为事实来源，返回实际行数。

不合格输入隔离并拒绝复用；旧 Parquet 和 checkpoint 不删除、不改写。
离线审计为每份输入生成内容哈希绑定的不可变 receipt，再生成审计报告。
失败保留为 QUARANTINED；不能用服务 RUNNING 或文件名日期宣称完成。

## 集群复现

在已验证的 release 目录运行，B 为现有持久卷 qtrade 根目录：

```sh
export PYTHONPATH="$PWD/src" LIVE_TRADING_ENABLED=false
"$B/venv/bin/python" -m scripts.audit_expansion_minutes \
  --root "$B/expansion" --output "$B/unified-data/minute-admission" --end 2026-10-23
"$B/venv/bin/python" -m scripts.build_observed_universe \
  --base "$B" --vendor-master "$B/pit-repair/master/<已验证内容哈希>.json" \
  --candidates "$B/expansion/candidates-2026-10-23.json" --target 1500
```

审计不访问供应商，不争抢 BaoStock 会话；运行前确认 CPU/I/O 预算。
catalog/versions 与 minute-admission/receipts 保留不可变内容，latest 指针原子替换。
统一增量采集和自动目录已经接通，日常覆盖与本节旧历史口径独立。

## 网页与只读发布

数据中心统一目录按代码/名称、day/5min、质量和目标/全部身份有界查询。
API `/unified/summary` 不带全证券明细，`/unified/securities` 每页上限 100。
整份发布输入有大小上限，验证外层和主表哈希、权限、身份及计数后才响应；
公开 API 无写入口。查询失败清除正常状态，目录身份变化要求刷新。
旧证据、研究页面和模型策略不被替代。当前增量不足 1500 全部合格，缺口如实展示；
每日在线预测、滚动重训、模型激活和十年五分钟历史尚未交付。
