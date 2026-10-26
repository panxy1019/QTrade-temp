# 冻结模型的新日线输入验收

状态：实现和 16 项时钟、血缘、覆盖检查完成，集群实际推理待验收。
本步骤为模型激活前的输入验收；不会自动激活候选，也不作为 AUTO_PREDICT 已上线的声明。

`scripts.validate_qlib_forward` 复用通过独立恢复的模型、DatasetH 和 Qlib 0.9.7
RMDLoader。先校验完整旧实验、处理器包、新不可变数据版本和依赖身份，之后才加载
本地可信 pickle。读取新 Provider 时保留训练计算起点，以 IT_LS 使用训练拟合状态，
禁止 fit。输入必须属于模型训练数据的可追溯后继，最新实际日必须为当前已完成交易日。

重新计算过去 20 个参考会话，要求索引、158 个特征及旧分数在原严格阈值内保持一致；
追加数据不能偷偷改写旧因子。当天预测必须精确覆盖“合格当前输入 ∩ 当天因果支持池”，
不得出现重复、缺失、额外证券或非有限分数。按分数降序、证券代码打破同分确定性排序。

每份结果绑定模型包、数据版本、观察池、真实数据日期、生成时刻和下一已核验会话开盘。
若已经错过该开盘，拒绝将迟到结果包装为之前可执行的信号。生成后没有发生的收益保持
未观察状态。保存不可变 scores/features Parquet 与 manifest，不更改原实验预测。

```sh
export PYTHONPATH="$PWD/src" LIVE_TRADING_ENABLED=false
"$B/qlib-env/bin/python" -m scripts.validate_qlib_forward \
  --archive "$B/online-research/float64-lightgbm/runs/$RUN_ID" \
  --data-root "$B/unified-data/qlib-versions/versions/$VERSION_ID" \
  --output "$B/online-research/forward-validation"
```

运行状态为 `FORWARD_INPUT_VALIDATED_NOT_ACTIVATED`。模型目录、人工批准、事件消费与
每日自动调度接通后才能宣称自动预测。当前幸存者偏差、历史 PIT、状态、交易约束与
复权认证限制随结果保留；分数不是收益率承诺或交易指令。
