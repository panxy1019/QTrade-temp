Browser tab: 1, Title: "QTrade 量化研究平台", URL: "https://q-trade-temp.vercel.app/#/models".
0 AXWebArea QTrade 量化研究平台, URL: q-trade-temp.vercel.app/#/models
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
			57 heading 在线模型管理, Value: 2
				58 text 在线模型管理
			59 text Qlib 研究
			60 button 刷新模型目录
			61 text 查看已验收的数据版本、冻结候选和操作记录。模型批准只影响研究模型选择；每日自动预测与重训按下方实际状态显示。
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
			75 text 最近合格的 Qlib 数据版本
			76 container
				77 container 真实数据截止日 : 2026-10-27（最近完整交易日：2026-10-27）
					78 text 真实数据截止日
					79 text :
					80 text 2026-10-27（最近完整交易日：2026-10-27）
				81 container 已验收范围 : 987 只当日合格 / 990 只历史保留
					82 text 已验收范围
					83 text :
					84 text 987 只当日合格 / 990 只历史保留
				85 container 当日缺口 : 002827、600621、605258
					86 text 当日缺口
					87 text :
					88 text 002827、600621、605258
				89 container 不可变版本 : 09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407 Copy
					90 text 不可变版本
					91 text :
					92 text 09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407
					93 button Copy
						94 image copy
			95 image info-circle
			96 text 日版本调度：当日版本已就绪
			97 text 调度心跳有效；要求截止日 2026-10-27。运行中的调度进程不等于当天数据已经验收；以最近合格版本及其缺口为准。
			98 image info-circle
			99 text 每日自动预测：已启用；自动重训：OFF
			100 text 当前候选输入验证与生产自动预测分开记录。验证分数未取得真实前向收益；完整历史 PIT、复权与交易规则认证仍有限制。
			101 text 数据版本目录与回退
			102 button 刷新数据版本
			103 text 目录展示清单身份，回退前集群会完整检查 Provider 文件。历史 PIT、复权与交易约束限制继续保留；此操作不授予 PAPER/SHADOW 执行权限，实盘保持关闭。
			104 container
				105 image exclamation-circle
				106 text 数据回退入口暂不可用 需要登录、有效控制心跳，以及模型目录与数据目录的当前版本一致。数据过期不会阻止选择已验收版本来恢复；预测仍需通过最新数据门禁。
			107 container
				108 container 当前数据版本 : 09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407 Copy
					109 text 当前数据版本
					110 text :
					111 text 09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407
					112 button Copy
						113 image copy
				114 container 当前事件身份 : 69c3430923515d90b93ccb6eba9d49ca667a606b8dcdb3701c59de449ae0bc6c Copy
					115 text 当前事件身份
					116 text :
					117 text 69c3430923515d90b93ccb6eba9d49ca667a606b8dcdb3701c59de449ae0bc6c
					118 button Copy
						119 image copy
				120 container 目录范围 : 3 / 3 个版本
					121 text 目录范围
					122 text :
					123 text 3 / 3 个版本
			124 container
				125 table
					126 row
						127 cell
							128 text 数据截止
						129 cell
							130 text 版本
						131 cell
							132 text 当日合格 / 历史保留 / 缺口
						133 cell
							134 text 操作
					135 row
						136 cell
							137 text 2026-10-27
						138 cell
							139 text 09a67b135def5dee
							140 button Copy
								141 image copy
						142 cell
							143 text 987 / 990 / 3
						144 cell
							145 text 当前版本
					146 row
						147 cell
							148 text 2026-10-26
						149 cell
							150 text d33a74663373b1d4
							151 button Copy
								152 image copy
						153 cell
							154 text 987 / 990 / 3
						155 cell
							156 button (disabled) 审阅数据回退
					157 row
						158 cell
							159 text 2026-10-26
						160 cell
							161 text 384ce479913266c3
							162 button Copy
								163 image copy
						164 cell
							165 text 982 / 990 / 8
						166 cell
							167 button (disabled) 审阅数据回退
				168 content list
					169 container Previous Page
						170 button (disabled) left
							171 image left
					172 text 1
					173 container Next Page
						174 button (disabled) right
							175 image right
			176 heading 数据回退回执, Value: 5
				177 text 数据回退回执
			178 table
				179 row
					180 cell
						181 text 时间
					182 cell
						183 text 请求
					184 cell
						185 text 结果
					186 cell
						187 text 拒绝原因
				188 row
					189 cell
						190 container
							191 image No data
							192 text No data
			193 text 受控滚动训练
			194 button (disabled) 配置训练政策
			195 text 按真实交易会话安排新候选；数据就绪、标签成熟和资源门禁通过后才执行。预测优先，失败保留当前模型；候选激活由独立的人工或自动批准政策控制。
			196 container
				197 container 生效政策 : 关闭自动重训
					198 text 生效政策
					199 text :
					200 text 关闭自动重训
				201 container 候选模型 : lightgbm
					202 text 候选模型
					203 text :
					204 text lightgbm
				205 container 最近创建任务的数据日 : 尚无自动重训尝试
					206 text 最近创建任务的数据日
					207 text :
					208 text 尚无自动重训尝试
			209 container
				210 table
					211 row
						212 cell
							213 text 候选输入验证
						214 cell
							215 text 状态
						216 cell
							217 text 诊断
					218 row
						219 cell
							220 text 29589b4486a09159
							221 button Copy
								222 image copy
						223 cell
							224 text COMPLETE
				225 content list
					226 container Previous Page
						227 button (disabled) left
							228 image left
					229 text 1
					230 container Next Page
						231 button (disabled) right
							232 image right
			233 table
				234 row
					235 cell
						236 text 任务
					237 cell
						238 text 数据截止
					239 cell
						240 text 成熟测试区间
					241 cell
						242 text 状态
					243 cell
						244 text 诊断
				245 row
					246 cell
						247 image No data
						248 text No data
			249 text 研究模型自动批准政策
			250 container
				251 container 当前政策 : 已启用
					252 text 当前政策
					253 text :
					254 text 已启用
				255 container 允许模型 : lightgbm
					256 text 允许模型
					257 text :
					258 text lightgbm
				259 container 政策版本 : 656f643bf547556bf9b7b5ef30b36100191bbc64009d1b0c78295fc648a5dbda
					260 text 政策版本
					261 text :
					262 text 656f643bf547556bf9b7b5ef30b36100191bbc64009d1b0c78295fc648a5dbda
				263 container 授权记录 : panxy1019-explicit-chat-approval · 2026-10-27T14:07:59.998990+00:00
					264 text 授权记录
					265 text :
					266 text panxy1019-explicit-chat-approval · 2026-10-27T14:07:59.998990+00:00
			267 container
				268 image info-circle
				269 text 日预测与候选替换分开记录 已批准 ACTIVE 在合格新日版本上自动预测。启用此政策后，唯一合格 LightGBM 候选通过当前版本验证、时钟与身份复核才可替换 ACTIVE；多个候选或检查失败均阻断。输入兼容验证不代表收益更优，政策不恢复或启动新的 PAPER 账户，也不开启实盘。
			270 container
				271 button (disabled) 启用 LightGBM 自动批准
				272 button (disabled) 关闭自动批准
			273 text 每日自动预测记录
			274 container
				275 text 已封存 。 存在身份匹配且尚未过期的研究预测。 分数仅用于研究排序，不是交易指令或收益承诺。
			276 container
				277 table
					278 row
						279 cell
							280 text 数据截止
						281 cell
							282 text 状态
						283 cell
							284 text 模型身份
						285 cell
							286 text 证券数
						287 cell
							288 text 实际生成
						289 cell
							290 text 预期研究执行
						291 cell
							292 text 诊断
					293 row
						294 cell
							295 button (collapsed) Description: Expand row, Secondary Actions: Expand
						296 cell
							297 text 2026-10-27
						298 cell
							299 text 已封存
						300 cell
							301 text 29589b4486a0
							302 button Copy
								303 image copy
						304 cell
							305 text 888
						306 cell
							307 text 2026-10-27T14:29:43.021259+00:00
						308 cell
							309 text 2026-10-28T09:30:00+08:00
					310 row
						311 cell
							312 button (collapsed) Description: Expand row, Secondary Actions: Expand
						313 cell
							314 text 2026-10-27
						315 cell
							316 text 已封存
						317 cell
							318 text 29589b4486a0
							319 button Copy
								320 image copy
						321 cell
							322 text 888
						323 cell
							324 text 2026-10-27T14:22:45.062695+00:00
						325 cell
							326 text 2026-10-28T09:30:00+08:00
					327 row
						328 cell
							329 button (collapsed) Description: Expand row, Secondary Actions: Expand
						330 cell
							331 text 2026-10-27
						332 cell
							333 text 已封存
						334 cell
							335 text 2d78cc4cc257
							336 button Copy
								337 image copy
						338 cell
							339 text 888
						340 cell
							341 text 2026-10-27T14:07:46.509705+00:00
						342 cell
							343 text 2026-10-28T09:30:00+08:00
					344 row
						345 cell
							346 button (collapsed) Description: Expand row, Secondary Actions: Expand
						347 cell
							348 text 2026-10-27
						349 cell
							350 text 已封存
						351 cell
							352 text 2d78cc4cc257
							353 button Copy
								354 image copy
						355 cell
							356 text 888
						357 cell
							358 text 2026-10-27T12:25:43.741275+00:00
						359 cell
							360 text 2026-10-28T09:30:00+08:00
					361 row
						362 cell
							363 button (collapsed) Description: Expand row, Secondary Actions: Expand
						364 cell
							365 text 2026-10-26
						366 cell
							367 text 已封存
						368 cell
							369 text 2d78cc4cc257
							370 button Copy
								371 image copy
						372 cell
							373 text 888
						374 cell
							375 text 2026-10-26T17:23:15.344571+00:00
						376 cell
							377 text 2026-10-27T09:30:00+08:00
				378 content list
					379 container Previous Page
						380 button (disabled) left
							381 image left
					382 text 1
					383 text 2
					384 container Next Page
						385 button right
							386 image right
			387 text 候选与历史模型
			388 button download 导出 JSON
				389 container download
				390 text 导出 JSON
			391 table
				392 row
					393 cell
						394 text 模型
					395 cell
						396 text 状态
					397 cell
						398 text 训练数据截止
					399 cell
						400 text 最新输入验证
					401 cell
						402 text 组合
					403 cell
						404 text 操作
				405 row
					406 cell
						407 button (collapsed) Description: Expand row, Secondary Actions: Expand
					408 cell
						409 text Alpha158 + Ridge
						410 container
							411 text d6c232d1500297d0
							412 button Copy
								413 image copy
					414 cell
						415 text 待批准政策校验
					416 cell
						417 text 2026-10-23
					418 cell
						419 text 当前数据尚无通过的验证
					420 cell
						421 text Top50 / 淘汰5
					422 cell
						423 button (disabled) 审阅激活
				424 row
					425 cell
						426 button (collapsed) Description: Expand row, Secondary Actions: Expand
					427 cell
						428 text Alpha158 + LightGBM
						429 container
							430 text 2d78cc4cc257653a
							431 button Copy
								432 image copy
					433 cell
						434 text 历史模型
					435 cell
						436 text 2026-10-23
					437 cell
						438 text 当前数据尚无通过的验证
					439 cell
						440 text Top50 / 淘汰5
					441 cell
						442 button (disabled) 审阅回滚
				443 row
					444 cell
						445 button (collapsed) Description: Expand row, Secondary Actions: Expand
					446 cell
						447 text Alpha158 + LightGBM
						448 container
							449 text 29589b4486a09159
							450 button Copy
								451 image copy
					452 cell
						453 text 当前研究模型
					454 cell
						455 text 2026-10-26
					456 cell
						457 text 2026-10-27 · 888 只策略合格证券
					458 cell
						459 text Top50 / 淘汰5
					460 cell
						461 button (disabled) 审阅激活
				462 row
					463 cell
						464 button (collapsed) Description: Expand row, Secondary Actions: Expand
					465 cell
						466 text Alpha158 + LightGBM
						467 container
							468 text 68d9287efc49c242
							469 button Copy
								470 image copy
					471 cell
						472 text 训练完成
					473 cell
						474 text 2026-10-27
					475 cell
						476 text 当前数据尚无通过的验证
					477 cell
						478 text Top50 / 淘汰5
					479 cell
						480 button (disabled) 审阅激活
				481 row
					482 cell
						483 button (collapsed) Description: Expand row, Secondary Actions: Expand
					484 cell
						485 text Alpha158 + Ridge
						486 container
							487 text e0036a4fa2656da7
							488 button Copy
								489 image copy
					490 cell
						491 text 训练完成
					492 cell
						493 text 2026-10-27
					494 cell
						495 text 当前数据尚无通过的验证
					496 cell
						497 text Top50 / 淘汰5
					498 cell
						499 button (disabled) 审阅激活
			500 text 模型操作回执
			501 container
				502 table
					503 row
						504 cell
							505 text 时间
						506 cell
							507 text 请求
						508 cell
							509 text 操作
						510 cell
							511 text 结果
						512 cell
							513 text 拒绝原因
					514 row
						515 cell
							516 text 2026-10-27T14:20:15.847042+00:00
						517 cell
							518 text fdb7526b08849916d4b44bc66d01ea3c
						519 cell
							520 text ACTIVATE
						521 cell
							522 text APPLIED
					523 row
						524 cell
							525 text 2026-10-26T14:52:26.286301+00:00
						526 cell
							527 text de31d80373b844d8ae5db601bcf2ded5
						528 cell
							529 text ACTIVATE
						530 cell
							531 text APPLIED
				532 content list
					533 container Previous Page
						534 button (disabled) left
							535 image left
					536 text 1
					537 container Next Page
						538 button (disabled) right
							539 image right

The focused UI element is 0 AXWebArea QTrade 量化研究平台, URL: q-trade-temp.vercel.app/#/models