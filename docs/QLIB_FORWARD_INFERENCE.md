# 冻结模型的新日线输入验收

状态：完整源码归档合同和真实双模型新日线推理已通过集群验收。
本步骤为模型激活前的输入验收；不会自动激活候选，也不作为 AUTO_PREDICT 已上线的声明。

源 `76fa292` 通过 773 项 Linux 检查，并在真实新版本上为两个候选各生成 888 只
支持池股票的分数。17,814 行历史参考保持一致，Ridge 最大分数误差 `2.24e-16`，
LightGBM 为 `0`。[首轮实际证据](evidence/qlib-forward-pilot-20261026.json)。
源 `5bb4a93` 补齐完整源码归档、文件复验和多日数据祖先链，779 项 Linux 检查通过。
最终两种模型都完成真实推理、归档和文件复验，各 888 个分数，158 因子、17,814 行
历史参考保持一致；每份归档绑定 12 份源码/日历文件，共 15 个已核验文件。
[最终验收证据](evidence/qlib-forward-final-acceptance-20261026.json)。
预期研究执行时间为 2026-10-27 09:30（上海）；收益仍未发生，模型仍未激活。

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
