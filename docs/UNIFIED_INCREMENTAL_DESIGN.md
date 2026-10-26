# Phase B：增量采集实施依据

状态：当前增量里程碑已通过集群及生产验收；历史回补和完整 Phase B 尚未完成。
最新覆盖、运行身份、测试和缺口见 [验收报告](UNIFIED_INCREMENTAL_ACCEPTANCE_20261026.md)。

已实现解析器：src/qtrade/market_data/sina_incremental.py；12 项针对性检查通过。
复现：`PYTHONPATH=src python -m pytest tests/test_sina_incremental.py`。
批次存储与采集核心见 scripts/unified_store.py、unified_queue.py、collect_unified_data.py。
新增检查已纳入 678 项 Linux 全套，生产当前 1497 日线 / 1494 五分钟合格。

可核验摘要：[有界日线/分钟源](evidence/sina-incremental-source-audit-20261026.json)、
[盘后快照源](evidence/sina-completed-quote-source-audit-20261026.json)、
[安装的 Qlib 0.9.7 接口/源码哈希](evidence/qlib-online-source-audit-20261026.json)。

## 已核实的源差异

2026-10-26 17:18–17:20 集群少量探测并保存原始响应与 SHA：

| 通道 | 实测内容 | 约束 |
|---|---|---|
| Sina KLine scale=240，100 条 | 股票和 ETF，至 2026-10-26，OHLCV | 没有 amount；不造 VWAP |
| Sina KLine scale=5，100 条 | 股票，含 amount，至当日 15:00 | 金额是供应商字段，未独立对账；复权口径待认证 |
| Sina quote，3 个身份 | 股票/ETF，34 字段，金额及日期/更新时间 | 与当日日线 OHLCV 一致后才可附金额；同供应商交叉接口不等于独立来源 |
| 既有 PIT status 原料 | 日线 OHLC、volume、amount、preclose、tradestatus、isST | 可复用校验过的字节；范围仍为冻结 990 池的部分身份，历史 available_at 未知 |

Quote 的 15:34:59/16:30:00 等时间是供应商快照更新时间，不能当作一笔交易时间。
原始日线、Sina native 分钟与旧 BaoStock qfq 历史必须保持不同价格基础及来源身份。
无法证明可拼接时，分别保存覆盖和缺口。

现场原件：持久卷 unified-data/source-audit/ 中的内容哈希文件，以及
sina-bounded-range.json、sina-daily-quote.json；这些是当次采样，不证明全部证券稳定可用。

## 写入与调度

新增数据通道与原始实验相互独立：在 `unified-data/lake/objects` 保存压缩原始包及
增量 Parquet，`batches/<id>.json` 封存配置、代码、源哈希、逐证券结果和阶段计数。
SQLite 账本放在本地盘，不放在 NFS；研究任务仍用原来的私有 Git + SQLite 队列。

```sh
export PYTHONPATH="$PWD/src" LIVE_TRADING_ENABLED=false
export QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true
# 一次有界采集；不要与持有同一数据库的维护进程同时启动。
"$B/venv/bin/python" -m scripts.collect_unified_data --base "$B" \
  --database /root/qtrade-local/unified-data.sqlite --source-revision "$REVISION" \
  --seconds 240 --maximum-symbols 3000 --group-size 25
# Supervisor 使用的持续维护入口（保留原网页兼容刷新）。
"$B/venv/bin/python" -m scripts.run_research_maintenance --base "$B" --unified \
  --database /root/qtrade-local/unified-data.sqlite --source-revision "$REVISION"
```

日线在 16:30 后按完整交易日采集，报价金额缺失或 OHLCV 不一致则阻断正式接纳并重试，
原始字节始终保留。分钟首次请求有界近两会话，后续按上次成功时间仅请求缺少的已完成
Bar；缺口不补造，原因未知不自动标停牌。两种频率独立，日常任务在批次之间轮换。
重启先重放已封存但未提交的批次，再重试未封存的中断任务。初期试验若存在价格完整但
金额缺失的日线，保留原批次和事件，排除其完整日线资格并补采，不改写旧 Parquet。

公开目录只增加逐证券的日常状态、阶段计数和时钟；压缩 JSON 格式避免重复存储大量
空白。历史覆盖、当前增量、Qlib 冻结版本三种身份仍独立，数据事件不会直接变成新预测。

1. 从冻结观察池建立独立 day/5min 计划，先按完整交易时钟请求有界新数据。
2. 先保存原始字节和查询身份，再解析、校验、对账，保留失败及最后成功状态。
3. 标准化含 OHLCV、可用 amount/VWAP、价格基础和完成状态；未知字段为空。
4. 按批次写不可变增量块，以证券/时间索引引用；不每天复制全部分钟历史。
5. 日常更新优先，低优先级补齐已证实可用的历史范围；BaoStock 使用现有互斥租约。
6. 调度台区分计划、请求、下载、解析、验收、对账、发布；无子进程的旧任务不能保持 RUNNING。
7. 通过数据就绪事件触发后续 Qlib 版本，不能直接跳成新预测。

## 实测空间及估算口径

171 份合格旧分钟文件：5,960,496 Bars / 101,309,367 字节，约 17 字节/Bar，
仅代表当前压缩 OHLCV Parquet，未包含新的金额、原始响应、索引、版本与模型归档。
日线 1000 只约 218 万 Bars / 54 MB。集群余量约 293 GiB，保留 100 GiB。

按每年 244 会话估算，1500 只十年日线约 366 万行，十年五分钟约 1.76 亿行。
按已有分钟压缩率机械外推约 3 GB，但不能把这个数字当全部占用预算；
加入金额及索引、原始响应和版本，先按 **30–80 GiB** 的工程预算测量校准。
旧 qfq 三年覆盖不证明有十年分钟源。空间暂不是主要阻点，来源覆盖和下载失败才是。
增量上线后必须交付单位证券/会话的实测字节、耗时、失败率和增长预测，
触及保留阈值暂停大量回补，不删除旧实验。

## 验收

跨重启恢复、批次幂等、失败不覆盖旧版本、未收盘 Bar 拒绝、身份/金额/网格错误
隔离、当日数据优先、受限重试、日线不因分钟失败而丢弃，以及网页真实状态。
只有这些测试和现场运行通过后，才能声称 Phase B 完成。
