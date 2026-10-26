# 批准后的每日自动研究预测

2026-10-26：实现与本地检查完成，集群及生产验收尚待记录。

沿用已审计 Qlib 0.9.7 RMDLoader、冻结 DatasetH/处理器、独立恢复和参考分数
一致性路径。没有重新实现训练算法；不新增网关。预测子进程中禁止 fit。

现有单写研究 worker 查询当前合格数据事件和人工激活回执，创建唯一预测任务。
身份绑定模型、批准 epoch、数据版本、数据事件、观察池、代码版本和数据截止日。
同一身份重复轮询不会重复执行；切回旧模型产生新的批准 epoch，不能重放旧预测。
未经批准的 STAGED 候选只显示 WAITING_FOR_APPROVAL。

任务、状态及事件保存在现有模型 SQLite。计算在独立子进程完成，主 worker
继续处理心跳和控制请求。新训练等待预测完成；已有训练期间仍可执行预测。
预测限时十分钟、OMP 两线程、磁盘保留 100 GiB，子进程不继承研究队列密钥。
退出会终止并回收子进程；异常重启先核对进程身份，封存结果可验证恢复。
不确定或失败任务保留，不自动重试同一身份。

每份结果保存批准请求、实际执行源码、158 因子、完整分数、来源及限制、生成时间、
下个已核验交易日开盘时间，全部文件哈希绑定。原子发布前重新校验 ACTIVE 与数据头。
中途切换模型/数据或错过执行时间的计算保留为 SUPERSEDED，不能成为当前信号。
已有输入验证不能改名冒充批准后生成的预测。

`#/models` 展示自动预测状态和任务历史；API 的 `current_prediction_usable`
结合当前交易日、心跳及执行时点动态计算。COMPLETE 是历史完成状态，不代表仍可用。
没有 ACTIVE 时等待人工批准；自动版本调度和滚动训练属于后续独立交付。

## 验证

```sh
PYTHONPATH="$PWD/src" python -m pytest tests/test_prediction_registry.py \
  tests/test_daily_prediction_artifact.py tests/test_online_prediction_worker.py \
  tests/test_forward_prediction.py tests/test_online_research.py tests/test_qlib_worker.py
cd web && npm test -- src/pages/OnlineModelsPage.test.tsx
```

Windows 专项使用短 `--basetemp` 避免路径长度限制，并仅在测试中替换不支持的
目录/只读文件 fsync；真实持久化、Linux 进程恢复和实际模型计算必须集群验收。
真实模型验收应使用隔离账本及明确 TEST_ONLY 批准，不得修改生产 ACTIVE。

## 限制

当前 990 历史股票池有幸存者偏差；最新 Provider 987 合格输入仍有三个缺口。
评分实际股票数量还受冻结模型支持范围约束，不能用 987 代替实际预测数量。
完整历史 PIT、精确交易规则与公司行为未认证。每日预测是研究分数，不是订单，
也不是已观测前向收益。PAPER/SHADOW、完整预测查询、自动日版本与滚动重训仍待实现。
`LIVE_TRADING_ENABLED=false`。
