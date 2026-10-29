# P0 实际发布复核：导出通过，公网交付仍未恢复

2026-10-28，实际时钟截至 21:49 上海时间。本轮没有宣告公网恢复或 STEP1
完成；保留一次人工辅助发布的 `PUSH_UNCERTAIN`，禁止重放该 intent。
生产 publisher 仍为 647839 / fba51aad，未再次交接、重启或改变预算。

## 已经验证

生产增量导出实测 33.592927 秒及 32.346050 秒成功，45 秒合同不变。临时
loopback 通道使用集群原身份和严格 known-host 检查，桌面只转发加密 SSH
字节；实际只读 ls 的单通道、非零双向字节、清理均通过。旧 v1 的零通道
假成功保留且不予验收，见独立审查。

v2 发布前全量复核在 `git cat-file --batch-check` 读取 5,987 个对象时耗尽
90 秒。v3 改为一次完整、受限的冻结树清单，逐个读取实际 immutable 分片，
同时验证 SHA256、Git blob SHA1、原质量合同及每处索引来源关系。目录使用
逐层 NOFOLLOW 的持有描述符，文件使用相对 open、NONBLOCK 和精确大小。
真实数据文件有合法 hardlink，允许它的前提是完整字节双哈希均匹配。

v3 独立 Linux 25 项全部通过、0 跳过，实际执行 POSIX 文件和目录操作；
作者 Windows 39 项通过但 POSIX 处使用 syscall shim，不冒称 Linux 结果。
原 v2 的 71 项为此前版本独立证据，不移用为 v3 的新测试数。

实际 21:36:59 完成的新复核：

| 项目 | 结果 |
| --- | --- |
| 固定 Git 提交 | dde33b30a54d46beb0197a80ddbcb35afb5830da |
| 索引条目 | 3,000 |
| 全部当前分片及引用 | 5,987 / 5,987 |
| 当前分片实际读取 | 167,397,950 bytes |
| 完整树清单 | 74,377 个对象，未截断；旧对象只计清单，不宣称逐体复核 |
| 全量复核耗时 | 15.926159 秒，其中清单 0.223150 秒、读取及语义校验 14.920948 秒 |
| 对应真实导出 | SUCCEEDED，33.592927 秒 |
| 冻结 plan SHA256 | f33c80814b18cc6e77150535b68b93e6525a1ccdadfaa0bd021707d4cc87416a |

## 真实失败与对账边界

21:38:31 对上述 plan 执行了唯一一次 non-force Git push。原生 Git 90 秒
超时，总操作 93.31 秒含已有清理；实际单通道上行 19,526,865 bytes、下行
3,674 bytes，没有成功 ACK。所有 pump 已退出，原始 intent、uncertain 和
失败 stderr 均保留，不通过换 namespace 重放。

21:42:35 和 21:49:48 的实际只读远端广告仍为
`5a0925f47e541726210a8879e2f2bed4ae2a62fd`。21:49:14 检查完整可读 `/proc`
没有匹配本次 operator/isolated Git/proxy 路径的残留进程；publisher 身份及
LIVE=false 保持。这些证明客户端结束与两个观测点远端未变，不证明可以
观测 GitHub 服务端 receive-pack，也不把原 uncertain 改写成已确认成功。

21:46 后的实际生产 probe 返回 `STALE_TRANSPORT`，正式部署仍为 ae33a7c8。
全 1,500 身份、3,000 索引与五条 API 样本完整性核对不等于新鲜度；日线
1,498/1,500、分钟 1,496/1,500 也不等于全池完整。原紧凑 probe 保留。

## 后续门槛

冷搬运与普通增量发布应分开：目前对首次大包的 90 秒传输仍未解决。新的
bundle/浅基础或增量对象协议只能先在完全断网的 bare fixtures 中证明
prerequisite、复用 blob、delta、并发及全部当前字节闭包。不能靠隐藏 lazy
fetch、无界 clone、提高预算或裁剪数据过关。

任何新的人工主分支交付方案都须绑定旧 intent/uncertain 和新鲜对账，明确
目标、有限数据集合、大小/时间上限、身份与并发条件，再进行独立审核。
已有一次操作意图不提供再次发送许可。正常 publisher 的自动轮次与人工
辅助交付保持分别记录；后者即使成功也不能替代普通 Git90、公网索引、
Vercel API 和持续新鲜度的实际验收。

证据：`docs/evidence/publication-relay-actual-20261028/manifest.json`。
所有 hash 是一致性证据，非外部身份签名；NFS 阻塞及最后 fsync 不宣称由
协作式 deadline 硬中断。没有 PAPER 恢复、实盘、数据补造或历史改写。
