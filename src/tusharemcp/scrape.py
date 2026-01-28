from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, parse_qs


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _coerce_type(type_text: str) -> dict[str, Any]:
    t = (type_text or "").strip().lower()
    if not t:
        return {"type": "string"}
    if t in {"str", "string", "text", "varchar"}:
        return {"type": "string"}
    if t in {"int", "integer", "long"}:
        return {"type": "integer"}
    if t in {"float", "double", "number", "decimal"}:
        return {"type": "number"}
    if t in {"bool", "boolean"}:
        return {"type": "boolean"}
    if "date" in t or "datetime" in t or "time" in t:
        return {"type": "string", "format": "date-time" if "time" in t else "date"}
    return {"type": "string", "ts_type": t}


def _is_required(v: str) -> bool:
    x = (v or "").strip().lower()
    return x in {"y", "yes", "是", "必填", "required", "true", "1"}


def _doc_id_from_url(url: str) -> str | None:
    try:
        q = parse_qs(urlparse(url).query)
        v = q.get("doc_id", [None])[0]
        return str(v) if v else None
    except Exception:
        return None


def _extract_api_name_from_code(text: str) -> str | None:
    # Docs often render code with tokenized spans, which becomes "pro . stock_basic (" after get_text().
    m = re.search(r"\bpro\s*\.\s*(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\s*\(", text)
    if m:
        return m.group("name")
    # Some pages may use pro.query('api_name', ...)
    m = re.search(r"\bpro\s*\.\s*query\s*\(\s*['\"](?P<name>[a-zA-Z_][a-zA-Z0-9_]*)['\"]", text)
    if m:
        return m.group("name")
    return None


def _extract_api_name_from_text(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"接口[:：]\s*([a-zA-Z_][a-zA-Z0-9_]*)", text)
    if m:
        return m.group(1)
    return None


@dataclass(frozen=True)
class ParsedApiDoc:
    api_name: str
    title: str
    description: str
    url: str
    doc_id: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    min_points: int
    max_rows: int | None
    is_special: bool
    permission_required: bool
    permission_granted: bool

    def to_spec(self) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "name": self.api_name,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "doc_id": self.doc_id,
            "input": self.input_schema,
            "output": self.output_schema,
            "min_points": self.min_points,
            "max_rows": self.max_rows,
            "is_special": self.is_special,
            "permission_required": self.permission_required,
            "permission_granted": self.permission_granted,
        }
        return spec


def _extract_min_points(text: str) -> int:
    hits = [int(x) for x in re.findall(r"(\d{1,7})\s*积分", text or "")]
    if not hits:
        return 0
    return min(hits)


def _extract_max_rows(text: str) -> int | None:
    if not text:
        return None
    patterns = [
        r"(?:限量|单次最大|单次最多|单次|每次|单笔|一次|每次返回行数|每次返回|每次可请求股票数|单次可请求股票数|单次请求股票数)[^\\d]{0,20}(\\d{1,7})\\s*(?:条|行|只)",
        r"(\\d{1,7})\\s*(?:条|行|只)[^\\n]{0,12}(?:每次|单次|返回|请求)",
    ]
    numbers: list[int] = []
    for pat in patterns:
        numbers.extend(int(x) for x in re.findall(pat, text))
    return max(numbers) if numbers else None


def _detect_permission_required(text: str) -> bool:
    if not text:
        return False
    return any(
        kw in text
        for kw in [
            "单独开权限",
            "单独开通",
            "需单独开通",
            "需单独申请",
            "权限开通",
            "需申请权限",
        ]
    )


def _parse_table(table) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    header_cells = table.find_all("tr")[0].find_all(["th", "td"])
    headers = [c.get_text(" ", strip=True) for c in header_cells]
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        values = [c.get_text(" ", strip=True) for c in cells]
        row = {headers[i]: values[i] if i < len(values) else "" for i in range(len(headers))}
        rows.append(row)
    return rows


