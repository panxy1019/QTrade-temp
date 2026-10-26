# Qlib 滚动训练接入

状态：实现与验收中。生产自动训练仍为 OFF；本阶段先验证原生训练路径。

`rolling_policy.py` 将 OFF/DAILY/WEEKLY/MONTHLY/CUSTOM 转成受限政策。
DAILY、WEEKLY、MONTHLY 分别按 1、5、20 个已验证交易会话判断；CUSTOM
只接受 2、5、10、20、40、60。失败尝试也计入间隔，不能通过重试无限重训。
已实现登录网页控制、签名任务队列、政策版本比较与原子任务声明。政策保存采用
幂等请求 ID，旧页面不能覆盖新政策；运行期间改为 OFF 不自动激活任何模型。
实际生产启用仍须用户保存政策，默认 OFF 不变。

每个版本最多生成一个窗口。默认训练 1260 会话、验证 126 会话、测试 60
会话；训练/验证、验证/测试之间各排除两个特征日期。标签为下一开盘至再下一
开盘收益，最新可评价特征日是数据截止日前两个真实会话，未来日历边界不入样本。
所有日期会经原生 `RollingGen` 再生成并与独立计划逐项比对。

执行复用 `TrainerR`、Alpha158、Ridge/LightGBM、已有 TopkDropout 回测和
不可变归档。LightGBM 用薄适配保留 300 轮、30 轮 early stopping；不复制算法。
TrainerR 保存的 DatasetH 用 IT_LS 恢复，禁止再次拟合处理器。

成熟测试窗口用于历史评价；截至当前输入日的最后 20 个会话另外用于恢复参考。
两者不能混用。独立进程验证特征、分数、拟合状态后才得到可继续验证的候选。
当前前向输入验证要求新版本晚于候选训练参考截止日，因此候选完成训练后仍需
下一合格版本的输入验证，不会因当前历史指标较高自动替换已激活模型。

集群操作员验收（只读已验收 Provider；输出到新目录）：

```bash
PYTHONPATH="$SOURCE/src:$SOURCE" LIVE_TRADING_ENABLED=false OMP_NUM_THREADS=2 \
  "$QLIB_PYTHON" -m scripts.run_qlib_rolling \
  --data-version-root "$VERSION_ROOT" --policy "$POLICY_JSON" \
  --output "$NEW_OUTPUT" --tracking "$NEW_TRACKING" --source-revision "$REVISION"
```

政策 JSON：

```json
{"mode":"OFF","custom_sessions":null,"model":"lightgbm","train_sessions":1260,"valid_sessions":126,"test_sessions":60,"seed":20261026}
```

OFF 政策的手工验收不启用自动训练。入口核验版本、来源快照、标签窗口及至少
100 GiB 剩余空间。生产队列与现有研究任务互斥，预测优先，训练限时一小时，
候选输入验证限时 15 分钟；重启将遗留任务标为中断，不无限重试同一任务。
候选在更晚合格数据到达后自动做冻结推理验证；失败保留 TRAINED 与失败任务，
通过才可 STAGED。验证也不激活模型。

2026-10-26 集群真实验收：原生 TrainerR 的 LightGBM 滚动候选完成，训练
2020-10-29 至 2026-01-06，验证 2026-01-09 至 2026-07-20，测试
2026-07-23 至 2026-10-22。独立进程恢复核对 17,813 行、158 特征，
分数差为 0，处理器未重拟合。候选未激活，当前生产模型仍是用户批准的原模型。
完整证据见 `evidence/qlib-rolling-signal-acceptance-20261026.json`。
新队列与网页控制的生产部署验收另行记录，不由手工训练成功代替。

限制继续随归档保存：当前池的幸存者偏差，历史 PIT/公司行为/交易约束未完整
认证，历史测试被观察过，日开盘与流动性约束为近似。没有真实前向收益或实盘授权。
