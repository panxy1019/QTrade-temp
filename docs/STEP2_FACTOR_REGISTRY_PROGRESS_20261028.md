# STEP2 最小因子版本登记：非生产实现

日期：2026-10-28。最新用户指令允许 STEP2 非生产实际开发，优先现有因子版本登记与
独立评价；此前“只有设计与测试准备”的范围不再限制本文件的隔离实现。生产采集、
ACTIVE、执行账本、服务、正式注册器/队列/API 和 STEP1 真实验收没有变化。

实现：[factor_registry.py](../src/qtrade/research/factor_registry.py)。
验证：[test_factor_registry.py](../tests/test_factor_registry.py)。先前设计与原生 Qlib
审计继续保存在 [STEP2_FACTOR_CONTRACT_DESIGN_20261028.md](STEP2_FACTOR_CONTRACT_DESIGN_20261028.md)；
其中“未实现”描述对应当时准备检查点，本文件记录随后新增的非生产能力。

## 复用决策及范围

先复核 `src/qtrade/research/factors.py`、`signal_baselines.py`、`workflow.py`、
`strategies/registry.py`、`persistence/catalog.py`、`research/artifacts.py` 以及
`scripts/atomic_json.py`。直接复用 PriceFactorSpec、average_ranks 和现有规范化
canonical/digest；持久化沿用 StrategyRegistry/MetadataCatalog 的 SQLite 不可变
比对与事务模式，使用 BEGIN IMMEDIATE、WAL、synchronous=FULL，不增加文件写入框架。

实际 Windows 临时目录探测发现现有 atomic_json immutable writer 在发布后目录
fsync 阶段返回 PermissionError(13)。ExperimentArtifactStore 的 exists/write
也不能替代并发不可变发布。因此本模块选择已有跨平台 SQLite 模式，没有修改共享
writer，没有吞掉 OSError，也没有将目录 fsync 未完成写成耐断电验收。原生 Qlib
描述符/指标可继续按先前审计复用；vn.py gateway 不承担本任务的因子身份或历史时钟。

只登记四项既有数学语义，不增加大量因子或开放表达式语言：

| alias | factor_id | 计算与最短输入 |
| --- | --- | --- |
| momentum20 | qtrade.price.momentum20 | close[t]/close[t-20]-1；21 个 close |
| reversal5 | qtrade.price.reversal5 | -(close[t]/close[t-5]-1)；6 个 close |
| lowvol20 | qtrade.price.lowvol20 | 20 个简单收益的负总体标准差，ddof=0；21 个 close |
| multifactor_equal_rank | qtrade.composite.multifactor_equal_rank | 上述三个确切版本的截面平均百分位秩等权平均；21 个 close |

这四项继续使用现有函数，未将 Qlib ROC/STD 改名当作同一因子，也未登记等权控制为
价格预测因子。无 eval、任意表达式求值、动态类加载、模型训练、策略晋级或订单接口。

## 身份、来源与持久化合同

`builtin_definitions(source_revision=..., price_basis=...)` 生成可信内置定义。每项
冻结公式、参数、日频/close/证券类型、价格基础、最短历史、缺失政策、时间政策、
依赖引用和实际五个源文件的 SHA256（registry、factors、signal_baselines 及复用的
workflow canonical/digest、data_freshness AUTHORITY）。source_revision 是调用者提供的
40 位版本标签；
**实际文件 hash 才是执行绑定，单独的 Git 标签不构成已经验证执行该提交的证明**。
独立实验仍应保存实际执行包版本与完整数据身份。

新登记边界固定 Decimal 精度 28、ROUND_HALF_EVEN、指数范围 -999999 至 999999、
capitals=1、clamp=0，显式仅开启 DivisionByZero/InvalidOperation/Overflow traps。
评分及截面秩在独立 localcontext 中计算，不继承或修改调用者精度、舍入、traps 与
flags。低波函数内部继续复用既有 float/pstdev；其 Python/stdlib 运行版本由独立
评价 manifest 记录，不能把 Decimal 上下文固定误写成跨所有 Python 版本逐位相同。

