# Qlib 训练态冻结与独立恢复

状态：代码和本地针对性验证已完成，等待集群真实训练／独立进程验收。
这是 Phase C 的一项门禁，不代表每日预测或完整 Online Serving 已完成。

旧模型只保存 model.pkl，无法证明恢复训练期拟合处理器。新增运行开关
`--restore-proof` 会进入新请求身份，保存 `params.pkl`、已拟合的 `dataset`、
158 因子顺序、处理器状态哈希、依赖版本、Provider 全文件哈希，及最后 20 个
会话的特征与预测参考。使用官方 TrainerR 的 `dump_all=False` 合同，避免把
全训练数据塞进在线模型文件。现有训练、评价、成本及时间切分仍由原脚本执行。

独立 Python 进程先校验文件和完整 Provider 清单，再通过官方 Qlib 0.9.7
RMDLoader 加载。设置 IT_LS，禁止 handler.fit／fit_process_data，确认拟合
处理器前后相同，索引、特征顺序、特征值和预测一致。预测允许数值误差
atol=rtol=1e-10；特征允许 1e-12，未知值的位置也必须一致。

只有全部通过才写 `independent-restore.json` 并封存新的完整实验。失败保留
`.partial` 尝试，旧 ACTIVE 模型和旧实验不改变。pickle 只用于操作者控制的
本地可信归档，网页不能上传或指定模型文件。

```sh
export PYTHONPATH="$PWD/src" LIVE_TRADING_ENABLED=false
"$B/qlib-env/bin/python" -m scripts.run_qlib_baseline \
  --data-root "$B/qlib-data-20261023" --output "$B/online-research/restore-pilot" \
  --tracking "$B/online-research/tracking" --source-revision "$REVISION" \
  --workflow-spec "$B/online-research/restore-spec.json" \
  --pit-evidence-root "$B/online-research/frozen-pit-sources" --restore-proof
```

首次验收复用已归档且哈希校验通过的 PIT 源快照，避免持续采集改变候选比较身份。
`restore-spec.json` 使用当前允许的 Alpha158/LightGBM 或 Ridge 参数合同；
新实验可以保存拟合 RobustZScoreNorm／Fillna，旧实验不会补写处理器。

该门禁仍不证明新数据版本、公司行为口径或实时输入合格。后续必须把新日线版本
与模型身份绑定，逐日保存不可变信号；不通过当天门禁则阻断新信号。滚动候选、
人工激活及回滚由 Phase D 另行验收，LIVE_TRADING_ENABLED=false。