def _map_input_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []

    def pick(row: dict[str, str], keys: Iterable[str]) -> str:
        for k in keys:
            if k in row:
                return row.get(k, "")
        return ""

    for row in rows:
        name = pick(row, ["参数名称", "参数", "字段", "名称", "字段名", "name"]).strip()
        if not name:
            continue
        type_text = pick(row, ["类型", "数据类型", "type"]).strip()
        req_text = pick(row, ["必填", "是否必须", "是否必填", "required", "是否"]).strip()
        desc = pick(row, ["描述", "说明", "desc", "备注"]).strip()
        default = pick(row, ["默认", "default"]).strip()

        schema = {**_coerce_type(type_text), "description": desc}
        if default:
            schema["default"] = default
        properties[name] = schema
        if _is_required(req_text):
            required.append(name)

    return {"type": "object", "properties": properties, "required": sorted(set(required))}


def _map_output_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []

    def pick(row: dict[str, str], keys: Iterable[str]) -> str:
        for k in keys:
            if k in row:
                return row.get(k, "")
        return ""

    for row in rows:
        name = pick(row, ["字段", "字段名", "名称", "参数名称", "name"]).strip()
        if not name:
            continue
        type_text = pick(row, ["类型", "数据类型", "type"]).strip()
        desc = pick(row, ["描述", "说明", "desc", "备注"]).strip()
        fields.append({"name": name, "description": desc, **_coerce_type(type_text)})
    return {"type": "array", "fields": fields}


def parse_api_doc_from_html(*, url: str, html: str, is_special: bool = False) -> ParsedApiDoc | None:
    try:
        from bs4 import BeautifulSoup
    except Exception as e:  # pragma: no cover
        raise SystemExit(f"Missing dependency for scraping: {e}. Install `pip install .[scrape]`.") from e

    soup = BeautifulSoup(html, "lxml")
    title = ""
    for tag in soup.find_all(["h1", "h2"], limit=5):
        t = tag.get_text(" ", strip=True)
        if t:
            title = t
            break
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)

    text = soup.get_text("\n", strip=True)
    api_name = None
    for code in soup.find_all(["code", "pre"], limit=20):
        api_name = _extract_api_name_from_code(code.get_text("\n", strip=True))
        if api_name:
            break
    if not api_name:
        api_name = _extract_api_name_from_code(text)
    if not api_name:
        api_name = _extract_api_name_from_text(text)
    if not api_name:
        return None

    def find_section_table(section_kw: str):
        for header in soup.find_all(["h2", "h3", "h4", "strong"]):
            if section_kw in header.get_text(" ", strip=True):
                table = header.find_next("table")
                if table:
                    return table
        return None

    input_table = find_section_table("输入参数")
    output_table = find_section_table("输出参数")

    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    output_schema: dict[str, Any] = {"type": "array", "fields": []}

    if input_table:
        input_rows = _parse_table(input_table)
        input_schema = _map_input_rows(input_rows)
    if output_table:
        output_rows = _parse_table(output_table)
        output_schema = _map_output_rows(output_rows)

    description = ""
    # Heuristic: first paragraph-like block below title
    first_p = soup.find("p")
    if first_p:
        description = first_p.get_text(" ", strip=True)

    min_points = _extract_min_points(text)
    max_rows = _extract_max_rows(text)
    permission_required = _detect_permission_required(text)

    return ParsedApiDoc(
        api_name=api_name,
        title=title or api_name,
        description=description,
        url=url,
        doc_id=_doc_id_from_url(url),
        input_schema=input_schema,
        output_schema=output_schema,
        min_points=min_points,
        max_rows=max_rows,
        is_special=is_special,
        permission_required=permission_required,
        permission_granted=False,
    )


def _build_special_doc_ids(base_html: str, base_url: str) -> set[str]:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return set()
    soup = BeautifulSoup(base_html, "lxml")
    special_ids: set[str] = set()
    for li in soup.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        if a.get_text(" ", strip=True) != "特色数据":
            continue
        for sub in li.find_all("a", href=True):
            href = sub.get("href") or ""
            if "doc_id=" not in href:
                continue
            full = urljoin(base_url, href)
            doc_id = _doc_id_from_url(full)
            if doc_id:
                special_ids.add(doc_id)
        break
    return special_ids


