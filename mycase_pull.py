#!/usr/bin/env python3
"""mycase-pull — Indiana MyCase / Odyssey public cause puller.

Public data only. Output is NOT a certified court record and NOT a filing.
Do not e-file from this tool.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

try:
    import requests
except ImportError:  # pragma: no cover
    print("mycase-pull requires the requests package", file=sys.stderr)
    sys.exit(2)

BANNER = (
    "PUBLIC MyCase / Odyssey data only. Not a certified court record. "
    "Not a filing. Do not e-file from this output."
)

BASE = "https://public.courts.in.gov/mycase"
SEARCH_URL = f"{BASE}/Search/SearchCases"
SUMMARY_URL = f"{BASE}/Case/CaseSummary"
HOME_URL = f"{BASE}/"

CAUSE_RE = re.compile(
    r"^\d{2}[A-Z0-9]{3}-\d{4}-[A-Z]{2}-\d{6}$",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_cause(raw: str) -> str:
    s = raw.strip().upper()
    if CAUSE_RE.match(s):
        return s
    # tolerate missing hyphens: 49D052203CM000001
    compact = re.sub(r"[^A-Z0-9]", "", s)
    m = re.match(r"^(\d{2})([A-Z0-9]{3})(\d{4})([A-Z]{2})(\d{6})$", compact)
    if not m:
        raise ValueError(
            f"Unrecognized cause format: {raw!r}. "
            "Expected e.g. 49D05-2203-CM-000001"
        )
    return f"{m.group(1)}{m.group(2)}-{m.group(3)}-{m.group(4)}-{m.group(5)}"


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "mycase-pull/1.0 (+local agent research; public MyCase only)"
            ),
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://public.courts.in.gov",
            "Referer": HOME_URL,
        }
    )
    return s


def warm(s: requests.Session) -> None:
    r = s.get(HOME_URL, timeout=60)
    r.raise_for_status()


def search_payload(cause: str) -> dict[str, Any]:
    return {
        "Mode": "ByCase",
        "CaseNum": cause,
        "CiteNum": None,
        "CrossRefNum": None,
        "First": None,
        "Middle": None,
        "Last": None,
        "Business": None,
        "DoBStart": None,
        "DoBEnd": None,
        "OANum": None,
        "BarNum": None,
        "SoundEx": False,
        "CourtItemID": None,
        "Categories": None,
        "Limits": None,
        "Advanced": False,
        "ActiveFlag": "All",
        "FileStart": None,
        "FileEnd": None,
        "CountyCode": None,
        "Skip": 0,
        "Take": 20,
        "Sort": "FileDate DESC",
        "NewSearch": True,
        "CaptchaAnswer": None,
    }


def search_cases(s: requests.Session, cause: str) -> dict[str, Any]:
    r = s.post(
        SEARCH_URL,
        json=search_payload(cause),
        headers={"Content-Type": "application/json"},
        timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    # Odyssey wraps as { Result: {...}, IsError: ... }
    return data.get("Result", data)


def case_summary(s: requests.Session, case_token: str) -> dict[str, Any]:
    r = s.get(
        SUMMARY_URL,
        params={"CaseToken": case_token},
        timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("Result", data)


def empty_message(search: dict[str, Any]) -> str | None:
    if search.get("CaptchaKey"):
        return (
            "MyCase returned a CAPTCHA challenge. "
            "Public automated pull blocked; open the site in a browser."
        )
    total = int(search.get("TotalResults") or 0)
    results = search.get("Results") or []
    if total == 0 or not results:
        # Prefer exact portal language when present
        for key in ("Message", "Error", "UserMessage", "EmptyMessage"):
            msg = search.get(key)
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
        return "No cases found matching the search criteria."
    return None


def pick_result(search: dict[str, Any], cause: str) -> dict[str, Any]:
    results = search.get("Results") or []
    want = cause.upper()
    for row in results:
        num = str(row.get("CaseNumber") or "").upper()
        if num == want or num.replace("-", "") == want.replace("-", ""):
            return row
    if len(results) == 1:
        return results[0]
    raise RuntimeError(
        f"Search returned {len(results)} results; none matched {cause}. "
        f"Numbers: {[r.get('CaseNumber') for r in results[:10]]}"
    )


def party_row(p: dict[str, Any]) -> dict[str, Any]:
    name = p.get("NameFMLS") or p.get("Name") or p.get("ExtendedName")
    addr = p.get("Address") or {}
    fee = p.get("FeeSummary") or {}
    return {
        "role": p.get("ExtConnCodeDesc") or p.get("ExtConnCode"),
        "name": name,
        "description": p.get("Description"),
        "removed_date": p.get("RemovedDate"),
        "address": {
            "line1": addr.get("Line1"),
            "city": addr.get("City"),
            "state": addr.get("State"),
            "zip": addr.get("Zip"),
        }
        if addr
        else None,
        "attorneys": [
            {
                "name": a.get("Name") or a.get("FullName"),
                "label": a.get("Label"),
                "work_phone": a.get("WorkPhone"),
            }
            for a in (p.get("Attorneys") or [])
            if a
        ],
        "fee_summary": {
            "charged": fee.get("TotalCharged") or fee.get("Charged"),
            "paid": fee.get("TotalPaid") or fee.get("Paid"),
            "balance": fee.get("Balance"),
        }
        if fee
        else None,
    }


def charge_row(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": c.get("ChargeNumber"),
        "statute": c.get("OffenseStatute"),
        "description": c.get("OffenseDescription"),
        "degree": c.get("OffenseDegree"),
        "orig_degree": c.get("OrigOffenseDegree"),
        "offense_date": c.get("OffenseDate"),
        "citation": c.get("CitationNumber"),
        "disposition": c.get("Disposition")
        or c.get("DispositionDescription")
        or c.get("ChargeDisposition"),
        "disposition_date": c.get("DispositionDate"),
        "modification": c.get("OffenseModification"),
    }


def event_row(e: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "date": e.get("EventDate"),
        "type": e.get("EventType") or e.get("BaseEventType"),
        "description": e.get("Description"),
        "judge": e.get("Judge") or e.get("JudgeName"),
    }
    # surface converted sentencing / disposition blobs when present
    for key in ("SEvent", "DispEvent", "JEvent", "HearingEvent", "CaseEvent"):
        if e.get(key):
            out[key] = e[key]
    return out


def ledger_from_parties(parties: list[dict[str, Any]]) -> dict[str, Any] | None:
    charged = paid = bal = None
    as_of = None
    categories: list[dict[str, Any]] = []
    for p in parties:
        fee = (p.get("FeeSummary") or {}) if p else {}
        if not fee:
            continue
        if fee.get("Balance") is not None:
            bal = fee.get("Balance")
        if fee.get("AsOf"):
            as_of = fee.get("AsOf")
        cats = fee.get("Categories") or []
        if cats:
            categories = cats
            try:
                charged = f"{sum(float(c.get('Charge') or 0) for c in cats):.2f}"
                paid = f"{sum(float(c.get('Payment') or 0) for c in cats):.2f}"
            except (TypeError, ValueError):
                charged = paid = None
        for cand in ("TotalCharged", "Charged", "AmountCharged"):
            if charged is None and fee.get(cand) is not None:
                charged = fee.get(cand)
        for cand in ("TotalPaid", "Paid", "AmountPaid"):
            if paid is None and fee.get(cand) is not None:
                paid = fee.get(cand)
    if charged is None and paid is None and bal is None:
        return None
    return {
        "charged": charged,
        "paid": paid,
        "balance": bal,
        "as_of": as_of,
        "categories": categories or None,
    }


def dispositions_from_events(events: list[dict[str, Any]]) -> dict[str, str]:
    """Map charge number -> latest DispositionType from DispEvent rows."""
    out: dict[str, str] = {}
    for e in events or []:
        de = e.get("DispEvent") or {}
        for ch in de.get("Charges") or []:
            num = str(ch.get("ChargeNumber") or "").strip()
            disp = ch.get("DispositionType")
            if num and disp:
                out[num] = str(disp)
            # also index by ChargeKey
            key = str(ch.get("ChargeKey") or "").strip()
            if key and disp:
                out[f"key:{key}"] = str(disp)
    return out


def structure(summary: dict[str, Any], *, cause: str, source_url: str) -> dict[str, Any]:
    parties = summary.get("Parties") or []
    charges = summary.get("Charges") or []
    events = summary.get("Events") or []
    disp_map = dispositions_from_events(events)
    charge_rows = []
    for c in charges:
        if not c:
            continue
        row = charge_row(c)
        num = str(c.get("ChargeNumber") or "").strip()
        key = str(c.get("ChargeKey") or "").strip()
        if not row.get("disposition"):
            row["disposition"] = disp_map.get(num) or disp_map.get(f"key:{key}")
        charge_rows.append(row)
    return {
        "banner": BANNER,
        "pulled_at": utc_now(),
        "source": {
            "portal": "Indiana MyCase / Odyssey Public Access",
            "home": HOME_URL,
            "search": SEARCH_URL,
            "summary_endpoint": SUMMARY_URL,
            "note": (
                "Case detail URLs use a session CaseToken; "
                "re-search by cause number. Not a stable permalink."
            ),
            "queried_cause": cause,
        },
        "case": {
            "case_number": summary.get("CaseNumber"),
            "case_key": summary.get("CaseKey"),
            "style": summary.get("Style"),
            "court": summary.get("Court"),
            "court_code": summary.get("CourtCode"),
            "county_code": summary.get("CountyCode"),
            "case_type": summary.get("CaseType"),
            "case_type_code": summary.get("CaseTypeCode"),
            "case_category_group": summary.get("CaseCategoryGroup"),
            "file_date": summary.get("FileDate"),
            "status": summary.get("CaseStatus"),
            "status_date": summary.get("CaseStatusDate"),
            "is_public": summary.get("IsPublic"),
            "is_active": summary.get("IsActive"),
            "expunged_flag": summary.get("ExpungedCaseFlag"),
        },
        "parties": [party_row(p) for p in parties if p],
        "charges": charge_rows,
        "events": [event_row(e) for e in events if e],
        "bonds": summary.get("Bonds"),
        "related": summary.get("Related"),
        "ledger": ledger_from_parties(parties),
        "raw_flags": {
            "InvalidToken": summary.get("InvalidToken"),
            "CaseNotFound": summary.get("CaseNotFound"),
            "AccessDenied": summary.get("AccessDenied"),
        },
    }


def render_md(doc: dict[str, Any]) -> str:
    c = doc["case"]
    lines = [
        f"# MyCase public pull — `{c.get('case_number')}`",
        "",
        f"> **{BANNER}**",
        "",
        f"Pulled: {doc['pulled_at']}  ",
        f"Portal: {doc['source']['home']}",
        "",
        "## Header",
        "",
        f"| Field | Value |",
        f"|---|---|",
        f"| Style | {c.get('style') or ''} |",
        f"| Court | {c.get('court') or ''} |",
        f"| Type | {c.get('case_type') or ''} (`{c.get('case_type_code') or ''}`) |",
        f"| Filed | {c.get('file_date') or ''} |",
        f"| Status | {c.get('status') or ''} ({c.get('status_date') or ''}) |",
        f"| CaseKey | {c.get('case_key') or ''} |",
        f"| Expunged flag | {c.get('expunged_flag')!r} |",
        "",
        "## Parties",
        "",
    ]
    for p in doc.get("parties") or []:
        lines.append(
            f"- **{p.get('role') or 'Party'}:** {p.get('name') or '(unnamed)'}"
            + (f" — {p.get('description')}" if p.get("description") else "")
        )
        for a in p.get("attorneys") or []:
            lines.append(
                f"  - Attorney: {a.get('name') or '?'} "
                f"({a.get('label') or ''})"
            )
    lines += ["", "## Charges", ""]
    if not doc.get("charges"):
        lines.append("_No charges on public summary._")
    else:
        lines.append("| Ct | Statute | Description | Degree | Offense date | Disposition |")
        lines.append("|---|---|---|---|---|---|")
        for ch in doc["charges"]:
            lines.append(
                "| {count} | {statute} | {description} | {degree} | {offense_date} | {disposition} |".format(
                    count=ch.get("count") or "",
                    statute=ch.get("statute") or "",
                    description=(ch.get("description") or "").replace("|", "/"),
                    degree=ch.get("degree") or "",
                    offense_date=ch.get("offense_date") or "",
                    disposition=ch.get("disposition") or "",
                )
            )
    lines += ["", "## Ledger (if public)", ""]
    led = doc.get("ledger")
    if led:
        lines.append(
            f"Charged: {led.get('charged')!r}; Paid: {led.get('paid')!r}; "
            f"Balance: {led.get('balance')!r}"
            + (f" (as of {led.get('as_of')})" if led.get('as_of') else '')
        )
    else:
        lines.append("_No party fee summary on this pull._")
    lines += [
        "",
        "## Events (public CCS rows)",
        "",
    ]
    evs = doc.get("events") or []
    if not evs:
        lines.append("_No events._")
    else:
        for e in evs[:80]:
            lines.append(
                f"- {e.get('date') or '?'}: "
                f"`{e.get('type') or ''}` — "
                f"{(e.get('description') or '').strip()}"
            )
        if len(evs) > 80:
            lines.append(f"- … {len(evs) - 80} more events omitted from markdown")
    lines += [
        "",
        "## Notes",
        "",
        "- Session CaseTokens expire; re-run by cause number.",
        "- Converted JTS cases may lack imaged documents and judge names.",
        "- This tool never appends to an Indiana XP petition packet.",
        "",
    ]
    return "\n".join(lines)


def load_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if "CaseNumber" in data or "Charges" in data:
        return data
    if isinstance(data.get("Result"), dict) and "CaseNumber" in data["Result"]:
        return data["Result"]
    raise ValueError(f"Fixture does not look like a CaseSummary: {path}")


def pull_live(cause: str) -> tuple[dict[str, Any], dict[str, Any]]:
    s = session()
    warm(s)
    search = search_cases(s, cause)
    empty = empty_message(search)
    if empty:
        raise SystemExit(f"NO_HIT: {empty}")
    row = pick_result(search, cause)
    token = row.get("CaseToken")
    if not token:
        raise RuntimeError(f"Search hit missing CaseToken: {row!r}")
    summary = case_summary(s, token)
    if summary.get("CaseNotFound"):
        raise SystemExit("NO_HIT: CaseNotFound on CaseSummary")
    if summary.get("InvalidToken"):
        raise RuntimeError("InvalidToken from CaseSummary — session expired; retry")
    if summary.get("AccessDenied"):
        raise SystemExit("NO_HIT: AccessDenied on CaseSummary")
    if summary.get("CaptchaKey"):
        raise SystemExit(
            "CAPTCHA: CaseSummary challenged. Use a browser and re-run later."
        )
    return search, summary


def write_outputs(doc: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cause = (doc["case"].get("case_number") or "unknown").replace("/", "_")
    # Stable names requested: cause.json / cause.md (plus cause-tagged copies)
    json_path = out_dir / "cause.json"
    md_path = out_dir / "cause.md"
    json_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    md_path.write_text(render_md(doc))
    tagged_json = out_dir / f"{cause}.json"
    tagged_md = out_dir / f"{cause}.md"
    tagged_json.write_text(json_path.read_text())
    tagged_md.write_text(md_path.read_text())
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Pull Indiana MyCase public case by cause number → cause.json + cause.md"
    )
    ap.add_argument(
        "--cause",
        required=True,
        help="Cause number, e.g. 49D05-2203-CM-000001",
    )
    ap.add_argument(
        "--out",
        default=".",
        help="Output directory (default: cwd)",
    )
    ap.add_argument(
        "--fixture",
        help="Skip network; parse a saved CaseSummary JSON (for offline/dev)",
    )
    ap.add_argument(
        "--append-packet",
        action="store_true",
        help="FORBIDDEN by default path: refused. Kept only to document the hard no.",
    )
    args = ap.parse_args(argv)

    if args.append_packet:
        print(
            "Refusing --append-packet. mycase-pull never writes an Indiana XP petition.",
            file=sys.stderr,
        )
        return 2

    try:
        cause = normalize_cause(args.cause)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    print(BANNER, file=sys.stderr)

    if args.fixture:
        summary = load_fixture(Path(args.fixture))
        search = {"fixture": True}
    else:
        try:
            search, summary = pull_live(cause)
        except requests.RequestException as e:
            print(f"NETWORK_ERROR talking to MyCase: {e}", file=sys.stderr)
            return 2

    if summary.get("CaseNumber"):
        # prefer portal spelling
        cause = str(summary["CaseNumber"])

    doc = structure(summary, cause=cause, source_url=HOME_URL)
    doc["search_meta"] = {
        "total_results": (search or {}).get("TotalResults")
        if isinstance(search, dict)
        else None,
        "fixture": bool(args.fixture),
    }

    out_dir = Path(args.out).expanduser().resolve()
    json_path, md_path = write_outputs(doc, out_dir)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
