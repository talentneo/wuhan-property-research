#!/usr/bin/env python3
"""Fast official Wuhan commodity-housing project crawler.

Pipeline:
  project search -> project detail -> building table -> room table -> TimeFL one-home-one-price pages

The script intentionally uses only the Python standard library and writes JSON/CSV/HTML outputs.
It preserves ASP.NET query values by converting Chinese values to GBK percent-encoding.

Examples:
  python3 query_official.py --name "示例项目名" --budget 130-170
  python3 query_official.py --dengjh "示例登记号" --budget 130-170 --workers 4
  python3 query_official.py --name "示例项目名" --budget 130-170 --area 90-120
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import html
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

BASE_INDEX_URL = "http://spfxm.whfgxx.org.cn:8083/spfxmcx/spfcx_index.aspx"
BASE_PROJECT_URL = "http://spfxm.whfgxx.org.cn:8083/spfxmcx/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0 Safari/537.36"
)
ZERO_GUID = "00000000-0000-0000-0000-000000000000"

# Marketing aliases are only a fast lookup layer. Every result is still verified through
# the official project detail page and its DengJH.
# Optional local alias examples can be added here. Keep public releases free of
# private research targets and company/project names.
ALIASES: dict[str, dict[str, str]] = {}

ROOM_FIELDS = [
    "房屋座落",
    "预售（现售）许可证号",
    "预售（现售）建筑面积（平方米）",
    "实测套内面积（平方米）",
    "预售（现售）单价（元/平方米）",
    "房屋总价款（元）",
    "交付标准",
    "计价标准",
]


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]*>", " ", text)
    return " ".join(text.split())


def normalize_name(name: str) -> str:
    return re.sub(r"[\s·・\-—_（）()：:，,。.]", "", name).lower()


def gbk_quote(value: str) -> str:
    try:
        raw = value.encode("gbk")
    except UnicodeEncodeError:
        raw = value.encode("utf-8")
    return urllib.parse.quote_plus(raw)


def canonical_url(url: str) -> str:
    """Return the official-site URL with query values percent-encoded as GBK.

    The function is idempotent: an already-encoded GBK URL stays unchanged.  Standard
    `parse_qsl` is unsuitable because it decodes GBK percent bytes as UTF-8 replacements.
    """
    url = html.unescape(url.strip())
    parts = urllib.parse.urlsplit(url)

    def encoded_component(raw: str) -> str:
        if not raw:
            return ""
        if "%" in raw:
            # Keep the original byte sequence when it is valid GBK. This makes the function idempotent.
            raw_bytes = urllib.parse.unquote_to_bytes(raw.replace("+", " "))
            try:
                raw_bytes.decode("gbk")
                return urllib.parse.quote_plus(raw_bytes)
            except UnicodeError:
                # Allow UTF-8 input when a caller intentionally supplies it.
                try:
                    raw_bytes.decode("utf-8")
                    return urllib.parse.quote_plus(raw_bytes)
                except UnicodeError:
                    return urllib.parse.quote_plus(raw_bytes, errors="replace")
        text = urllib.parse.unquote_plus(raw)
        try:
            return urllib.parse.quote_plus(text.encode("gbk"))
        except UnicodeEncodeError:
            return urllib.parse.quote_plus(text.encode("utf-8"))

    pairs = [
        f"{encoded_component(key)}={encoded_component(value)}"
        for key, _, value in [component.partition("=") for component in parts.query.split("&") if component]
    ]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(pairs), parts.fragment))



def decode_body(body: bytes) -> str:
    """Official pages lie sometimes: many ASP pages say UTF-8 but emit GBK bytes."""
    if body.startswith(b"\xef\xbb\xbf"):
        return body.decode("utf-8-sig", errors="replace")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("gbk", errors="replace")


def parse_guid(url: str) -> str:
    query = urllib.parse.urlparse(url).query
    values = urllib.parse.parse_qs(query, keep_blank_values=True).get("gid", [])
    return values[0] if values else ""


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("万", "").strip()
    if not text or text in {"—", "-", "暂无", "未公开"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def parse_range(value: str | None) -> tuple[float | None, float | None] | None:
    if not value:
        return None
    parts = re.split(r"[-—~至,，\s]+", value.strip())
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"range must look like 130-170: {value}")
    try:
        return float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid range: {value}") from exc


def slugify(value: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", value.strip()).strip("-")
    return value[:80] or "project"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{random.randrange(1 << 24)}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


class HttpClient:
    def __init__(self, cache_dir: Path | None = None, timeout: int = 30, refresh: bool = False):
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.refresh = refresh

    def read_cache(self, url: str) -> bytes | None:
        if self.cache_dir is None or self.refresh:
            return None
        path = self.cache_path(url)
        return path.read_bytes() if path.exists() else None

    def cache_path(self, url: str) -> Path:
        key = hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()
        return self.cache_dir / key  # type: ignore[index]

    def request_bytes(self, url: str, data: dict[str, str] | None = None) -> bytes:
        """Fetch through curl. These official IIS/ASP pages respond much faster and more reliably than urllib."""
        encoded_url = canonical_url(url)
        command = [
            "curl", "-L", "-sS", "--compressed", "--max-time", str(self.timeout),
            "-A", USER_AGENT,
            "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.8",
        ]
        if data is not None:
            body = "&".join(f"{gbk_quote(k)}={gbk_quote(v)}" for k, v in data.items())
            command += ["-H", "Content-Type: application/x-www-form-urlencoded", "--data-raw", body]
        command.append(encoded_url)
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
        return result.stdout

    def get_text(self, url: str, retries: int = 2) -> str:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return decode_body(self.request_bytes(url))
            except Exception as exc:  # network/timeout/transient ASP error
                last_error = exc
                time.sleep(0.15 * (attempt + 1))
        raise RuntimeError(f"GET failed: {url}: {last_error}")

    def cached_get_text(
        self,
        url: str,
        retries: int = 4,
        validator: Callable[[str], bool] | None = None,
    ) -> str:
        cached = self.read_cache(url)
        if cached is not None:
            text = decode_body(cached)
            if validator is None or validator(text):
                return text

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                body = self.request_bytes(url)
                text = decode_body(body)
                if validator is None or validator(text):
                    if self.cache_dir is not None:
                        path = self.cache_path(url)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        tmp = path.with_name(f".{path.name}.{os.getpid()}.{random.randrange(1 << 24)}.tmp")
                        tmp.write_bytes(body)
                        os.replace(tmp, path)
                    return text
                last_error = RuntimeError("ASP runtime error or empty response")
            except Exception as exc:
                last_error = exc
            time.sleep(min(1.5, 0.18 * (attempt + 1)) + random.random() * 0.08)
        raise RuntimeError(f"GET failed after {retries + 1} attempts: {url}: {last_error}")


@dataclass
class ProjectResult:
    official_name: str
    dengjh: str
    detail_url: str
    total_units: int | None = None
    housing_sold: int | None = None
    housing_for_sale: int | None = None
    non_housing_sold: int | None = None
    non_housing_for_sale: int | None = None


def extract_hidden_fields(page: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        patterns = [
            rf'<input[^>]+name=["\']{re.escape(name)}["\'][^>]+value=["\']([^"\']*)["\']',
            rf'<input[^>]+value=["\']([^"\']*)["\'][^>]+name=["\']{re.escape(name)}["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, page, re.I | re.S)
            if match:
                fields[name] = html.unescape(match.group(1))
                break
        if name not in fields:
            raise RuntimeError(f"Missing ASP.NET hidden field: {name}")
    return fields


def parse_index_results(page: str) -> list[ProjectResult]:
    results: list[ProjectResult] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.I | re.S):
        match = re.search(
            r"<a[^>]+href=[\"']?([^\"' >]*spfcx_mx\.aspx[^\"' >]*)[\"' ?][^>]*>(.*?)</a>",
            row_html,
            re.I | re.S,
        )
        if not match:
            continue
        href = html.unescape(match.group(1))
        official_name = clean_text(match.group(2))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(href).query, keep_blank_values=True)
        dengjh = query.get("DengJH", query.get("dengJH", [""]))[0]
        values: list[str] = []
        for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.I | re.S):
            text = clean_text(cell)
            if text and text != official_name and dengjh not in text:
                values.append(text)

        def optional_int(index: int) -> int | None:
            if index >= len(values) or values[index] in {"—", "-", ""}:
                return None
            return int(to_float(values[index]) or 0)

        results.append(
            ProjectResult(
                official_name=official_name,
                dengjh=dengjh,
                detail_url=urllib.parse.urljoin(BASE_PROJECT_URL, href),
                total_units=optional_int(0),
                housing_sold=optional_int(1),
                housing_for_sale=optional_int(2),
                non_housing_sold=optional_int(3),
                non_housing_for_sale=optional_int(4),
            )
        )
    return results


def post_project_search(client: HttpClient, term: str, district: str = "") -> list[ProjectResult]:
    entry = client.get_text(BASE_INDEX_URL)
    fields = extract_hidden_fields(entry)
    fields.update(
        {
            "tbtxt": "",
            "DropDownList_xzq": district,
            "xmmc": term,
            "xmdz": "",
            "kfs": "",
            "query": "查询",
        }
    )
    page = decode_body(client.request_bytes(BASE_INDEX_URL, fields))
    return parse_index_results(page)


def resolve_project(
    client: HttpClient,
    name: str | None,
    official_name: str | None,
    dengjh: str | None,
    district: str,
) -> tuple[ProjectResult, str]:
    """Return (project, resolution_method). Alias is a hint only; official detail verifies it."""
    if dengjh:
        detail_url = f"{BASE_PROJECT_URL}spfcx_mx.aspx?DengJH={dengjh}"
        detail = parse_project_detail(client.cached_get_text(detail_url, validator=lambda s: "项目名称" in s and "Label" not in s and "参数错误" not in s))
        found = [r for r in post_project_search(client, detail["项目名称"], district) if r.dengjh == dengjh]
        if found:
            found[0].detail_url = detail_url
            return found[0], "dengjh+official-search"
        return (
            ProjectResult(official_name=detail["项目名称"], dengjh=dengjh, detail_url=detail_url),
            "dengjh",
        )

    normalized = normalize_name(name or "")
    alias = ALIASES.get(normalized)
    if alias and not official_name:
        dengjh = alias["dengjh"]
        detail_url = f"{BASE_PROJECT_URL}spfcx_mx.aspx?DengJH={dengjh}"
        detail = parse_project_detail(client.cached_get_text(detail_url, validator=lambda s: "项目名称" in s and "Label" not in s and "参数错误" not in s))
        if detail["项目名称"] != alias["official_name"]:
            raise RuntimeError(f"Alias verification failed: expected {alias['official_name']}, got {detail['项目名称']}")
        found = [r for r in post_project_search(client, alias["official_name"], district) if r.dengjh == dengjh]
        if found:
            found[0].detail_url = detail_url
            return found[0], "alias+official-search"
        return (
            ProjectResult(official_name=alias["official_name"], dengjh=dengjh, detail_url=detail_url),
            "alias",
        )

    terms: list[str] = []
    if official_name:
        terms.append(official_name)
    if name:
        if name not in terms:
            terms.append(name)
        clean = re.sub(r"^(武汉)?", "", name)
        if clean and clean not in terms:
            terms.append(clean)

    all_results: list[ProjectResult] = []
    for term in terms:
        results = post_project_search(client, term, district)
        all_results.extend(results)
        if len(results) == 1:
            return results[0], f"official-search:{term}"
        if len(results) > 1:
            # Prefer an exact official-name match; otherwise return the first result but list others in metadata.
            exact = [r for r in results if r.official_name == term]
            if len(exact) == 1:
                return exact[0], f"official-search:{term}:exact"
            return results[0], f"official-search:{term}:multiple"
    if all_results:
        return all_results[0], "official-search:first"
    raise RuntimeError("No official project found. Try --dengjh, --official-name, or a shorter project keyword.")


def parse_project_detail(page: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.I | re.S):
        cells = [clean_text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.I | re.S)]
        if len(cells) < 2:
            continue
        key = cells[-2].rstrip("：:")
        value = cells[-1]
        if key:
            data[key] = value
    return data


def parse_building_table(page: str, detail_url: str) -> list[dict[str, Any]]:
    buildings: list[dict[str, Any]] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.I | re.S):
        match = re.search(
            r"<a[^>]+href=[\"']?([^\"' >]*spfcx_fang\.aspx[^\"' >]*)[\"' ?][^>]*>(.*?)</a>",
            row_html,
            re.I | re.S,
        )
        if not match:
            continue
        href = html.unescape(match.group(1))
        cells = [clean_text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.I | re.S)]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(href).query, keep_blank_values=True)
        buildings.append(
            {
                "building_name": clean_text(match.group(2)),
                "floors": to_float(cells[1]) if len(cells) > 1 else None,
                "units": to_float(cells[2]) if len(cells) > 2 else None,
                "building_area": to_float(cells[3]) if len(cells) > 3 else None,
                "surveyor": cells[4] if len(cells) > 4 else "",
                "house_dengjh": query.get("houseDengJh", query.get("housedengjh", [""]))[0],
                "room_table_url": canonical_url(urllib.parse.urljoin(detail_url, href)),
            }
        )
    return buildings


def style_status(tag: str, label: str) -> tuple[str, list[str]]:
    flags: list[str] = []
    if "*" in label:
        flags.append("抵押")
    if "⊗" in label or "×" in label:
        flags.append("查封")
    if "△" in label:
        flags.append("限制销售")
    if flags:
        return "限制/配套", flags
    if "FFFF00" in tag.upper():
        return "已售", flags
    # The site represents unsold cells with `<td style=>` or white/FFFFFF.
    style_match = re.search(r"style\s*=\s*[\"']?([^\"'>]*)", tag, re.I)
    style = (style_match.group(1) if style_match else "").strip().rstrip(">")
    if not style or "FFFFFF" in style.upper():
        return "未售", flags
    return "未知/其他", flags


def parse_room_table(page: str, building: dict[str, Any]) -> list[dict[str, Any]]:
    rooms: list[dict[str, Any]] = []
    # Rows are emitted with missing close tags; splitting on <tr> is more reliable than an HTML tree here.
    for row_html in re.split(r"<tr(?:\s[^>]*)?>", page, flags=re.I)[1:]:
        row_html = re.split(r"</tr>", row_html, maxsplit=1, flags=re.I)[0]
        cells = re.findall(r"(<td[^>]*>)(.*?)</td>", row_html, re.I | re.S)
        if len(cells) < 4:
            continue
        meta = [clean_text(value) for _, value in cells[:3]]
        for tag, cell in cells[3:]:
            link = re.search(
                r"<a[^>]+href=[\"']?([^\"' >]+TimeFL\.aspx[^\"' >]*)[\"' ?][^>]*>(.*?)</a>",
                cell,
                re.I | re.S,
            )
            if not link:
                continue
            label = clean_text(link.group(2))
            status, flags = style_status(tag, label)
            url = html.unescape(link.group(1))
            gid = parse_guid(url)
            rooms.append(
                {
                    "building_name": building["building_name"],
                    "building": str(meta[0] or "").strip(),
                    "unit": meta[1] if len(meta) > 1 else "",
                    "floor": meta[2] if len(meta) > 2 else "",
                    "room": label,
                    "status": status,
                    "flags": flags,
                    "detail_url": url,
                    "gid": gid,
                    "has_detail": bool(gid and gid != ZERO_GUID),
                }
            )
    return rooms


def parse_detail_page(page: str, url: str) -> dict[str, Any]:
    detail: dict[str, Any] = {"detail_url": url, "gid": parse_guid(url)}
    for field in ROOM_FIELDS:
        pattern = (
            r"<td[^>]*>\s*" + re.escape(field) + r"\s*：?\s*</td>\s*<td[^>]*>(.*?)</td>"
        )
        match = re.search(pattern, page, re.I | re.S)
        detail[field] = clean_text(match.group(1)) if match else ""
    detail["企业自留房"] = "该房屋为企业自留房" in page
    detail["面积"] = to_float(detail.get(ROOM_FIELDS[2], ""))
    detail["单价"] = to_float(detail.get(ROOM_FIELDS[4], ""))
    detail["总价万"] = (
        to_float(detail.get(ROOM_FIELDS[5], "")) / 10000
        if to_float(detail.get(ROOM_FIELDS[5], "")) is not None
        else None
    )
    return detail


def fetch_detail_task(args: tuple[dict[str, Any], HttpClient]) -> dict[str, Any]:
    room, template_client = args
    # A plain URL request is enough and avoids sharing one CookieJar across threads.
    client = HttpClient(template_client.cache_dir, template_client.timeout, template_client.refresh)
    try:
        page = client.cached_get_text(
            room["detail_url"],
            retries=5,
            validator=lambda text: "房屋座落" in text and "运行时错误" not in text,
        )
        detail = parse_detail_page(page, room["detail_url"])
        return {**room, **detail, "detail_error": ""}
    except Exception as exc:
        return {**room, "detail_error": str(exc)}


def within_range(value: float | None, bounds: tuple[float | None, float | None] | None) -> bool:
    if bounds is None:
        return True
    low, high = bounds
    if value is None:
        return False
    return (low is None or value >= low) and (high is None or value <= high)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = sorted({key for row in rows for key in row if not isinstance(row.get(key), (dict, list))})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in keys})


def building_label(building_name: str) -> str:
    match = re.search(r"(\d+)(?:号住宅楼)?$", building_name or "")
    return f"{match.group(1)}#" if match else building_name or ""


def natural_numbers(value: Any) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", str(value or ""))]


def natural_key(value: Any) -> list[Any]:
    return natural_numbers(value) or [987654321]


def floor_numeric(value: Any) -> float:
    numbers = natural_numbers(value)
    return sum(numbers) / len(numbers) if numbers else 987654321.0


def unit_display(value: Any) -> str:
    text = str(value or "").strip()
    return "无独立单元" if text in {"", "/"} else f"{text}单元" if text.isdigit() else text


def room_display_name(floor: Any, room: Any) -> str:
    floor_text = str(floor or "").strip()
    room_text = str(room or "").strip()
    if floor_text.isdigit() and room_text.isdigit():
        return f"{floor_text}{room_text}"
    return room_text


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_one_price_dataset(project: dict[str, Any], query: dict[str, Any], detail_rows: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in detail_rows if not row.get("detail_error") and row.get("单价") is not None]
    rooms: list[dict[str, Any]] = []
    for row in successful:
        flags = row.get("flags") or []
        rooms.append(
            {
                "building": row.get("building", ""),
                "buildingLabel": building_label(row.get("building_name", "")),
                "buildingName": row.get("building_name", ""),
                "unit": row.get("unit", ""),
                "unitDisplay": unit_display(row.get("unit", "")),
                "floor": row.get("floor", ""),
                "room": row.get("room", ""),
                "roomName": room_display_name(row.get("floor"), row.get("room")),
                "area": row.get("面积"),
                "price": row.get("单价"),
                "totalPrice": round((row.get("总价万") or 0) * 10000, 2) if row.get("总价万") is not None else None,
                "totalPriceWan": row.get("总价万"),
                "saleStatusText": row.get("status", ""),
                "saleStatus": "sold" if row.get("status") == "已售" else "available" if row.get("status") == "未售" else "restricted",
                "flags": flags,
                "selfRetained": bool(row.get("企业自留房")),
                "delivery": row.get("交付标准", ""),
                "certificate": row.get("预售（现售）许可证号", ""),
                "detailUrl": row.get("detail_url", ""),
            }
        )

    certificates: list[dict[str, Any]] = []
    cert_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for room in rooms:
        cert_groups[room["certificate"] or "未披露"].append(room)
    for name, cert_rooms in cert_groups.items():
        prices = [float(room["price"]) for room in cert_rooms if room.get("price") is not None]
        certificates.append(
            {
                "name": name,
                "houseNum": len(cert_rooms),
                "availableRoomNum": sum(room["saleStatus"] == "available" for room in cert_rooms),
                "soldRoomNum": sum(room["saleStatus"] == "sold" for room in cert_rooms),
                "restrictedRoomNum": sum(room["saleStatus"] == "restricted" for room in cert_rooms),
                "selfRetainedRoomNum": sum(room["selfRetained"] for room in cert_rooms),
                "avgPrice": round(average(prices), 2) if prices else None,
                "rooms": sorted(cert_rooms, key=lambda room: (natural_key(room["buildingLabel"]), natural_key(room["unit"]), -floor_numeric(room["floor"]), natural_key(room["roomName"]))),
            }
        )
    certificates.sort(key=lambda item: item["name"])

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for room in rooms:
        grouped[(room["buildingName"], room["unitDisplay"], str(room["floor"]))].append(room)
    floor_groups: list[dict[str, Any]] = []
    for (building_name, unit, floor), floor_rooms in grouped.items():
        prices = [float(room["price"]) for room in floor_rooms if room.get("price") is not None]
        totals = [float(room["totalPriceWan"]) for room in floor_rooms if room.get("totalPriceWan") is not None]
        areas = [float(room["area"]) for room in floor_rooms if room.get("area") is not None]
        status_counts = Counter(room["saleStatusText"] for room in floor_rooms)
        floor_groups.append(
            {
                "building_name": building_name,
                "building": building_label(building_name),
                "unit": unit,
                "floor": floor,
                "room_count": len(floor_rooms),
                "status_counts": dict(status_counts),
                "sold_count": status_counts.get("已售", 0),
                "unsold_count": status_counts.get("未售", 0),
                "restricted_count": status_counts.get("限制/配套", 0),
                "self_retained_count": sum(room["selfRetained"] for room in floor_rooms),
                "area_min": min(areas) if areas else None,
                "area_max": max(areas) if areas else None,
                "price_min": min(prices) if prices else None,
                "price_avg": average(prices),
                "price_max": max(prices) if prices else None,
                "total_min_wan": min(totals) if totals else None,
                "total_avg_wan": average(totals),
                "total_max_wan": max(totals) if totals else None,
                "rooms": sorted(floor_rooms, key=lambda room: natural_key(room["roomName"])),
            }
        )
    floor_groups.sort(key=lambda group: (natural_key(group["building"]), natural_key(group["unit"]), -floor_numeric(group["floor"])))

    # Also aggregate by building + floor across all units. This is the visual
    # "building -> floor -> priced rooms" arrangement used in the HTML report.
    building_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for room in rooms:
        building_groups[(room["buildingName"], str(room["floor"]))].append(room)
    building_floor_groups: list[dict[str, Any]] = []
    for (building_name, floor), floor_rooms in building_groups.items():
        prices = [float(room["price"]) for room in floor_rooms if room.get("price") is not None]
        totals = [float(room["totalPriceWan"]) for room in floor_rooms if room.get("totalPriceWan") is not None]
        areas = [float(room["area"]) for room in floor_rooms if room.get("area") is not None]
        status_counts = Counter(room["saleStatusText"] for room in floor_rooms)
        units = sorted({room["unitDisplay"] for room in floor_rooms}, key=natural_key)
        building_floor_groups.append(
            {
                "building_name": building_name,
                "building": building_label(building_name),
                "floor": floor,
                "units": units,
                "unit_count": len(units),
                "room_count": len(floor_rooms),
                "status_counts": dict(status_counts),
                "sold_count": status_counts.get("已售", 0),
                "unsold_count": status_counts.get("未售", 0),
                "restricted_count": status_counts.get("限制/配套", 0),
                "self_retained_count": sum(room["selfRetained"] for room in floor_rooms),
                "area_min": min(areas) if areas else None,
                "area_max": max(areas) if areas else None,
                "price_min": min(prices) if prices else None,
                "price_avg": average(prices),
                "price_max": max(prices) if prices else None,
                "total_min_wan": min(totals) if totals else None,
                "total_avg_wan": average(totals),
                "total_max_wan": max(totals) if totals else None,
                "rooms": sorted(floor_rooms, key=lambda room: (natural_key(room["unit"]), natural_key(room["roomName"]))),
            }
        )
    building_floor_groups.sort(key=lambda group: (natural_key(group["building"]), -floor_numeric(group["floor"])))

    return {
        "source": "Wuhan official project/room/TimeFL pages",
        "generatedAt": query["run_time"],
        "project": project,
        "query": query,
        "summary": {
            "roomRecords": len(rooms),
            "available": sum(room["saleStatus"] == "available" for room in rooms),
            "sold": sum(room["saleStatus"] == "sold" for room in rooms),
            "restricted": sum(room["saleStatus"] == "restricted" for room in rooms),
            "selfRetained": sum(room["selfRetained"] for room in rooms),
            "floorGroups": len(floor_groups),
            "buildingFloorGroups": len(building_floor_groups),
        },
        "certificates": certificates,
        "rooms": sorted(rooms, key=lambda room: (natural_key(room["buildingLabel"]), natural_key(room["unit"]), -floor_numeric(room["floor"]), natural_key(room["roomName"]))),
        "floor_groups": floor_groups,
        "building_floor_groups": building_floor_groups,
    }


def flatten_floor_groups(floor_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in floor_groups:
        row = {key: value for key, value in group.items() if key != "rooms"}
        row["rooms_json"] = json.dumps(group.get("rooms", []), ensure_ascii=False, separators=(",", ":"))
        rows.append(row)
    return rows


def write_reference_floor_map(path: Path, one_price: dict[str, Any]) -> None:
    """Write a static, reference-dashboard-like building/unit/floor price matrix."""
    project = one_price.get("project", {})
    # The matrix is for residential building/unit/floor reading. Ancillary cells with
    # no independent unit (shops, facilities, stations) would distort the floor grid;
    # they remain available in one-price.json and checked_details.csv.
    rooms = [
        room for room in one_price.get("rooms", [])
        if str(room.get("unitDisplay") or room.get("unit") or "") != "无独立单元"
    ]
    map_summary = {
        **one_price.get("summary", {}),
        "roomRecords": len(rooms),
        "available": sum(room.get("saleStatus") == "available" for room in rooms),
        "sold": sum(room.get("saleStatus") == "sold" for room in rooms),
        "restricted": sum(room.get("saleStatus") == "restricted" for room in rooms),
        "selfRetained": sum(bool(room.get("selfRetained")) for room in rooms),
    }

    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    def money(value: Any) -> str:
        number = to_float(value)
        return "—" if number is None else f"{number:,.0f} 元/㎡"

    def total_money(value: Any) -> str:
        number = to_float(value)
        return "—" if number is None else f"{number:,.0f} 元"

    def area_text(value: Any) -> str:
        number = to_float(value)
        return "—" if number is None else f"{number:,.2f} ㎡"

    # Reproduce the reference dashboard's deterministic price palette.
    unique_prices = sorted({float(room["price"]) for room in rooms if room.get("price") is not None})
    price_colors = {
        value: f"hsl({(index * 137.508) % 360:.2f},72%,{40 + (index % 4) * 7}%)"
        for index, value in enumerate(unique_prices)
    }

    columns_seen: set[tuple[str, str]] = set()
    columns: list[dict[str, str]] = []
    floors_seen: set[str] = set()
    floors: list[str] = []
    matrix: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    max_rooms_per_column = 1
    for room in rooms:
        building = str(room.get("buildingLabel") or room.get("building") or "")
        unit = str(room.get("unitDisplay") or room.get("unit") or "")
        floor = str(room.get("floor") or "")
        column = (building, unit)
        if column not in columns_seen:
            columns_seen.add(column)
            columns.append({"building": building, "unit": unit})
        if floor not in floors_seen:
            floors_seen.add(floor)
            floors.append(floor)
        matrix[(building, unit, floor)].append(room)
        max_rooms_per_column = max(max_rooms_per_column, len(matrix[(building, unit, floor)]))

    columns.sort(key=lambda item: (natural_key(item["building"]), natural_key(item["unit"])))
    floors.sort(key=lambda floor: -floor_numeric(floor))
    building_names = sorted({column["building"] for column in columns}, key=natural_key)
    building_unit_counts = Counter(
        column["building"] for column in columns if column["unit"] != "无独立单元"
    )
    # Open with the building that has the most unit columns; for this project that is 5#,
    # which closely matches the requested dense floor-map screenshot.
    default_building = max(building_names, key=lambda name: building_unit_counts[name]) if building_names else "全部"
    column_max_rooms = {
        (column["building"], column["unit"]): max(
            [len(matrix.get((column["building"], column["unit"], floor), [])) for floor in floors] or [1]
        )
        for column in columns
    }

    header_cells = ['<div class="floor-corner">楼层</div>']
    for column in columns:
        header_cells.append(
            f"<header class='floor-column-head' data-building='{esc(column['building'])}' data-max-rooms='{column_max_rooms[(column['building'], column['unit'])]}'><strong>{esc(column['building'])}</strong>"
            f"<span>{esc(column['unit'])}</span></header>"
        )

    row_html: list[str] = []
    for floor in floors:
        cells = [f"<div class='floor-axis-y'>{esc(floor)}</div>"]
        for column in columns:
            room_list = matrix.get((column["building"], column["unit"], floor), [])
            if not room_list:
                cells.append(
                    f"<div class='building-cell' data-building='{esc(column['building'])}' data-rooms='0'><div class='room-cells'>"
                    f"<div class='floor-missing' aria-label='{esc(column['building'])} {esc(column['unit'])} {esc(floor)} 无房源'>—</div>"
                    "</div></div>"
                )
                continue
            room_list.sort(key=lambda room: natural_key(room.get("roomName", "")))
            room_cards: list[str] = []
            for room in room_list:
                status = room.get("saleStatus")
                available = status == "available"
                css_status = "available" if available else "sold"
                status_text = room.get("saleStatusText") or ("未售" if available else "已售")
                if room.get("selfRetained"):
                    status_text += "·自留"
                price = room.get("price")
                title = (
                    f"{column['building']} {column['unit']} {floor}F {room.get('roomName','')}｜"
                    f"{area_text(room.get('area'))}｜{money(price)}｜{total_money(room.get('totalPrice'))}｜"
                    f"{room.get('saleStatusText','')}{'；企业自留' if room.get('selfRetained') else ''}"
                )
                color_style = ""
                if available and price is not None:
                    color_style = f" style='--price-color:{price_colors[float(price)]}'"
                flags = " ".join(esc(flag) for flag in room.get("flags", []))
                room_cards.append(
                    f"<a class='floor-room {css_status}' href='{esc(room.get('detailUrl',''))}' target='_blank' "
                    f"title='{esc(title)}' aria-label='{esc(title)}'{color_style}>"
                    f"<span class='floor-price'>{esc(money(price))}</span>"
                    f"<strong>{esc(room.get('roomName',''))}</strong>"
                    f"<span class='floor-meta'>{esc(area_text(room.get('area')))}</span>"
                    f"<span class='floor-total'>{esc(total_money(room.get('totalPrice')))}</span>"
                    f"<span class='status {css_status}'>{esc(status_text)}</span>"
                    f"{f'<span class=\'status abnormal\'>{flags}</span>' if flags else ''}"
                    "<span class='price-chip' aria-hidden='true'></span></a>"
                )
            cells.append(
                f"<div class='building-cell' data-building='{esc(column['building'])}' data-rooms='{len(room_list)}'><div class='room-cells'>" + "".join(room_cards) + "</div></div>"
            )
        row_html.append(f"<div class='floor-row' data-floor='{esc(floor)}'>" + "".join(cells) + "</div>")

    building_width = max_rooms_per_column * 119 + 8
    matrix_html = (
        f"<div class='floor-matrix' style='--floor-columns:{len(columns)};--floor-count:{len(floors)};"
        f"--building-width:{building_width}px' aria-label='楼栋从左到右、楼层从上到下、同一楼层横向一排'>"
        "<div class='floor-row header-row'>" + "".join(header_cells) + "</div>" + "".join(row_html) + "</div>"
    )

    summary = map_summary
    content = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(project.get('official_name',''))} · 楼层价格地图</title>
<style>
:root{{--ease-out:cubic-bezier(.22,.61,.36,1);--panel:#1d1d20;--panel-soft:#2c2c2e;--ink:#f5f5f7;--muted:#a1a1a6;--line:#38383a;--brand:#2997ff;--focus:#64a8ff}}
*{{box-sizing:border-box}}body{{margin:0;background:#0b141c;color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.page{{display:flex;flex-direction:column;height:100vh;overflow:hidden}}
.page-head{{flex:0 0 auto;display:flex;justify-content:space-between;gap:16px;align-items:center;padding:14px 18px;border-bottom:1px solid #38383a;background:#101922}}
.page-head h1{{margin:0;font-size:18px;letter-spacing:-.02em}}.page-head p{{margin:2px 0 0;color:#a1a1a6;font-size:12px}}.summary{{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}}.summary span{{padding:5px 9px;border:1px solid #38383a;border-radius:999px;background:#1d1d20;color:#a1a1a6;font-size:11px;font-weight:750}}.summary b{{color:#f5f5f7}}
.one-price{{display:flex;flex:1;min-height:0;flex-direction:column}}.floor-toolbar{{position:relative;z-index:5;display:flex;flex-wrap:wrap;align-items:center;gap:6px;min-height:52px;padding:8px 14px;border-bottom:1px solid #38383a;background:#101922}}
.floor-toolbar button,.zoom-control{{height:34px;padding:0 12px;border:1px solid #48484a;border-radius:999px;background:#1d1d20;color:#f5f5f7;font:inherit;font-size:12px;font-weight:700}}
.floor-toolbar button{{cursor:pointer}}.floor-toolbar button:hover{{border-color:#2997ff;color:#64a8ff}}.building-tabs{{display:flex;align-items:center;gap:6px;margin-right:auto}}.building-tabs span{{color:#a1a1a6;font-size:11px;font-weight:800}}.building-tabs button.active{{border-color:#2997ff;background:#123451;color:#cbe6ff}}.zoom-control{{display:flex;align-items:center;gap:8px;color:#a1a1a6}}.zoom-control input{{accent-color:#2997ff}}.floor-note{{margin:0;padding:9px 16px;border-bottom:1px solid #38383a;background:#0d1720;color:#a1a1a6;font-size:12px}}
.floor-results{{--floor-scale:1;--pan-x:0px;--pan-y:0px;--map-surface:#0b141c;--map-panel:#1d1d20;--map-panel-soft:#2c2c2e;--map-ink:#f5f5f7;--map-muted:#a1a1a6;--map-line:#38383a;--map-line-strong:#5a5a5e;--map-brand:#2997ff;--map-brand-ink:#64a8ff;--map-focus:#64a8ff;--map-available:#30d158;--map-sold:#ff453a;--map-ghost:#98989d;--floor-axis:52px;--floor-height:96px;--room-width:112px;position:relative;flex:1;min-height:0;overflow:hidden;background:var(--map-surface);color:var(--map-ink)}}
.floor-stage.floor-map{{position:relative;z-index:1;height:100%;overflow:hidden;cursor:grab;touch-action:none;overscroll-behavior:contain;user-select:none;-webkit-user-select:none;background-image:radial-gradient(circle at 50% 0,#24242758 0,transparent 62%),repeating-linear-gradient(90deg,#f5f5f70d 0 1px,transparent 1px 32px),repeating-linear-gradient(180deg,#f5f5f70d 0 1px,transparent 1px 32px)}}.floor-stage.floor-map:active,.floor-stage.floor-map.dragging{{cursor:grabbing}}
.floor-canvas{{position:relative;display:block;width:max-content;padding:18px;transform-origin:0 0;transform:translate3d(var(--pan-x),var(--pan-y),0) scale(var(--floor-scale));will-change:transform}}
.floor-matrix{{display:grid;width:max-content;grid-template-columns:var(--floor-axis) repeat(var(--floor-columns),var(--building-width));grid-template-rows:58px repeat(var(--floor-count),var(--floor-height));grid-auto-rows:var(--floor-height);gap:0 12px}}.floor-row{{display:grid;grid-column:1/-1;grid-template-columns:var(--floor-axis) repeat(var(--floor-columns),var(--building-width));align-items:stretch}}.floor-row.header-row{{grid-auto-rows:58px;min-height:58px}}.floor-corner{{display:flex;align-items:end;padding:0 0 9px;color:var(--map-muted);font-size:10px;font-weight:650}}.floor-column-head{{display:grid;align-content:center;gap:2px;min-height:52px;padding:7px 9px;border:1px solid var(--map-line);border-radius:12px;background:var(--map-panel);text-align:center}}.floor-column-head strong{{color:var(--map-ink);font-size:13px}}.floor-column-head span{{color:var(--map-muted);font-size:10px}}.floor-axis-y{{display:flex;align-items:center;justify-content:center;border-right:1px solid #48484a;color:var(--map-muted);font-size:11px;font-weight:650}}
.building-cell{{position:relative;height:var(--floor-height);min-width:0;overflow:hidden;padding:4px;border:1px solid #48484a;border-radius:10px;background:#1d1d20}}.room-cells{{display:grid;height:calc(var(--floor-height) - 10px);grid-template-rows:100%;grid-auto-rows:100%;grid-auto-flow:column;grid-auto-columns:var(--room-width);gap:6px}}
.floor-room{{position:relative;display:block;width:100%;height:100%;min-width:0;min-height:0;padding:7px 9px 6px 10px;overflow:hidden;border:1px solid #48484a;border-left:4px solid var(--price-color,var(--map-brand));border-radius:7px;background:var(--map-panel);color:var(--map-ink);text-decoration:none;box-shadow:0 1px 2px #00000040}}.floor-room.available{{background:color-mix(in srgb,var(--map-available) 7%,var(--map-panel))}}.floor-room.sold{{--price-color:var(--map-ghost);border-color:#48484a;background:color-mix(in srgb,var(--map-ghost) 12%,var(--map-panel));color:var(--map-ghost)}}.floor-room:hover,.floor-room:focus-visible{{border-color:#2997ff;box-shadow:0 0 0 3px #2997ff24,0 8px 20px #00000040;outline:none}}.floor-price{{display:block;color:var(--map-muted);font-size:9px;font-weight:650;line-height:1.2;font-variant-numeric:tabular-nums}}.floor-room strong{{display:block;margin-top:1px;font-size:12px;line-height:1.2;color:currentColor}}.floor-meta{{display:block;margin-top:2px;color:var(--map-muted);font-size:9px}}.floor-total{{display:block;margin-top:2px;color:var(--map-muted);font-size:9px;font-variant-numeric:tabular-nums}}
.status{{display:inline-flex;height:17px;align-items:center;padding:0 5px;margin-top:3px;border-radius:999px;background:#2c2c2e;color:#a1a1a6;font-size:8px;font-weight:800}}.status.available{{color:#75d9a5;background:#12351f}}.status.sold{{color:#f0b76a;background:#3b2a12}}.status.abnormal{{margin-left:3px;color:#ff9d7a;background:#43110c}}.price-chip{{position:absolute;top:5px;right:5px;width:10px;height:10px;border:2px solid #1d1d20;border-radius:3px;background:var(--price-color,var(--map-brand))}}.floor-room.sold .price-chip{{background:var(--map-ghost)}}.floor-missing{{display:flex;width:100%;height:100%;min-height:100%;align-items:center;justify-content:center;border:1px solid #48484a;border-radius:7px;background:#242427;color:#98989d;font-size:11px;font-weight:650}}
.page.fullscreen .page-head,.page.fullscreen .floor-note{{display:none}}.page.fullscreen .floor-toolbar{{position:absolute;top:12px;right:12px;z-index:20}}@media(max-width:760px){{.page-head{{align-items:flex-start;flex-direction:column}}.summary{{justify-content:flex-start}}}}
</style></head><body><div class="page" id="page">
<header class="page-head"><div><h1>{esc(project.get('official_name',''))} · 楼层价格地图</h1><p>官方楼盘表 + TimeFL 一房一价 · 生成时间 {esc(one_price.get('generatedAt',''))}</p></div><div class="summary"><span>房源 <b>{esc(summary.get('roomRecords',0))}</b></span><span>未售 <b>{esc(summary.get('available',0))}</b></span><span>已售 <b>{esc(summary.get('sold',0))}</b></span><span>自留 <b>{esc(summary.get('selfRetained',0))}</b></span><span>楼栋/单元 <b>{esc(len(columns))}</b></span></div></header>
<div class="one-price"><div class="floor-toolbar"><div class="building-tabs" id="buildingTabs" data-default="{esc(default_building)}"><span>楼栋</span><button type="button" data-building="全部" class="active">全部</button>{''.join(f'<button type="button" data-building="{esc(name)}">{esc(name)}</button>' for name in building_names)}</div><button id="zoomOut" type="button">－</button><label class="zoom-control">缩放 <input id="zoomRange" type="range" min="18" max="220" value="100"><span id="zoomScale">100%</span></label><button id="zoomIn" type="button">＋</button><button id="reset" type="button">复位</button><button id="fullscreen" type="button">全屏地图</button></div>
<p class="floor-note">楼栋/单元从左到右，楼层从上到下；同一楼层横向一排。每个方块渲染备案单价、房号、面积、总价和状态；价格色块表示同价房源。地图仅展示有独立单元的住宅格；商业/配套无独立单元房源见 one-price.json。</p>
<div class="floor-results" id="results"><div class="floor-stage floor-map" id="stage" tabindex="0" role="application" aria-label="楼层分布地图，可拖拽、滚轮平移、Ctrl加滚轮缩放"><div class="floor-canvas" id="canvas">{matrix_html}</div></div></div></div></div>
<script>
(function(){{var stage=document.getElementById('stage'),canvas=document.getElementById('canvas'),range=document.getElementById('zoomRange'),label=document.getElementById('zoomScale'),page=document.getElementById('page');var state={{scale:1,x:0,y:0}},matrix=document.querySelector('.floor-matrix'),tabs=document.getElementById('buildingTabs');
function applyBuilding(name){{var headers=[].slice.call(matrix.querySelectorAll('.floor-column-head'));var rows=[].slice.call(matrix.querySelectorAll('.floor-row:not(.header-row)'));var visibleHeaders=headers.filter(function(item){{return name==='全部'||item.dataset.building===name}});var maxRooms=1;visibleHeaders.forEach(function(item){{item.style.display='';maxRooms=Math.max(maxRooms,Number(item.dataset.maxRooms||1))}});headers.forEach(function(item){{if(visibleHeaders.indexOf(item)<0)item.style.display='none'}});var visibleCount=0;rows.forEach(function(row){{var cells=[].slice.call(row.querySelectorAll('.building-cell'));var visibleCells=cells.filter(function(item){{return name==='全部'||item.dataset.building===name}});var hasRooms=name==='全部'||visibleCells.some(function(item){{return Number(item.dataset.rooms||0)>0}});cells.forEach(function(item){{item.style.display=visibleCells.indexOf(item)>=0?'':'none'}});row.style.display=hasRooms?'':'none';if(hasRooms)visibleCount++}});matrix.style.setProperty('--floor-columns',String(visibleHeaders.length));matrix.style.setProperty('--floor-count',String(visibleCount));matrix.style.setProperty('--building-width',(maxRooms*119+8)+'px');tabs.querySelectorAll('button').forEach(function(item){{item.classList.toggle('active',item.dataset.building===name)}});setTimeout(fit,0)}}
tabs.addEventListener('click',function(e){{var button=e.target.closest('button[data-building]');if(button)applyBuilding(button.dataset.building)}});
function clamp(v,min,max){{return Math.min(max,Math.max(min,v))}}
function apply(){{document.getElementById('results').style.setProperty('--floor-scale',state.scale.toFixed(3));document.getElementById('results').style.setProperty('--pan-x',state.x.toFixed(2)+'px');document.getElementById('results').style.setProperty('--pan-y',state.y.toFixed(2)+'px');canvas.style.transform='translate3d('+state.x+'px,'+state.y+'px,0) scale('+state.scale.toFixed(4)+')';range.value=String(Math.round(state.scale*100));label.textContent=Math.round(state.scale*100)+'%'}}
function fit(){{var w=Math.max(1,canvas.offsetWidth),h=Math.max(1,canvas.offsetHeight),aw=Math.max(1,stage.clientWidth-32),ah=Math.max(1,stage.clientHeight-32);var all=Math.min(aw/w,ah/h,1),height=Math.min(ah/h,1);state.scale=clamp(all>=.55?all:height,.18,2.2);state.x=Math.max(0,(stage.clientWidth-w*state.scale)/2);state.y=Math.max(0,(stage.clientHeight-h*state.scale)/2);apply()}}
function zoomAt(x,y,next){{next=clamp(next,.18,2.2);var old=state.scale,wx=(x-state.x)/old,wy=(y-state.y)/old;state.scale=next;state.x=x-wx*next;state.y=y-wy*next;apply()}}
stage.addEventListener('wheel',function(e){{e.preventDefault();if(e.ctrlKey||e.metaKey){{var r=stage.getBoundingClientRect();zoomAt(e.clientX-r.left,e.clientY-r.top,state.scale*Math.exp(-e.deltaY*.0022))}}else{{state.x-=e.deltaX;state.y-=e.deltaY;apply()}}}},{{passive:false}});
var pointers=new Map(),moved=false;stage.addEventListener('pointerdown',function(e){{if(e.button!==0)return;moved=false;pointers.set(e.pointerId,{{x:e.clientX,y:e.clientY}});stage.classList.add('dragging');stage.setPointerCapture(e.pointerId)}});stage.addEventListener('pointermove',function(e){{if(!pointers.has(e.pointerId))return;var p=pointers.get(e.pointerId),dx=e.clientX-p.x,dy=e.clientY-p.y;if(Math.hypot(dx,dy)>3)moved=true;pointers.set(e.pointerId,{{x:e.clientX,y:e.clientY}});state.x+=dx;state.y+=dy;apply()}});function end(e){{pointers.delete(e.pointerId);if(!pointers.size)stage.classList.remove('dragging')}}stage.addEventListener('pointerup',end);stage.addEventListener('pointercancel',end);
document.getElementById('zoomRange').addEventListener('input',function(){{var r=stage.getBoundingClientRect();zoomAt(r.width/2,r.height/2,Number(this.value)/100)}});document.getElementById('zoomIn').onclick=function(){{var r=stage.getBoundingClientRect();zoomAt(r.width/2,r.height/2,state.scale+.08)}};document.getElementById('zoomOut').onclick=function(){{var r=stage.getBoundingClientRect();zoomAt(r.width/2,r.height/2,state.scale-.08)}};document.getElementById('reset').onclick=fit;document.getElementById('fullscreen').onclick=function(){{var on=page.classList.toggle('fullscreen');this.textContent=on?'退出全屏':'全屏地图';if(document.fullscreenElement)document.exitFullscreen();else if(page.requestFullscreen)page.requestFullscreen();setTimeout(fit,80)}};document.addEventListener('fullscreenchange',function(){{setTimeout(fit,80)}});window.addEventListener('resize',fit);applyBuilding(tabs.dataset.default||'全部');}})();
</script></body></html>"""
    atomic_write(path, content)


