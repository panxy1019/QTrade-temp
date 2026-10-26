# Qlib 不可变日线版本

状态：真实集群 Provider 重载与原子提交已验收；网页目录与自动调度尚待接入。
该模块是 Phase C 的数据入口；每日预测、模型激活及页面接入另行验收。

源 `2dc5946` 全部 757 项 Linux 检查通过。2026-10-26 首个真实版本
`384ce479913266c3e65798e28a85c22bec3b6d3db90629b2c8928c0c141c83c6`
保留 990 只历史，982 只有合格当日输入；002827、600621、605258、688018、688090、
688617、688690、688777 共 8 只没有当前价格。独立 Qlib 重载验证全部 982 只，
3 个未来日历边界的价格为空；17,398 个版本文件校验通过。数据库只有一个就绪事件，
重复提交没有生成新事件。[集群证据](evidence/qlib-data-version-acceptance-20261026.json)。

`scripts.build_qlib_daily_version` 复用原始日线准备分区、已有 Provider 构建器、
因果流动性股票池与来源约束。旧 990 只历史样本保持原身份；当前 1500 观察池
不因此自动获得完整历史或 PIT 资格。当天已完成且合格的分区进入新版本；
失败输入保存历史，当前价格留空。未尝试或尚在写入的任务阻断构建。

构建器校验旧历史前缀、原件哈希、当日批次血缘、固定复权锚点及基准重叠，
保留最新公开状态来源快照。实际数据日期与用于 Qlib 回测步长的日历边界
占位日分别记录；占位日不能出现价格。基准新增成交额未知，保持为空。

独立 Qlib 0.9.7 进程重新加载 Provider，逐一检查原始开盘价、复权收盘、
成交量、因子、VWAP×成交量与金额；检查缺失证券及边界日期没有填造行情。
成功后按文件清单和来源绑定生成不可变版本 ID。所有归档在持久卷，本地
SQLite 使用 WAL/FULL；版本记录、ready 指针和 `QLIB_DATA_READY` 事件同事务提交。
文件损坏或事务失败不会推进指针，重复提交不生成重复事件，也不撤销人工回退。

初始化中断会将未完成暂存移入 `staging/incomplete` 保留后重建。封存后中断
通过不可变 build receipt 恢复，不重写 Provider。重命名后同步两个父目录。
`latest.json` 只是数据库头部的派生只读摘要，不能作为提交事实来源。

```sh
export PYTHONPATH="$PWD/src" LIVE_TRADING_ENABLED=false
"$B/venv/bin/python" -m scripts.build_qlib_daily_version \
  --base "$B" --previous "$B/qlib-data-20261023" \
  --preparation "$B/unified-data/qlib-preparation/$PREPARATION_ID" \
  --database /root/qtrade-local/qlib-versions.sqlite \
  --qlib-python "$B/qlib-env/bin/python" --source-revision "$REVISION"
```

执行使用非阻塞本地租约并要求持久卷至少 100 GiB 空闲。此命令不训练模型，
也不将候选切为 ACTIVE。目录回退需要已验收版本、准确的当前头部和显式操作者；
回退产生独立事件，旧版本和旧预测不改写。调度及登录后的控制面尚待集成。

当前限制：同供应商的重叠及复权校验不是独立公司行为认证；当前股票样本存在
幸存者偏差；ST/停牌/退市及历史交易约束仍不完整。`RESEARCH_READY_QUALIFIED_SUBSET`
只表示所列研究子集能加载，不能解释为全部 1500 证券、完整 PIT 或实盘可交易。
LIVE_TRADING_ENABLED=false。
