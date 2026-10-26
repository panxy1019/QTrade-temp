# 退市证券公共日线探测任务编译器

本交付离线编译、封存和重放核验退市日线探测任务，不发出供应商请求，不接入第二个历史执行器。
`LIVE_TRADING_ENABLED=false`；证券池、Provider、模型、历史实验和采集服务均不改变。

交接基线为已部署 `5d4e7bf`：977 项 Linux 检查通过，独立审计确认 552/990 份历史状态内容
合格、438 股缺状态、336 个官方退市 A 股身份没有价格覆盖；完整历史 PIT 认证仍为零。
这是主代理提供的已验收状态，不是本分支新增的下载或集群验收。

## 具体来源与复用缺口

| 来源或组件 | 可核验的输入 / 复用内容 | 仍存在的边界 |
| --- | --- | --- |
| 已保存 SSE 终止上市 JSON | 原始 SHA256、字节数、分页总数、每行身份和日期 | 当前列表不是过去版本；摘牌日期不是最后交易日或公告可得时间 |
| 已保存 SZSE 终止上市工作簿 | 原始 SHA256、字节数、四列 schema、行数 | 原始列表及日期有相同历史可得性边界 |
| 官方对账 `b620f4a2…` | 重放全部 372 行，保留 342 条 A 身份行、336 个唯一身份及争议 | 310 条日期相符记录不能直接称为 310 个可认证任务；重复、争议和请求期外记录须分别处理 |
| 对账所用 BaoStock 主表 | 按对账中的主表哈希读取原快照，复核唯一代码、股票类型、非活跃状态和日期 | 不用当前刷新后的主表替代旧输入；代码和日期相符不证明历史主体身份永远不变 |
| `backfill_plan.py` / `HistoryQueue` | 月份切分和最多 31 个自然日的请求范围 | 现有 3000 scopes 从 `ACTIVE_OBSERVED` 目标重建；退市证券不能冒充活跃证券加入 |
| `history_backfill_store.py` | `request_for` 的原始 day/adjustflag=3 合同、原始捕获、质量验收、不可变 receipt 及重放 | day 捕获包含 tradestatus，不包含 isST、精确涨跌停或公司行为；不能补造完整状态 |
| `run_history_backfill.py` / `vendor_session.py` | 有界子进程、当前采集优先、100 GiB 储备、writer/vendor 两个共享租约 | 新 root 自带 scheduler，单独共享租约不能继承另一个 root 的失败退避 |
| vn.py gateway / 执行适配 | 不新增使用；延续已有来源缺口审计 | 无法提供缺失的历史原文、可得时间或退市价格谱系 |

原始来源及 prior acceptance 见 `PIT_OFFICIAL_SOURCE_AUDIT.md`、`PIT_OFFICIAL_RECONCILIATION.md`、
`PIT_PUBLIC_REPAIR.md` 和 `UNIFIED_HISTORY_BACKFILL_EXECUTOR.md`。本次没有进行网页或接口新抓取。

## 编译规则

入口为 `scripts.plan_public_delisted_history`，纯编译规则位于
`qtrade.research.delisted_probe_plan`。

1. 按不可变对账的哈希读取其原始主表和冻结 990 股票 dataset，不回写任何源。
2. 重查 SSE 原始 JSON、SZSE 工作簿的字节哈希、大小、接收时钟和分页/schema/行数。
   用已有 `official_master.reconcile` 逐字段重建全部对账结果；不能重签汇总后掩盖日期争议。
   每个官方文件最多 5 MB；工作簿最多 100 个 ZIP 条目、25 MB 解压总量；JSON 产物最多 15 MB。
3. 只为唯一、无 issues、`DATES_AGREE_UNCERTIFIED` 且与原主表股票/非活跃身份及日期一致的
   A 股行编译任务。B 股、不确定身份、重复、缺失主表、类型/状态/日期争议全部保留逐行原因。
4. 请求期必须为已结束且不超过 3660 天的范围。退市月份早于请求期的记录保留为
   `OUTSIDE_REQUESTED_RESEARCH_HORIZON`，不是认定来源没有数据。
5. 初始窗口为来源报告退市日期所在的**整个月份**，再与请求期相交，最多 31 个自然日。
   例如报告日期 2020-08-17 得到 2020-08-01 至 2020-08-31；没有把 8 月 17 日当作最后交易日。
   上市日期也不裁剪为权威边界。未来执行器可以按已有月游标向前探索，不能把空月解释为停牌。
6. `request-adapter.json` 保存用现有 `HistoryStore.request_for` 验证的具体 code、fields、日期、
   `frequency=d`、`adjustflag=3` 请求。只有任务准备，没有 login、会话租约或数据请求。

任务 ID 绑定原始对账行、对账/master/dataset 身份、来源、频率和请求窗口。计划身份绑定
代码 revision、原输入、请求期、全部任务和保留的逐行决定。编译时钟不参与该身份，因此同样的
输入、代码和参数重复运行会返回原封存结果；原来的封存时钟也不变。

