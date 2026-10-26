# 统一信号基线与组合边界

状态：Phase E 首个信号层正在验收，尚未完成策略对照或 PAPER 全链路。

复用现有 `PriceFactorSpec`：20 会话动量、5 会话反转、20 会话低波动；
多因子是三个因子的截面平均百分位，重复值共用平均排名。另有全池等权基准、
ETF 正动量趋势规则。所有信号使用相同的连续 21 会话输入；缺失、不合法价格
或日期不一致从整个比较集合排除，绝不前向填充。

`signal_family` 只生成分数。`allocation` 是独立的指示权重构造：TopK 等权
或全池等权，默认总权重 95%。负分数仍可以进入排名，因为模型分数不是预期
收益的绝对阈值。原 `construct_targets` 会按正分数过滤并立即计算手数，
不能直接用来承载此研究合同；保留原实现不改。vn.py 执行组件在此层不适用。

目前只封存指示权重，不用未知的下一开盘价捏造手数、成交或收益。尚未完成
持仓对账、换手限制、当日交易约束和 PAPER 成交会明确标为 false。
因此这些权重不是执行合格的组合。TopK 等权也不等同于已接入的 TopkDropout。

集群输入为已经验收的日版本，先全量核验 hash，再通过原生 Qlib 读取收盘价：

```bash
PYTHONPATH="$SOURCE/src:$SOURCE" LIVE_TRADING_ENABLED=false \
  "$QLIB_PYTHON" -m scripts.run_qlib_signal_baselines \
  --data-version-root "$VERSION_ROOT" --output "$NEW_OUTPUT" \
  --source-revision "$REVISION"
```

每份输出包含原始输入 Parquet、信号、指示权重、实现源码哈希及不可变 manifest。
真实生成时间必须早于下一个已验证交易会话的预期研究执行时点；历史回测和实际
前向收益均不在这份输出内。已有股票 Provider 未纳入 ETF，ETF 趋势代码虽有测试，
实际 ETF 数据与策略实验仍待验收，不能把股票输入标成 ETF。

下一项：统一同一成熟评价区间、来源、证券资格、成本和组合规则的历史对照；
再按真实持仓接入 PAPER/SHADOW 前向台账与页面。当前池幸存者偏差、完整 PIT、
复权和交易规则限制继续保留。
