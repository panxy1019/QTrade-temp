# STEP2 四因子接口独立验收与归档读取修复

2026-10-28。第一轮只操作本机合成临时 READY/Parquet、独立测试与既有报告的只读
重放；没有集群、网页、生产 API、PAPER、ACTIVE、服务或订单操作。
`LIVE_TRADING_ENABLED=false` 保持。原实验、签名数据、共享状态文档没有改写。

## 范围与来源

先读取 AGENTS、CURRENT_STATE、MASTER_PLAN、DECISIONS（特别是 ADR-201/203/204）、
四因子登记/评价进展与 Strategy 接口审计，再检查实际实现及原有测试。
沿用已记录的 vn.py 缺口结论：网关不承担 READY 身份、因子内容版本、双时钟及
研究报告重放合同。本轮复用现有 FactorRegistry、DataVersionCatalog、评价器和
SQLite 不可变 INSERT，没有另建登记器、指标框架或执行路径。

新增 [独立测试](../tests/test_step2_factor_contract_independent.py)，修复
[factor_evaluation.py](../src/qtrade/research/factor_evaluation.py) 的报告读取验证。
测试自行构造 8 个证券、31 个价格会话和一个占位日历边界，使用 5 个连续决策会话
及四项既有定义。没有导入原测试 fixture、PriceFactorSpec 或项目指标作为数值真值。
测试中的 revision 字符串仅是合成标签，执行来源由实际源文件摘要绑定。

## 实际发现及修复前失败

原 `validate_report` 能发现摘要变化和指标数值不一致，但在重新计算各文档摘要后，
下面三种时点矛盾均通过验证：

1. OBSERVED_SYSTEM 已接受样本的一个 observed_at 改为未来时点。
2. history_sessions 最后一个会话改成未来日期。
3. label_exit_session 改成未来日期，保留有效标签和数值指标。

独立测试实际得到 **3 failed，14 deselected**，均为 `DID NOT RAISE ValueError`；
没有使用 xfail。修复前 JUnit、完整评价源码及独立测试源码保存在本机：
`E:/QTrade-temp/step2-factor-contract-acceptance-20261028/red-temporal.xml`、
`red-factor_evaluation.py`、`red-test_step2_factor_contract_independent.py`。
修复前评价源码 SHA256 为
`782f245d2845ab6b06e18c8c99aa7f844fae2f953fde96e4604a99a4c76e5b3d`。

首次修复后的整组运行记录另存 `first-fix.xml`：81 passed、1 failed。
该失败来自独立组合 oracle 将“逐因子百分位再平均”改成“秩求和后除”，在 Decimal
精度 28 下引入不同的 ties。已按登记的逐因子百分位政策纠正 oracle，没有放宽
RankIC 容差或修改实现来迎合错误预期。此前失败记录仍保留。

## 窄修合同

归档读取现在从保存的请求和日历重新推导历史网格及标签端点，验证：

- 日历日期规范、排序、唯一、摘要和 READY 起止日；决策确属连续共同会话。
- 每个因子需要的共同历史长度、逐依赖会话、时钟字段及条数，不接受错位或缺条。
- available_at/observed_at 必须可解析为带时区时点；known/clock_reason 与保存时钟
  一致。OBSERVED_SYSTEM 有效特征必须在精确决策屏障前已知；事后模式仍允许明确
  披露的未知/迟到时钟，没有把事后结果升级为历史实际计算。
- 标签端点恰为冻结日历的 t+h；15:00 上海计划成熟与保存的成熟拒绝原因一致。
  测试包含真实历史屏障前一微秒，并非只拒绝 2099 这一特殊日期。
- 完整十文件来源清单、因子定义来源摘要与归档源码摘要一致；规格 schema、证券
  范围、价格基础及频率匹配，归档也重新执行非生产依赖单元限额。

原来的摘要、固定政策、逐样本/逐日指标、分组、换手、稳定性、冗余及汇总复算仍
保留。未改变因子数学语义、请求格式、报告 schema 或旧归档身份。

## 独立验收覆盖

| 合同 | 实际检查 |
| --- | --- |
| 精确版本 | 未知 id/版本、latest、摘要不匹配、缺字段、重复引用拒绝；评价边界重新核对合成依赖存在 |
| 输入时点 | 双时钟分别覆盖恰好屏障、晚一微秒、未知；事后模式保留原时钟且可正常重验 |
| 特征/标签隔离 | 改变未来标签价格不改变特征；退出标签任一时钟晚于屏障均不计有效样本 |
| 部分覆盖 | 缺共同会话、非正价格、缺标签分别披露；保留全部候选 ledger/sample，按因子实际配对 |
| 缺失指标 | 未成熟标签保持 null、无分组和明确原因；因子间冗余仍按独立有效特征计算 |
| 数值 | 从原合成 close 直接计算四因子，独立 Pearson/平均 ties 秩、分组及高低组差/换手；比较 IC/RankIC、ddof1 IR、固定稳定性 blocks 和六组冗余 |
| 不可覆盖 | 同 evaluation_id 的另一份有效报告被拒绝；旧 payload/hash 不变；新身份可链接原评价追加复验 |
| 归档合同 | 重新摘要后的未来/迟到/未知时钟、错误或缺失网格、虚假 known、错标签端点/成熟度、范围和来源漂移拒绝 |

