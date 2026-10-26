# Qlib 来源绑定与附加历史状态限制

本轮修复把历史状态原文、来源身份和实际交易掩码接入同一实验。它没有消除
当前流动性样本的幸存者偏差，也没有把现在接收的历史回填变成历史 PIT 证据。

## 从来源到执行

1. 读取校验通过的 `pit-repair/coverage.json`，只读取它列明的状态文件。
2. 检验主表内容哈希、逐股票状态哈希、股票/日期身份、顺序、接收时钟和覆盖数量。
   拒绝伪造的历史可得时间、未来接收时间、重复日期和损坏来源。
3. 执行器将来源固定在 `workflow/source-snapshots/<SHA256>`。网页初始化表单时
   带入该 SHA256；任务执行前再次核对。运行期间的回填不能改变已固定任务。
4. `request.json` 将配置、源码、行情清单哈希及来源快照哈希共同绑定到 run ID。
   来源变化必须产生不同实验身份，不能复用旧结果。
5. 完整主表、状态文件及覆盖报告进入实验的 `pit-sources/`。归档校验器同时检查
   文件字节、请求、摘要和来源身份，不能只凭汇总行数宣称可复现。
6. 在 Qlib provider 写入前附加限制，之后通过实际
   `Exchange.check_stock_limit` 重载检查每种原因的样本。记录保存在
   `constraint-reload-check.json`；缺少可用来源时如实记录，不能伪造检查成功。

原始固定基线继续保留。新来源协议为
`ALIGNED_SUPPORTED_UNIVERSE_SOURCE_BOUND_CONSTRAINTS_V2`，不与旧协议直接作模型归因。
Ridge 与 LightGBM 必须具有相同行情、来源快照、源码、切分、种子、组合与成本，
才能把差异解释为模型对照。来源不同也会阻止对照。

## 限制政策

| 情况 | 新增处理 | 不能据此声称什么 |
| --- | --- | --- |
| 来源无状态 | 保留已有价格近似与容量限制；统计缺失覆盖 | 不是已认证正常交易 |
| 来源报告停牌，但行情存在非零成交日线 | 双向限制并记录冲突 | 不认定供应商已准确还原停牌原因 |
| 原始开/收盘价、成交量或成交额对不上 | 双向限制，保留原价格及冲突账本 | 不猜测复权或成交量单位来消除冲突 |
| 对账一致且 isST=1 | 研究政策禁买，原有禁卖仍保留 | ST 禁买不是交易所的全面买入禁令 |
| 对账一致且 isST=0 | 保留原掩码，不解除任何旧限制 | 不是精确涨跌停认证 |

原始开/收盘价允许 CNY0.011 误差，原始成交量必须一致，成交额误差不超过
`max(CNY1, 1ppm)`。容差属于已冻结的研究合同，不能为了提高覆盖率随意放宽。
来源的复权口径由现有采集器固定为 `adjustflag=3`，与标准化日线保留的原始字段
对账。对账使用完整历史日线，只属于事后来源检查；不能宣称在当日开盘决策时已知。

所有新掩码均大于等于原掩码：状态回填只增加限制，不解除原有保守限制。
既有 4.5% 调整后开盘变动近似、前一交易日原始成交量 1% 容量及 100 股单位仍保留。
精确历史涨跌停价格、上市初期例外、集合竞价成交、T+1/交收细节、公司行为账本及
历史 ST 状态的实际可得时间仍待独立核验，所有结果保持 `NOT_ELIGIBLE`。

## 实验审计文件

- `request.json`：完整输入身份和运行身份。
- `pit-sources/`：运行实际使用的原文、校验清单及接收时钟。
- `execution-constraints.json`：逐股票与总计的对账、缺失、冲突、新增限制数量。
- `constraint-ledger.parquet`：有来源状态的逐股票逐日判定及前后掩码。
- `constraint-reload-check.json`：实际 Qlib 方向限制调用结果。
- `checksums.json`：完整归档文件字节校验；不是历史正确性的证明。

网页只发布有限汇总，完整逐日账本保留在集群。旧实验冻结的 13 股等覆盖数字不会
被新回填数字覆盖；新实验记录新快照。输入库存 990 股也不等于每天实际可投资数量。

## 复现

先检出实验记录的源码提交，并使用原来的冻结行情版本和 Qlib 环境。
`ARCHIVE` 必须指向已核验的新协议归档，`DATA` 是对应行情清单所在目录。

```bash
PYTHONPATH=src python -m pytest tests/test_source_constraints.py tests/test_qlib_workflow.py
python -m ruff check .
python -m mypy src

PYTHONPATH=src "$QLIB_PYTHON" -m scripts.run_qlib_baseline \
  --data-root "$DATA" --output "$NEW_OUTPUT" --tracking "$NEW_TRACKING" \
  --source-revision "$REVISION" --workflow-spec "$ARCHIVE/workflow-spec.json" \
  --pit-evidence-root "$ARCHIVE/pit-sources"
```

不要用正在变化的采集目录替代归档中的 `pit-sources` 来声称复现。
未来模型对照可以从同一冻结来源建立两份配置；只改变名称和模型。