def write_html_report(path: Path, report: dict[str, Any], rows: list[dict[str, Any]], one_price: dict[str, Any]) -> None:
    project = report["project"]
    style = """
    :root{--ink:#172033;--muted:#65748b;--line:#dfe5ee;--bg:#f4f6fa;--card:#fff;--blue:#174eb0;--red:#c62828;--green:#128a5c;--gray:#68758a;--amber:#8a4b00}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.5}
    .wrap{max-width:1400px;margin:auto;padding:24px 16px 60px}header{background:linear-gradient(135deg,#102a5e,#174eb0,#1d86d6);color:#fff;border-radius:20px;padding:28px;margin-bottom:16px}
    h1{margin:0 0 8px;font-size:30px}.meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.meta span{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.25);border-radius:99px;padding:5px 10px;font-size:13px}
    .kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:16px}.kpi{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px}.kpi b{display:block;font-size:24px}.kpi span{color:var(--muted);font-size:13px}
    section{background:#fff;border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:14px}.scroll{overflow:auto;border:1px solid var(--line);border-radius:10px}
    table{border-collapse:collapse;width:100%;font-size:13px}th{background:#f1f4f9;text-align:left;padding:9px;position:sticky;top:0}td{padding:8px 9px;border-top:1px solid var(--line);vertical-align:top}.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
    .muted{color:var(--muted)}a{color:var(--blue)}.warn{background:#fff8e6;color:var(--amber);border:1px solid #f0d48a;border-radius:99px;padding:3px 8px;font-size:12px;white-space:nowrap}
    .floor-map{display:grid;grid-template-columns:repeat(auto-fit,minmax(680px,1fr));gap:16px;align-items:start}.building-card{border:1px solid var(--line);border-radius:16px;overflow:hidden;background:#fff}.building-head{padding:13px 15px;background:#eaf1f9;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:10px;align-items:center}.floor-row{display:grid;grid-template-columns:66px 205px 1fr;gap:10px;padding:10px 13px;border-bottom:1px solid var(--line);align-items:start}.floor-row:last-child{border-bottom:0}.floor-no{font-weight:800;color:#26364f}.floor-price{border-left:3px solid #174eb0;padding-left:8px}.floor-price b{display:block;font-size:15px}.floor-price small{color:var(--muted)}
    .floor-row{display:grid;grid-template-columns:62px 1fr;gap:10px;padding:9px 12px;border-bottom:1px solid var(--line)}.floor-row:last-child{border-bottom:0}.floor-no{font-weight:800;color:#33415c}.room-line{display:flex;flex-wrap:wrap;gap:7px}
    .room{display:grid;grid-template-columns:1fr;min-width:112px;padding:7px 8px;border-radius:9px;border:1.5px solid;text-decoration:none;font-size:11.5px;line-height:1.35}.room b{font-size:14px}.room small{display:block;color:inherit;opacity:.78}.room.sold{color:#8f1d1d;background:#fff1f0;border-color:#f2b8b5}.room.available{color:#085d43;background:#edfff8;border-color:#95dcc2}.room.restricted{color:#4b5563;background:#f2f4f7;border-color:#cbd5e1}.room .tag{display:inline-block;margin-top:3px;background:rgba(255,255,255,.65);border-radius:4px;padding:0 4px}
    .legend{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:10px 0 14px;font-size:12px}.dot{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:4px}
    @media(max-width:900px){.kpis{grid-template-columns:repeat(2,1fr)}header{padding:20px}h1{font-size:24px}.floor-map{grid-template-columns:1fr}}
    """
    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    def fmt(value: Any, digits: int = 0, suffix: str = "") -> str:
        if value is None or value == "":
            return "—"
        try:
            number = float(value)
        except ValueError:
            return esc(value)
        return f"{number:,.{digits}f}{suffix}"

    table_rows = []
    for row in rows:
        retained = "企业自留" if row.get("企业自留房") else ""
        table_rows.append(
            "<tr>"
            f"<td>{esc(building_label(row.get('building_name','')))}</td><td>{esc(unit_display(row.get('unit','')))}</td>"
            f"<td>{esc(row.get('floor',''))}F</td><td>{esc(room_display_name(row.get('floor'), row.get('room')))}</td>"
            f"<td>{esc(row.get('status',''))}{' / '.join([''] + row.get('flags', []))}</td>"
            f"<td>{esc(retained)}</td><td class='num'>{fmt(row.get('面积'),2,'㎡')}</td>"
            f"<td class='num'>{fmt(row.get('单价'),0,'元/㎡')}</td><td class='num'>{fmt(row.get('总价万'),2,'万')}</td>"
            f"<td>{esc(row.get('预售（现售）许可证号',''))}</td>"
            f"<td><a href='{esc(row.get('detail_url',''))}' target='_blank'>单套</a></td></tr>"
        )
    table_html = "".join(table_rows) or "<tr><td colspan='11' class='muted'>无符合条件房源</td></tr>"

    # Visual hierarchy is strictly: building -> floor -> priced room cells.
    # Units remain visible on each room chip, rather than splitting one floor across cards.
    building_floor_groups = one_price.get("building_floor_groups", [])
    building_cards: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for group in building_floor_groups:
        building_cards[group["building"]].append(group)
    building_html_parts: list[str] = []
    for building, groups in sorted(building_cards.items(), key=lambda item: natural_key(item[0])):
        floor_html: list[str] = []
        for group in groups:
            chips: list[str] = []
            for room in group.get("rooms", []):
                status = room.get("saleStatus", "restricted")
                retained = "<span class='tag'>自留</span>" if room.get("selfRetained") else ""
                flags = "".join(f"<span class='tag'>{esc(flag)}</span>" for flag in room.get("flags", []))
                title = f"{building} {room.get('unitDisplay','')} {room.get('roomName','')}｜{fmt(room.get('area'),2,'㎡')}｜{fmt(room.get('price'),0,'元/㎡')}｜{fmt(room.get('totalPriceWan'),2,'万')}｜{room.get('saleStatusText','')}{'；企业自留' if room.get('selfRetained') else ''}"
                unit_tag = f"<span class='tag'>{esc(room.get('unitDisplay',''))}</span>"
                chips.append(
                    f"<a class='room {esc(status)}' href='{esc(room.get('detailUrl',''))}' target='_blank' title='{esc(title)}'>"
                    f"<b>{esc(room.get('roomName',''))}</b>{unit_tag}"
                    f"<small>{fmt(room.get('area'),2,'㎡')}</small>"
                    f"<small class='price'>{fmt(room.get('price'),0,'元/㎡')}</small>"
                    f"<small>{fmt(room.get('totalPriceWan'),1,'万')}</small>{retained}{flags}</a>"
                )
            floor_html.append(
                f"<div class='floor-row'><div class='floor-no'>{esc(group.get('floor',''))}F</div>"
                f"<div class='floor-price'><b>{fmt(group.get('price_min'),0)}—{fmt(group.get('price_max'),0)}</b><small>元/㎡ · 均价 {fmt(group.get('price_avg'),0,'元/㎡')}</small><small>{group.get('room_count',0)}套 · 未售{group.get('unsold_count',0)}/已售{group.get('sold_count',0)}/限制{group.get('restricted_count',0)}</small></div>"
                f"<div><div class='room-line'>{''.join(chips)}</div></div></div>"
            )
        building_html_parts.append(
            f"<article class='building-card'><div class='building-head'><strong>{esc(building)}</strong><span class='muted small'>{' / '.join(groups[0].get('units', []))} · {len(groups)} 个价格楼层</span></div>{''.join(floor_html)}</article>"
        )
    floor_map_html = "".join(building_html_parts) or "<p class='muted'>未抓取单套价格，暂无楼层价格分布。</p>"

    summary = one_price.get("summary", {})
    content = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(project['official_name'])} 楼栋·单元·楼层价格</title><style>{style}</style></head><body><div class="wrap">
