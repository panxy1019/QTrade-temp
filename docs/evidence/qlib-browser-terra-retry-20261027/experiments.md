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
			56 text Qlib 研究 公开研究结果 · 登录后提交研究 各业务分区按自身时间与身份刷新 深色模式
			55 switch 1
		57 container
			58 heading 实验中心, Value: 2
				59 text 实验中心
			60 text Qlib 日频研究
			61 button 刷新研究目录
			62 text 查看研究任务进度与原日期归档；已完成历史结果到回测分析对照，前向目标和后来观察到模拟交易查看。 任务由集群串行执行，状态约每五分钟发布；网页每15秒核对。旧ETF和75股分钟研究已移至历史工作区。
			63 text 研究账户
			64 container
				65 container
					66 text * 研究账户 :
				67 text field (settable) * 研究账户 :, Value: <redacted>
				68 container
					69 text * 研究密码 :
				70 container
					71 text field (settable) * 研究密码 :, Value: <redacted>
					72 checkbox Description: Show, Value: 0
						73 image eye-invisible
				74 button 登 录
			75 text 这是本平台独立研究账户，与Vercel管理账户和券商账户分开。密码不写入浏览器本地实验。
			76 text 集群执行器
			77 container
				78 container 状态 : IDLE
					79 text 状态
					80 text :
					81 text IDLE
				82 container 状态快照心跳 : 2026-10-27T14:57:07.939693+00:00
					83 text 状态快照心跳
					84 text :
					85 text 2026-10-27T14:57:07.939693+00:00
				86 container 并发 : 1；任务最长60分钟，异常中断不自动重跑
					87 text 并发
					88 text :
					89 text 1；任务最长60分钟，异常中断不自动重跑
			90 container
				91 table
					92 row
						93 cell
							94 text 实验
						95 cell
							96 text 模型
						97 cell
							98 text 状态
						99 cell
							100 text 历史测试区间 / 完成时间
						101 cell
							102 text 历史累计收益
						103 cell
							104 text 研究结论
						105 cell
							106 text 操作
					107 row
						108 cell
							109 button (collapsed) Description: Expand row, Secondary Actions: Expand
						110 cell
							111 text 原始固定基线（对照档案）
						112 cell
							113 text lightgbm
						114 cell
							115 text 归档完成
						116 cell
							117 text 原区间见档案；完成 2026-10-25T13:24:53.254558+00:00
						118 cell
							119 text 111.29%
						120 cell
							121 text 非PIT诊断·不可晋级
						122 cell
							123 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=alpha158-lightgbm-daily-v1-beb391a43169a5f0
					124 row
						125 cell
							126 button (collapsed) Description: Expand row, Secondary Actions: Expand
						127 cell
							128 text unified-bound-ridge-repair-20261027
						129 cell
							130 text linear
						131 cell
							132 text 归档完成
						133 cell
							134 text 2024-01-02 至 2026-10-27；完成 2026-10-27T14:49:00.859292+00:00
						135 cell
							136 text 86.66%
						137 cell
							138 text 非PIT诊断·不可晋级
						139 cell
							140 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=713e9f4de8bb456aa7fc906bf636dce6
					141 row
						142 cell
							143 button (collapsed) Description: Expand row, Secondary Actions: Expand
						144 cell
							145 text unified-bound-lightgbm-repair-20261027
						146 cell
							147 text lightgbm
						148 cell
							149 text 归档完成
						150 cell
							151 text 2024-01-02 至 2026-10-27；完成 2026-10-27T14:38:52.662312+00:00
						152 cell
							153 text 102.48%
						154 cell
							155 text 非PIT诊断·不可晋级
						156 cell
							157 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=8377ca63d95f45b18593e23f1332ad51
					158 row
						159 cell
							160 button (collapsed) Description: Expand row, Secondary Actions: Expand
						161 cell
							162 text unified-bound-ridge-api-20261027
						163 cell
							164 text linear
						165 cell
							166 text 已取消
						167 cell
							168 text 2024-01-02 至 2026-10-27；完成 尚未完成
						169 cell
							170 text —
						171 cell
							172 text 非PIT诊断·不可晋级
					173 row
						174 cell
							175 button (collapsed) Description: Expand row, Secondary Actions: Expand
						176 cell
							177 text unified-bound-lightgbm-20261027
						178 cell
							179 text lightgbm
						180 cell
							181 text 运行失败
						182 cell
							183 text 2024-01-02 至 2026-10-27；完成 尚未完成
						184 cell
							185 text —
						186 cell
							187 text 非PIT诊断·不可晋级
					188 row
						189 cell
							190 button (collapsed) Description: Expand row, Secondary Actions: Expand
						191 cell
							192 text archived-compare-lightgbm-20261026
						193 cell
							194 text lightgbm
						195 cell
							196 text 归档完成
						197 cell
							198 text 2024-01-02 至 2026-10-23；完成 2026-10-26T06:35:44.712270+00:00
						199 cell
							200 text 117.23%
						201 cell
							202 text 非PIT诊断·不可晋级
						203 cell
							204 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=a76123ab31f24bf3a347e347e3ba9161
					205 row
						206 cell
							207 button (collapsed) Description: Expand row, Secondary Actions: Expand
						208 cell
							209 text source-bound-lightgbm-prod-20261026
						210 cell
							211 text lightgbm
						212 cell
							213 text 归档完成
						214 cell
							215 text 2024-01-02 至 2026-10-23；完成 2026-10-26T04:49:23.941900+00:00
						216 cell
							217 text 117.23%
						218 cell
							219 text 非PIT诊断·不可晋级
						220 cell
							221 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=4fc474f9f5f94199914b5c8802eb5127
					222 row
						223 cell
							224 button (collapsed) Description: Expand row, Secondary Actions: Expand
						225 cell
							226 text source-bound-ridge-prod-20261026
						227 cell
							228 text linear
						229 cell
							230 text 归档完成
						231 cell
							232 text 2024-01-02 至 2026-10-23；完成 2026-10-26T04:45:06.367093+00:00
						233 cell
							234 text 89.00%
						235 cell
							236 text 非PIT诊断·不可晋级
						237 cell
							238 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=6f04b986eaeb454c8c61579f1cea266b
					239 row
						240 cell
							241 button (collapsed) Description: Expand row, Secondary Actions: Expand
						242 cell
							243 text production-e2e-lightgbm-20261026
						244 cell
							245 text lightgbm
						246 cell
							247 text 归档完成
						248 cell
							249 text 2024-01-02 至 2026-10-23；完成 2026-10-26T03:39:31.946817+00:00
						250 cell
							251 text 122.43%
						252 cell
							253 text 非PIT诊断·不可晋级
						254 cell
							255 link Description: 分析结果, Value: q-trade-temp.vercel.app/#/backtest?experiment=316ff5642f3344d588c8de2646598314
					256 row
						257 cell
							258 button (collapsed) Description: Expand row, Secondary Actions: Expand
						259 cell
							260 text production-login-acceptance-20261026
						261 cell
							262 text lightgbm
						263 cell
							264 text 已取消
						265 cell
							266 text 2024-01-02 至 2026-10-23；完成 尚未完成
						267 cell
							268 text —
						269 cell
							270 text 非PIT诊断·不可晋级
				271 content list
					272 container Previous Page
						273 button (disabled) left
							274 image left
					275 text 1
					276 text 2
					277 container Next Page
						278 button right
							279 image right

The focused UI element is 17 experiment 实验中心