from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from pypdf import PdfReader
from rapidfuzz import fuzz

from notify import send_summary

BASE_URL = "https://judiciary.karnataka.gov.in/causelistSearch.php"
ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
ADVOCATE_GROUPS: dict[str, list[str]] = CONFIG["advocates"]
QUERY_SEEDS: list[str] = CONFIG.get("query_seeds") or [v[0] for v in ADVOCATE_GROUPS.values()]
BENCHES = ["Bengaluru Bench", "Dharwad Bench", "Kalaburagi Bench"]
DAYS_AHEAD = int(os.getenv("DAYS_AHEAD", "7"))
FUZZY_CONFIRM = int(os.getenv("FUZZY_CONFIRM", "88"))
FUZZY_UNCERTAIN = int(os.getenv("FUZZY_UNCERTAIN", "74"))
HEADINGS = {"PRELIMINARY HEARING", "ADMISSION", "ORDERS", "FURTHER HEARING", "FINAL HEARING", "REGULAR HEARING", "NOTICE", "FOR ORDERS", "DIRECTION", "COMPLIANCE", "FRESH MATTER/S"}
CASE_RE = re.compile(r"\b(?:WP|RFA|RP|CRL\.?P|W\.?A|MFA|COMAP|CCC|CP)\s*[A-Z0-9./-]*\d+[A-Z0-9./-]*\b", re.I)


def norm(value: str) -> str:
    value = str(value or "").replace("\u00a0", " ")
    value = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", value)
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9]+", " ", value).strip().lower())


def compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", norm(value))


def best_match(text: str) -> tuple[str | None, int, str | None]:
    normalized, compacted = norm(text), compact(text)
    best: tuple[str | None, int, str | None] = (None, 0, None)
    for group, variants in ADVOCATE_GROUPS.items():
        for variant in variants + [group]:
            score = max(fuzz.ratio(normalized, norm(variant)), fuzz.partial_ratio(normalized, norm(variant)))
            if len(compact(variant)) < 8:
                score = fuzz.ratio(compacted, compact(variant))
            if score > best[1]:
                best = (group, int(score), variant)
    return best


def match_advocate(text: str) -> tuple[str | None, int, str | None]:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    best = best_match(raw)
    words = raw.split()
    for width in range(min(6, len(words)), 1, -1):
        for index in range(0, len(words) - width + 1):
            candidate = best_match(" ".join(words[index:index + width]))
            if candidate[1] > best[1]:
                best = candidate
    return best


async def choose_select(page, predicate) -> Any:
    for select in await page.locator("select").all():
        try:
            options = await select.locator("option").all_text_contents()
            if any(predicate(option) for option in options):
                return select
        except Exception:
            continue
    raise RuntimeError("Could not identify required select field")


async def choose_input(page, patterns: list[str]):
    for inp in await page.locator("input").all():
        try:
            input_type = (await inp.get_attribute("type") or "").lower()
            if input_type in {"hidden", "submit", "button", "image", "checkbox", "radio"}:
                continue
            blob = " ".join(await inp.get_attribute(name) or "" for name in ("name", "id", "placeholder", "aria-label"))
            if any(pattern.lower() in blob.lower() for pattern in patterns):
                return inp
        except Exception:
            continue
    return None


