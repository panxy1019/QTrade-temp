# STEP2 独立日频因子评价：非生产最小实现

2026-10-28。仅新增隔离研究模块、显式 CLI、测试和本文档；不接入生产 API、任务
队列、ACTIVE、模型训练、组合、PAPER/SHADOW、服务或订单。`LIVE_TRADING_ENABLED`
必须保持 `false`，评价和归档写入均重新检查。12:52已完成固定日版本中891只合格
股票的短窗口实际诊断；不是完整990证券或全市场实证，也没有生产/API/UI接线。

## 实现与复用

- `src/qtrade/research/factor_evaluation.py`：独立评价与 `FactorEvaluationStore`。
- `scripts/run_daily_factor_evaluation.py`：显式输入、独立输出目录和可审阅 JSON 导出。
- `tests/test_daily_factor_evaluation.py`：真实格式但完全合成的 READY/Parquet 隔离集成。

来源/缺口审计沿用 `STEP2_FACTOR_CONTRACT_DESIGN_20261028.md`：复用
`ready_dataset`/`verify_version` 的完整文件和 READY 准入复验、FactorRegistry 的四个
既有允许因子、`metrics` 的 Pearson/平均秩、现有注册器的 SQLite 原子不可变登记模式。
vn.py 网关不提供本模块所需的数据版本、共同日历或双信息时钟；不引入网关。
没有表达式、Python 字符串或自定义模块执行入口；没有新增复杂模型/因子。

## 固定合同

输入必须为已准入的确切 READY 归档路径和其准入数据库。开始/结束都完整复验绑定，
读取每个 Parquet 前后再核对实际文件摘要。不读取变化中的湖或跟随最新头。
仅支持 `QLIB_NORMALIZED_ADJUSTMENT_UNCERTIFIED` 日频 close；实际候选证券必须是
显式排序去重的 READY supported universe 子集，不能把子集称为 1500 或历史全市场。

请求精确字段：`ordered_factor_refs`、`instruments`、`decisions`、
`label_horizon_sessions`、`knowledge_mode`、`quantiles`、`min_pairs`、
`stability_block_sessions`、`source_revision`、`revalidates_evaluation_id`。
每个 decision 只包含 `session` 与带时区的 `decision_at`；研究会话必须为冻结日历的
连续区间。决策须为同一上海会话的 15:00 或之后；UTC 与上海表示的同一瞬间等价。
共同历史按日历会话取数，不以较早观测行补足缺会话；按冻结资格区间记录每项剔除。

`RETROSPECTIVE_RECONSTRUCTION` 允许已观察历史的事后诊断：每项依赖原有
`available_at`/`observed_at` 原样保留，未知仍为空。`OBSERVED_SYSTEM` 额外要求
两时钟均已知且不晚于精确决策；缺任一时钟或晚一微秒的依赖拒绝进入评分。
后者仅检查依赖在屏障时刻的可知性，报告始终不声称历史上实际计算过该因子。
实际 `computed_at` 取真实完成时刻，独立于传入标签截止屏障，不能回写为旧决策时间。

标签固定为独立的 `close[t+h]/close[t]-1`，h 为 1–20 个冻结会话。特征仅消费截至 t
的 close；标签消费 t 与 t+h。终点须存在于该版本，且其上海会话 15:00 不晚于评价
截止屏障；已有标签时钟晚于屏障也拒绝。未知精确时钟的事后模式仅声明会话计划
成熟度，不能认证真正的历史可得时刻。该标签不同于旧模型的 next-open-to-open 标签。

不拟合、不搜索参数、不选择方向、不划分伪装未见的训练/验证/测试段；窗口事先给定，
全部披露为看过的事后历史。成本合同固定为毛标签诊断，不扣费、不模拟滑点或成交，
分组均值/组间差不能当作已实现的组合净收益。

## 指标与缺失政策

- 每日截面 IC/Pearson、RankIC/Spearman；ties 使用平均秩；按证券身份对齐。
  少于预声明 `min_pairs` 或任一常量截面输出 `null` 和原因，绝不以零替代无效相关。
- 有效日等权平均。ICIR/RankICIR 使用样本标准差 `ddof=1`，不年化；少于两有效日
  或标准差为零时为 `null` 和原因。旧 `pstdev`/零输出结果不改写、不混成同一政策。
- 分组按评分升序、同分证券 id 排序，以 floor 边界确定人数；高组减低组。
  常量仍可给出确定性的标签分组描述，但相关性为 null；这不是因子有效性证明。
- 最高组换手为相邻请求会话的新增最高组证券数/当日最高组人数；中间无有效组则
  不跨缺日连接。首日/缺相邻组输出 null。样本进出完整保留。
