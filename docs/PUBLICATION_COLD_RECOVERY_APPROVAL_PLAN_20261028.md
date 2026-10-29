# 单次冷交付审批方案：已完成准备，尚未发送

检查点：2026-10-28 22:59 上海时间。原 A/B/C 授权下的实现与测试继续有效；
本文件单独申请一次 **180 秒 Git 冷发送**。日常导出45秒、日常Git90秒不变。

## 需要批准的具体动作

通过集群现有SSH身份，用原生、非force的Git，将下表固定提交尝试发送一次至
既有观测仓库main。新建独占授权与发送声明，绑定本计划的原始JSON字节。
不修改发布器配置、不交接或重启owner、不重新消费旧声明，也不循环重试。

| 绑定 | 本次准备结果 |
| --- | --- |
| 新固定提交 | `abe2986cd6e3f3faeb4984454498e4c67f1646d3` |
| 预期远端main | `5a0925f47e541726210a8879e2f2bed4ae2a62fd` |
| Prepared plan SHA256 | `9a459c194021e6cadc1cec8dd09ecb8f7e86d8c5cab81d972a1108e7edbf4fb8` |
| 操作器SHA256 | `738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa` |
| 发布源码 | `fba51aade2ee2410dda087e95cf054e53375ed3a` |
| 新独占目录 | `/root/qtrade-local/operator-evidence/publication-cold-recovery-20261028T143000Z` |
| 实际冻结时间 | 2026-10-28 22:58:15，集群时钟 |

原始计划见
`evidence/publication-cold-recovery-plan-20261028/actual/publication-cold-prepare-20261028T145758Z/target.json`。
此处目录时间只是命名，实际操作时间以各原始记录为准。

旧90秒辅助尝试的INTENT、UNCERTAIN及旧target/plan在新计划内明确交叉绑定。
它们继续保留失败与不确定含义，不能重命名为新请求或使用旧许可重发。

## 为什么另行审批

用户要求“全冷预算最终取值必须附性能证据和新版验收，经单独审批”，原A/B/C
不是任意延长生产合同。此前一次实际辅助90秒发送超时，远端没有前进；不能
再把同一个声明当可重试任务。此次提出新声明和独立冷预算，**180秒尚未实测
通过，也不承诺一定恢复**。

同一真实对象范围的诊断已排除几种不成立的优化：原生wire生成约2.70秒、
19.445MB；non-delta缓存反而变42.750MB，未安装；aux/metadata/negotiate未
减少重传。私有SFTP和loopback样本较快，但尚不足以区分GitHub传输和服务端
确认的全部耗时。因此新操作器保存有界阶段进度，以便即使失败也能定位。

详细负证据见 `P0_COLD_TRANSPORT_DIAGNOSTICS_20261028.md`；不提高普通预算
或安装未经证明的Git参数来掩盖问题。

## 已完成的测试与集群准备

- 最终精确源码：Windows64项、Linux64项，均0失败/0跳过；独立离线7项。
  独立审计复现了中间版Popen清理异常导致无界wait的问题，新版显式清理并
  关闭管道；旧源码、原失败XML与中间61项结果均保留。
- 22:56:50实际DIRECT SSH只读读取：远端main仍为上表5a0925f4。使用原严格
  hostkey配置；只保存公开known_hosts字节数与SHA，不读取私钥内容。
- 首次prepare因提交HEAD与刚完成的export收据尚未对应而拒绝，没有生成target
  或发送声明。之后只读观察到32.701586秒成功export及匹配的已提交index，才
  重新进行准备；没有绕过绑定检查、改收据或计时。
- 第二次实际prepare耗时15.914秒，核验3,000个条目、5,987个当前分片、
  167,397,950字节及每个引用的质量/来源关系，SHA1 Git blob与SHA256同时匹配。
  74,377是历史对象树的元数据数量，不声称读遍其全部历史字节。