async def run_advocate_search(page, bench: str, query: str, start: date, end: date) -> str:
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(700)
    bench_select = await choose_select(page, lambda x: any(name in x.lower() for name in ("bengaluru", "dharwad", "kalaburagi", "kalburagi")))
    target = "kalburagi bench" if "kalaburagi" in bench.lower() else bench.lower()
    options = await bench_select.locator("option").all_text_contents()
    chosen = next((option for option in options if target in option.lower()), None) or next((option for option in options if bench.lower().split()[0] in option.lower()), None)
    if chosen is None:
        raise RuntimeError(f"Bench option not found: {bench}; options={options}")
    await bench_select.select_option(label=chosen)
    search_by = await choose_select(page, lambda x: x.strip().lower() == "advocate")
    await search_by.select_option(label=next(option for option in await search_by.locator("option").all_text_contents() if option.strip().lower() == "advocate"))
    await page.wait_for_timeout(400)
    advocate = await choose_input(page, ["advocate"])
    if advocate is None:
        raise RuntimeError("Advocate name input was not found")
    await advocate.fill(query)
    date_inputs = []
    for inp in await page.locator("input").all():
        try:
            input_type = (await inp.get_attribute("type") or "text").lower()
            blob = " ".join(await inp.get_attribute(name) or "" for name in ("name", "id", "placeholder")).lower()
            if (input_type == "date" or "dd/mm/yyyy" in blob or "date" in blob) and await inp.is_visible():
                date_inputs.append(inp)
        except Exception:
            pass
    if len(date_inputs) < 2:
        raise RuntimeError("Could not locate both cause-list date fields")
    await date_inputs[0].fill(start.strftime("%Y-%m-%d") if (await date_inputs[0].get_attribute("type") or "").lower() == "date" else start.strftime("%d/%m/%Y"))
    await date_inputs[1].fill(end.strftime("%Y-%m-%d") if (await date_inputs[1].get_attribute("type") or "").lower() == "date" else end.strftime("%d/%m/%Y"))
    candidates = [page.get_by_role("button", name=re.compile(r"GET DETAILS|GET LIST", re.I)), page.locator('input[type="submit"][value*="GET" i]'), page.locator('button:has-text("GET DETAILS")'), page.locator('button:has-text("GET LIST")')]
    for locator in candidates:
        try:
            if await locator.count():
                await locator.first.click()
                break
        except Exception:
            continue
    else:
        raise RuntimeError("Could not find GET DETAILS / GET LIST button")
    try:
        await page.wait_for_load_state("networkidle", timeout=45000)
    except PlaywrightTimeoutError:
        await page.wait_for_timeout(2500)
    return await page.content()


def html_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())


def tables_from_html(html: str) -> list[pd.DataFrame]:
    try:
        return pd.read_html(io.StringIO(html))
    except ValueError:
        return []


def find_case_context(lines: list[str], case_number: str) -> dict[str, str]:
    target = norm(case_number)
    indexes = [index for index, line in enumerate(lines) if target and target in norm(line)]
    if not indexes:
        return {}
    index = indexes[0]
    nearby = lines[max(0, index - 80):index + 4]
    result = {"court_hall": "", "list_no": "", "session": "", "judges": ""}
    patterns = {"court_hall": r"(?:COURT\s*HALL|HALL)\s*(?:NO\.?|NUMBER)?\s*[:\-]?\s*([A-Z0-9 /-]+)", "list_no": r"(?:CAUSE\s*LIST|LIST)\s*NO\.?\s*[:\-]?\s*([A-Z0-9 /-]+)"}
    for line in reversed(nearby):
        for key, pattern in patterns.items():
            match = re.search(pattern, line, re.I)
            if match and not result[key]:
                result[key] = match.group(1).strip(" -:")
        upper = re.sub(r"\s+", " ", line).strip().upper()
        if not result["session"] and (upper in HEADINGS or any(heading in upper for heading in HEADINGS) or re.search(r"DAILY|SUPPLEMENTARY|2:?30\s*PM|SPECIAL LIST", upper)):
            result["session"] = line.strip()
    for position in range(max(0, index - 35), index):
        if re.search(r"\bBEFORE\b|HON['’]?BLE", lines[position], re.I):
            judges = []
            for line in lines[position + 1:index]:
                if re.search(r"COURT\s*HALL|CAUSE\s*LIST|DAILY LIST", line, re.I):
                    break
                if line.strip():
                    judges.append(line.strip())
            result["judges"] = " ".join(judges[-4:])
    return result


