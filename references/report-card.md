# Wuhan property linked-evidence card

Use this shape for the final user-facing report. Keep it compact but complete; adapt labels to new-home or second-hand property. Never silently omit the five mandatory focus areas.

## Card skeleton

1. **Card header**
   - Project marketing name + official filing name.
   - Query date and evidence date.
   - Large verdict badge: `买入 / 可比 / 观望 / 放弃`.
   - One-sentence reason.
   - User budget and whether the project fits.

2. **Key-facts grid**
   - District / board.
   - Official project ID and developer.
   - Sale status.
   - Official units / housing for-sale count.
   - Nearest metro.
   - Current school-zone conclusion.
   - Main layout matching the budget.
   - Biggest risk.

3. **Price evidence**
   - Separate these columns: 备案价, 开发商报价/折后价, 挂牌价, 网签成交价.
   - State “未公开 / 未查到” instead of mixing portal prices into official fields.
   - If a budget exists, show per-layout estimated total, upper-bound unit price, and fit verdict.

4. **Five mandatory modules**
   - 户型：rooms, area, floor/orientation if known, actual floor-plan image and source link.
   - 楼盘与产品：buildings, floors, units/layer, elevators/stairs, floor height, plot ratio, green ratio, parking, delivery.
   - 不利因素：use a table with `官方确认 / 规划披露 / 地图观察 / 销售披露 / 待核实`.
   - 学区：official year + elementary + junior high + confirmation route; no invented tier or promotion rate.
   - 地铁交通：line/station, route basis, distance/time, bus, roads, commute implications.

5. **Comparables & liquidity**
   - 3–5 nearby new/second-hand comparables.
   - Use reference-price labels, never “备案价”, unless the official table proves it.

6. **Risk & action**
   - Top three to five risks.
   - Buy/compare/wait conclusion.
   - Exact questions and documents to request before any payment.

7. **Evidence & links**
   Required table:

   | 事实 | 来源 | 来源/页面日期 | 链接 | 证据等级 |
   |---|---|---|---|---|

   Include the query date in the card header or table. Prefer deep links. If only an entry page is public, include the exact search terms/form inputs.

## Markdown card style

- Use one top-level card title and horizontal rules to separate logical zones.
- Use compact tables, not long paragraphs, for numbers.
- Use clear markers: ✅ fit, ⚠️ caution, ❌ reject, 🔍 verify.
- Mark every estimated value as `估算`.
- Put unresolved items in a final “必须核实” block, not in the official-data table.
- Do not hide negative findings. If a fact is missing, show it as missing.

## Official link formatting examples

For Wuhan commodity-housing ASP.NET pages, retain the exact working encoded query. Use the following pattern only as documentation; replace the placeholder with the exact verified value in a real report:

- Project detail: `http://spfxm.whfgxx.org.cn:8083/spfxmcx/spfcx_mx.aspx?DengJH=<GBK-percent-encoded-official-code>`
- Building table: `http://spfxm.whfgxx.org.cn:8083/spfxmcx/spfcx_lpb.aspx?DengJH=<GBK-percent-encoded-official-code>`

If another project requires UTF-8 or a different GBK encoding, verify the link before publishing it; do not assume one encoding works for every system.

Other useful evidence-link examples for a current project card:

- [建设工程规划许可查询入口](https://spxx.zrzyhgh.wuhan.gov.cn/wsbs/jgggjyh850.asp?cid=1501&id=3), with exact verified query conditions
- [自然资源和规划部门公开服务](https://zrzyhgh.wuhan.gov.cn/), linked to the exact land-result page when found
- [区人民政府或教育局官方划片公告](<district-government-announcement-url>)