def scrape_tushare_docs(
    *,
    base_url: str,
    output_path: str | Path,
    max_pages: int | None = None,
    delay_seconds: float = 0.2,
    storage_state_path: str | None = None,
) -> dict[str, Any]:
    try:
        import requests
        from bs4 import BeautifulSoup
    except Exception as e:  # pragma: no cover
        raise SystemExit(f"Missing dependency for scraping: {e}. Install `pip install .[scrape]`.") from e

    # If user provides storage_state, fall back to Playwright for authenticated scraping.
    use_playwright = storage_state_path is not None
    if use_playwright:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:  # pragma: no cover
            raise SystemExit(
                f"Missing dependency for scraping: {e}. Install `pip install .[scrape]` and run `playwright install chromium`."
            ) from e

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    apis: dict[str, dict[str, Any]] = {}
    visited: set[str] = set()

    # Collect doc links from the base page HTML (fast, usually contains full menu).
    base_html = requests.get(base_url, timeout=30).text
    soup = BeautifulSoup(base_html, "lxml")
    hrefs = []
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        if "doc_id=" not in href:
            continue
        hrefs.append(urljoin(base_url, href))

    unique_links = []
    seen = set()
    for u in hrefs:
        if u in seen:
            continue
        seen.add(u)
        unique_links.append(u)

    special_doc_ids = _build_special_doc_ids(base_html, base_url)

    if max_pages is not None:
        unique_links = unique_links[:max_pages]

    if use_playwright:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=storage_state_path)
            page = context.new_page()
            for url in unique_links:
                if url in visited:
                    continue
                visited.add(url)
                try:
                    page.goto(url, wait_until="networkidle")
                    page.wait_for_timeout(900)
                    html = page.content()
                except Exception:
                    continue
                doc_id = _doc_id_from_url(url)
                parsed = parse_api_doc_from_html(
                    url=url,
                    html=html,
                    is_special=bool(doc_id and doc_id in special_doc_ids),
                )
                if parsed:
                    apis[parsed.api_name] = parsed.to_spec()
                time.sleep(max(0.0, delay_seconds))
            context.close()
            browser.close()
    else:
        # No auth: use plain HTTP fetch (much faster than headless browser).
        session = requests.Session()
        fallback_playwright = None
        browser = context = page = None
        try:
            from playwright.sync_api import sync_playwright as _sync_playwright
        except Exception:
            _sync_playwright = None

        if _sync_playwright is not None:
            fallback_playwright = _sync_playwright().start()
            browser = fallback_playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

        for url in unique_links:
            if url in visited:
                continue
            visited.add(url)
            try:
                html = session.get(url, timeout=30).text
            except Exception:
                continue
            doc_id = _doc_id_from_url(url)
            parsed = parse_api_doc_from_html(
                url=url,
                html=html,
                is_special=bool(doc_id and doc_id in special_doc_ids),
            )
            if parsed is None and page is not None:
                try:
                    page.goto(url, wait_until="networkidle")
                    page.wait_for_timeout(900)
                    html = page.content()
                    parsed = parse_api_doc_from_html(
                        url=url,
                        html=html,
                        is_special=bool(doc_id and doc_id in special_doc_ids),
                    )
                except Exception:
                    parsed = None
            if parsed:
                apis[parsed.api_name] = parsed.to_spec()
            time.sleep(max(0.0, delay_seconds))

        if context is not None:
            context.close()
        if browser is not None:
            browser.close()
        if fallback_playwright is not None:
            fallback_playwright.stop()

    spec_doc: dict[str, Any] = {
        "meta": {"version": 1, "generated_at": _now_iso(), "base_url": base_url, "count": len(apis)},
        "apis": apis,
    }
    output_path.write_text(json.dumps(spec_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return spec_doc


def cli_main() -> None:
    parser = argparse.ArgumentParser(description="Offline Tushare docs scraper (Playwright) -> tushare_api_specs.json")
    parser.add_argument(
        "--base-url",
        default="https://tushare.pro/document/2",
        help="Tushare docs base URL (Vue rendered).",
    )
    parser.add_argument(
        "--output",
        default="data/tushare_api_specs.json",
        help="Output JSON spec path.",
    )
    parser.add_argument("--max-pages", type=int, default=None, help="Limit scraped pages for quick runs.")
    parser.add_argument("--delay-seconds", type=float, default=0.2, help="Delay between pages (polite crawling).")
    parser.add_argument(
        "--storage-state",
        default=None,
        help="Optional Playwright storage state JSON (for logged-in sessions).",
    )
    args = parser.parse_args()
    scrape_tushare_docs(
        base_url=args.base_url,
        output_path=args.output,
        max_pages=args.max_pages,
        delay_seconds=args.delay_seconds,
        storage_state_path=args.storage_state,
    )
