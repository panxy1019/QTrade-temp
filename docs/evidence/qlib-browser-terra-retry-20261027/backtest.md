Browser tab: 1, Title: "QTrade 量化研究平台", URL: "https://q-trade-temp.vercel.app/#/backtest".
0 AXWebArea QTrade 量化研究平台, URL: q-trade-temp.vercel.app/#/backtest
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
			57 heading 回测分析, Value: 2
				58 text 回测分析
			59 text Qlib 日频研究
			60 button 刷新研究目录
			61 text 仅对照已完成的历史实验，保留各自测试区间、来源和成本假设；此处净值不是今天的 PAPER 或 SHADOW 结果。 任务由集群串行执行，状态约每五分钟发布；网页每15秒核对。旧ETF和75股分钟研究已移至历史工作区。
			62 text 统一历史策略对照
			63 button 刷新历史对照
			64 text 冻结样本外区间的七类历史研究结果。六类排名信号共享TopkDropout配置；全池等权为配置对照。此处不展示每日预测或PAPER前向收益。
			65 container
				66 image exclamation-circle
				67 text 诊断性历史结果，不可据此晋级交易 当前股票池存在幸存者偏差，历史PIT与交易限制未完整认证；每项限制适用于全部策略。等权恒定信号的IC无定义，显示为—。
			68 container
				69 container 信号区间 : 2024-01-02—2026-10-23
					70 text 信号区间
					71 text :
					72 text 2024-01-02—2026-10-23
				73 container 共同逐日股票记录 : 601,013
					74 text 共同逐日股票记录
					75 text :
					76 text 601,013
				77 container 股票池 : 逐日同时具备两模型分数和完整21交易日价格；不前填缺失值
					78 text 股票池
					79 text :
					80 text 逐日同时具备两模型分数和完整21交易日价格；不前填缺失值
				81 container 成交时点 : 前一交易日信号驱动下一开盘；首日可能为空仓
					82 text 成交时点
					83 text :
					84 text 前一交易日信号驱动下一开盘；首日可能为空仓
				85 container 时间切分 : {"test":["2024-01-02","2026-10-23"],"train":["2017-04-05","2021-12-28"],"valid":["2022-01-04","2023-12-26"]}
					86 text 时间切分
					87 text :
					88 text {"test":["2024-01-02","2026-10-23"],"train":["2017-04-05","2021-12-28"],"valid":["2022-01-04","2023-12-26"]}
				89 container IC标签 : Ref($open,-2)/Ref($open,-1)-1
					90 text IC标签
					91 text :
					92 text Ref($open,-2)/Ref($open,-1)-1
				93 container 归档交易约束版本 : RECONCILED_BACKFILL_ADDITIONAL_BLOCKS_V1
					94 text 归档交易约束版本
					95 text :
					96 text RECONCILED_BACKFILL_ADDITIONAL_BLOCKS_V1
				97 container 初始资金／基准 : 10,000,000／SH000300
					98 text 初始资金／基准
					99 text :
					100 text 10,000,000／SH000300
			101 table
				102 row
					103 cell
						104 text 策略
					105 cell
						106 text IC
					107 cell
						108 text RankIC
					109 cell
						110 text 成本后累计收益
					111 cell
						112 text 成本后年化收益
					113 cell
						114 text Sharpe
					115 cell
						116 text 最大回撤
					117 cell
						118 text 日均双边换手
				119 row
					120 cell
						121 text 20日动量
					122 cell
						123 text -0.0032
					124 cell
						125 text -0.0275
					126 cell
						127 text 2.33%
					128 cell
						129 text 0.86%
					130 cell
						131 text 0.2110
					132 cell
						133 text -46.17%
					134 cell
						135 text 16.29%
				136 row
					137 cell
						138 text 5日反转
					139 cell
						140 text -0.0025
					141 cell
						142 text 0.0173
					143 cell
						144 text 46.99%
					145 cell
						146 text 15.39%
					147 cell
						148 text 0.5741
					149 cell
						150 text -40.23%
					151 cell
						152 text 19.85%
				153 row
					154 cell
						155 text 20日低波动
					156 cell
						157 text -0.0007
					158 cell
						159 text 0.0348
					160 cell
						161 text 53.01%
					162 cell
						163 text 17.13%
					164 cell
						165 text 1.1641
					166 cell
						167 text -12.33%
					168 cell
						169 text 14.26%
				170 row
					171 cell
						172 text 多因子等秩
					173 cell
						174 text 0.0004
					175 cell
						176 text 0.0261
					177 cell
						178 text 65.12%
					179 cell
						180 text 20.49%
					181 cell
						182 text 1.2057
					183 cell
						184 text -14.53%
					185 cell
						186 text 19.05%
				187 row
					188 cell
						189 text 全池等权（配置对照）
					190 cell
						191 text —
					192 cell
						193 text —
					194 cell
						195 text 102.16%
					196 cell
						197 text 29.90%
					198 cell
						199 text 1.1806
					200 cell
						201 text -21.75%
					202 cell
						203 text 0.71%
				204 row
					205 cell
						206 text Alpha158 + Ridge
					207 cell
						208 text 0.0189
					209 cell
						210 text 0.0467
					211 cell
						212 text 87.47%
					213 cell
						214 text 26.31%
					215 cell
						216 text 1.2137
					217 cell
						218 text -32.18%
					219 cell
						220 text 17.53%
				221 row
					222 cell
						223 text Alpha158 + LightGBM
					224 cell
						225 text 0.0232
					226 cell
						227 text 0.0518
					228 cell
						229 text 126.71%
					230 cell
						231 text 35.56%
					232 cell
						233 text 1.2708
					234 cell
						235 text -32.17%
					236 cell
						237 text 17.43%
			238 text 全池等权每日调整至共同池等权，使用相同风险仓位和Exchange成本/容量限制；不使用TopkDropout的TopK、淘汰数和最短持有期。其收益差异不能仅归因于信号。
			239 container
				240 text Alpha158 + LightGBM
				241 combo box (collapsed) Description: 选择历史净值策略, Secondary Actions: Expand
				242 image down
			243 container
				244 text 共同持仓配置： {"forbid_all_trade_at_limit":false,"hold_thresh":2,"n_drop":5,"only_tradable":false,"risk_degree":0.95,"topk":50}
			245 container
				246 text 共同成本与交易约束： {"close_cost":0.0013,"deal_price":"open","freq":"day","impact_cost":0,"limit_threshold":["$buy_block","$sell_block"],"min_cost":5,"open_cost":0.0008,"trade_unit":100,"volume_threshold":["cum","$capacity"]}
			247 container
				248 text •  当前股票池存在幸存者与历史筛选偏差
			249 container
				250 text •  完整历史PIT尚未认证
			251 container
				252 text •  历史信息可得时点未知
			253 container
				254 text •  事后样本外回测，不是前向实绩
			255 container
				256 text •  日线开盘、成交容量与涨跌停代理不能替代集合竞价回放
			257 container
				258 text •  全池等权采用独立配置规则，不属于相同TopK持仓规则比较
			259 container
				260 text •  本历史对照未纳入ETF；ETF收盘价诊断单独展示
			261 container
				262 text 不可变对照身份： f6b9e52b1c30c4f46e0733d02bb4e800450a9acbb67a8954d63a2bb857dde943
				263 button Copy
					264 image copy
			265 container
				266 text 冻结数据： a56d093e439345619d2a0419be4e6191886164d8bc849ee05ef25452c26a0759
				267 button Copy
					268 image copy
			269 container
				270 text 来源快照： b39408fa65995fc593e85c43e0b1c6bd6d5ae2443efdbe0f42873c1de6a83687
				271 button Copy
					272 image copy
			273 container
				274 text 执行源码： f6c26aa4ba934897fa49fec38d2a433d90adfc98
				275 button Copy
					276 image copy
			277 button download 导出 JSON
				278 container download
				279 text 导出 JSON
			280 text ETF 同池历史组合模拟
			281 button 刷新 ETF 历史对照
			282 text 两种固定规则在同一 ETF 池、相同日期与成本假设下比较；前一日收盘决策、下一交易日开盘模拟成交。此处为历史组合模拟，与股票模型对照及真实 SHADOW 观察分开。
			283 container
				284 image exclamation-circle
				285 text 假设驱动的历史模拟，不构成 PAPER 准入 当前证券池非历史 PIT；原生价格的复权与分红拆分尚未认证。手数、费用及滑点属于模拟假设；未模拟真实停牌、涨跌停、容量与结算限制。短区间不能支持稳健性结论。
			286 container
				287 container 固定共同 ETF 池 : 30 只；不拼接旧数据、不前填缺失值
					288 text 固定共同 ETF 池
					289 text :
					290 text 30 只；不拼接旧数据、不前填缺失值
				291 container 共同暖启动 : 21 个收盘会话
					292 text 共同暖启动
					293 text :
					294 text 21 个收盘会话
				295 container 评价区间 : 2026-09-01 — 2026-10-26（34 日）
					296 text 评价区间
					297 text :
					298 text 2026-09-01 — 2026-10-26（34 日）
				299 container 初始模拟资金 : 1,000,000
					300 text 初始模拟资金
					301 text :
					302 text 1,000,000
				303 container 每边假设费率／滑点 : 0.10%／0.05%
					304 text 每边假设费率／滑点
					305 text :
					306 text 0.10%／0.05%
				307 container 假设交易单位 : 100 份；两组均为95%目标预算
					308 text 假设交易单位
					309 text :
					310 text 100 份；两组均为95%目标预算
			311 table
				312 row
					313 cell
						314 text 策略
					315 cell
						316 text 假设成本后累计收益
					317 cell
						318 text 最大回撤
					319 cell
						320 text 模拟成交笔数
					321 cell
						322 text 累计假设费用
					323 cell
						324 text 期末模拟资产
				325 row
					326 cell
						327 text 20日正动量 · 每日前3等权
					328 cell
						329 text -15.63%
					330 cell
						331 text -17.00%
					332 cell
						333 text 118
					334 cell
						335 text 20,003.18
					336 cell
						337 text 843,726.66
				338 row
					339 cell
						340 text 固定全池 · 每日等权
					341 cell
						342 text -8.08%
					343 cell
						344 text -9.72%
					345 cell
						346 text 734
					347 cell
						348 text 1,182.59
					349 cell
						350 text 919,231.85
			351 text 等权基准覆盖固定全池，不限于正动量证券；差异同时来自信号选择与组合配置。最终收盘信号没有下一开盘数据时不成交；期末持仓按收盘估值，不强制清仓。现金利息为零。
			352 container
				353 text 20日正动量 · 每日前3等权
				354 combo box (collapsed) Description: 选择ETF历史模拟策略, Secondary Actions: Expand
				355 image down
			356 container
				357 table
					358 row
						359 cell
							360 text 日期
						361 cell
							362 text 模拟现金
						363 cell
							364 text 模拟资产
						365 cell
							366 text 双边换手／前日资产
						367 cell
							368 text 假设费用
						369 cell
							370 text 持仓数
					371 row
						372 cell
							373 button (collapsed) Description: Expand row, Secondary Actions: Expand
						374 cell
							375 text 2026-09-01
						376 cell
							377 text 48,794.75
						378 cell
							379 text 985,795.65
						380 cell
							381 text 95.03%
						382 cell
							383 text 950.25
						384 cell
							385 text 3
					386 row
						387 cell
							388 button (collapsed) Description: Expand row, Secondary Actions: Expand
						389 cell
							390 text 2026-09-02
						391 cell
							392 text 48,036.79
						393 cell
							394 text 961,289.19
						395 cell
							396 text 62.42%
						397 cell
							398 text 615.37
						399 cell
							400 text 3
					401 row
						402 cell
							403 button (collapsed) Description: Expand row, Secondary Actions: Expand
						404 cell
							405 text 2026-09-03
						406 cell
							407 text 46,876.87
						408 cell
							409 text 965,015.07
						410 cell
							411 text 128.38%
						412 cell
							413 text 1,234.08
						414 cell
							415 text 3
					416 row
						417 cell
							418 button (collapsed) Description: Expand row, Secondary Actions: Expand
						419 cell
							420 text 2026-09-04
						421 cell
							422 text 47,415.21
						423 cell
							424 text 971,487.11
						425 cell
							426 text 63.64%
						427 cell
							428 text 614.18
						429 cell
							430 text 3
					431 row
						432 cell
							433 button (collapsed) Description: Expand row, Secondary Actions: Expand
						434 cell
							435 text 2026-09-07
						436 cell
							437 text 48,843.43
						438 cell
							439 text 956,623.03
						440 cell
							441 text 0.43%
						442 cell
							443 text 4.19
						444 cell
							445 text 3
					446 row
						447 cell
							448 button (collapsed) Description: Expand row, Secondary Actions: Expand
						449 cell
							450 text 2026-09-08
						451 cell
							452 text 48,241.61
						453 cell
							454 text 963,072.51
						455 cell
							456 text 0.13%
						457 cell
							458 text 1.28
						459 cell
							460 text 3
					461 row
						462 cell
							463 button (collapsed) Description: Expand row, Secondary Actions: Expand
						464 cell
							465 text 2026-09-09
						466 cell
							467 text 48,369.08
						468 cell
							469 text 968,982.88
						470 cell
							471 text 0.70%
						472 cell
							473 text 6.75
						474 cell
							475 text 3
					476 row
						477 cell
							478 button (collapsed) Description: Expand row, Secondary Actions: Expand
						479 cell
							480 text 2026-09-10
						481 cell
							482 text 47,886.34
						483 cell
							484 text 970,573.64
						485 cell
							486 text 63.56%
						487 cell
							488 text 615.89
						489 cell
							490 text 3
				491 content list
					492 container Previous Page
						493 button (disabled) left
							494 image left
					495 text 1
					496 text 2
					497 text 3
					498 text 4
					499 text 5
					500 container Next Page
						501 button right
							502 image right
			503 text 完整目标、模拟成交和逐日会计记录可导出核验；下列限制适用于全部结果。
			504 container
				505 text •  历史模拟使用明确假设，不是 PAPER 或真实前向记录
			506 container
				507 text •  固定当前 ETF 池，历史时点成员资格未认证
			508 container
				509 text •  原生价格的复权、分红与拆分尚未核对
			510 container
				511 text •  手数、费用、滑点与下一开盘成交规则均为模拟假设
			512 container
				513 text •  未执行真实容量、参与率、价差、涨跌停、停牌或结算约束
			514 container
				515 text •  仅做多、不借贷、现金利息为零、期末不强制清仓
			516 container
				517 text •  数据为事后采集，无法证明历史时点的信息可得性
			518 container
				519 text •  共同评价窗口较短，年化指标不能证明长期有效性
			520 container
				521 text •  未拼接旧数据或前填缺失值；不会自动改写 SHADOW 指标
			522 container
				523 text 实验身份： 6cdc93fa7243a1e70e780953feb160583c9fade3b390df3c6aef58ede51ac3bd
				524 button Copy
					525 image copy
			526 container
				527 text 执行源码： b284da5ba8e1ab1edbc7b45b07b9001d67acf66c
				528 button Copy
					529 image copy
			530 button download 导出 JSON
				531 container download
				532 text 导出 JSON
			533 container
				534 text unified-bound-ridge-repair-20261027 · linear
				535 combo box (collapsed) Description: 选择Qlib实验, Secondary Actions: Expand
				536 image down
			537 container
				538 text 选择另一实验进行对照
				539 combo box (collapsed) Description: 选择对照实验, Secondary Actions: Expand
				540 image down
			541 link Description: 新建研究任务, Value: q-trade-temp.vercel.app/#/research
			542 container
				543 text Alpha158 + Ridge · unified-bound-ridge-repair-20261027-4a6f63ee966a766f 组合评价  2024-01-02 — 2026-10-27 ；IC标签  2024-01-02 — 2026-10-23 。输入数据截至 2026-10-27 。 实际资格区间覆盖 891 只股票， 158 个特征；每日可用股票数随已完成历史和成交额变化。完整资格区间保存在集群实验审计中。
				544 text 累计收益
				545 text 86.66%
				546 text 年化收益
				547 text 26.02%
				548 text Sharpe
				549 container
					550 text 1 .2015
				551 text 最大回撤
				552 text -32.51%
				553 text IC
				554 container
					555 text 0 .0195
				556 text RankIC
				557 container
					558 text 0 .0472
				559 text ICIR（未年化）
				560 container
					561 text 0 .1367
				562 text RankICIR（未年化）
				563 container
					564 text 0 .3112
				565 text 双边日均换手
				566 text 17.71%
				567 text 基准累计收益
				568 text 18.02%
			569 container
				570 text 数据与交易约束审计
				571 container
					572 text 非完整历史PIT 不可晋级 BIASED_CURRENT_SAMPLE
				573 text 新工作流训练/排名/投资范围对齐，使用完成历史做动态资格与流动性选择；样本本身的幸存者偏差尚未消除。
				574 container
					575 text •  当前流动性样本回投历史，幸存者与筛选偏差尚未消除
				576 container
					577 text •  已查看历史的事后研究，不是新的未见前向证据
				578 container
					579 text •  历史ST及精确涨跌停尚未完整认证
				580 container
					581 text •  缺行情不等于已认证停牌原因
				582 container
					583 text •  供应商复权尚未与独立公司行为账本对账
				584 container
					585 text •  开盘价及前日量容量是研究近似，未重放集合竞价
				586 container
					587 text •  回填状态只增加研究限制，历史可得时间未认证；ST禁买是研究政策
				588 container
					589 text 本实验冻结的PIT修复覆盖： 状态回填990/990股、2,168,788行；来源主表9,022条。尚未用于精确规则回测，历史可得性待核验。  完整来源摘要随实验JSON导出。
				590 container
					591 text 已参与本次回测的附加状态限制：原始行情对账 2,141,014 根，覆盖 990 股；来源冲突 8,275 根双向限制。新增禁买 28,646 根、禁卖 8,130 根。缺状态 10,490 根继续保留原近似限制，不能认定为已认证可交易。
				592 container
					593 text 以上为输入库全历史的掩码计数，不是测试期委托或实际成交次数。来源快照： 467366395184edc65dd2ac079982393efdd516302302604c0a0fbfce664ef449 。回填为事后诊断；ST禁买是研究政策，精确涨跌停与完整历史PIT仍未认证。逐股票逐日账本保存在集群归档中。
			594 container
				595 text 固定配置与结果身份 结果哈希： 248f4b86e0ba837ce01b633c585ebc7043289dfbdfdf8ffb2f92342fa8e63f6e
				596 button Copy
					597 image copy
				598 text 数据哈希： b52a40230fa49b46cde7fc25014638234de85e05de0d1ff93705e49d3a977df3
				599 button Copy
					600 image copy
				601 text 源码： 096387b8088e4361c9e5cd9cd2c165e21819fead
				602 button Copy
					603 image copy
				604 text 对照协议： ALIGNED_SUPPORTED_UNIVERSE_SOURCE_BOUND_CONSTRAINTS_V3 {
  "buy_cost_bps": 8,
  "data_binding": {
    "actual_end": "2026-10-27",
    "classification": "PUBLIC_SOURCE_RESEARCH_OBSERVATION_ONLY",
    "data_version_id": "09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407",
    "dataset_id": "qlib-version-09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407",
    "dataset_sha256": "b52a40230fa49b46cde7fc25014638234de85e05de0d1ff93705e49d3a977df3",
    "evidence_sha256": "e19fba2b11aa60b5b67b5e308635a6f6e647dee646c4c0025c6d472c10fc3b16",
    "external_order_transmission": false,
    "live_trading_enabled": false,
    "manifest_sha256": "3e3406bc45e3a366bdcc57631648c3f05917e98f2d47a70df5c7482e8425f79b",
    "point_in_time": false,
    "promotion": "NOT_ELIGIBLE",
    "ready_event_id": "69c3430923515d90b93ccb6eba9d49ca667a606b8dcdb3701c59de449ae0bc6c",
    "real_data_paper_readiness": false,
    "realtime_readiness": false,
    "schema_version": "qtrade.qlib-research-data-binding.v1",
    "source_snapshot_sha256": "467366395184edc65dd2ac079982393efdd516302302604c0a0fbfce664ef449",
    "start": "2016-10-24",
    "supported_symbols": 891,
    "supported_universe_sha256": "3da952813acf86a0f53b7a299f7faac95f105c12014959bb61bd36e63a608e26"
  },
  "dataset_id": "qlib-version-09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407",
  "feature_set_version": "qlib-0.9.7-Alpha158-v1",
  "features": "Alpha158",
  "hold_thresh": 2,
  "initial_cash": 10000000,
  "min_cost": 5,
  "model": "linear",
  "n_drop": 5,
  "name": "unified-bound-ridge-repair-20261027",
  "risk_degree": 0.95,
  "seed": 20261026,
  "segments": {
    "test": [
      "2024-01-02",
      "2026-10-27"
    ],
    "train": [
      "2017-04-05",
      "2021-12-28"
    ],
    "valid": [
      "2022-01-04",
      "2023-12-26"
    ]
  },
  "sell_cost_bps": 13,
  "source_snapshot_sha256": "467366395184edc65dd2ac079982393efdd516302302604c0a0fbfce664ef449",
  "topk": 50,
  "universe_policy": "supported_a_share"
}
				605 button download 导出 JSON
					606 container download
					607 text 导出 JSON
				608 link Description: 复用配置创建对照, Value: q-trade-temp.vercel.app/#/research?from=713e9f4de8bb456aa7fc906bf636dce6

The focused UI element is 0 AXWebArea QTrade 量化研究平台, URL: q-trade-temp.vercel.app/#/backtest