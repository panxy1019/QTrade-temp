# 12｜来源、核对范围与已知界面差异

[返回学习目录](README.md)

## 1. 本手册依据

核对于 2026-10-25，北京时间。业务源码基于提交 381b9e095f3c56f16fc06f8e678c611b08ba66c6；Qlib 实际实验源码提交为 207a6b7a4eb324956139a98529110f166039095f。二者承担网页与研究运行的不同身份。

| 依据 | 本次做了什么 | 能支持什么 |
|---|---|---|
| 线上页面 | 查看策略中心、策略研究，切换底部十个页签 | 实际文字、指标、状态和布局 |
| 默认分钟预览 | 运行一次默认三连阳、5bp、T+1开盘 | 已计算结果和运行后门控组件 |
| 104候选按钮 | 尝试一次，前端拒绝响应 | 当前该路径未通过，不能写为成功展示 |
| 页面代码 | 核对表格、表单、条件组件、状态映射 | 当前实现设计及隐藏条件区 |
| 后端与研究代码 | 核对公式、时间标签、标签、评价和归档 | 指标真正算法，不只采用名称推测 |
| 正式文档与配置 | 核对数据范围、固定基线、现有限制 | 运行口径与可复现身份 |
| 官方资料与原始论文 | 核对Qlib、树模型、统计方法 | 原理背景；不代替本项目实现核对 |

本次没有运行新的正式基线、保存本地实验、注册策略、启动PAPER/SHADOW/LIVE、修改交易路径或替你确定后续交付计划。分钟预览是既有已查看历史上的只读教学操作。

## 2. 截图与文字证据

| 文件 | 内容 | 使用位置 |
|---|---|---|
| 01-research-qlib-overview.jpg | Qlib数据、指标与曲线局部 | 05 |
| 02-strategy-center.jpg | 策略中心完整页面 | README、02 |
| 03-research-page-full.jpg | 未运行预览前的研究页全景 | 03 |
| 04-intraday-metrics.jpg | 本次默认分钟预览绩效与统计 | 06 |
| 05-discovery-contract-rejected.jpg | 104候选响应拒绝及默认控件 | 06 |
| 06-factor-search.jpg | 旧日频因子搜索 | 03 |
| 07-forward-incomplete.jpg | 旧前向1/20与局部通过项 | 10 |

图片位于 assets；同目录 DOM 文本是实际查看时的网页结构证据，不是人为编造截图。宽表格需要横向滚动，本手册用文字列出完整字段。全景图较长，适合单独打开放大。

## 3. 本次发现并已在正文区分的差异

| 项目 | 已确认情况 | 本手册如何处理 |
|---|---|---|
| 104候选挖掘 | 响应身份/安全契约未通过前端验证 | 标为线上实测失败；条件结果区只解释源码设计 |
| 方向草案比较符 | 草案可能写>=，引擎实际使用> | 解释恰等于阈值的边界，不替换真实规则 |
| 策略中心最大回撤 | 原始小数直接展示 | 给出乘100的转换例子 |
| Qlib每日最多替换5文字 | 常态淘汰参数5；初始建仓与空缺补仓另有数量 | 精确说明n_drop而不将文案当总买单上限 |
| 分钟10:05标签 | 结束标签；开盘价属于约10:00区间起点 | 把标签与区间分开，披露成交近似 |
| 胜率、换手、敞口与成本 | 不同模块有不同输入/单位 | 在07中分开定义 |
| 前向来源说明0/20 | 实际正式旧进度已为1/20，局部门可能为绿 | 以实际计数与INCOMPLETE解释，记录旧文案差异 |
| 全局绿色新鲜度与旧日期 | 评价不同数据/实验对象 | 分开数据、发布、实验时钟 |
| 因子目录7与Alpha158 | 两个不同集合 | 不解释为仅有7特征，也不叫158条有效策略 |
| 配置目标与完成数 | 日频基线接受990/1000；分钟400是目标 | 不把配置目标当成实际已完成数量 |
| 11条旧候选、14条归档和Qlib | 不同目录与归档流程 | 不强行相加后称为注册策略数 |
| “签名”文案 | 部分采用内容哈希封装 | 不冒充权威来源或公钥认证 |
| 模板载入入口 | 源码中当前工作区initialTemplateId=null，旧版入口才解析URL模板参数 | 已记录实现差异；本次端到端浏览器复查超时，未宣称实测修复 |
| SHADOW命名 | 前向证据名称与PAPER→SHADOW状态序列并列 | 不自行推断审批先后，需在未来运行工程中统一合同 |

104候选拒绝的具体根因本次未定位，不能推断哪一端已算错；也没有绕过契约校验。该项适合后续单独列为修复验收任务，由你学习后安排。

部分旧页面在报告缺失时使用数字0作显示回退；因此看到0必须先确认数据对象可用，而不是把“缺少报告”解释成“真实收益恰好零”。

## 4. 不是本次验收结论的内容