- 固定长度的连续请求会话 blocks 报告覆盖、有效天数、IC/RankIC、正方向比例及
  组间差；末尾不足一个 block 仍保留，不选择最优分段。
- 冗余为同日同证券、同价格基础的因子两两 Pearson/Spearman；报告每对样本、
  每日原因及有效日等权汇总，完全独立于因子与标签的相关性。
- 全部因子/标签/指标运算使用局部 Decimal precision28、ROUND_HALF_EVEN、固定
  指数范围与异常 traps，不改变调用方上下文。原复用 Pearson 的 sqrt 和时间序列
  stdev 使用 Python float；固定政策、Python 完整版本和包版本均归档。

## 归档、复验与边界

每次实际 CLI 尝试先追加 UUID attempt；失败追加仅含异常类型的失败收据，不删除
原尝试。中断可留下没有结果/失败收据的 attempt，不谎称成功；重试必须为新身份。
完整结果作为 SQLite 单事务文档 bundle INSERT；同身份并发写入仅一个成功，旧身份
不能覆盖，失败身份不能事后完成。`revalidates_evaluation_id` 必须解析到同库原报告。
新数据/PIT来源产生新数据版本、新 spec 和新 evaluation；保留旧输入/指标/失败。

bundle 保存请求、精确因子定义/refs、完整版本文件摘要清单、日历/资格账本、样本
值/标签/依赖时钟、每日指标/分组/换手、稳定性、冗余、摘要、实际源码字节快照及
包/数值政策。每个文档均有规范化摘要，报告 manifest 和 spec 再分别内容绑定。
`validate_report`/`get` 除检查哈希、身份、来源快照和因子/数据绑定外，还从归档样本
在同政策下重算日指标、分组、换手、稳定性、冗余和汇总；重新哈希但混入不一致
指标的报告仍拒绝。此为归档内部数值复验；不能替代原始价格真实性或原数据重算。
内容摘要不是抵御有权重写全部 SQLite 字节并重算一切的密码学签名。

CLI 必须给出输出目录，拒绝与仓库源码、选定数据目录、两个输入数据库重叠，拒绝
prod/production/active/services/forward-paper 命名路径、输出祖先符号链接，以及输出
SQLite/sidecar 符号链接或多硬链接别名，避免借输出路径写回受保护输入。
各次写连接前重新检查，构造对象后替换路径也拒绝。
输入 FactorRegistry 以只读方式打开。提交后的 JSON 审阅导出采用独占创建；如果导出
单独失败，已提交 bundle 仍在，可通过 `get` 审阅，不能声称 JSON 已交付。没有生产
目录自动发现/部署能力；调用者仍须选择明确隔离的新研究目录。

为 P0 非生产范围硬限制，不静默截断：最多4因子、6000证券、2520请求会话、4096
日历行，且 `证券数 × 决策数 × 各因子历史长度之和 <= 1,000,000`。此工作量检查
在数据读取前执行。只解码所需 warmup 至最后标签的日期窗口和 date/close/可选时钟
列；Parquet 元数据行数不能大于日历。每文件压缩大小<=32MiB、合计<=256MiB；所选
列元数据未压缩大小每文件<=64MiB、合计<=256MiB；实际 Arrow 每表<=32MiB、合计
<=128MiB，检查后才转 Python 行。这些是拒绝式工程限额，尚非实际大样本 RSS/SLA 证明。

始终披露当前流动性筛选、生存者及预筛偏差、已看历史、未认证 PIT/历史主表/退市/
交易状态/精确涨跌停/复权/公司行为限制。没有生成因子优越性、显著性、模型激活或
交易恢复结论。Alpha158 批量独立评价、原生 Qlib Provider 真值矩阵及生产/API/UI
接线仍未完成。下面的实际评价仅证明四个已有因子的非生产流程。

## 12:52 实际合格日版本运行

运行源码为 `8e038c86df93562b663a348fcee129371feef546`，集群隔离目录的266个
运行文件逐一与Git归档摘要核对。版本固定为
`09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407`；
891只支持证券，决策日为10月20、21、22、23、26日，各日15:30，下一交易日收盘
收益标签，四个已有因子，307395个历史依赖单元。10月27日尚无该版本内的次日
标签，因此不纳入这次评价，更没有使用28日未收盘数据。

12:51:27至12:52:01实际运行33.46秒，峰值RSS520972KiB（约509MiB）；这是评价
进程观测，不是发布器的512MB来源字节预算。原始自包含报告61775392字节，gzip
约2MB；SQLite结果留在独立operator目录。生产输入只读，前后模型、owner及冻结
账本控制指纹一致。没有覆盖生产注册器、实验、ACTIVE、信号或账本。

