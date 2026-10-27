# 历史数据版本的真实研究与复用验收

上海时间2026-10-28 01:58。**已通过真实生产任务证明：可以选择旧的不可变READY版本
做研究，正式归档仍绑定所选旧版本，系统默认与serving版本不会因此倒退。**

这次是事后历史复现，未创建新的前向观察，也不代表全部STEP1通过。

## 本次实验的身份与时间

| 项目 | 实际值 |
| --- | --- |
| 生产任务 | `578bb6107577406185782a08e1c499d7` |
| 模型与特征 | Ridge，Alpha158，`qlib-0.9.7-Alpha158-v1` |
| 本次选择的数据 | `d33a74663373b1d4a0d7cf1ecf1622b34657a6fda08aacc7bacadb70d47ec4fa`，截至10月26日 |
| 系统最新数据 | `09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407`，截至10月27日，未改变 |
| 源数据规模 | 2,158,792条日线，990个历史股票分区；模型支持891只，三个数量不能互相替代 |
| 冻结策略版本 | `2e849335616ce055b72eb61c480ca378121f50d3a69d28ff2ba187ca371ddafb` |
| 实际执行源码 | `096387b8088e4361c9e5cd9cd2c165e21819fead`，与网页main版本分别记录 |
| 新模型工件 | `98d27455a7cb224ef3ecbd7cf850455f386fbb93baf8137a4d789e142ab91795` |
| 任务提交 / 完成 | 10月28日01:37 / 01:46:07，均为实际上海时钟 |
| 独立校验 / 公网核验 | 01:49 / 01:52，复用规格01:53:58 |
| 策略 / 模型状态 | EXPERIMENT；新模型核验时为TRAINED，activation_count=0 |