当前价格基础允许列表是 SINA_NATIVE_ADJUSTMENT_UNCERTIFIED、
QLIB_NORMALIZED_ADJUSTMENT_UNCERTIFIED、SYNTHETIC_UNADJUSTED。选择标签不认证输入
确实具有该价格口径；独立 evaluator 必须验证数据合同与选择完全一致。

`factor_version` 是规范化定义主体的 SHA256；`definition_sha256` 覆盖该主体及版本。
精确引用为 `{factor_id, factor_version, definition_sha256}`。公式、来源、价格基础或
依赖变化会形成新版本；没有 latest/别名解析入口。每次返回的读取结果由持久化 JSON
重新解析，外部修改内嵌 dict 不会更改已存定义。

定义必须与可信内置合同精确一致：即使攻击者重新计算 hash，未知字段、代码路径、
未来公式、被改的参数/双时钟政策/权限类型/依赖或源路径仍被拒绝。运行前再次比对
当前源码；源不一致的版本不得静默用新代码执行。Composite 只能引用同来源、同口径
的三个基础版本；登记和读取都核对依赖真实存在、完整定义及三项引用一致。

`FactorRegistry(database)` 明确接收一个本地 SQLite 路径；不自动寻找生产目录。
单项登记和整组四项登记均以事务提交。已存在 id/version 只能接受完全相同的规范化
定义；不同内容报冲突，不进行 UPDATE/修复。登记时刻独立保存，不进入数学版本；
相同定义重试保留第一次 registered_at。

`FactorRegistry(database, read_only=True)` 要求既有文件，跳过建库/建表；通过 SQLite
URI mode=ro、PRAGMA query_only 和只读 BEGIN 读取，显式拒绝登记。没有隐式初始化、
数据版本准入、ACTIVE、策略生命周期、信号或 PAPER/SHADOW 写入。

数据库路径及现有父目录不得为符号链接；主 SQLite 和 -wal/-shm/-journal 必须是
普通单链接文件（st_nlink=1），构造和每次连接均重查。只读模式也保守拒绝硬链接。
这阻止通过 SQLite/sidecar 别名修改其他文件；显式传入路径仍是可信操作者的选择，
模块不能仅凭路径名识别所有生产库，CLI 还必须核对保护输入的 samefile 身份与输出隔离。

## 给独立 evaluator 的接口

```python
from qtrade.research.factor_registry import (
    FactorDefinition, FactorRef, FactorRegistry,
    builtin_definitions, evaluate_builtin, factor_ref, validate_definition,
)

# 由操作者明确选择隔离数据库、实际版本标签和价格基础。
registry = FactorRegistry(isolated_database)
refs = registry.register_builtins(
    source_revision=actual_source_revision,
    price_basis="SYNTHETIC_UNADJUSTED",
    now=actual_aware_time,
)

# 后续独立任务只读取确切版本，评分前仍检查实际源码。
reader = FactorRegistry(isolated_database, read_only=True)
definition = reader.get(refs[0])
scores = evaluate_builtin(definition, admitted_histories)
```

`admitted_histories` 为 `dict[str, tuple[Decimal, ...]]`。评分器拒绝空范围、短历史、
非正/非有限值或其他数值类型；不补齐数据、不静默剔除证券。它只计算已准入历史，
**不接收数据行时钟，也不自行认证共同交易日历、证券资格或 PIT**。available_at 与
observed_at 不晚于 decision_at 的逐输入校验、历史模式披露、标签成熟、切分、样本
范围与资格/剔除账本，由独立 evaluator 承担；定义中的时间政策不是已经完成的时钟
认证。实际数值计算时刻和来源/数据版本仍需由任务归档。

## 已运行的检查

