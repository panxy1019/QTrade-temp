# 近期状态窗口与历史可知性

这是独立的研究观察链路；固定历史回补、价格准入与 PAPER 执行保持各自原有来源。
实现不认证完整历史 PIT，也不将停牌证券补成零成交价格行。

## 修复的问题

原 `collect_pit_evidence` 绑定 `qlib-data-20261023/dataset.json` 的固定 end，完成
990 只后会跳过已有 status 文件。主表刷新不会使历史状态自动延长。

新增 `scripts.collect_recent_status` 复用现有日历、BaoStock SDK 0.9.3、共享供应商
非阻塞锁、逐股队列/退避、内容质量校验和原子存储。每个窗口包含最近三个**已完成**
日频会话；沿用上海16:30日频截止政策，开盘时不会请求/认证尚未结束的当天。
显式传入数据集决定股票范围，当前使用990只研究样本，不声称覆盖1500目标池。

目录 `recent-status/windows/<window_sha256>/` 绑定完整数据集身份、日历、股票集合
及三天窗口；响应、合格状态和每轮覆盖凭证分别保存。新增数据日或输入版本产生新
窗口，旧窗口字节不变。`recent-status/current.json` 只是最近一次轮次指针。

一次正常轮次最多20只新股票、另加最多5只到期重试；每轮之后等待300秒。缺日期和
供应商失败不会被认作完整窗口，未知标志保留未知，失败股不会饿死尚未尝试的股票。空响应原文
仍保存为 SDK 解码结果，原始 socket 字节未捕获，不能声称网络层原文保全。
非阻塞会话锁忙时不登录、不计一次请求。可用空间低于100GiB则拒绝新轮次。
非交易状态与ST字段缺失保留未知，不认证交易许可。
启动或重启 watcher 也检查既有覆盖凭证的实际时刻，不会立即重复消耗下一批预算。

## 两个时间必须同时满足

`known_state` 原先只检查 available_at，遗漏 observed_at 是否晚于决策。
现两者均须带时区且不晚于 decision_at。比如一份历史公告声称早已发布，但系统
直到今天才获取，不能给昨天已冻结的交易决策提供依据。此处是研究适配器校验，
尚未把新来源替换进现有交易执行约束。

近期记录的 `system_available_at=observed_at` 为实际本次获取时刻；历史公告发布时间
未认证时保留空值。`historical_available_at=null`、`certified_at_decision=false`、
`historical_pit_certified=false` 不变。文件哈希是内容身份校验，不是供应商数字签名。

## 复现

在新 Git revision 的隔离、逐文件核验源码目录执行：

```bash
LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src:$PWD" "$B/venv/bin/python" -B \
  -m scripts.collect_recent_status --base "$B" \
  --dataset "$B/qlib-data-20261023/dataset.json" --source-revision "$REVISION"
```

默认一次轮次；持续模式额外传 `--watch`。持续模式使用独立
`/root/qtrade-local/recent-status-worker.lock`，不接管固定历史 PIT owner、维护采集
owner 或 forward owner。上面的命令模板不证明服务已经部署，须以实际运行凭证为准。

```powershell
$env:PYTHONPATH='src;.'
python -B -m pytest tests/test_recent_status.py tests/test_qlib_workflow.py `
  tests/test_pit_status_quality.py tests/test_pit_collection_queue.py tests/test_pit_source.py
python -B -m ruff check scripts/collect_recent_status.py tests/test_recent_status.py `
  src/qtrade/research/pit_repair.py tests/test_qlib_workflow.py
python -B -m mypy --follow-imports=silent scripts/collect_recent_status.py `
  src/qtrade/research/pit_repair.py
```

## 限制与来源复用

参考既有 [vn.py来源与缺口审计](VNPY_GAP_ANALYSIS.md)：网关及标准行情对象无法
提供供应商状态的历史可得时间、闭市窗口版本和回补认知时点，本次复用本仓库已有
供应商会话/队列/质量/存储组件作窄适配，不引入执行网关。

窗口完整只说明三天的供应商记录齐备，不证明历史全市场股票池、退市股价格、复权、
精确涨跌停价、公告变更链或当日开盘状态。近期状态也不会删除原 Sina 失败收据或
自动更改研究股票池/模型/交易准入。分钟缺口治理在维护 owner 活跃时仍仅做只读计划。
完整 STEP1 仍需要当日真实收盘链路和网页验收。
