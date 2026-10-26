# Qlib 训练态冻结与独立恢复

状态：冻结数据的训练态恢复已通过真实集群验收；新日线版本及每日预测继续实施。
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

首次恢复将数据起点缩短到最近 20 个会话，Qlib float32 的 STD/CORR 在
2,814,612 个特征单元中有 4 处不同，最大绝对差 1.7881393432617188e-7；
预测值最大差为零，拟合处理器哈希相同。门禁仍按既定阈值拒绝该尝试。
修复保存训练 handler 的原计算起点，恢复完整原始范围后仅对末 20 会话比较，
保持严格阈值；不重新拟合，也不将窗口改变带来的差异归为“已一致”。
这会多计算历史特征，先测量资源成本，再优化有界窗口。
[首轮拒绝及诊断证据](evidence/qlib-restore-window-diagnosis-20261026.json)。

源 1bb13a9 的 729 项 Linux 检查全部通过；修复后重新训练 LightGBM，
20 会话／17,814 行／158 因子通过严格特征比较，预测最大差为零，拟合处理器不变。
11,963 个完整归档文件通过校验；模型包 193,867 字节，拟合 DatasetH 13,806 字节，
不包含全部训练帧。候选未激活，仍绑定 2026-10-23 冻结数据。
[真实训练与恢复证据](evidence/qlib-fitted-restore-acceptance-20261026.json)。

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

## 新日线准备

`scripts.prepare_qlib_incremental` 复用旧冻结日线与统一采集已验收的 day 批次，
按证券验证旧文件、前一真实数据日的原始 OHLCV 重叠、当天金额和完成状态。
保存当次 HFQ 原件并校验整个旧历史的因子前缀，固定旧归一化锚点后只追加新行。
旧行必须逐字段保持一致。该一致性属于同供应商研究兼容性，尚非公司行为独立认证。

分区保存为内容哈希文件；失败原始响应保留，下一次可抓新原件，不覆盖旧分区。
沿用已验收的逐证券尝试／重试调度，默认每次 25 个新证券、至多 5 个到期重试，
轮次限 180 秒，两个批次之间可恢复。单独本地进程租约避免重复准备任务。

```sh
export QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true
"$B/venv/bin/python" -m scripts.prepare_qlib_incremental --base "$B" \
  --previous "$B/qlib-data-20261023" --source-revision "$REVISION" \
  --maximum-symbols 25
```

输出 `PREPARATION_ONLY_NOT_A_READY_QLIB_VERSION`。这一步不写 ready 指针，
也不触发旧模型；基准／日历适配、不可变 Provider 构建及原子发布仍须验收。

源 438c628 的全部 734 项 Linux 检查通过。集群首批 3 只证券的旧行保持不变，
当日金额、重叠价格与因子前缀通过；当前冻结 990 池中 987 只有合格当日日线输入，
另外 002827／600621／605258 保持阻断。后续分批准备运行中，不称作 Provider 已就绪。