def extract_party_pair(text: str) -> tuple[str, str]:
    clean = re.sub(r"\s+", " ", str(text or "").replace("\u00a0", " ")).strip()
    for pattern in (r"PET(?:ITIONER)?\.?\s*:?\s*(.*?)\s+V/?S?\.?\s+(.*?)\s+RES(?:PONDENT)?\.?\s*:?(.*)$", r"PET\.?\s*:?\s*(.*?)\s+RES(?:P)?\.?\s*:?\s*(.*)$"):
        match = re.search(pattern, clean, re.I)
        if match:
            groups = match.groups()
            return groups[0].strip(" -|"), groups[-1].strip(" -|")
    bare_pair = re.search(r"^(.+?)\s+V/?S\.?\s+(.+)$", clean, re.I)
    if bare_pair:
        return bare_pair.group(1).strip(" -|"), bare_pair.group(2).strip(" -|")
    return "", ""


def record_from_values(values: list[str], page_lines: list[str], bench: str, query: str) -> tuple[dict | None, bool]:
    values = [re.sub(r"\s+", " ", value).strip(" -|\n\t") for value in values]
    row_text = " | ".join(values)
    group, score, variant = max((match_advocate(value) for value in values + [row_text]), key=lambda match: match[1])
    case_match = next((CASE_RE.search(value) for value in values), None)
    if not group or score < FUZZY_UNCERTAIN or not case_match:
        return None, False
    case_number = case_match.group(0).strip()
    context = find_case_context(page_lines, case_number)
    petitioner, respondent = extract_party_pair(row_text)
    if not petitioner or not respondent:
        for line in page_lines:
            if norm(case_number) in norm(line):
                petitioner, respondent = extract_party_pair(line)
                if petitioner and respondent:
                    break
    parties = f"{petitioner} V/s {respondent}" if petitioner and respondent else ""
    record = {"Court Hall & Bench (Name of Judges)": f"{bench} | Court Hall {context.get('court_hall', '')} | {context.get('judges', '')}".strip(" |"), "Item / Serial Number & Session Type": f"List {context.get('list_no', '')}; {context.get('session', '')}; Search seed: {query}", "Case Number": case_number, "Case Type": context.get("session", ""), "Petitioner V/s Respondent": parties, "Advocate Name & Variant Matched": f"{variant} (matched to {group}, score {score})", "Bench": bench, "Search Seed": query}
    return record, score >= FUZZY_CONFIRM


def row_records(frame: pd.DataFrame, page_lines: list[str], bench: str, query: str) -> tuple[list[dict], list[dict]]:
    confirmed, uncertain = [], []
    for _, row in frame.fillna("").astype(str).iterrows():
        record, is_confirmed = record_from_values(row.tolist(), page_lines, bench, query)
        if record:
            (confirmed if is_confirmed else uncertain).append(record)
    return confirmed, uncertain


def pdf_records(pdf_bytes: bytes, bench: str, query: str) -> tuple[list[dict], list[dict]]:
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf_bytes)).pages)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    confirmed, uncertain = [], []
    for index, line in enumerate(lines):
        if CASE_RE.search(line):
            record, is_confirmed = record_from_values(lines[max(0, index - 2):index + 3], lines, bench, query)
            if record:
                (confirmed if is_confirmed else uncertain).append(record)
    return confirmed, uncertain


async def fetch_pdf_links(page, request, bench: str, query: str) -> tuple[list[dict], list[dict]]:
    confirmed, uncertain = [], []
    for anchor in await page.locator("a").all():
        href = await anchor.get_attribute("href")
        if not href or ".pdf" not in href.lower():
            continue
        url = href if href.startswith("http") else f"{BASE_URL.rsplit('/', 1)[0]}/{href.lstrip('/')}"
        response = await request.get(url, timeout=60000)
        if response.ok:
            records, unsure = pdf_records(await response.body(), bench, query)
            confirmed.extend(records)
            uncertain.extend(unsure)
    return confirmed, uncertain