数值 oracle 使用 Python 标准库的数学/统计函数，容差 2e-13；不是另一供应商、另一
Python 运行时或独立价格源的认证。恒定/不足样本及 CLI/硬链接/失败尝试等已有
检查也随本次回归运行，没有以新测试替代它们。

## 验证与旧实验兼容性

新增独立测试 **32 项通过**；最终目标回归包括原 registry/evaluation、合同准备、
factor_search 与 signal_baselines，合计 **120 passed，0 failed/errors/skipped**，
耗时 26.35 秒。JUnit 保存在同一临时证据目录的 `regression-final.xml`。
Ruff 和评价模块定向 mypy 通过，`git diff --check` 通过。

原 891 股五日实验 `factor-eval-9ca107f1ff4843769b813d4b663eed63` 已通过修复后读取器
重验，报告 manifest SHA256 仍为
`61208f117890d0a453e084f92b6edc1d6b931b195d415b1b184104d72fbdb536`。
原 report.json.gz 的 SHA256 重放前后均为
`47bed3add84aff16f901531115c17ac4534cfd667b6716595a1ce098b9af37dd`。
独立只读重放收据为本机 `archived-replay-final.json`，耗时约 1.26 秒。
这只是原报告内部合同/数值的重新验算，没有重跑或覆盖原实验。

最终读取器源码 SHA256：
`3e1c6b4c57a4483fac160d7b410daa28eede4d75580fe9383e3ea1f6c3815f8f`。

复现（仓库根目录、本机临时合成数据）：

```powershell
$env:PYTHONPATH = 'src;.'
$env:LIVE_TRADING_ENABLED = 'false'
python -m pytest tests/test_step2_factor_contract_independent.py tests/test_daily_factor_evaluation.py tests/test_factor_registry.py tests/test_step2_factor_contract_readiness.py tests/test_factor_search.py tests/test_signal_baselines.py
python -m ruff check src/qtrade/research/factor_evaluation.py tests/test_step2_factor_contract_independent.py
python -m mypy src/qtrade/research/factor_evaluation.py --follow-imports=silent
```

## 限制

v1 原归档没有标签退出行的 available_at/observed_at，也没有资格 interval 文件的
原文字节。本次可重验已保存的特征双时钟、标签端点和计划成熟合同，不能仅凭旧
bundle 独立重建标签源时钟、资格事实或原始价格真实性；这些仍须原 READY 版本。
生成路径本身对退出标签双时钟仍有拒绝式校验，本次新增测试实际覆盖了该路径。

内容摘要是内部一致性检查，不是有权重写全部档案者无法伪造的密码学签名。
第一轮仅本地 Windows 验证；恢复后的 Linux 隔离验收见下节，仍无性能/SLA 或断电认证。
原五日实验的选择/生存者、已看历史、PIT/复权未认证及短窗口限制继续保留。
独立因子报告到 StrategySpec/Builder/生产 API 的接线仍未完成；本验收不授予
因子优越性、模型晋级、PAPER 恢复、生产或真实交易权限。

## 恢复后的 S2-A 独立审查与 Linux 隔离验收

人类随后明确恢复 ABC，允许非生产研发和集群隔离验收，覆盖方案中尚写为
WAITING/PAUSED 的阶段状态。本节仅完成方案 S2-A；没有提交 Git、部署服务或修改
ACTIVE、PAPER、账本、原实验、签名数据及共享状态文件。

重新审查窄修的入口顺序、共同日历、精确双时钟、拒绝原因、标签端点/15:00 成熟、
完整来源清单、数值回放及不可覆盖路径，未发现需要在此阶段继续修改的具体阻断。
旧 v1 的原始标签退出时钟/资格事实证据缺口仍按上文保留，不以规则推导替代原始事实。

本轮使用完整 Git 基线
`f036bca197272df4b1f885dfe8165a27b67c36cd` 的 **2,068 个文件**，仅覆盖评价模块、
独立测试和本文档的验收前快照，共 **2,070 个候选文件**。两个可执行候选文件仍为：

| 文件 | SHA256 |
| --- | --- |
| factor_evaluation.py | `3e1c6b4c57a4483fac160d7b410daa28eede4d75580fe9383e3ea1f6c3815f8f` |
| test_step2_factor_contract_independent.py | `76473a69d527bf1a6bbcfbdfad57de402cb0fd2a807d1f75de0c8fbee5e3050e` |

本文档的执行包快照为 `8eaf7ebbb74665e691ec4918c784d3161b4aa2ba6371e44d736a7eeacf9558c2`；
本节是在验收后追加的记录，不伪称这段后来文字已存在于测试执行包。
完整基线和三文件覆盖包各自归档 SHA256；Linux 执行前后逐文件验证全部候选的字节数
和 SHA256 相等。发布器只取基线 Git 字节，没有混入另一工作线的未提交候选。

