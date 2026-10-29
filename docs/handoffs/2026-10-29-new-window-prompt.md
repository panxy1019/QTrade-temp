# 新 Codex 窗口启动提示词

将以下内容粘贴到新窗口，并让新窗口使用 `E:/QTrade-git`。

---

请接手我的QTrade项目。先只读核对代码和数据，再按以下授权方向持续推进非生产研究开发。
不要从头搭建，不要把全部STEP1/C/D/E或公网发布通过作为离线STEP2前置条件。

先读：

1. `E:/QTrade-git/AGENTS.md`
2. `E:/QTrade-git/CURRENT_STATE.md`
3. `E:/QTrade-git/plans/MASTER_PLAN.md`
4. `E:/QTrade-git/DECISIONS.md`
5. `E:/QTrade-git/docs/handoffs/2026-10-29-step2-research-priority.md`

最新方向优先于旧heartbeat/旧“全部STEP1完成才开始STEP2”的文字：

- STEP2因子、策略与真实回测75%；每日数据和最小校验20%；高级发布协议预留5%。
  这是目标精力比例，不是工时统计或CPU配额。
- STEP1每日目标：自动产出来源一致、完整性通过、Qlib可读的不可变快照，失败保留上一版本。
- 公网发布、98d历史Proof、D门槛、完整集群交付继续保留未通过，不阻断合格冻结版的离线研究。
- 不继续扩展MinIO/R2/Publisher状态机。已有协议成果保留，必要交付另列。

仓库是 https://github.com/panxy1019/QTrade-temp 。代码基线be4faa79，分支
`codex/qlib-handover-production-acceptance`，PR35仍DRAFT，后面有本次文档提交。
先看git status，不reset/覆盖其他窗口工作；保留未跟踪node_modules。
环境默认cwd可能是E:/Q，这不是应直接工作的最新仓库。

2026-10-29上海09:32—09:33只读实测：正式日head为
`b82baaac956745f4e02a520a35b2aa277b0037510a4d164d798db803ce77e0d5`，截至10月28日；
历史证券990、当前合格988、workflow去重891只；1,726是多段资格区间行数，不是股票数。
原四因子16日实验绑定旧09a67版本，截至10月27日。两版均不是891股十年矩形完整数据，
首次研究须重验实际覆盖和完整档案。本轮没有重新跑全量verify_version。

真实已有：四因子momentum20/reversal5/lowvol20/multifactor_equal_rank的不可变登记、
891股16日IC/RankIC评价；登记型受保护队列/Registry隔离闭环；新原生适配器Linux33测试。
**另有旧七策略真实Qlib对照，2024-01-02至2026-10-23，678会话/601,013共同样本行，
含净收益/回撤/换手。** comparison_id=f6b9e52b1c30c4f46e0733d02bb4e800450a9acbb67a8954d63a2bb857dde943。
先读docs/QLIB_UNIFIED_STRATEGY_COMPARISON.md和原生执行证据；不要误报系统没有真实历史回测。
当前缺口是新注册因子/独立长窗评价与旧执行引擎/归档的统一可重复接线，不是所有能力都要重做。

首个交付：先复核/解释旧七策略结果，核对它与当前891冻结版/refs的范围和来源差异，
复用已有结果；再用 `scripts/run_factor_strategy_backtest.py` 做新登记链的最小真实16日
重放并扩四项，避免为接线盲目重算所有旧结果。
该阶段只证明真实组合工程闭环，不把已看的16日称为未见样本。

紧接着审计长窗口实现：当前四因子评价1,000,000依赖单元上限使891股同算最多16日，
回测100,000评分行上限只容约112日。不能仅调大常量；复用原评价函数做有界分批、
边界warmup和可追溯聚合，组合跨块保持持仓/现金连续。旧报告/限额/来源检查不改写。

冻结train/valid/test、标签、交易时间、成本和基准。拟合处理器只用train，选择只用valid，
test不调参；标签终点做purge。不要用当前ACTIVE模型回测它训练期内的旧日期。
TopkDropout内部取前会话信号，避免双重平移。close标签IC与next-open净组合收益分别披露。
报告实际IC/RankIC、分组/稳定性、净收益、最大回撤、换手、费用和基准；保留偏差/PIT/复权限制。
同规格新run_id重跑，不覆盖旧实验；负结果也属于有效研究交付。

配置/连接详见交接正文，不含密码：集群数据根
`/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade`，状态根`/root/qtrade-local`；
研究Python用`qlib-env/bin/python3.11`和Qlib0.9.7，不混装API环境。
同机可import既有 `E:/QTrade-temp/authorized_cluster_read.py` 的connect入口；不要读取其正文、
打印密钥或复制到Git。NFS预留100GiB、本地2GiB，新大报告放独立NFS研究目录。

保持LIVE_TRADING_ENABLED=false、rolling OFF，不激活/替换ACTIVE、不重启owner、
不恢复/重放旧PAPER授权，不补历史会话/成交/价格，不连接实盘。Paper Trading是后续独立路径。
修改共享worker会影响旧20文件来源指纹，先做显式执行release兼容审计，不能删旧校验。
公网目前仍陈旧，但合格冻结版离线研究可以继续。

如并行，普通子agent用GPT-6.1-sol xhigh；浏览器操作用GPT-6.1 Terra high子agent。
先给出你核实的当前状态和M0/M1的具体执行步骤，随后推进实际研究，不重新展开高级发布协议。

---

备注：旧Desktop/step2.txt虽不在原位置，但指南已归档plans/STEP2_USER_GUIDE.txt。
保留原文，旧“先全部STEP1”的顺序已被本次最新指令替代，不需要等待文件重传。
