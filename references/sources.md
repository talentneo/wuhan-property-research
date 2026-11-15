# Wuhan property data sources

## Official and highest-authority sources

- Wuhan Housing and Urban Renewal Bureau: https://zgj.wuhan.gov.cn/
- Wuhan commodity-housing project query: http://spfxm.whfgxx.org.cn:8083/spfxmcx/spfcx_index.aspx
  - For bulk or room-level queries, use the skill script `scripts/query_official.py`; see `references/code-query.md`.
  - Search by district, project name, address, or developer. The marketing name may differ from the filing name.
  - Follow the official three-level hierarchy: project detail (`spfcx_mx.aspx?DengJH=...`) → building table (`spfcx_lpb.aspx?DengJH=...`) → room/building table (`spfcx_fang.aspx?dengJH=...&houseDengJh=...`). Preserve parameter spelling and GBK percent-encoding exactly as returned.
  - The room table marks each unit with white/blank style = unsold, yellow `#FFFF00` = sold, and overlay symbols: `*` mortgage, `⊗` seizure, `△` sales restriction. Non-residential/ancillary rooms can also carry `△`.
  - Each room number links to a `TimeFL.aspx?gid=...` pre-sale/sale plan page; capture building/unit/floor/room, area, filed unit price/total price, delivery standard, permit number, and any retained-house/mortgage/seizure/restriction warnings there. A zero GUID means no usable room-level detail page.
  - Project list shows total units, housing sold/for sale, and non-housing sold/for sale.
  - Project detail → building table → building → room can show sale status, unit price, mortgage, seizure, and restriction. For projects permitted after 2016-01-01, unit prices may appear two working days after the pre-sale permit is issued.
  - Attach both the search entry and the working direct detail/building-table links in the final evidence table.
  - These ASP.NET pages can require GBK percent-encoded query values. Preserve the URL exactly after verification. Example for DengJH `<官方登记号>`:
    - Project detail: http://spfxm.whfgxx.org.cn:8083/spfxmcx/spfcx_mx.aspx?DengJH=<GBK-percent-encoded-official-code>
    - Building table: http://spfxm.whfgxx.org.cn:8083/spfxmcx/spfcx_lpb.aspx?DengJH=<GBK-percent-encoded-official-code>
  - Do not silently convert GBK `<GBK-encoded-byte>` to UTF-8 `<UTF-8-encoded-byte>`; test which encoding the page actually accepts.
- Personal commodity-housing contract filing query: http://spfxm.whfgxx.org.cn:8083/grxxcx/zz_grxxcx_index.aspx
  - Requires filing registration number, ID number, district, and project name. Use only for the buyer's own contract. It can expose transaction amount, signing date, and contract status.
  - Privacy guardrail: do **not** automate this personal query with this skill or script. If a buyer explicitly chooses to use it, guide them through the official page without collecting, logging, screenshotting, or storing their ID, filing registration number, password/verification code, contract details, or transaction result. In any shared report, record only “buyer personally verified on the official page on <date>”; never reproduce a full ID or contract number.
  - The script in this repository is designed for public project/building/room data, not personal contract lookups.
- Daily new commodity-housing online-signing/filing statistics: http://119.97.201.20/spfcjtj/Sectionlist.aspx
  - Daily signing is an official transaction-volume measure, not same-day subscriptions and not project-level price.
- Monthly commodity-housing sale/pre-sale monitoring reports: https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/spzfxysydjcjb/
  - Provides district-level monthly commodity-housing and total commodity-building transaction counts/areas.
- NBS 70-city price releases: https://www.stats.gov.cn/sj/zxfb/
  - Use for new/second-hand price index trends. This is an index, not an absolute average price.
- Wuhan Natural Resources and Urban-Rural Construction Bureau planning queries
  - Construction planning-permit query entry: https://spxx.zrzyhgh.wuhan.gov.cn/wsbs/jgggjyh850.asp?cid=1501&id=3
  - The legacy ASP endpoint can require GBK-encoded keyword and department parameters; verify the URL after construction.
  - Query pattern: `keyword=<GBK-percent-encoded-project-or-parcel-keyword>&dept_ID=<department-code>`; verify the exact URL before publishing.
  - For land results, start from the bureau's land-transaction query service and link the exact public result actually used.
- Wuhan Education Bureau and district government/enrollment announcements
  - Use only the current official enrollment zone (“划片/对口”) and quote the effective year. Avoid relying on portal or agent school tables for final conclusions.

## Useful secondary references

- Fang.com Wuhan new homes, second-hand listings, floor plans, project details, and nearby comparables.
- Lianjia/Beike or other portals for挂牌价, community history, and platform transaction records.
- Maps for metro walking distance, road noise, surrounding facilities, and adverse land uses.
- Public credit/court/company systems for developer, landlord, and project risk signals.

## Interpretation rules

- 备案价/一房一价 is authoritative for publicly filed new homes but may not equal final discounted contract price.
- Portal “均价” is usually reference/listing data and can lag or mix product types.
- Daily/monthly official statistics are aggregate volume, not individual project prices.
- School “梯队” is market vocabulary, not an official classification. Promotion metrics for compulsory-education schools are often absent, non-comparable, or unofficial; if no reliable metric exists, say so.
- Walking distances should state the route and map source; straight-line distance is not sufficient.