| 因子 | 五日有效证券对数 | 平均IC | 平均RankIC |
| --- | --- | --- | --- |
| 20日动量 | 888 / 888 / 888 / 885 / 885 | 0.1001 | 0.1264 |
| 5日反转 | 891 / 891 / 891 / 888 / 888 | -0.1127 | -0.0830 |
| 20日低波 | 888 / 888 / 888 / 885 / 885 | 0.1697 | 0.2308 |
| 等权秩组合 | 888 / 888 / 888 / 885 / 885 | 0.1356 | 0.1785 |

这些数字是**已看历史的五日流程诊断**，不用于选出优胜因子、改变方向、宣称样本外
有效或授予交易权限。缺失历史/标签按因子和日期排除并保留原因，没有为了达到891
而补造价格。报告同时保存分组、稳定性、重复信息和限制；五日长度不足以评价长期
稳健性。当前样本选择、生存者偏差、PIT与复权未认证继续明确披露。

实验身份为 `factor-eval-9ca107f1ff4843769b813d4b663eed63`。
[实际运行与原失败清单](evidence/p0-publisher-factor-20261028/real-daily-evaluation/archive-manifest.json)
保留两个在读取数据前发生的operator失败：辅助文件名遮蔽Python标准库；复用测试
目录缺少要求的源码文件。后续使用新的完整Git归档目录完成运行，未跳过源码检查。
清单中的空下载占位文件不作为远端收据或成功证据。

只读重放归档报告的复现命令（不访问集群、不创建生产结果）：

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
@'
import gzip, json
from pathlib import Path
from qtrade.research.factor_evaluation import validate_report
p = Path('docs/evidence/p0-publisher-factor-20261028/real-daily-evaluation/actual-891-stock/report.json.gz')
validate_report(json.loads(gzip.decompress(p.read_bytes())))
print('ARCHIVED_REPORT_REPLAY_PASSED')
'@ | python -
```

## 实际隔离验证与复现

主任务12:44后补充：冻结的评价源码 `782f245d...`、CLI `291f97c7...` 与登记器
`72fa94ba...` 在集群隔离目录逐文件验 hash；新增65项与相关13项合计78项通过、
零跳过，Windows相同78项通过。Ruff及定向mypy通过。独立复核另行重放时间因果、
过滤读取、指标一致性及链接防护；此前两项真实缺陷的失败证据与最终通过均保留。
见[完整证据清单](evidence/p0-publisher-factor-20261028/evaluation-acceptance/archive-manifest.json)。
这些测试独立于上面的实际股票池诊断，也不表示生产上线。

Windows 新增33项通过；覆盖6个合成证券×29个观察会话的真实 Parquet/版本清单/
READY SQLite，并执行实际独立 Python CLI 子进程与 JSON 导出。涵盖原函数数值
对照、手算 ties RankIC=5/6、样本 ddof1、共同网格缺口、未成熟标签、迟到/未知双
时钟、等价时区、收盘前拒绝、未来数据不泄漏、调用方 Decimal 上下文不变、受限
解码、同身份并发冲突、失败原子回滚、追加复验及原报告不变、哈希/数值混包拒绝。
对重哈希后删除限制、改变标签/成本/切分/数值政策、声称历史实际计算或PIT也拒绝。
这些是合成工程集成，绝非真实池因子收益实证或新的 Qlib 原生执行证据。

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
$env:LIVE_TRADING_ENABLED='false'
python -m pytest -o addopts= tests/test_daily_factor_evaluation.py -q
python -m ruff check src/qtrade/research/factor_evaluation.py scripts/run_daily_factor_evaluation.py tests/test_daily_factor_evaluation.py
python -m mypy src/qtrade/research/factor_evaluation.py scripts/run_daily_factor_evaluation.py --follow-imports=silent
```

对已审阅请求和隔离数据副本，CLI 复现形状如下；路径和 refs 必须为实际选择，不得
把示意路径当作可自动创建的生产输入：

```powershell
python scripts/run_daily_factor_evaluation.py --version-root E:/QTrade-temp/daily-input/versions/<exact-version-id> --data-database E:/QTrade-temp/daily-input/versions.sqlite --factor-database E:/QTrade-temp/factors.sqlite --request E:/QTrade-temp/factor-request.json --output-root E:/QTrade-temp/daily-evaluation-results
```

stdout 仅输出 evaluation_id/specification_sha256/report_manifest_sha256。
完整自包含审阅 JSON 在显式输出目录，且同一目录保留事务 bundle 和失败/未完成尝试。
