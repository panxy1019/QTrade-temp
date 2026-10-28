# S2-C：因子策略严格合同及原子登记适配

日期：2026-10-28。范围为非生产合同实现及隔离验收。
`LIVE_TRADING_ENABLED=false`；没有订单、网关、服务操作、ACTIVE/PAPER、模型激活或
自动晋级，也没有改写已有因子定义、评价报告、READY 档案或共享状态文档。

## 实现和复用边界

新增 `src/qtrade/research/factor_strategy_contract.py` 和独立测试
`tests/test_factor_strategy_contract.py`。复用已有四因子白名单、FactorRegistry、
FactorEvaluationStore/validate_report、READY 全档案验证器和 StrategySpec/StrategyRegistry。
vn.py 网关、订单及事件组件没有因子定义、评价来源或 READY 身份验证能力；本层无需
引入交易组件。Qlib 原生 TopkDropout/Exchange 的实际执行留在后续适配阶段。

经协调授权，对 `src/qtrade/strategies/registry.py` 做一个窄重构：将旧 Qlib 入口已经
验证后的原子写入抽成私有 `_register_validated_research_experiment`。旧入口保留
QLIB validator、身份、权限、幂等和生命周期语义；新
`register_factor_research_experiment` 只懒加载因子 schema validator，不接受任意
schema、callable 或旧 Qlib 合同。没有复制第二套登记 SQL、数据库或任务框架。

两类研究合同使用原有 strategies/contracts/jobs/transitions 表；因子父子关系同时
写入原有 lineage 表。事务采用 `BEGIN IMMEDIATE` 和 `synchronous=FULL`。
任何 job、规格、合同、父策略或插入冲突均回滚整个事务。

## v1 合同内容与验证层次

`qtrade.factor-strategy-definition.v1` 的内容摘要形成 `strategy_version`，
`strategy_id=factor-<strategy_version>`。下列内容都进入身份：

| 内容 | 严格边界 |
| --- | --- |
| `factor_ref` | 精确 factor_id/version/definition_sha256，必须已登记；不解析 alias/latest |
| `signal_kind` | `FACTOR_SINGLE` 或现有等秩 `FACTOR_COMPOSITE`，与定义依赖一致；一个组合引用完整内置三因子 DAG，不开放自选权重、符号或动态公式 |
| `evaluation_ref` | 精确 evaluation_id/specification_sha256/report_manifest_sha256；所选定义必须确实在该评价中，且有至少一个有效 IC 日 |
| `evaluation_context` | 保存 READY binding、有序评价因子、确切证券、决策区间/数量/完整决策摘要、标签、原诊断 cost/split、knowledge_mode、资格 ledger/定义/所选 summary 摘要、计算时点及原有全部研究限制 |
| `segments` | 显式 train/valid/test，按真实交易日验证边界和标签 horizon purge，位于相同 READY 版本；原评价决策及其标签终点必须落在所声明 test 内 |
| 组合 | 固定日频 TopkDropout；topk 1–min(100,样本数)，n_drop 1–20 且不超过 topk，hold_thresh 2–20，risk_degree 0.1–0.95；最少持有步不是固定退出期 |
| 执行/成本 | 冻结下一会话 open、100 股单位、方向和容量字段、买卖各 1–100 bp、最低费 0–100、初始资金 10 万–1 亿；非负十进制字符串，不接收 bool/float 数值别名；当前 slippage 和 impact 固定 0，规则仍未认证 |
| source | 13 份固定源文件的实际字节摘要和 40 位 revision 标签；标签不是 Git 提交认证，完整隔离包另有 Git 基线/文件清单证明 |
| parent | 精确父 strategy_id/version/contract_sha256 和明确的变更理由/假设/摘要；事务内验证父实际存储合同、规格、版本及同 family，不只信任摘要列 |

保留原评价的 `RAW_CLOSE_TO_CLOSE`、gross diagnostic、单个 post-hoc 窗口和
`unseen_test_claim=false`。新 segments 是拟议研究分组，没有训练、拟合或分组回测
结果；它们不把原诊断窗口变为未见样本。拟议 next-open 成交/成本也不追溯应用于
close 诊断指标。合同明确 `adapter_implemented=false`、`backtest_implemented=false`、
`rules_certified=false`、`paper_admission=false`、`model_activation=false`。

`freeze_strategy` 从真实通过 validate_report 的报告和精确 FactorRegistry 引用构建。
`validate_strategy` 只证明归档对象结构、内容身份和固定语义，可离线读取旧源绑定；
它不能证明客户端自签字段是实际本地事实。
`verify_bound_strategy/register_strategy` 才重新读取真实评价存储、精确因子及依赖、
实际 READY 档案/准入事件/支持名单，并核对当前源文件后执行登记。
直接 Registry 入口与旧 Qlib 入口一样属于结构登记边界；未来 worker 必须调用完整
本地证据适配器，不能把它当作 READY/评价来源检查的替代。

StrategySpec 的 family 为 `QTRADE_EXISTING_FACTOR`、model 为
`NONE_FIXED_FACTOR_SCORE`，factor_set 保存完整因子版本引用；parameters 只保留
既有 `contract_sha256` 索引，不承载未知因子/评价字段。首次登记只能 EXPERIMENT/NONE。
相同 job 幂等，新 job 可指向相同版本，原 created_at/updated_at、血缘和唯一初始
transition 保留。外部已经改变状态/角色的因子策略不能经此入口重新打开或晋级。

## 验收证据

最终冻结包：Git `fba51aade2ee2410dda087e95cf054e53375ed3a` 的 2,194 个文件，加恰好
3 个候选 overlay，得到 2,196 个文件；不包含其它并发未提交文件。