隔离根为
`/root/qtrade-local/operator-evidence/factor-contract-s2a-20261028T094016Z-01e1eb42`。
代码、临时 SQLite/Parquet、pytest basetemp、日志和结果均在该本地操作目录内。
唯一使用现有数据根的用途是读取已安装 Python 和检查数据盘空间，没有写入生产目录。
Python `-B`、`PYTHONDONTWRITEBYTECODE=1`、pytest `-p no:cacheprovider`、显式
PYTHONPATH/TMPDIR 与 `LIVE_TRADING_ENABLED=false` 均进入实际命令/环境收据。

| 门槛/结果 | 实际记录 |
| --- | --- |
| 数据挂载预留 | 前 287157780480、后 287156731904 bytes，均大于 100 GiB |
| 本地操作卷预留 | 前 25905577984、后 25802891264 bytes，均大于 2 GiB；device 与数据挂载不同 |
| Linux 目标回归 | **120 passed，0 failed/errors/skipped**；pytest 17.27 秒，完整测试子进程 17.84 秒 |
| 同包 Windows 复验 | **120 passed，0 failed/errors/skipped**；pytest 18.07 秒，完整测试子进程 18.67 秒；全部 2,070 文件前后摘要匹配 |
| 测试子进程峰值 RSS | 182200 KiB；不是发布器预算或整个平台内存上限 |
| Linux 运行时 | Anaconda Python 3.14.6，GCC 14.3.0 |
| 包完整性 | 全部 2,070 文件执行前后逐字节摘要匹配，未生成源码缓存或改写执行包 |
| 原 891 股报告 | 原身份/manifest 通过新 reader 重验，2.43 秒；压缩文件前后仍为 `47bed3add84aff16f901531115c17ac4534cfd667b6716595a1ce098b9af37dd` |

本机完整证据为
`E:/QTrade-temp/factor-contract-s2a-20261028T094016Z-01e1eb42/evidence-manifest.json`，
含 Git/覆盖 tar、完整文件清单、操作器、空间与执行前后源码收据、Linux JUnit/原始
stdout/stderr、旧报告重验收据。原三项 red、首轮 oracle 失败、120 项 Windows 成功和
原报告收据另以 prior-windows-evidence.tar 原字节保留，没有被 Linux 成功替换。
只使用已授权连接函数导入 SSH 能力；证据中不保存或输出认证材料。

在该既有隔离执行包重现目标回归：

```bash
R=/root/qtrade-local/operator-evidence/factor-contract-s2a-20261028T094016Z-01e1eb42
P=/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python
cd "$R/source"
LIVE_TRADING_ENABLED=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$R/source/src:$R/source" TMPDIR="$R/tmp" \
  "$P" -B -m pytest -p no:cacheprovider \
  tests/test_step2_factor_contract_independent.py tests/test_daily_factor_evaluation.py \
  tests/test_factor_registry.py tests/test_step2_factor_contract_readiness.py \
  tests/test_factor_search.py tests/test_signal_baselines.py
```

本节结论仅为双平台工程/旧档案兼容验收，不是新的全股票池因子实验、长期稳健性、
生产交付或实盘认证。

## 后续接口接线建议

1. S2-B 先冻结可用 READY 版本、确切证券、共同会话、标签截止和新评价计划。当前
   四项 required_closes 合计为 69；891 证券的 16 个决策会话即 983664 个依赖单元，
   17 会话将超过既有 1000000 上限。长窗口须有明确有界分块/新实验与汇总合同，或
   先单独审查资源政策，不能静默扩大预算、截掉样本或将跨块首日换手当连续值。
2. 完整因果证据若需自包含，另定义 v2，冻结标签两端时钟、资格区间原字节及其
   READY 来源绑定，以新身份生成新实验；保留 v1 原样及明确兼容读取，不回填重签。
3. S2-C 新增受限 schema，冻结 factor_ref、evaluation_ref（id/spec/manifest）、同一
   READY data_binding、证券/标签/split、signal_kind、组合/执行/成本和实际来源；
   因子、评价和策略身份各自验证，不把新字段藏进旧 parameters，也不从 IC 推导净收益。
4. 复用现有 StrategySpec/StrategyRegistry 原子 job link、EXPERIMENT/NONE 角色及
   specification 预览→签名队列→worker→runner→结果读取。在各边界独立核对相同
   新 schema 和确切 refs，保留旧预览/同 request_id 不同内容/取消/失败的拒绝语义。
5. Builder 首版限定四因子、合格日频和已支持 Qlib 0.9.7 组合/执行规则；展示精确
   版本、样本缺口、毛标签成本合同和 PIT/生存者/复权限制。研究成功不触发 ACTIVE、
   PAPER 恢复或实盘；网页接线和真实视觉验收另以实际交付证据验收。