- 22:59:33读取563个本地进程cmdline，无权限拒绝，无旧relay目录范围的客户进程；
  这不证明不可见的GitHub服务端receive-pack不存在。发布器647839/start ticks
  1022098698仍为fba，LIVE=false。旧声明字节相同；新授权、PREFLIGHT、INTENT、
  SEND_STARTED、RESULT与UNCERTAIN文件全部不存在。

上述是代码、协议与准备证据，**不是实际180秒发送性能验收**。原raw重放仍由
已有发布器合同及独立全量oracle认证，本次prepare不冒充再次重放全部私有raw。

## 获批后的执行边界

1. 根据新的真实用户回复，以create-only/fsync创建授权，绑定上述plan SHA、
   实际时间与授权引用。文件SHA和kind只是可信操作员的声明，不能自行证明
   人类身份，未收到回复前不创建。发送时授权不得早于计划或超过1小时。
2. 再核owner、来源、旧声明、计划、授权、严格known_hosts字节和实际远端。
   已经是固定target则只读确认不发送；既非预期base也非target则停止对账。
3. 创建新INTENT和SEND_STARTED，原生非force发送一次。冷Git子进程最长180秒，
   沿用TERM2秒、KILL/wait2秒的owned清理；这些不是整个多阶段操作的总SLA。
   ordinary ls、各本地审计命令仍各受既有有界预算控制。
4. LS与push之间没有跨事务锁。原生Git保证自己的oldOID/非force规则，独立
   fixture验证了竞争拒绝和合法fast-forward；不声称前置LS能阻止所有竞态。
5. 超时、中断、非零退出、清理或证据落盘不确定均保留声明并停止，禁止自动
   重发。不force、不删除竞争提交、不修改冻结模型/信号/账本/交易约束。

stderr只保留枚举/压缩/写入/远端解析的数值阶段和单调时间；未知文字仅保存
字节数与SHA。Writing100%不是服务端ACK。原始私有配置、密钥与任意trace不落库。

## 发送后逐层验收，不能跳步

| 层 | 必须取得的真实证据 |
| --- | --- |
| 固定本地版本 | 本次plan与全部索引/当前分片的精确绑定；原失败保留 |
| Git发送 | 退出0后仍独立ls-remote核实际main，记录与固定target的关系 |
| 公开内容 | 固定公开commit的索引/目录/抽查分片与预期hash及来源对应 |
| Vercel API | 相邻实际deployment、真实GET、索引身份、版本绑定、API系列对照 |
| 日常连续恢复 | 至少3个后续自然生产轮次，分别记录export≤45、Git≤90及实际public/API |
| 新鲜度 | 后续当前index年龄≤180秒、catalog≤1200秒；覆盖、停牌、分钟PARTIAL另列 |
| STEP1 | 新公开交付上的C；D旧档案合同及E执行日/换模范围分别真实复核 |

一次旧快照冷发送成功可能暂时仍陈旧，必须等自然追赶后的当前公开新鲜度。
冷ACK不能代替日常发布恢复，更不能宣布整个STEP1完成。若失败，保持未通过。

22:54:48实际公开probe仍STALE_TRANSPORT，main5a09；index为10:36:23，catalog为
20:07:27。这是本次审批前的真实负证据，不刷新时间伪装为新数据。

## 不变的边界与后续工作

保持LIVE=false、rolling=OFF、现有研究批准政策、当前ACTIVE与既有账本；不做
PAPER恢复、候选人工激活或实盘。自动回访仍PAUSED。本次操作只涉及公开研究
观测数据。历史PIT、复权、退市和多年分钟连续覆盖仍独立治理，不妨碍合格范围
的STEP2非生产研究。新因子Builder仍是EXPERIMENT/NONE登记，非组合收益回测。

总证据manifest SHA256：
`7e6791c2439b73071f5d55bb97f6520940b44339225c7bf72955db1fca39d2e6`。
独立复现命令及测试源码在该证据目录的final/与independent/；只运行pytest不发送。
真正send命令不作为无需授权可直接执行的示例提供。