| 候选文件 | SHA-256 |
| --- | --- |
| 新 factor_strategy_contract.py | `2434723896a8f3d646d08c325f3bb4bcf8b6f03d227c67bba0e8e45dcec5ec37` |
| Registry 窄重构（LF） | `6426cf4b376a99b216177aa17841760b80f5636389f21562b3fe0454f5f245a4` |
| 新 test_factor_strategy_contract.py | `f7296001f23afc7917e99f239aca578ab11b37320273ce0ba48fec8319ff355f` |

新增 73 项独立合同测试覆盖两种内置信号、完整引用和身份变化、未知/未来/不成熟或
不匹配输入、真实日历 purge、成本/规则、坏存储、父关系、job 冲突、幂等与注入的
末端 INSERT 故障回滚。旧 Qlib 合同与 worker/Builder 拒绝测试同时回归，两个公开
登记入口互相拒绝另一 schema。模型/执行入口没有接线。

最终 LF 源码：Linux **303 passed、0 skipped**，pytest 59.87 秒、完整 child
60.489 秒、最大 child RSS 222,656 KiB；同包 Windows **302 passed、1 skipped**，
pytest 66.92 秒、完整 child 68.327 秒。唯一跳过为 Linux 进程身份检查，新增 73 项
在两平台均无跳过。Ruff 与两份源码 mypy 通过。三个源/测试文件均为 LF；Registry 的 filtered/unfiltered
Git object ID 都为 `e696ced7e186ce0ead258521e9f4a6c4aac09b84`，可与 staged bytes 对照。
更早 d090 混合换行版本的 Linux 303 / Windows 302+1 结果单独保留，不外推为 LF 结果。

最终 Linux 根：
`/root/qtrade-local/operator-evidence/factor-strategy-s2c-20261028T111832Z-22bd6f85`。
两平台 2,196 个源码文件均在测试前后逐字节核对 SHA/长度。
Linux NFS 前后 284,155,707,392 / 284,062,384,128 bytes、local 前后
20,229,095,424 / 20,041,568,256 bytes，满足 100 GiB / 2 GiB 保留门槛。
所有写入均在自己的 local operator 临时目录；只对 NFS 做 statvfs。

原 891 股票报告使用最终 LF 隔离包 reader 重验成功，2.298 秒；原 gzip 字节未变：
SHA `47bed3add84aff16f901531115c17ac4534cfd667b6716595a1ce098b9af37dd`，
manifest `61208f117890d0a453e084f92b6edc1d6b931b195d415b1b184104d72fbdb536`。
本次没有为这份旧报告虚构新执行结果或覆盖原实验。

证据位置：`docs/evidence/step2-factor-strategy-s2c-20261028/manifest.json`。
清单绑定最终 Linux/Windows XML、源码前后清单、overlay、固定 Git 基线清单、driver、
资源与旧报告重验收据，也保留扩展依赖清单前的成功结果、换行转换收据、Windows
首次长路径解包失败和长 basetemp 导致的 11 项旧集成 fixture setup errors。
后两者更换较短 owned 路径后使用完全相同的包重验，不改实现或测试来掩盖失败。

PowerShell 复现（目录需使用短路径以容纳 Git 证据树）：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:MYPYPATH = $env:PYTHONPATH
$env:LIVE_TRADING_ENABLED = 'false'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m pytest -p no:cacheprovider tests/test_factor_strategy_contract.py tests/test_factor_registry.py tests/test_daily_factor_evaluation.py tests/test_step2_factor_contract_independent.py tests/test_step2_factor_contract_readiness.py tests/test_qlib_strategy_contract.py tests/test_step2_strategy_readiness.py tests/test_qlib_bound_research_integration.py tests/test_qlib_worker.py tests/test_qlib_workflow.py tests/test_strategy_registry.py tests/test_strategy_comparison.py
python -B -m ruff check src/qtrade/research/factor_strategy_contract.py src/qtrade/strategies/registry.py tests/test_factor_strategy_contract.py
python -B -m mypy --explicit-package-bases src/qtrade/research/factor_strategy_contract.py src/qtrade/strategies/registry.py
```

## 后续最小接线与限制

1. 在现有受保护 `/specification` 增加明确的 factor kind；选择已登记的精确版本及
   已归档评价，调用本模块构建，返回完整冻结合同/预期版本。修改任一字段使预览失效。
2. 沿用现有 HMAC、同源鉴权、request_id、Git CAS 和有界容量。新队列请求携带紧凑
   spec/精确 refs/source_revision/expected_strategy_version；不把大报告或全证券合同
   塞进当前 16,000-byte reader。worker 必须已有可独立解析的不可变因子/评价归档，
   重新构建并比对版本；未知 kind 仍拒绝，不能伪装成 QLIB_RESEARCH_JOB。
3. 在同一 Worker 增加显式有界分支，复用取消/超时/重启/资源优先级；ingest 和 runner
   分别核验本地 READY/评价/因子/source，再调用完整 register_strategy 适配器。
   这不是跨数据库分布式事务；源档案必须保持不可变，登记事务只原子关联引用。
4. 后续 signal adapter 才把冻结的日频分数接入原生 TopkDropout/Exchange。用同样本、
   同资格、同执行/费用/容量做独立重算，归档净结果并重新核验所有引用。新拟合模型、
   自定义组合、全池等权、再平衡周期及非零滑点需要各自明确的版本化能力和验收。

现有评价 v1 未归档完整标签退出双时钟或资格区间源字节的限制仍存在，详见
`STEP2_FACTOR_CONTRACT_ACCEPTANCE_20261028.md`；本合同只承接其真实来源和披露，不
补造历史 PIT、退市全覆盖或因果准入。没有执行 Qlib 原生回测、生产网页/Git 网络
往返、集群训练或 PAPER 验收。当前完成的是第一阶段严格合同与原子关联能力。
