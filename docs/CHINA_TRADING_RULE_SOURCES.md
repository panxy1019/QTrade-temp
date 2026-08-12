# China trading-rule source register

Verified: 2026-08-12. Implement rules as effective-dated data, never as
unversioned strategy constants.

## Current primary rulebooks

- SSE Trading Rules 2026, 上证发〔2026〕41号, published 2026-04-24,
  effective 2026-07-06:
  <https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml>
- SZSE Trading Rules 2026, 深证上〔2026〕551号, published 2026-04-24,
  effective 2026-07-06:
  <https://www.szse.cn/lawrules/rule/trade/current/t20260424_620190.html>
- SZSE rulebook PDF:
  <https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf>

The 2026-07-06 boundary matters to historical simulation. For example, main
board risk-warning stock limits changed from 5% to 10%; the platform must select
the applicable rule by instrument and date.

## Rule dimensions

Store venue, board, instrument type, ETF asset class, risk-warning/listing
status, effective interval, buy lot and quantity step, odd-lot disposition,
price tick/limit/cage, sellability and settlement lags, suspension status, fee
policy, side-specific tax, source URL and version.

- Main-board A shares generally use 100-share buy lots, CNY 0.01 ticks, T+1
  sellability, and a date/status-sensitive limit.
- STAR/ChiNext rules differ in minimum/step/order cap and 20% limit semantics.
- ETF auction trades generally use 100-unit buy lots and CNY 0.001 ticks.
- Domestic equity ETFs are T+1 sellable. Same-day resale applies only to
  designated classes such as qualifying bond, money-market, gold, commodity,
  and cross-border products. Store the exchange designation; never infer T+0
  from the generic `ETF` label or security code.
- Suspensions and resumptions come from point-in-time exchange/issuer status,
  not from missing OHLC data.

ETF primary sources:

- SSE ETF FAQ: <https://www.sse.com.cn/assortment/fund/etf/question/c/c_20240118_5734754.shtml>
- SSE revolving-trade FAQ: <https://etf.sse.com.cn/fund/quertion/>
- SZSE fund rules: <https://docs.static.szse.cn/www/lawrules/rule/fund/W020220610536157903323.pdf>
- SZSE ETF FAQ: <https://investor.szse.cn/knowledge/fund/trade/t20191030_571641.html>

## Cost model and double-counting rule

- Broker commission is negotiated. The statutory ceiling/current policy source
  is effective 2002-05-01:
  <https://neris.csrc.gov.cn/falvfagui/rdqsHeader/mainbody?navbarId=1&secFutrsLawId=b25a17fb262742f2aea99515380cf3bb>
- A-share exchange handling is 0.00341% both sides from 2023-08-28:
  <https://www.csrc.gov.cn/csrc/c100028/c7426794/content.shtml>
- Stock regulatory fee is 0.002% both sides; funds are exempt:
  <https://www.csrc.gov.cn/csrc/c100028/c1002446/content.shtml>
- Seller-only securities stamp tax is currently 0.05% following the half-rate
  reduction from 2023-08-28:
  <https://fgk.chinatax.gov.cn/zcfgk/c102416/c5211343/content.html>
- ETF exchange handling and exemptions must be selected by product/date; SSE
  current fee page: <https://www.sse.com.cn/services/tradingservice/charge/ssecharge/>
  and SZSE: <https://www.szse.cn/marketServices/deal/payFees/index.html>.

The 2002 commission policy describes commission as inclusive of exchange and
regulatory charges. If importing a broker statement's all-in commission, do not
add those components again. A decomposed simulator should model broker service,
handling and regulation components explicitly, constrain them to the agreed
commission policy, then add applicable transfer fee and seller-only stamp tax.

