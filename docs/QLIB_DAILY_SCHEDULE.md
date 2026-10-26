# 日版本自动调度

实现：`scripts/run_qlib_data_service.py`。复用已验收的日线准备器、Provider 构建器、
Qlib 独立 reload 和原子数据事件；不另造采集器，不绕过既有金额、复权及来源对账。

服务每五分钟尝试一个有界步骤，独立十秒心跳。仅最近完整交易日相对当前数据头恰好
推进一个核验会话、日常采集处于新鲜 WAITING、磁盘保留至少 100 GiB 时准备输入。
每步最多 25 只新输入；沿用逐证券重试队列，已尝试的失败仍隔离。
全部可用输入都尝试后才构建合格子集，缺口保留在版本。不能跨断档补造 K 线。

每天冻结前版本和执行代码；当天中途改变代码/输入身份需要核对，不能无声重绑定。
新版本发布后当天自动周期停止重复构建；当天后续补齐缺口仍需独立修复版本。
这项限制避免把每次重试时间变化变成新数据版本。实际准备文件哈希用于构建重试身份，
而不是进程时间或重试次数。步骤开始前先保存重试期限，构建失败至少等三十分钟。

服务、准备、Provider 分别有本地主机排他租约，沿用手工入口的租约名称。
子进程继承服务租约；父进程异常退出时它仍持锁，独立十五分钟硬截止后退出。
正常停止按进程组终止并回收子进程。日常采集不等待本服务。
数据头与就绪事件仍只在独立 reload 和全部文件校验后事务发布。

```sh
PYTHONPATH="$PWD/src" LIVE_TRADING_ENABLED=false QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true \
  python -m scripts.run_qlib_data_service --base "$B" \
  --database /root/qtrade-local/qlib-versions.sqlite \
  --qlib-python "$B/qlib-env/bin/python" --source-revision "$REVISION"
# 验证当前日已就绪且没有重复构建：追加 --once。
PYTHONPATH="$PWD/src" python -m pytest tests/test_qlib_data_service.py
```

调度状态在 `unified-data/qlib-schedule/`，由模型目录只读发布并在模型页显示。
心跳正常不等于数据最新，必须同时查看 cycle、真实截止日、缺口与验证结果。

当前验收边界：本地针对性验证完成；Linux 全套、集群正常停启和当前日幂等验证待记录。
未来首个新交易日真实自动生成，不能用当天已有手工版本替代该项证据。
没有改变原 990 历史样本池；完整 PIT、精确交易规则、缺日回补仍待修复。
`LIVE_TRADING_ENABLED=false`，没有模型批准或交易入口。