可在[这份正式回测结果](https://q-trade-temp.vercel.app/#/backtest?experiment=578bb6107577406185782a08e1c499d7)
中查看；页面实际DOM/交互本轮仍未验收，下面的生产API与档案证据分别保留。

## 真正验证了哪些环节

1. 从实际生产研究目录中按完整ID选择已准入的10月26日版本；同一天存在另一份版本，
   没有仅凭截止日取第一项。选择的是原C链路已固定的previous_version_id。
2. 使用已有受保护登录，在生产规格预览中冻结完整data binding和strategy_version；
   通过真实`POST /api/research/jobs`提交。同一request_id的相同重试返回同一任务，
   未生成第二项研究。凭据、cookie和登录响应体均不落盘。
3. 集群接收与runner分别验证旧版READY准入及实际全部输入；任务的实际`--data-root`
   指向d33a7466，当前版本09a67b13没有替换该路径或原spec。
4. 独立重读全部**12,486个归档文件**及精确文件集合，核对checksums、summary、来源
   快照、旧版准入、strategy-definition、真实任务事件、策略注册器和训练模型关联。
   执行目录中的580份源码/配置/测试文件与096387b对应Git对象和字节逐份一致。
5. 已拟合模型与处理器在新的进程恢复，fit方法禁止、处理器不变、158列顺序一致；
   恢复分数最大绝对差为**2.3592239273284576e-16**。这证明恢复一致性，不证明收益优越。
6. 新模型以TRAINED工件登记，没有激活；原模型生命周期行、ACTIVE批准、日数据epoch、
   模型批准政策、14本前向账本指纹、owner/instance及其他8个跟踪owner均保持不变。
7. 实际默认域名相邻deployment均为mainae33，公开catalog及result均展示该实验。
   Registry中的EXPERIMENT关联同一任务、策略版本和模型工件；默认spec仍选10月27日。
8. 实际复用规格接口返回HTTP200，原spec逐字段相等，并绑定原结果哈希；没有用当前
   默认参数补齐旧档案，也没有将旧数据悄悄升级。

## 网页上如何操作，以及日期为什么可以不同

- 在“策略研究”的数据版本选择器中选择所需READY版本，再选择特征、模型、时间切分、
  组合与成本。表单预览产生新的固定策略版本，登录后提交。
- 已接受的研究在“实验中心”查看真实进度，完成后在“回测分析”选择对应实验。
- 在正式结果中使用“复用配置创建对照”，会从已归档规格复制；改变模型或参数后，
  需要重新预览并生成新的strategy_version，再提交新任务。
- 历史实验显示10月26日、系统最新版本显示10月27日、任务完成显示10月28日，是三种
  不同含义：**研究数据截止、当前服务数据截止、实际运行时刻**。这三者不能统一改写成
  “今天”，否则归档无法复现。这次API已明确证明它们分别保留。

这些步骤对应已接通的程序流程与本次真实API验收；本轮浏览器工具未取得DOM/截图，
不能将步骤说明当作已完成的视觉点击验收。

## 明确保留的限制

- 使用当前选定股票池回投历史，历史PIT尚未认证；旧版数据不等于真实当时可获得的数据。
- 公共源复权、ST/停牌与精确限价仍有独立限制，执行是日频近似。
- 本次在已知10月27日数据存在后研究10月26日，不是未见数据的前向试验；即便后台
  后续生成兼容性验证，也不能因此声称通过真正的未来日验证。
- 本次没有恢复新PAPER，没有改变既有一次授权或关闭实盘保护；rolling继续OFF。
- 10月28日完整前向观察及浏览器验收仍是STEP1未完成项，STEP2尚未开始。
- 控制状态不变性对应本次实际时点。后续合法新会话、数据推进或模型变更不应改写这份
  参考；静态旧档案复核与新时点运行核对应分别保存。

## 证据与只读复现

### 02:17补充：自动验证已发生，不能当作未见数据检验

后台随后自动完成验证任务`1ed2fd215098f7e64421f9419584662f`。ModelRegistry在
10月28日01:49:26记录VALIDATING→VALIDATED→STAGED，新Ridge的activation_count仍为0。
实际15份验证档案逐份校验，12份绑定源码与096387b的Git对象一致；已拟合处理器不变、
禁止fit。当前批准政策只允许lightgbm，因此这个Ridge没有替换ACTIVE29589b44。

| 时间边界 | 实际上海时刻 | 含义 |
| --- | --- | --- |
| 10月27日数据准入 | 10月27日20:23:08 | 新输入当时已存在 |
| 这个Ridge训练完成 | 10月28日01:44:38 | 使用固定10月26日研究版本 |
| 兼容性验证生成 | 10月28日01:49:20 | 对已存在的27日输入恢复预测 |
| 独立只读核验 | 10月28日02:17:16 | 确认自动生命周期、档案和未激活状态 |

**数据截止日晚于训练样本截止日，不等于验证数据是在模型冻结后才出现的。**
本例只接受输入兼容性，不认证未见数据、前向收益或整个STEP1。与此同时，当前ACTIVE
LightGBM两本账本仅有10月28日09:30目标、尚无当日会话；14本指纹、批准/epoch、服务
owner/instance均与原参考一致，没有新建或恢复该Ridge的PAPER。

首版只读脚本曾将`2026-10-28 09:30:00+08:00`与带`T`的字符串直接比较，触发断言。
第二版按带时区datetime比较相同瞬间后通过；未改动原时间或账本。保存了两版脚本及
失败观察，原失败的stderr只存在工具记录，未声称另有原始stderr文件。

对应[独立核验](evidence/qlib-archived-version-20261028/calendar-boundary/verification.json)、
[验证档案](evidence/qlib-archived-version-20261028/calendar-boundary/validation.json)、
[实际生命周期事件](evidence/qlib-archived-version-20261028/calendar-boundary/candidate-events.json)
与[失败观察](evidence/qlib-archived-version-20261028/calendar-boundary/operator-failed-observation.json)
保留不同范围。该检查只写新证据目录，所有业务数据库以mode=ro/query_only读取。

### 原历史研究任务

完整证据分开保存：

- [生产提交与同请求重试](evidence/qlib-archived-version-20261028/production-submit/attempt.json)
- [独立档案、模型与生命周期检查](evidence/qlib-archived-version-20261028/cluster/verification.json)
- [实际任务与Registry事件](evidence/qlib-archived-version-20261028/cluster/job-and-registry.json)
- [默认生产结果交付](evidence/qlib-archived-version-20261028/production-result/verification.json)
- [原始规格复用核验](evidence/qlib-archived-version-20261028/production-result/reuse-verification.json)

只读查看真实任务和固定输入，不会新建实验或重新消费恢复请求：

```sql
SELECT id,state,updated,error,
       json_extract(request,'$.spec.data_binding.data_version_id') AS selected_version,
       json_extract(request,'$.expected_strategy_version') AS strategy_version,
       json_extract(result,'$.model_artifact_id') AS model_id
FROM jobs WHERE id='578bb6107577406185782a08e1c499d7';
SELECT rowid,id,at,state FROM events
WHERE id='578bb6107577406185782a08e1c499d7' ORDER BY rowid;
```

使用SQLite URI `mode=ro`、`PRAGMA query_only=ON`、短事务及显式关闭。
独立校验的确切命令、源码哈希和时钟见[原始执行记录](evidence/qlib-archived-version-20261028/cluster/verification-attempt.json)，
原始归档继续留在集群不可变目录。操作脚本中的写出目标需改为新证据目录才能重审，
不要覆盖现有成功/失败凭证，也不要重跑提交脚本制造重复研究。
