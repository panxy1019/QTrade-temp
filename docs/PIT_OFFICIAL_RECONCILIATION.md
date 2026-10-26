# 官方退市名单与供应商主表对账

用户于 2026-10-26 确认暂无授权历史数据库，继续推进公共源可核验部分。
本工程使用已经保存的交易所原文，不要求密钥、不购买数据，也不重抓原文来覆盖
第一次探测失败。来源说明见 [官方来源审计](PIT_OFFICIAL_SOURCE_AUDIT.md)。

## 对账规则

1. 校验探测记录的内容校验和，再逐字节校验上交所 JSON、深交所工作簿的 SHA256
   与大小。上交所行数必须等于分页总数；深交所列结构、行数必须与记录一致。
2. 读取现有校验通过的 BaoStock 主表，保留其原始接收时间；采用完整交易所前缀
   加证券代码精确匹配，不以简称相似程度匹配。
3. 保留每条官方记录及行号、原始行哈希、原始日期和证券类型。B 股或不能确定
   A 股身份的记录隔离；A/B 共同上市记录的日期语义也单独标为待核验。
4. 对照上市日期、来源报告的退市日期、供应商证券类型与非活跃状态。重复身份、
   日期冲突和缺失均保留。两边日期相同只称 `DATES_AGREE_UNCERTIFIED`。
5. 输出独立的不可变对账记录，包含原文身份、主表身份、当前研究样本身份。
   不改写 Qlib 数据、股票池、状态快照或旧实验。

这能回答“官方退市名单中的哪些证券在供应商主表中缺失或矛盾”，不能证明过去
每一天的全市场股票池。官方列表的摘牌日期不是最后交易日，也不是公告发布时间。
当前 990 股样本的选择偏差仍然存在，不能因为完成一次名单对账就删除限制。

## 运行与验证

使用集群已有含 pandas/openpyxl 的数据环境；无需修改 Qlib 环境。

```bash
LIVE_TRADING_ENABLED=false PYTHONPATH=src python -m pytest tests/test_official_master.py
LIVE_TRADING_ENABLED=false PYTHONPATH=src python -m scripts.reconcile_official_master \
  --base "$BASE" --receipt "$BASE/pit-independent-sources/verified-probe.json"
```

`--receipt` 应是实际校验通过的 `qtrade.official-source-probe.v1` 记录。
原文位于 `$BASE/pit-independent-sources/<raw_sha256>.json` 或 `.xlsx`；
结果位于该目录的 `reconciliation/<evidence_sha256>.json`，以排他方式创建。
校验和表示内容身份，不是交易所数字签名或历史可得性的认证。

验收应记录实际统计和完整后端结果，不能根据测试中的合成证券推断真实覆盖率。
后续退市股行情、公司行为、历史 ST/停牌和精确交易规则补齐仍需分别验收。