- 没有完整验证所有模板载入路径、每一种自定义方向和退出组合。
- 没有在浏览器建立2—8条本地候选再逐一回放相关性与Holm流程；它们依据代码/契约解释。
- 没有因为某个局部门通过而重新认定策略有效。
- 没有重新认证历史ST、精确涨跌停、停牌原因、公司行为与退市处理。
- 没有证明Qlib和分钟事件组合可以按相同成交条件直接比较。
- 没有将现有旧前向合同计数自动补齐到今天。

## 5. 项目源码入口

| 主题 | 文件 |
|---|---|
| 策略/研究/实验页面 | [DataPages.tsx](../../web/src/pages/DataPages.tsx) |
| 模板与API合同 | [app.py](../../src/qtrade/api/app.py) |
| 因子目录 | [factorEvidenceCatalog.ts](../../web/src/research/factorEvidenceCatalog.ts) |
| 分钟完整工作台 | [IntradayFactorResearchLab.tsx](../../web/src/components/IntradayFactorResearchLab.tsx) |
| 分钟研究公式 | [intraday_factor.py](../../src/qtrade/research/intraday_factor.py) |
| 绩效/因子统计 | [metrics.py](../../src/qtrade/research/metrics.py) |
| 日频组合与回测 | [multi_asset.py](../../src/qtrade/research/multi_asset.py) |
| 完整实验规格 | [runner.py](../../src/qtrade/research/runner.py) |
| 策略注册与血缘 | [registry.py](../../src/qtrade/strategies/registry.py) |
| Qlib配置 | [qlib_daily_baseline.json](../../configs/qlib_daily_baseline.json) |
| Qlib训练、预测与指标 | [run_qlib_baseline.py](../../scripts/run_qlib_baseline.py) |
| Qlib数据转换 | [qlib_adapter.py](../../src/qtrade/research/qlib_adapter.py) |
| Qlib网页 | [QlibBaseline.tsx](../../web/src/components/QlibBaseline.tsx) |
| 当前数据入口 | [CurrentWorkspace.tsx](../../web/src/components/CurrentWorkspace.tsx) |
| 即时回测与规格草案 | [BacktestLab.tsx](../../web/src/components/BacktestLab.tsx) |
| 通用曲线显示 | [EquityChart.tsx](../../web/src/charts/EquityChart.tsx) |

## 6. 可继续阅读的一手资料

以下用于原理核对；本项目没有因为引用某库文档就安装或直接调用该库的所有方法。

1. [Qlib工作流](https://qlib.readthedocs.io/en/latest/component/workflow.html)：配置化数据、模型、评价与产物记录。
2. [Qlib组合策略](https://qlib.readthedocs.io/en/latest/component/strategy.html)：预测与组合是不同组件。
3. [Qlib v0.9.7 Alpha158源码](https://github.com/microsoft/qlib/blob/v0.9.7/qlib/contrib/data/loader.py)：核对特征，优先看锁定版本。
4. [Qlib v0.9.7 TopkDropout源码](https://github.com/microsoft/qlib/blob/v0.9.7/qlib/contrib/strategy/signal_strategy.py)：持仓、更替与资金分配。
5. [LightGBM原理](https://lightgbm.readthedocs.io/en/stable/Features.html)：提升树、直方图与模型特点；网站文档可能高于本项目4.6版本。
6. [HAC/Newey–West参考](https://www.statsmodels.org/stable/generated/statsmodels.stats.sandwich_covariance.cov_hac.html)：误差相关性背景。
7. [Holm与多重检验参考](https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html)：不同错误控制目标。
8. [Stationary Bootstrap参考](https://bashtage.github.io/arch/bootstrap/generated/arch.bootstrap.StationaryBootstrap.html)：时间序列块重采样。
9. [The Probability of Backtest Overfitting原始论文](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)：CSCV/PBO的研究问题与限制。

英文官方文档可先只读概述与参数；不要把其示例收益或默认成本直接搬成你的研究结论。

## 7. 文档校验与复查方法

本次校验检查章节/源码/图片链接是否存在、图片文件格式与尺寸、章节覆盖、关键教学计算、运行配置字段以及业务文件是否被修改。结果保存在 verification.json，文件摘要保存在 SHA256SUMS.txt。

文档不是业务实现变更，没有为它重跑整套后端/前端策略测试；既有测试通过情况属于原基线验收，不冒充这次新跑过。

在仓库根目录复查已交付文件摘要的PowerShell命令：

    Get-Content docs/learning/SHA256SUMS.txt | ForEach-Object {
        $learningDigest, $learningRelative = $_ -split '  ', 2
        $learningPath = Join-Path 'docs/learning' $learningRelative
        $learningActual = (Get-FileHash -Algorithm SHA256 -LiteralPath $learningPath).Hash.ToLower()
        if ($learningActual -ne $learningDigest) { throw "Learning file changed: $learningRelative" }
    }

阅读入口为docs/learning/README.md；本地校验统计记录为docs/learning/verification.json。哈希复查只证明文件与这次交付一致，不评价未来网页是否已发生变化。

以后修改网页时，可以按以下顺序复查：更新截图→核对字段单位和时序→更新组件索引→核对公式与代码→检查链接和图片→标记核对日期。若数据或实验口径变更，应保存新版本身份，避免只改文字。