<header><h1>{esc(project['official_name'])} · 楼栋 / 楼层价格</h1><div>{esc(report['query']['name_or_id'])} · 官方代码查询 · {esc(report['query']['run_time'])}</div><div class="meta"><span>DengJH：{esc(project['dengjh'])}</span><span>总套数：{esc(project.get('total_units'))}</span><span>住房可售：{esc(project.get('housing_for_sale'))}</span><span>筛选：{esc(report['query']['filter_text'])}</span></div></header>
<div class="kpis"><div class="kpi"><b>{esc(report['room_summary']['total'])}</b><span>解析房表格子</span></div><div class="kpi"><b>{esc(report['room_summary'].get('未售',0))}</b><span>未售</span></div><div class="kpi"><b>{esc(report['room_summary'].get('已售',0))}</b><span>已售</span></div><div class="kpi"><b>{esc(report['detail_summary']['success'])}</b><span>单套价格核验成功</span></div><div class="kpi"><b>{esc(summary.get('floorGroups',0))}</b><span>楼栋×楼层价格组</span></div><div class="kpi"><b>{esc(report['room_link_summary']['no_valid_detail_links'])}</b><span>无有效单套价格链接</span></div></div>
<section><h2>结论摘要</h2><p>{esc(report['conclusion'])}</p><p class="muted">白色未售不等于可买；单套页若标注企业自留房，必须向销售确认是否释放。绿色=未售，红色=已售，灰色=限制/配套。</p></section>
<section><h2>价格覆盖说明</h2><p>官方房表共解析 {esc(report['room_summary']['total'])} 个格子，其中 {esc(report['room_link_summary']['valid_detail_links'])} 个有有效 TimeFL 单套链接，已全部纳入下方楼层价格图；{esc(report['room_link_summary']['no_valid_detail_links'])} 个无有效链接或为零 GUID，不能核价，明细见 unpriced_rooms.csv。</p><div class="scroll"><table><thead><tr><th>楼栋</th><th>有效价格链接</th><th>无有效链接</th><th>房表格子</th><th>状态分布</th></tr></thead><tbody>{''.join(f"<tr><td>{esc(item['building'])}</td><td class='num'>{esc(item['valid_detail_links'])}</td><td class='num'>{esc(item['no_valid_detail_links'])}</td><td class='num'>{esc(item['room_cells'])}</td><td>{esc(item['status_counts'])}</td></tr>" for item in report['room_link_summary']['no_valid_detail_by_building'])}</tbody></table></div></section>
<section><h2>楼栋 × 每层价格分布</h2><div class="legend"><span><i class="dot" style="background:#95dcc2"></i>未售</span><span><i class="dot" style="background:#f2b8b5"></i>已售</span><span><i class="dot" style="background:#cbd5e1"></i>限制/配套</span><span>每个方块=一套：房号 / 面积 / 备案单价 / 备案总价</span></div><div class="floor-map'>{floor_map_html}</div></section>
<section><h2>筛选结果 {len(rows)} 套</h2><div class="scroll"><table><thead><tr><th>楼栋</th><th>单元</th><th>楼层</th><th>房号</th><th>官方状态</th><th>自留</th><th>建面㎡</th><th>备案单价</th><th>备案总价万</th><th>预售证</th><th>链接</th></tr></thead><tbody>{table_html}</tbody></table></div></section>
<section><h2>官方入口</h2><p><a href="{esc(project['detail_url'])}" target="_blank">项目明细</a> · <a href="{esc(report['building_table_url'])}" target="_blank">楼盘表·栋表</a></p></section>
</div></body></html>"""
    atomic_write(path, content)



def fetch_building_task(args: tuple[dict[str, Any], HttpClient]) -> tuple[dict[str, Any], str]:
    building, template_client = args
    client = HttpClient(template_client.cache_dir, template_client.timeout, template_client.refresh)
    page = client.cached_get_text(
        building["room_table_url"],
        retries=4,
        validator=lambda text: "楼盘表" in text and ("TimeFL" in text or "标识" in text),
    )
    return building, page


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    name_group = parser.add_mutually_exclusive_group(required=True)
    name_group.add_argument("--name", help="营销案名/官方项目名；支持内置别名")
    name_group.add_argument("--dengjh", help="官方登记号，例如 示例登记号")
    parser.add_argument("--official-name", help="强制用官方项目名查询")
    parser.add_argument("--district", default="", help="行政区，可留空")
    parser.add_argument("--budget", type=parse_range, help="总价区间，单位万，例如 130-170")
    parser.add_argument("--area", type=parse_range, help="建筑面积区间，例如 90-120")
    parser.add_argument("--exclude-self-retained", action="store_true", help="排除单套页标注企业自留房的房源")
    parser.add_argument("--details-status", choices=("all", "unsold"), default="all", help="抓取单套价格的状态范围：all=全部可点击房源，unsold=仅未售；默认all")
    parser.add_argument("--no-details", action="store_true", help="只解析房表状态，不逐套抓取TimeFL价格")
    parser.add_argument("--workers", type=int, default=4, help="单套详情并发数，默认4")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP超时秒数")
    parser.add_argument("--cache-dir", type=Path, default=Path.home()/".cache/wuhan-property-research", help="缓存目录")
    parser.add_argument("--no-cache", action="store_true", help="不在本机写入URL缓存；适合隐私敏感查询")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新抓取")
    parser.add_argument("--out-dir", type=Path, help="输出目录；默认 wuhan-output/<项目>-<时间>")
    parser.add_argument("--no-html", action="store_true", help="不生成HTML")
    args = parser.parse_args()

    if args.workers < 1 or args.workers > 12:
        parser.error("--workers must be between 1 and 12")

    run_time = datetime.now().isoformat(timespec="seconds")
    query_name = args.name or args.dengjh or ""
    out_dir = args.out_dir or Path("wuhan-output") / f"{slugify(query_name)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = None if args.no_cache else args.cache_dir
    client = HttpClient(cache_dir, args.timeout, args.refresh)

    # 1. Resolve project and get detail/list counts.
    project, resolution = resolve_project(client, args.name, args.official_name, args.dengjh, args.district)
    detail_data = parse_project_detail(client.cached_get_text(project.detail_url, validator=lambda s: "项目名称" in s and "Label" not in s and "参数错误" not in s))
    if detail_data.get("项目名称"):
        project.official_name = detail_data["项目名称"]
    project.detail_url = canonical_url(project.detail_url)

    # 2. Building table.
    building_table_url = canonical_url(f"{BASE_PROJECT_URL}spfcx_lpb.aspx?DengJH={project.dengjh}")
    building_page = client.cached_get_text(building_table_url, validator=lambda s: "楼盘表" in s and "spfcx_fang" in s)
    buildings = parse_building_table(building_page, building_table_url)
    if not buildings:
        raise RuntimeError("Official building table has no clickable building rows")

    # 3. Room tables, fetched concurrently. Building count is normally small (5--30 pages).
    rooms: list[dict[str, Any]] = []
    building_summaries: list[dict[str, Any]] = []
    building_workers = min(args.workers, max(1, len(buildings)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=building_workers) as executor:
        building_pages = list(
            executor.map(fetch_building_task, [(building, client) for building in buildings])
        )
    for number, (building, page) in enumerate(building_pages, 1):
        current_rooms = parse_room_table(page, building)
        rooms.extend(current_rooms)
        counter = Counter(room["status"] for room in current_rooms)
        building_summaries.append(
            {
                **building,
                "parsed_cells": len(current_rooms),
                "status_counts": dict(counter),
            }
        )

    # 4. Optional TimeFL details. Default is all clickable rooms so the report can show
    # every building + unit + floor price, including already-sold comparables.
    detail_rows: list[dict[str, Any]] = []
    if not args.no_details:
        if args.details_status == "unsold":
            targets = [room for room in rooms if room["status"] == "未售" and room["has_detail"]]
        else:
            targets = [room for room in rooms if room["has_detail"]]
        max_workers = min(args.workers, max(1, len(targets))) if targets else 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            detail_rows = list(
                executor.map(
                    fetch_detail_task,
                    [(room, client) for room in targets],
                )
            )

    # 5. Filtering. Budget candidates include price-fit self-retained units.
    filtered_rows: list[dict[str, Any]] = []
    for row in detail_rows:
        # Candidate filtering applies only to current unsold rooms. Sold rooms remain in
        # the full floor-price map for price comparables, but never enter the buy list.
        if row.get("status") != "未售":
            continue
        if not within_range(row.get("总价万"), args.budget):
            continue
        if not within_range(row.get("面积"), args.area):
            continue
        if args.exclude_self_retained and row.get("企业自留房"):
            continue
        filtered_rows.append(row)
    filtered_rows.sort(
        key=lambda row: (
            row.get("总价万") or 10**9,
            row.get("building_name", ""),
            str(row.get("unit", "")),
            to_float(row.get("floor")) or 0,
            str(row.get("room", "")),
        )
    )

    room_counter = Counter(room["status"] for room in rooms)
    detail_success = sum(not row.get("detail_error") for row in detail_rows)
    retained_count = sum(bool(row.get("企业自留房")) for row in detail_rows)
    buyable_count = sum(
        row.get("status") == "未售"
        and not row.get("detail_error")
        and not row.get("企业自留房")
        and not row.get("flags")
        and within_range(row.get("总价万"), args.budget)
        and within_range(row.get("面积"), args.area)
        for row in detail_rows
    )
    filter_text = "; ".join(
        part
        for part in [
            f"预算 {args.budget[0]}-{args.budget[1]}万" if args.budget else "",
            f"面积 {args.area[0]}-{args.area[1]}㎡" if args.area else "",
            "排除企业自留房" if args.exclude_self_retained else "",
            "未抓取单套价格" if args.no_details else "",
        ]
        if part
    )
    if args.no_details:
        conclusion = "已快速解析项目、栋表和房表；未逐套核价，不能判断单套是否在预算内。"
    elif args.exclude_self_retained:
        conclusion = (
            f"单套核价成功 {detail_success} 套，其中企业自留 {retained_count} 套；"
            f"按当前筛选且可初步视为可买候选 {buyable_count} 套。"
        )
    else:
        conclusion = (
            f"单套核价成功 {detail_success} 套，其中企业自留 {retained_count} 套；"
            f"价格/面积匹配 {len(filtered_rows)} 套，但企业自留房源必须先向销售确认释放。"
        )

    report: dict[str, Any] = {
        "query": {
            "name_or_id": query_name,
            "official_name_arg": args.official_name or "",
            "district": args.district,
            "budget": list(args.budget) if args.budget else None,
            "area": list(args.area) if args.area else None,
            "exclude_self_retained": args.exclude_self_retained,
            "details_status": "none" if args.no_details else args.details_status,
            "workers": args.workers,
            "run_time": run_time,
            "filter_text": filter_text or "全部未售且可核价房源",
            "resolution": resolution,
        },
        "project": asdict(project),
        "project_detail": detail_data,
        "building_table_url": building_table_url,
        "buildings": building_summaries,
        "room_summary": {"total": len(rooms), **dict(room_counter)},
        "room_link_summary": {
            "total": len(rooms),
            "valid_detail_links": sum(room["has_detail"] for room in rooms),
            "no_valid_detail_links": sum(not room["has_detail"] for room in rooms),
            "no_valid_detail_by_building": [
                {
                    "building_name": building["building_name"],
                    "building": building_label(building["building_name"]),
                    "room_cells": sum(room["building_name"] == building["building_name"] for room in rooms),
                    "valid_detail_links": sum(room["has_detail"] and room["building_name"] == building["building_name"] for room in rooms),
                    "no_valid_detail_links": sum(not room["has_detail"] and room["building_name"] == building["building_name"] for room in rooms),
                    "status_counts": dict(Counter(room["status"] for room in rooms if room["building_name"] == building["building_name"])),
                }
                for building in buildings
                if any(not room["has_detail"] and room["building_name"] == building["building_name"] for room in rooms)
            ],
        },
        "detail_summary": {
            "mode": "none" if args.no_details else args.details_status,
            "targets": len(detail_rows),
            "success": detail_success,
            "errors": len(detail_rows) - detail_success,
            "self_retained": retained_count,
            "initial_buyable_candidates": buyable_count,
        },
        "conclusion": conclusion,
    }

    # Build the reference-style, building/unit/floor-oriented one-price dataset.
    one_price = build_one_price_dataset(asdict(project), report["query"], detail_rows)
    flat_floor_groups = flatten_floor_groups(one_price["floor_groups"])
    flat_building_floor_groups = flatten_floor_groups(one_price["building_floor_groups"])

    # Flatten detail rows into CSV-friendly fields.
    flat_details = []
    for row in detail_rows:
        flat = {**row}
        flat.pop("flags", None)
        flat["flags"] = ",".join(row.get("flags", []))
        flat_details.append(flat)
    flat_filtered = []
    for row in filtered_rows:
        flat = {**row}
        flat.pop("flags", None)
        flat["flags"] = ",".join(row.get("flags", []))
        flat_filtered.append(flat)

    atomic_write(out_dir / "report.json", json.dumps(report, ensure_ascii=False, indent=2, default=str))
    atomic_write(out_dir / "one-price.json", json.dumps(one_price, ensure_ascii=False, indent=2, default=str))
    write_csv(out_dir / "all_rooms.csv", rooms)
    write_csv(out_dir / "checked_details.csv", flat_details)
    write_csv(out_dir / "unpriced_rooms.csv", [room for room in rooms if not room["has_detail"]])
    write_csv(out_dir / "building_floor_prices.csv", flat_building_floor_groups)
    write_csv(out_dir / "floor_prices.csv", flat_floor_groups)
    write_csv(out_dir / "filtered.csv", flat_filtered)
    if args.details_status == "unsold":
        write_csv(out_dir / "checked_unsold_details.csv", flat_details)
    if not args.no_html:
        write_reference_floor_map(out_dir / "floor-map.html", one_price)
        write_html_report(out_dir / "report.html", report, filtered_rows, one_price)

    print(json.dumps({
        "project": project.official_name,
        "dengjh": project.dengjh,
        "official_counts": {
            "total_units": project.total_units,
            "housing_sold": project.housing_sold,
            "housing_for_sale": project.housing_for_sale,
            "non_housing_for_sale": project.non_housing_for_sale,
        },
        "buildings": len(buildings),
        "room_cells": len(rooms),
        "room_status": dict(room_counter),
        "checked_details": len(detail_rows),
        "detail_success": detail_success,
        "self_retained": retained_count,
        "floor_price_groups": len(one_price["floor_groups"]),
        "building_floor_price_groups": len(one_price["building_floor_groups"]),
        "filtered": len(filtered_rows),
        "initial_buyable_candidates": buyable_count,
        "output_dir": str(out_dir.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
