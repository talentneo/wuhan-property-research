---
name: wuhan-property-research
description: Research and compare Wuhan residential property projects using official filing, transaction, planning, school, metro, floor-plan, and adverse-factor evidence; produce a decision-oriented linked-evidence card. Use when the user asks to investigate a Wuhan new-home, second-hand, or neighborhood purchase or asks for 备案价、成交、学区、地铁、户型、不利因素, or a purchase recommendation.
---

# Wuhan Property Research

Produce a decision-oriented Wuhan property report. Present the result as **one complete, polished card** rather than loose notes.

Read `references/sources.md` for authoritative Wuhan data sources and their limits. Read `references/report-card.md` for the required card structure and evidence-link formatting. Use `references/report-template.md` only as a completeness checklist; the user-facing deliverable is the linked-evidence card.

## Fast official-data path

For project identity, sale-status grids, or room-level filing prices, run `scripts/query_official.py` first instead of opening official pages manually. Read `references/code-query.md` for commands, output interpretation, cache/refresh behavior, and concurrency limits. The script produces JSON/CSV/HTML; still verify directly with `--refresh` before payment.

## Required investigation

1. Resolve the official project identity. Marketing names and filing names often differ. Search the official project system by project name, developer, and address. Record the exact filing name, developer, permit/certificate status, total units, housing units sold/for sale, and non-housing counts.
2. Open the official building table when available. Check the intended building/unit sale status, filing unit price/total price, mortgage, seizure, and sales restriction. If the official record is absent, state that clearly and treat the project as not yet publicly filed/verified until proven otherwise.
3. Cross-check market data. Use official daily/monthly statistics for transaction volume, then third-party platforms only as market-reference evidence. For second-hand homes, compare recent same-community/same-layout transactions and bank evaluation assumptions where available; do not infer citywide conclusions from a single listing.
4. Apply the user's five mandatory focus areas to every project report:
   - Floor plans: include each main layout's rooms, area, floor, orientation, and an actual floor-plan image or image link. Mark unavailable images explicitly; never substitute a marketing rendering for a floor plan.
   - Buildings and height: building count, floors, units/layers, floor height if disclosed, density/plot ratio/green ratio, towers, and unit mix. Differentiate 洋房/小高层/高层.
   - Adverse factors: inspect noise roads/railways, high-voltage lines, substations, garbage facilities, hospitals/morgue-related uses, industrial pollution, flood/prone areas, commercial exhaust, school crowding, construction land, cemetery/funeral uses, and anything visible on maps or disclosed in sales documents. Record “not found” rather than claiming none.
   - Schools: identify the current official elementary and junior-high enrollment zone, source, and effective year. Add school tier and promotion/academic metrics only with a source. Label market “梯队” as non-official opinion. Explain that zones can change annually and a contract cannot guarantee admission.
   - Metro/transport: identify the nearest metro line/station and walking distance; add bus, arterial-road, and commute notes when relevant.
5. If a budget is supplied, calculate whether each layout fits before comparing projects. Include rough total price, discount assumptions, necessary renovation costs for bare-shell units, and whether the layout is above/below budget.
6. End the card with a concise buy/compare/wait verdict, top risks, and the exact questions/documents the buyer must request before paying.

## Evidence and links

Every decisive fact needs a named source and, when public, a direct link. Do not merely say “官方查询” or “网上资料”.

- Include an **Evidence & links** table in every card: fact, source, source/page date, link, and evidence level (`official`, `planning`, `portal`, `map`, `market`, or `unverified`).
- Attach direct links for at least: official project detail, official building table, planning/land result when found, school-zone announcement, floor-plan page, comparable-project pages, and any transaction/statistics page used.
- If a direct URL is unavailable or requires POST/session/login, link the public entry page and state the exact search criteria or form inputs used. Do not invent a deep link.
- Preserve working query strings exactly. Wuhan ASP.NET pages may use GBK percent-encoding, not UTF-8. For example, DengJH `<官方登记号>` may become `<GBK-percent-encoded-official-code>`; do not silently normalize it to `<UTF-8-encoded-byte>2600345`.
- For floor plans, embed the actual image when possible and also link the source page. If saving the image locally, use an absolute filesystem path in Markdown and retain the source URL in the evidence table.
- Include the query date and distinguish publication/update date from access date.

## Privacy and data handling

- This skill is for **public** property research. Do not ask for or store an ID number, contract number, password, verification code, bank account, buyer name, phone number, or personal contract query result.
- The personal commodity-housing contract query is manual and consent-based at most. Do not automate it, log its inputs, save its screenshots, or copy personal results into a report. In shared output, say only that the buyer personally verified it on the official page and give the date.
- Generated reports and caches reveal which projects, districts, layouts, and budgets a user is researching. Keep them outside the repository by default; never commit `wuhan-output/`, local cache files, reports, CSV/JSON exports, screenshots, or floor-plan downloads containing personal information.
- Some official Wuhan endpoints use plain HTTP. Treat project names, addresses, and other query terms as observable on the network. Prefer public/non-sensitive search terms and warn the user before any personal lookup.
- Respect source websites, robots/site policies, maintenance windows, and rate limits. Keep concurrency low and stop if the service rejects requests.

## Evidence and safety rules

- Cite or name the source for decisive facts and dates. Official sources outrank portal data.
- Do not invent school zones, promotion rates, filing prices, transaction counts, distances, or floor heights.
- Put uncertain or conflicting data in a “待核实” section and explain how to verify it.
- For pre-sale projects, warn against paying a large deposit before verifying the pre-sale permit, one-home-one-price table, official building table, and supervised funds account.
- Clearly separate objective data, market reference data, and the agent's judgment.