## 来源与剩余 P0

复用 Qlib 的方向限制实现，未引入新的撮合器或网关；vn.py 执行组件不能提供缺失的
历史主表和供应商来源证据。本轮源码审计依据
[Qlib 0.9.7 Exchange](https://github.com/microsoft/qlib/blob/v0.9.7/qlib/backtest/exchange.py)。
[BaoStock 官方接口页](https://baostock.com/baostock/index.php/Python_API%E6%96%87%E6%A1%A3)
本次访问未返回可用正文，因此字段语义未被当成独立来源认证，真实字段与原行情
仍需按上述容差对账。第三方摘要没有被当作规则权威。

剩余最高优先级：独立全市场和退市股历史对账、历史成分及状态可得时间、官方原始
涨跌停和特殊上市期规则、公司行为与交收约束、集合竞价和停牌恢复。完成研究任务
不会自动获得 PAPER 或 LIVE 权限，`LIVE_TRADING_ENABLED=false`。

## 2026-10-26 集群实跑与归档

两份任务均由生产网页登录提交，执行源码为
`846a836702e8c8f4c020e90159565fcc869b6fa7`，行情清单哈希为
`a56d093e439345619d2a0419be4e6191886164d8bc849ee05ef25452c26a0759`，
固定来源快照为
`b39408fa65995fc593e85c43e0b1c6bd6d5ae2443efdbe0f42873c1de6a83687`。
任务配置仅名称、模型不同。每份归档的 11,955 个文件逐一校验通过，
每份 `constraint-reload-check.json` 的 12 次实际 Qlib 方向检查通过。

| 项目 | Ridge | LightGBM |
| --- | --- | --- |
| 任务 ID | `6f04b986eaeb454c8c61579f1cea266b` | `4fc474f9f5f94199914b5c8802eb5127` |
| 实验 ID | `source-bound-ridge-prod-20261026-a5157438bd6cc6a0` | `source-bound-lightgbm-prod-20261026-fbd3630e653be6a1` |
| IC | 0.01937 | 0.02426 |
| RankIC | 0.04697 | 0.05205 |
| 成本后区间收益 | 89.00% | 117.23% |
| 成本后年化收益 | 26.69% | 33.42% |
| 成本后 Sharpe | 1.238 | 1.210 |
| 最大回撤 | −32.35% | −33.33% |
| 平均日换手 | 17.57% | 17.59% |

测试区间为 2024-01-02 至 2026-10-23，共 678 个组合交易日；
训练 2017-04-05 至 2021-12-28，验证 2022-01-04 至 2023-12-26。
两者均使用 Alpha158、TopkDropout（50/5、最少持有 2 日）、95% 风险仓位、
初始资金 1,000 万元、买入 8bps、卖出 13bps、最低费用 5 元及同一种子。
同区间基准收益为 19.65%。990 股为行情库存，训练、排名和交易支持范围
为 891 股；这不是每日恒定的可投资数量。99 股科创板仍排除在支持范围之外。

36 股状态来源共 86,586 行，只有与现有行情日期匹配的行参与以下对账。
全输入库共 2,157,805 根日线，其中对账一致 84,302 根、冲突 549 根、
缺状态 2,072,954 根；新增禁买掩码 1,817 根、禁卖 539 根。
这些数字覆盖输入库全历史，不是测试期委托或实际成交次数。
来源中的额外日期也不会被补造成价格行。旧实验的来源和收益不会被改写。

这些结果仍是已查看历史上的非 PIT 诊断，不是新的未见样本外证据。
Ridge 旧协议的 91.43% 与本次 89.00% 不能作为模型差异归因；新旧执行协议
不同，网页拒绝直接对照。两份新任务可以按相同协议进行模型对照，
但收益较高不等于风险更小，更不代表获得 PAPER/LIVE 准入。

机器证据见 [来源约束验收记录](evidence/qlib-source-constraints-20261026.json)。
生产浏览器已验证
[同来源模型对照](evidence/qlib-source-bound-comparison-20261026.png)和
[跨协议归因保护](evidence/qlib-source-bound-cross-protocol-20261026.png)。
生产结果地址：
[Ridge](https://q-trade-temp.vercel.app/#/backtest?experiment=6f04b986eaeb454c8c61579f1cea266b)、
[LightGBM](https://q-trade-temp.vercel.app/#/backtest?experiment=4fc474f9f5f94199914b5c8802eb5127)。

完整归档保留在集群的 `workflow/jobs/<任务ID>/output/runs/<实验ID>/`。
在对应源码版本运行以下命令可重新校验归档（只读，不重训）：

```bash
ARCHIVE="$ARCHIVE" PYTHONPATH=src python -c \
  'import os; from pathlib import Path; from qtrade.research.qlib_publication import verify_archive; print(verify_archive(Path(os.environ["ARCHIVE"]))["run_id"])'
```

后端完整 Linux 检查 579 项通过，最终协议相关检查 72 项通过；前端完整
204 项通过，最终来源固定相关检查 9 项通过。验收说明的文字补充另经页面
5 项测试、ESLint、TypeScript 和生产构建检查通过。