## 持久化与恢复

默认目录独立于已有活跃历史队列：

```text
<base>/pit-repair/delisted-plans/<plan-sha256>/
  plan.json                 全部任务、每条原始对账行的处理结果及边界
  reconciliation.json       原对账对象
  master.json               对账所用主表
  dataset.json              原冻结研究样本
  official-raw.json          两份官方原文的可逆 base64 副本
  request-adapter.json       未提交的具体请求
  manifest.json              最后封存的完成标记、字节清单及实现哈希
```

输入、任务和 adapter 均以排他不可变写入保存。崩溃留下没有 manifest 的部分目录时，重复运行
会核对已有内容后补齐；发现同名内容不同则失败，绝不覆盖。manifest 最后写入，重启后重新验
每个文件的实际字节和完整来源重放，不把“目录存在”当完成。已封存目录被改写会被拒绝。

`verify_bundle` 还重建任务和具体请求，拒绝重签后把 `execution_enabled`、下载数或 PIT 标志
改成已执行/已认证的记录。返回的行数始终为零。真正的目录 fsync 仍须 Linux 验收；Windows
测试只替换操作系统不支持的目录 fsync，不改变实现的持久化合同。

## 执行接入尚未完成

本计划不能直接交给现有 3000 scopes 活跃计划校验器，也没有自动修改它的允许范围。
后续执行接入需要专门的退市 scope 校验，并复用同一历史 writer lease、vendor session lease、
子进程截止、当前采集优先和不可变 receipt。最关键的是：**跨 root 统一历史源失败退避，并在
请求前、持有会话租约后登录前、请求运行中检查 PIT 的真实失败退避。**仅取得会话锁不能满足
这个条件。由主代理统一桥接后再启动来源请求；本交付没有第二个运行服务或 `--execute` 参数。

既有 438 个缺状态仍由原受监督状态队列处理，本日线计划不会把 day 请求的 tradestatus 冒充
含 isST 的完整状态文件。原文发布日期、历史版本、精确规则、交收、集合竞价、公司行为、历史
主体变化及完整股票池仍未认证。即使未来取得日线价格，`historical_available_at` 也保持未知，
不能因此认证完整 PIT、把当前股票池回填历史，或晋级为 PAPER/LIVE。

## 复现与集群离线验收

本地 16 项新增检查覆盖：原文与对账重放、争议/重复/期外隔离、完整月份锚、请求边界、来源
篡改、未知历史可得时间、重启幂等、崩溃恢复、禁止覆盖、禁止伪称执行，以及通过现有
HistoryStore 封存并重放一条合格日线的集成测试。联同官方/PIT 审计及内容验收共 51 项通过。
扩大到既有历史队列与计划后为 151 项通过、1 项既有 Linux 子进程检查在 Windows 跳过；
Ruff、125 个 source 模块的 mypy 和新增脚本的 Linux 目标严格 mypy 均通过。

```powershell
$env:PYTHONPATH='E:/QTrade-pit-agent/src'
$env:MYPYPATH='E:/QTrade-pit-agent/src'
$env:LIVE_TRADING_ENABLED='false'
python -m pytest tests/test_public_delisted_plan.py tests/test_official_master.py tests/test_public_pit_audit.py tests/test_pit_status_quality.py
python -m ruff check scripts/plan_public_delisted_history.py src/qtrade/research/delisted_probe_plan.py tests/test_public_delisted_plan.py
python -m mypy src
python -m mypy --explicit-package-bases --platform linux --follow-imports=silent scripts/plan_public_delisted_history.py
```

主代理在已核验的集群 release 执行以下离线命令即可产生真实任务数量；供应商仍在退避时也可
编译，因为不会发请求。不要把下面的计划封存命令替换为下载命令：

```bash
export LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src" MYPYPATH="$PWD/src"
python -m pytest tests/test_public_delisted_plan.py tests/test_official_master.py \
  tests/test_public_pit_audit.py tests/test_pit_status_quality.py tests/test_history_backfill.py
python -m scripts.plan_public_delisted_history --base "$BASE" \
  --reconciliation "$BASE/pit-independent-sources/reconciliation/b620f4a277d1ddacd7b779cd663ac7901ed38e019d97e2b1e941566202f8fc89.json" \
  --start 2016-10-24 --end 2026-10-23 --source-revision "$VERIFIED_SOURCE_REVISION"
# 用相同输入再运行一次；manifest/plan hash、原 source、所有既有历史与模型字节应保持不变。
python -m pytest
```

如果对账版本或对应 dataset 已变，应显式使用并核验新的完整来源链，不能把缺失输入替换为
当前默认值。新计划的具体候选数、争议数和期外数必须读取真实封存产物，本地合成样例不能
替代这项集群取证。
