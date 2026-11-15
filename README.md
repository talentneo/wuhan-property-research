# Wuhan Property Research

一个面向武汉买房决策的开源研究技能：把官方楼盘、备案/一房一价、成交统计、规划、教育、地铁、户型和不利因素整理成可溯源的 HTML/证据卡片。它偏重“证据分级 + 结论可核对”，不提供投资承诺。

## 功能

- [GitHub Pages](https://talentneo.github.io/wuhan-property-research/)
- 官方项目身份解析：营销名 / 备案名 / DengJH 对齐。
- 楼栋、单元、楼层、房号状态和一房一价批量核验。
- 输出 JSON、CSV、交互式楼层价格矩阵和 HTML 报告。
- 强制证据表：事实、来源、日期、链接、证据等级。
- 覆盖规划、学校划片、地铁距离、户型、不利因素和付款前核验问题。
- 只使用 Python 标准库和系统 `curl`，无遥测、无第三方分析组件。

## 安装

把本目录复制或克隆到你的技能目录，例如：

```bash
git clone https://github.com/talentneo/wuhan-property-research.git
mkdir -p ~/.codex/skills
cp -R wuhan-property-research/. ~/.codex/skills/wuhan-property-research/
```

要求：

- Python 3.10+
- `curl`
- 可访问武汉相关官方查询网站

## 使用

在技能根目录执行：

```bash
python3 scripts/query_official.py \
  --name "示例项目名" \
  --budget 130-170 \
  --details-status all \
  --workers 4
```

只看当前未售房源：

```bash
python3 scripts/query_official.py \
  --name "示例项目名" \
  --details-status unsold \
  --workers 4
```

隐私敏感查询可禁用本机 URL 缓存：

```bash
python3 scripts/query_official.py --name "某项目" --no-cache
```

更多命令、字段解释和官方网站限制见 [`references/code-query.md`](references/code-query.md)。

## 输出

默认写入 `wuhan-output/<项目>-<时间>/`，主要包括：

- `report.json` / `one-price.json`
- `all_rooms.csv` / `checked_details.csv` / `filtered.csv`
- `floor_prices.csv` / `building_floor_prices.csv`
- `floor-map.html` / `report.html`

输出目录已被 `.gitignore` 排除。请不要把个人研究报告提交到仓库。

## 隐私与安全边界

- 本项目不采集遥测，也不包含 API key、账号或用户数据。
- 个人商品房合同备案查询需要身份证号、备案登记号等敏感信息。本仓库脚本不会 automate 这个查询；建议用户只在官方网站自行查询，不要把证件号、合同号、验证码、截图或查询结果交给任何 AI 工具。
- 生成报告和缓存会暴露你关注的小区、预算、户型和区域，请按需保存、加密或删除。
- 部分官方旧系统使用 HTTP 明文。查询词、项目名、地址可能被网络中间方观察到；不要在官方查询中使用私人敏感信息，除非你已理解并接受风险。
- 默认缓存位于 `~/.cache/wuhan-property-research/`，可用 `--cache-dir` 指定位置，或用 `--no-cache` 禁写缓存。
- 请遵守目标网站政策和服务能力；默认并发为 4，不要对官方服务施加高负载。

## 数据来源与免责声明

本项目只整理公开信息，不代表武汉市政府或任何平台，也不保证官方页面长期不变。房产决策前必须以政府部门、开发商、银行和合同文件的最新书面信息为准；本项目的输出仅为研究辅助，不构成投资、法律或税务建议。

## 许可

代码和文档使用 [MIT License](LICENSE) 许可。外部网站内容的权利归其来源方所有，引用时请遵守来源规则。
