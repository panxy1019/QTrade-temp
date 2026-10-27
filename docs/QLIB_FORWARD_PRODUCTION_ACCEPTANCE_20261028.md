# 2026-10-28 正式网页与前向链路验收

截至上海时间01:22，**获准的定向交接已完成，正式网站已更新，6本当前PAPER运行，
2本退休账本停止。全STEP1仍未完成：当前模型首个完整前向日与本轮浏览器视觉验收待补。**

01:58补充：另已完成[历史版本选择的真实研究验收](QLIB_HISTORICAL_VERSION_RESEARCH_20261028.md)，
旧版Ridge任务、正式归档、恢复与公开复用规格均通过。它不改变本页的跨日或视觉未完成项。

## 实际运行的版本

| 范围 | 版本与实际效果 |
| --- | --- |
| GitHub / Vercel 正式网页 | PR34已合并，main `ae33a7c8c27ea721eb3be1e4430b562a44b67f91`；默认域名的实际deployment与相邻捕获一致 |
| 集群前向服务 | 源码 `9846a7f30774d6ae4b4d41f02b493c5246be87d9`，新owner383859，无initialize-paper；582份文件与Git对象一致 |
| 工作流、日预测与signals | 执行来源仍为已单独核验的096387b；ACTIVE LightGBM为29589b44，没有用网页版本冒充集群运行版本 |
| 正式数据 | 10月27日版本09a67b13；990历史/987当日合格/891支持池/888预测分数各有不同含义 |
| 保护边界 | LIVE=false、rolling OFF；原恢复claim均保持已消费，退休模型不恢复 |

默认页面为[QTrade](https://q-trade-temp.vercel.app/#/paper)，Vercel项目管理地址不用于展示研究页面。

## 已通过的生产检查

1. **01:15:08公开C链路通过。** 已独立回放的正式版本、当前批准模型预测和signals，
   与默认生产API和实际mainae33匹配；保留各响应的原始字节、时钟、header与哈希。
   该通过明确不包括候选later-version、前向账本、ETF或浏览器。
2. **14本前向控制状态已送达默认域名。** `service_fresh=true`、`RUNNING`、`errors=[]`；
   5基线+当前LightGBM的6本PAPER均NORMAL/running且无待恢复；原模型2本保留但停止。
   所有冻结目标、准入、会话、成交与旧恢复历史保持不变。
3. **01:15:20全池公开传输通过。** 固定公开commit为
   `f810733b40bfa6bc220417c6e38e99d1bf6e72a0`；1500证券身份、3000索引项、目录/索引Git
   字节与5条固定行情样本一致。索引/目录年龄124.22/137.90秒，状态FRESH_TRANSPORT。
   日线1497/1500、五分钟1496/1500继续标为PARTIAL，不能据此认证全池/全天SLA。
4. **网页新内容已实际交付。** 保存真实HTML、入口JS、前向页面chunk的字节哈希及相邻
   相同deployment，实际chunk包含“全部账本保留”“退休模型账本已停止”及
   “保持停止，不自动恢复”。这证明新构建文件送达，尚未证明浏览器正确渲染。

正式C的[不可变结果](evidence/qlib-forward-handover-20261028/production-ae33/C-public/C-public.json)、
[14本控制核验](evidence/qlib-forward-handover-20261028/production-ae33/stock-control/control-delivery-verification.json)、
[1500全池探针](evidence/qlib-forward-handover-20261028/production-ae33/handover-market-ae33-20261027T171504Z-ecb9a4.json)
及[静态交付记录](evidence/qlib-forward-handover-20261028/production-ae33/asset-content-verification.json)
分别证明上述范围，不相互替代。

## 尚未通过与原因

**完整14本日审计为BLOCKED。** 10本基线的10月27日观察和2本退休关闭凭证通过；
当前模型的PAPER/SHADOW没有10月27日会话，因为其激活发生在当日收盘后。
冻结目标面向10月28日。不得补造激活前历史，也不能将退休关闭当作新日观察。

**浏览器视觉仍未通过。** 按用户要求均由Terra high子agent操作：旧Chrome扩展报
`nodeRepl.fetch request failed`；重置后直接新建IAB报`Browser is not available: iab`。
没有取得本轮DOM或截图，不表示网站故障。退休标注、全部账本可见性、页面交互及控制台
的视觉确认继续保留，见[原始失败报告](evidence/qlib-forward-handover-20261028/production-ae33/browser-terra-report.md)。

历史PIT、复权、ST/停牌/精确限价和多年分钟连续覆盖仍未独立认证。零成交和纯现金
也不能认证交易限制已经完备，每份实验继续显示这些限制。

## 后续顺序

1. 保持当前服务稳定，等待10月28日真实交易与收盘数据；未知交易状态仍阻断新风险。
2. 验证当日采集、质量、正式不可变日版本，再检查批准模型预测、signals和新候选的
   真正later-version验证。已有C通过不应改写为新日通过，新模型不自动授予PAPER恢复。
3. 检查当前12本的当日观察和退休2本关闭证明，逐本核对冻结目标、阻断、现金/持仓/
   成交/估值，再捕获实际生产字节做公开审计；零成交必须有真实阻断或无交易证据。
4. 浏览器通道可用后完成视觉检查；正常等待不重复创建账户、不重放已消费请求、不以
   改时钟或清除失败状态制造通过。
5. 并行继续有限队列回补、历史成员/退市证据与交易约束治理。当前封存队列76,824条、
   独立退市192条是队列记录数，不能理解为全部历史数据量；空间仍保留100GiB。
6. STEP1实际门禁齐备后，再按用户STEP2指南推进因子Registry、独立评价、统一对照、
   版本生命周期与受控Strategy Builder，而后扩展模型/特征及文本事件因子。

已创建本聊天的有限收盘回访`qtrade-10-28`，计划在10月28日16:40至23:40每小时检查，
最多8次。正常等待不重复通知，达成目的、过时或次数结束后删除；次数结束不代表验收完成。
这是本机应用中的回访，需电脑开机且应用运行，参见[官方说明](https://learn.chatgpt.com/docs/automations?surface=app)。
集群中的采集、回补、研究与发布服务独立运行，不依赖本机的这项回访。

## 只读复现

使用新文件名，公开检查采用实际时钟。全池探针不会改变集群或账户：

```powershell
$env:PYTHONPATH='src;.'
python -B -m scripts.probe_unified_publication --expected-production-revision ae33a7c8c27ea721eb3be1e4430b562a44b67f91 --report E:/QTrade-temp/market-check-new.json
```

集群日审计在已验证源码目录执行，PUBLIC_CAPTURE须为重新获取并验签的内层publication，
同时保留其原始外层HTTP和实际deployment绑定；OUTPUT使用新的不可变路径：

```bash
LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src:$PWD" python -B \
  -m scripts.audit_qlib_forward_transition \
  --base "$B" --local-root /root/qtrade-local/forward-research \
  --published-forward "$PUBLIC_CAPTURE" \
  --approved-model-id 29589b4486a09159eb3a07153c54fba609fbc80b52c168dd665cd46660be8487 \
  --model-database /root/qtrade-local/online-models.sqlite \
  --day 2026-10-28 --output "$OUTPUT"
```

收盘或数据条件未满足时应保留BLOCKED。已完成的生产handover/recover不得作为复现命令重跑。