def dedupe(records: list[dict]) -> list[dict]:
    seen, output = set(), []
    for record in records:
        key = (record.get("Bench"), record.get("Case Number"), record.get("Petitioner V/s Respondent"), record.get("Advocate Name & Variant Matched", "").split(" (matched")[0])
        if key not in seen:
            seen.add(key)
            output.append(record)
    return output


def save_results(records: list[dict], uncertain: list[dict]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    columns = ["Court Hall & Bench (Name of Judges)", "Item / Serial Number & Session Type", "Case Number", "Case Type", "Petitioner V/s Respondent", "Advocate Name & Variant Matched"]
    confirmed_frame, uncertain_frame = pd.DataFrame(dedupe(records), columns=columns), pd.DataFrame(dedupe(uncertain), columns=columns)
    confirmed_frame.to_excel(RESULTS / "latest_matches.xlsx", index=False)
    confirmed_frame.to_csv(RESULTS / "latest_matches.csv", index=False)
    uncertain_frame.to_excel(RESULTS / "latest_uncertain.xlsx", index=False)
    uncertain_frame.to_csv(RESULTS / "latest_uncertain.csv", index=False)
    for frame, filename, title in ((confirmed_frame, "latest_matches.md", "Confirmed Karnataka High Court Advocate Matches"), (uncertain_frame, "latest_uncertain.md", "Uncertain / Manual Review Matches")):
        body = "No matches." if frame.empty else frame.to_markdown(index=False)
        (RESULTS / filename).write_text(f"# {title}\n\n{body}\n", encoding="utf-8")


def update_history(records: list[dict]) -> list[dict]:
    path = RESULTS / "history.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"cases": {}}
    old_cases, new_cases, new_records = previous.get("cases", {}), {}, []
    for record in dedupe(records):
        key = f"{record.get('Bench')}|{record.get('Case Number')}"
        digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
        new_cases[key] = digest
        if key not in old_cases:
            new_records.append(record)
    path.write_text(json.dumps({"updated": date.today().isoformat(), "cases": new_cases}, indent=2), encoding="utf-8")
    return new_records


async def search_bench(context, bench: str, start: date, end: date) -> tuple[list[dict], list[dict], list[str]]:
    confirmed, uncertain, errors = [], [], []
    for query in QUERY_SEEDS:
        page = await context.new_page()
        try:
            html = await run_advocate_search(page, bench, query, start, end)
            lines = html_text(html).splitlines()
            for table in tables_from_html(html):
                records, unsure = row_records(table, lines, bench, query)
                confirmed.extend(records)
                uncertain.extend(unsure)
            records, unsure = await fetch_pdf_links(page, context.request, bench, query)
            confirmed.extend(records)
            uncertain.extend(unsure)
        except Exception as exc:
            errors.append(f"{bench} / {query}: {type(exc).__name__}: {exc}")
        finally:
            await page.close()
    return confirmed, uncertain, errors


async def async_main() -> int:
    start, end = date.today(), date.today() + timedelta(days=max(0, DAYS_AHEAD - 1))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1200})
        results = await asyncio.gather(*(search_bench(context, bench, start, end) for bench in BENCHES))
        await browser.close()
    confirmed = dedupe([record for result in results for record in result[0]])
    uncertain = dedupe([record for result in results for record in result[1]])
    errors = [error for result in results for error in result[2]]
    save_results(confirmed, uncertain)
    new_records = update_history(confirmed)
    summary = {"run_date": str(start), "search_end_date": str(end), "confirmed_count": len(confirmed), "new_count": len(new_records), "uncertain_count": len(uncertain), "errors": errors}
    (RESULTS / "run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if new_records:
        send_summary(summary, new_records)
    print(json.dumps(summary, indent=2))
    return 2 if not confirmed and not uncertain and len(errors) >= len(BENCHES) * len(QUERY_SEEDS) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(async_main()))
