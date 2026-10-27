Browser tab: 1, Title: "QTrade 量化研究平台", URL: "https://q-trade-temp.vercel.app/#/research".
0 AXWebArea QTrade 量化研究平台, URL: q-trade-temp.vercel.app/#/research
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
			57 heading 策略研究, Value: 2
				58 text 策略研究
			59 text Qlib 日频研究
			60 button 刷新研究目录
			61 text 冻结数据、时间切分、模型、组合与成本后，提交集群任务。 任务由集群串行执行，状态约每五分钟发布；网页每15秒核对。旧ETF和75股分钟研究已移至历史工作区。
			62 text 研究账户
			63 container
				64 container
					65 text * 研究账户 :
				66 text field (settable) * 研究账户 :, Value: <redacted>
				67 container
					68 text * 研究密码 :
				69 container
					70 text field (settable) * 研究密码 :
					71 checkbox Description: Show, Value: 0
						72 image eye-invisible
				73 button 登 录
			74 text 这是本平台独立研究账户，与Vercel管理账户和券商账户分开。密码不写入浏览器本地实验。
			75 text 研究输入
			76 container
				77 text qlib-version-09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407  ·  990 股输入库存 ·  2,159,779 根日线 · 截至 2026-10-27 。训练和排名排除科创板，实际动态资格数量以实验审计为准。
			78 text 历史状态来源快照将在表单初始化时固定；后续回填不会改变这份任务。需要比较模型时，请使用同一来源快照。
			79 container
				80 text 数据与交易约束审计
				81 container
					82 text 非完整历史PIT 不可晋级 BIASED_CURRENT_SAMPLE
				83 text 新工作流训练/排名/投资范围对齐，使用完成历史做动态资格与流动性选择；样本本身的幸存者偏差尚未消除。
				84 container
					85 text •  当前流动性样本回投历史，幸存者与筛选偏差尚未消除
				86 container
					87 text •  已查看历史的事后研究，不是新的未见前向证据
				88 container
					89 text •  历史ST及精确涨跌停尚未完整认证
				90 container
					91 text •  缺行情不等于已认证停牌原因
				92 container
					93 text •  供应商复权尚未与独立公司行为账本对账
				94 container
					95 text •  开盘价及前日量容量是研究近似，未重放集合竞价
			96 text Qlib训练与回测配置
			97 container
				98 container
					99 text 不可变研究数据版本 
					100 container
						101 text 2026-10-27 · READY · qlib-version-09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407
						102 combo box (collapsed) Description: 不可变研究数据版本, Secondary Actions: Expand
					103 image down
				104 container
					105 text 特征集版本 
					106 container
						107 text qlib-0.9.7-Alpha158-v1
						108 combo box (disabled, collapsed) Description: 特征集版本, Secondary Actions: Expand
					109 image down
				110 container
					111 text 已发布策略版本起点 
					112 container
						113 text 从已发布完整合同载入
						114 combo box (collapsed) Description: 已发布策略版本起点, Secondary Actions: Expand
					115 image down
			116 container
				117 text 本任务固定来源： 467366395184edc65dd2ac079982393efdd516302302604c0a0fbfce664ef449
				118 button Copy
					119 image copy
			120 container
				121 container 固定数据版本 : 09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407
					122 text 固定数据版本
					123 text :
					124 text 09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407
				125 container READY 事件 : 69c3430923515d90b93ccb6eba9d49ca667a606b8dcdb3701c59de449ae0bc6c
					126 text READY 事件
					127 text :
					128 text 69c3430923515d90b93ccb6eba9d49ca667a606b8dcdb3701c59de449ae0bc6c
				129 container 支持证券 : 891
					130 text 支持证券
					131 text :
					132 text 891
			133 container
				134 container
					135 text * 实验名称
				136 text field (settable) * 实验名称, Value: alpha158-lightgbm, ID: name
				137 container
					138 text * 预测模型
				139 container
					140 text LightGBM
					141 combo box (collapsed) * 预测模型, Secondary Actions: Expand
					142 image down
				143 container
					144 text * 训练开始
				145 date field (settable) * 训练开始, Value: 2017-04-05, ID: train_start
					146 container
						147 stepper
						148 stepper
						149 stepper
					150 pop up button 显示日期选择器 显示日期选择器
				151 container
					152 text * 训练结束
				153 date field (settable) * 训练结束, Value: 2021-12-28, ID: train_end
					154 container
						155 stepper
						156 stepper
						157 stepper
					158 pop up button 显示日期选择器 显示日期选择器
				159 container
					160 text * 验证开始
				161 date field (settable) * 验证开始, Value: 2022-01-04, ID: valid_start
					162 container
						163 stepper
						164 stepper
						165 stepper
					166 pop up button 显示日期选择器 显示日期选择器
				167 container
					168 text * 验证结束
				169 date field (settable) * 验证结束, Value: 2023-12-26, ID: valid_end
					170 container
						171 stepper
						172 stepper
						173 stepper
					174 pop up button 显示日期选择器 显示日期选择器
				175 container
					176 text * 测试开始
				177 date field (settable) * 测试开始, Value: 2024-01-02, ID: test_start
					178 container
						179 stepper
						180 stepper
						181 stepper
					182 pop up button 显示日期选择器 显示日期选择器
				183 container
					184 text * 测试结束
				185 date field (settable) * 测试结束, Value: 2026-10-27, ID: test_end
					186 container
						187 stepper
						188 stepper
						189 stepper
					190 pop up button 显示日期选择器 显示日期选择器
				191 container
					192 text * 目标持仓Top K
				193 container
					194 stepper (settable, integer) * 目标持仓Top K, Value: 50, ID: topk
					195 button Increase Value
						196 image up
					197 button Decrease Value
						198 image down
				199 container
					200 text * 每日淘汰名额
				201 container
					202 stepper (settable, integer) * 每日淘汰名额, Value: 5, ID: n_drop
					203 button Increase Value
						204 image up
					205 button Decrease Value
						206 image down
				207 container
					208 text * 最少持有交易步
				209 container
					210 stepper (settable, integer) * 最少持有交易步, Value: 2, ID: hold_thresh
					211 button Increase Value
						212 image up
					213 button (disabled) Decrease Value
						214 image down
				215 container
					216 text * 目标股票仓位
				217 container
					218 stepper (settable, float) * 目标股票仓位, Value: 0.949999988079071, Details: 0.95, ID: risk_degree
					219 button (disabled) Increase Value
						220 image up
					221 button Decrease Value
						222 image down
				223 container
					224 text * 买入成本bp
				225 container
					226 stepper (settable, integer) * 买入成本bp, Value: 8, ID: buy_cost_bps
					227 button Increase Value
						228 image up
					229 button Decrease Value
						230 image down
				231 container
					232 text * 卖出成本bp
				233 container
					234 stepper (settable, integer) * 卖出成本bp, Value: 13, ID: sell_cost_bps
					235 button Increase Value
						236 image up
					237 button Decrease Value
						238 image down
				239 container
					240 text * 最低成本元
				241 container
					242 stepper (settable, integer) * 最低成本元, Value: 5, ID: min_cost
					243 button Increase Value
						244 image up
					245 button Decrease Value
						246 image down
				247 container
					248 text * 初始研究资金
				249 container
					250 stepper (settable, integer) * 初始研究资金, Value: 10000000, ID: initial_cash
					251 button Increase Value
						252 image up
					253 button Decrease Value
						254 image down
				255 container
					256 text * 随机种子
				257 container
					258 stepper (settable, integer) * 随机种子, Value: 20261026, ID: seed
					259 button Increase Value
						260 image up
					261 button Decrease Value
						262 image down
				263 text 固定合同：Alpha158＋训练期拟合归一化，下一交易日开盘，TopkDropout，前日量1%容量；分区边界须隔离未来标签。
				264 text 已有历史已被查看，所有结果保持事后研究诊断。模型超参数采用已声明固定配置，当前表单不进行无界搜索。
				265 button (disabled) 提交集群研究任务
				266 button (disabled) 预览冻结策略规格

The focused UI element is 0 AXWebArea QTrade 量化研究平台, URL: q-trade-temp.vercel.app/#/research