Windows 新登记器检查 **32 项通过，0 failed/errors/skipped**。与先前准备检查、
factor_search、signal_baselines 合并的相关检查 **55 项通过，0 skipped（1.60s）**。
本模块及新测试 Ruff 通过，模块定向 mypy 通过。

实际临时 SQLite 集成包含：

- 四个版本登记→重启读取→不同调用时刻幂等重试；源文件逐个 hash 比对，最初时刻不变。
- 四项评分与现有 signal_family 的同一组合成历史一致，输入对象不变。
- 同一数据库 12 个竞争请求、6 个工作线程，只保留四条不可变版本记录。
- 第三项 INSERT 的受控 SQLite 中断将前两项一起回滚；移除临时 trigger 后成功重试。
- 未来公式、任意代码字段、变更后的时钟/权限/参数、源路径及伪造依赖在重签后仍拒绝。
- 缺依赖、依赖内容交换、同版本变更与已损坏持久化内容失败关闭，不在原位修复。
- 新价格基础产生四个新版本，原四个定义字节不变；源不同的描述符不能在当前代码执行。
- 只读读取不改变定义数据库字节；缺数据库不建目录，直接写 SQL 和 register 都失败。
- LIVE_TRADING_ENABLED 改为 true 后拒绝新建、登记及评分；缺失/非法 close 不填充。
- 调用者使用 prec=6、ROUND_DOWN、收紧指数范围、开启 Inexact/Rounded traps 并保留
  flags 时，四项输出仍与默认上下文完全一致，调用者上下文逐项不变。
- 对主 SQLite 和三种 sidecar 的实际硬链接尝试，在打开前拒绝，保护文件字节不变；
  对象构造后才增加的别名也不能绕过每次连接检查。

复现（PowerShell，仓库根目录）：

```powershell
$env:PYTHONPATH = 'src'
$env:LIVE_TRADING_ENABLED = 'false'
python -m pytest tests/test_factor_registry.py
python -m pytest tests/test_factor_registry.py tests/test_step2_factor_contract_readiness.py tests/test_factor_search.py tests/test_signal_baselines.py
python -m ruff check src/qtrade/research/factor_registry.py tests/test_factor_registry.py
python -m mypy src/qtrade/research/factor_registry.py --follow-imports=silent
```

## 限制和下一验收

12:40 后主任务补充：同一候选源文件哈希在集群独立目录核验，新增32项及相关13项
共45项通过、零跳过；Windows相同45项通过。Ruff和定向mypy通过。
[双平台证据](evidence/p0-publisher-factor-20261028/registry-linux-windows/archive-manifest.json)
包含源码清单、原始JUnit、日志和隔离操作器。初次预检错误地把100GiB数据盘预留
用在28GiB剩余的根overlay，尚未建远端目录即拒绝；改为分别验证数据盘100GiB和
operator盘1GiB后成功。数据盘实测约271GiB可用，不是实际数据盘耗尽。

本文件记录非生产模块及双平台隔离测试；独立评价归档与真实
合格日频版本由主任务分别取证，不用工程通过冒充研究完成。本模块不包含 Alpha158 的
158 条正式登记，不包含通用依赖图/公式引擎、生产 API/队列/网页、晋级或执行接线。

登记库应放在本地文件系统，不承诺网络文件系统上的锁/断电语义；并发测试验证的是
同机线程/独立连接竞争，尚未模拟机器断电。当前 schema 只理解这四个可信定义；
未来改变数学语义或 schema 时，应保留旧解析/回放适配，不能用新公式重写旧版本。
内置版本绑定当前五个源文件的完整字节，因此无关源码编辑也会保守地产生新版本。
这会增加版本数量，但避免声称较新计算代码仍是旧版本。

PIT/退市历史改善后的评价必须追加新数据版本和关联原实验的新报告，保留旧失败、
输入、指标和结论。研究登记不授予交易权限；LIVE_TRADING_ENABLED=false 以及原有
portfolio → risk → OMS → execution → gateway、halt 和人工恢复/成功对账边界保持。
