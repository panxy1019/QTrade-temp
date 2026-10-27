Browser tab: 1, Title: "QTrade 量化研究平台", URL: "https://q-trade-temp.vercel.app/#/experiments".
0 AXWebArea QTrade 量化研究平台, URL: q-trade-temp.vercel.app/#/experiments
	1 container root
		2 container
			3 text Q QTrade 量化研究平台
			4 menu
				5 area-chart 总览
					6 image area-chart
					7 text 总览
				8 stock 行情中心
					9 image stock
					10 text 行情中心
				11 thunderbolt 策略中心
					12 image thunderbolt
					13 text 策略中心
				14 fund 策略研究
					15 image fund
					16 text 策略研究
				17 experiment 实验中心
					18 image experiment
					19 text 实验中心
				20 line-chart 回测分析
					21 image line-chart
					22 text 回测分析
				23 experiment 在线模型
					24 image experiment
					25 text 在线模型
				26 thunderbolt 每日研究信号
					27 image thunderbolt
					28 text 每日研究信号
				29 database 历史工作区
					30 image database
					31 text 历史工作区
				32 pie-chart 模拟交易
					33 image pie-chart
					34 text 模拟交易
				35 bar-chart 持仓
					36 image bar-chart
					37 text 持仓
				38 reconciliation 委托与成交
					39 image reconciliation
					40 text 委托与成交
				41 safety-certificate 风控中心
					42 image safety-certificate
					43 text 风控中心
				44 database 数据中心
					45 image database
					46 text 数据中心
				47 monitor 系统监控
					48 image monitor
					49 text 系统监控
			50 text 实盘模式不可用
			51 text 仅回测 / 模拟 / 影子
			52 image left
		53 container
			54 text Qlib 研究 公开研究结果 · 登录后提交研究 各业务分区按自身时间与身份刷新 深色模式
			55 switch 1
		56 container
			57 heading 实验中心, Value: 2
				58 text 实验中心
			59 text Qlib 日频研究
			60 button 刷新研究目录
			61 text 查看研究任务进度与原日期归档；已完成历史结果到回测分析对照，前向目标和后来观察到模拟交易查看。 任务由集群串行执行，状态约每五分钟发布；网页每15秒核对。旧ETF和75股分钟研究已移至历史工作区。
			62 text 研究账户
			63 container
				64 container
					65 text * 研究账户 :
				66 text field (settable) * 研究账户 :, Value: <redacted>
				67 container
					68 text * 研究密码 :
				69 container
					70 text field (settable) * 研究密码 :, Value: <redacted>
					71 checkbox Description: Show, Value: 0
						72 image eye-invisible
				73 button 登 录
			74 text 这是本平台独立研究账户，与Vercel管理账户和券商账户分开。密码不写入浏览器本地实验。
			75 text 集群执行器
			76 container
				77 container 状态 : IDLE
					78 text 状态
					79 text :
					80 text IDLE
				81 container 状态快照心跳 : 2026-10-27T14:57:07.939693+00:00
					82 text 状态快照心跳
					83 text :
					84 text 2026-10-27T14:57:07.939693+00:00
				85 container 并发 : 1；任务最长60分钟，异常中断不自动重跑
					86 text 并发
					87 text :
					88 text 1；任务最长60分钟，异常中断不自动重跑
			89 container
				90 table
					91 row
						92 cell
							93 text 实验
						94 cell
							95 text 模型
						96 cell
							97 text 状态
						98 cell
							99 text 历史测试区间 / 完成时间
						100 cell
							101 text 历史累计收益
						102 cell
							103 text 研究结论
						104 cell
							105 text 操作
					106 row
						107 cell
							108 button (collapsed) Description: Expand row, Secondary Actions: Expand
						109 cell
							110 text 原始固定基线（对照档案）
						111 cell
							112 text lightgbm
						113 cell
							114 text 归档完成
						115 cell
							116 text 原区间见档案；完成 2026-10-25T13:24:53.254558+00:00
						117 cell
							118 text 111.29%
						119 cell
							120 text 非PIT诊断·不可晋级
						121 cell
							122 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=alpha158-lightgbm-daily-v1-beb391a43169a5f0
					123 row
						124 cell
							125 button (expanded) Description: Collapse row, Secondary Actions: Collapse
						126 cell
							127 text unified-bound-ridge-repair-20261027
						128 cell
							129 text linear
						130 cell
							131 text 归档完成
						132 cell
							133 text 2024-01-02 至 2026-10-27；完成 2026-10-27T14:49:00.859292+00:00
						134 cell
							135 text 86.66%
						136 cell
							137 text 非PIT诊断·不可晋级
						138 cell
							139 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=713e9f4de8bb456aa7fc906bf636dce6
					140 row
						141 cell
							142 container
								143 text 实验ID： 713e9f4de8bb456aa7fc906bf636dce6
								144 button Copy
									145 image copy
							146 container
								147 text 规格哈希： 4faa626e5da6cbc552105e4892987562c31bed4a6b6aea69e9af12d622e130c6
								148 button Copy
									149 image copy
							150 container
								151 text 代码： 096387b8088e4361c9e5cd9cd2c165e21819fead
								152 button Copy
									153 image copy
							154 content list
								155 container
									156 text 2026-10-27T14:31:52.191455+00:00 · 等待集群接收
								157 container
									158 text 2026-10-27T14:42:03.236775+00:00 · 执行中
								159 container
									160 text 2026-10-27T14:43:21.190738+00:00 · 校验并转换数据
								161 container
									162 text 2026-10-27T14:44:42.906429+00:00 · 计算特征
								163 container
									164 text 2026-10-27T14:46:26.844523+00:00 · 训练模型
								165 container
									166 text 2026-10-27T14:46:50.636109+00:00 · 组合回测
								167 container
									168 text 2026-10-27T14:51:01.144375+00:00 · 归档完成
							169 container
								170 text 数据与交易约束审计
								171 container
									172 text 非完整历史PIT 不可晋级 BIASED_CURRENT_SAMPLE
								173 text 新工作流训练/排名/投资范围对齐，使用完成历史做动态资格与流动性选择；样本本身的幸存者偏差尚未消除。
								174 container
									175 text •  当前流动性样本回投历史，幸存者与筛选偏差尚未消除
								176 container
									177 text •  已查看历史的事后研究，不是新的未见前向证据
								178 container
									179 text •  历史ST及精确涨跌停尚未完整认证
								180 container
									181 text •  缺行情不等于已认证停牌原因
								182 container
									183 text •  供应商复权尚未与独立公司行为账本对账
								184 container
									185 text •  开盘价及前日量容量是研究近似，未重放集合竞价
								186 container
									187 text •  回填状态只增加研究限制，历史可得时间未认证；ST禁买是研究政策
								188 container
									189 text 本实验冻结的PIT修复覆盖： 状态回填990/990股、2,168,788行；来源主表9,022条。尚未用于精确规则回测，历史可得性待核验。  完整来源摘要随实验JSON导出。
								190 container
									191 text 已参与本次回测的附加状态限制：原始行情对账 2,141,014 根，覆盖 990 股；来源冲突 8,275 根双向限制。新增禁买 28,646 根、禁卖 8,130 根。缺状态 10,490 根继续保留原近似限制，不能认定为已认证可交易。
								192 container
									193 text 以上为输入库全历史的掩码计数，不是测试期委托或实际成交次数。来源快照： 467366395184edc65dd2ac079982393efdd516302302604c0a0fbfce664ef449 。回填为事后诊断；ST禁买是研究政策，精确涨跌停与完整历史PIT仍未认证。逐股票逐日账本保存在集群归档中。
							194 button download 导出实验记录
								195 container download
								196 text 导出实验记录
					197 row
						198 cell
							199 button (collapsed) Description: Expand row, Secondary Actions: Expand
						200 cell
							201 text unified-bound-lightgbm-repair-20261027
						202 cell
							203 text lightgbm
						204 cell
							205 text 归档完成
						206 cell
							207 text 2024-01-02 至 2026-10-27；完成 2026-10-27T14:38:52.662312+00:00
						208 cell
							209 text 102.48%
						210 cell
							211 text 非PIT诊断·不可晋级
						212 cell
							213 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=8377ca63d95f45b18593e23f1332ad51
					214 row
						215 cell
							216 button (collapsed) Description: Expand row, Secondary Actions: Expand
						217 cell
							218 text unified-bound-ridge-api-20261027
						219 cell
							220 text linear
						221 cell
							222 text 已取消
						223 cell
							224 text 2024-01-02 至 2026-10-27；完成 尚未完成
						225 cell
							226 text —
						227 cell
							228 text 非PIT诊断·不可晋级
					229 row
						230 cell
							231 button (collapsed) Description: Expand row, Secondary Actions: Expand
						232 cell
							233 text unified-bound-lightgbm-20261027
						234 cell
							235 text lightgbm
						236 cell
							237 text 运行失败
						238 cell
							239 text 2024-01-02 至 2026-10-27；完成 尚未完成
						240 cell
							241 text —
						242 cell
							243 text 非PIT诊断·不可晋级
					244 row
						245 cell
							246 button (collapsed) Description: Expand row, Secondary Actions: Expand
						247 cell
							248 text archived-compare-lightgbm-20261026
						249 cell
							250 text lightgbm
						251 cell
							252 text 归档完成
						253 cell
							254 text 2024-01-02 至 2026-10-23；完成 2026-10-26T06:35:44.712270+00:00
						255 cell
							256 text 117.23%
						257 cell
							258 text 非PIT诊断·不可晋级
						259 cell
							260 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=a76123ab31f24bf3a347e347e3ba9161
					261 row
						262 cell
							263 button (collapsed) Description: Expand row, Secondary Actions: Expand
						264 cell
							265 text source-bound-lightgbm-prod-20261026
						266 cell
							267 text lightgbm
						268 cell
							269 text 归档完成
						270 cell
							271 text 2024-01-02 至 2026-10-23；完成 2026-10-26T04:49:23.941900+00:00
						272 cell
							273 text 117.23%
						274 cell
							275 text 非PIT诊断·不可晋级
						276 cell
							277 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=4fc474f9f5f94199914b5c8802eb5127
					278 row
						279 cell
							280 button (collapsed) Description: Expand row, Secondary Actions: Expand
						281 cell
							282 text source-bound-ridge-prod-20261026
						283 cell
							284 text linear
						285 cell
							286 text 归档完成
						287 cell
							288 text 2024-01-02 至 2026-10-23；完成 2026-10-26T04:45:06.367093+00:00
						289 cell
							290 text 89.00%
						291 cell
							292 text 非PIT诊断·不可晋级
						293 cell
							294 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=6f04b986eaeb454c8c61579f1cea266b
					295 row
						296 cell
							297 button (collapsed) Description: Expand row, Secondary Actions: Expand
						298 cell
							299 text production-e2e-lightgbm-20261026
						300 cell
							301 text lightgbm
						302 cell
							303 text 归档完成
						304 cell
							305 text 2024-01-02 至 2026-10-23；完成 2026-10-26T03:39:31.946817+00:00
						306 cell
							307 text 122.43%
						308 cell
							309 text 非PIT诊断·不可晋级
						310 cell
							311 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=316ff5642f3344d588c8de2646598314
					312 row
						313 cell
							314 button (collapsed) Description: Expand row, Secondary Actions: Expand
						315 cell
							316 text production-login-acceptance-20261026
						317 cell
							318 text lightgbm
						319 cell
							320 text 已取消
						321 cell
							322 text 2024-01-02 至 2026-10-23；完成 尚未完成
						323 cell
							324 text —
						325 cell
							326 text 非PIT诊断·不可晋级
				327 content list
					328 container Previous Page
						329 button (disabled) left
							330 image left
					331 text 1
					332 text 2
					333 container Next Page
						334 button right
							335 image right

The focused UI element is 125 button (expanded) Description: Collapse row, Secondary Actions: Collapse