# Fast official project/building/room query

Use `scripts/query_official.py` whenever the request needs project-level counts, every building's sale-status grid, or many `TimeFL.aspx` one-home-one-price records. This is much faster and less error-prone than manually opening pages.

## Requirements

- Python 3.10+
- `curl`
- Network access to `spfxm.whfgxx.org.cn:8083` and `119.97.201.22:8080`

## Commands

Resolve by marketing name / official name. By default the script fetches every clickable room's TimeFL price—sold and unsold—so it can produce a complete building + unit + floor price map:

```bash
python3 scripts/query_official.py \
  --name "示例项目名" \
  --budget 130-170 \
  --details-status all \
  --workers 4
```

For a faster pass that only prices current unsold rooms:

```bash
python3 scripts/query_official.py \
  --name "示例项目名" \
  --budget 130-170 \
  --details-status unsold \
  --workers 4
```

Resolve directly by official DengJH:

```bash
python3 scripts/query_official.py \
  --dengjh "示例登记号" \
  --budget 130-170 \
  --area 90-120 \
  --workers 4
```

Exclude units whose official detail page says 企业自留房:

```bash
python3 scripts/query_official.py \
  --name "示例项目名" \
  --budget 130-170 \
  --exclude-self-retained
```

Fast structural pass without per-room prices:

```bash
python3 scripts/query_official.py \
  --name "示例项目名" --no-details
```

Before a payment decision, bypass stale cache:

```bash
python3 scripts/query_official.py \
  --dengjh "示例登记号" --budget 130-170 --refresh
```

## Outputs

In the selected output directory:

| File | Meaning |
|---|---|
| `report.json` | Machine-readable project/building/room/detail summary and query parameters |
| `all_rooms.csv` | Every parsed cell in every official room table |
| `unpriced_rooms.csv` | Room-table cells without a valid TimeFL/zero-GUID price link; these cannot be priced and are explicitly excluded from the floor-price map |
| `checked_details.csv` | Every TimeFL room actually fetched (all statuses, or unsold-only when requested) |
| `one-price.json` | Reference-style complete one-price dataset: project, certificates, all room records, and building/unit/floor groups |
| `floor_prices.csv` | One row per `楼栋 × 单元 × 楼层`, with room count, status counts, area/price/total min-avg-max, and room JSON |
| `building_floor_prices.csv` | One row per `楼栋 × 楼层` across all units; this is the exact hierarchy rendered in the HTML dashboard |
| `floor-map.html` | Reference-dashboard-like dark interactive matrix: building/unit columns left-to-right, floors top-to-bottom, and each room tile rendering filed unit price, room number, area, total price, status, and self-retained marker |
| `filtered.csv` | Current **unsold** rooms passing budget/area/self-retained filters; sold rooms stay only in the floor-price map as comparables |
| `report.html` | Explicit building/unit/floor price dashboard plus filtered room table |

`checked_unsold_details.csv` is also written only when `--details-status unsold` is selected for backward compatibility.

Run commands from the skill root. Default output is `wuhan-output/<project>-<timestamp>`. Responses are cached under `~/.cache/wuhan-property-research`; cached repeat runs are near-instant. Add `--no-cache` when even local, URL-keyed cache files are unwanted.

## Interpretation rules

- The project-list count is official aggregate data.
- Room-table cells include housing, commercial, ancillary, and facility cells; do not equate all white cells with sellable homes.
- White/blank means unsold, yellow `#FFFF00` means sold, `*` mortgage, `⊗` seizure, and `△` restriction.
- A TimeFL page may say `该房屋为企业自留房`; such a unit is not an initially buyable candidate even if the list says 可售.
- `filtered` contains only current unsold rooms whose price/area criteria matched. Sold rooms appear in the floor map for comparables but never enter the candidate filter.
- `floor_prices.csv` and the HTML floor map answer “楼栋 / 单元 / 每层价格”: each floor row shows every room's area, filed unit price, total price, status, and self-retained flag, plus that floor's min/average/max price.
- `initial_buyable_candidates` requires: unsold, no restriction flag, successful detail parse, not self-retained, and within the supplied budget/area bounds.
- Zero GUID TimeFL links have no usable price detail and remain unverified.

## Operational limits

- Keep workers at 4 by default. Raise only to 8 for a large project; higher values can trigger ASP runtime errors.
- Avoid official maintenance time 01:00--05:00.
- If transient ASP runtime errors appear, lower workers and rerun; successful pages remain cached.
- This script does not replace the rest of the property report: school district, floor-plan images, planning/adverse factors, metro route, developer risk, and comparable projects still require their own evidence.
