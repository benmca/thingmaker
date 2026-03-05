#!/usr/bin/env python3
"""
extract_history.py — Pull interesting citations from Vivaldi browser history,
and extract bookmarks, rendered as a two-tab HTML report.

Usage:
    python3 extract_history.py [--output report.html] [--db /path/to/History] [--days 90]
"""

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# Chrome epoch starts 1601-01-01; offset from Unix epoch in seconds
CHROME_EPOCH_OFFSET_S = 11644473600

DEFAULT_DB = Path.home() / "Library/Application Support/Vivaldi/Default/History"
DEFAULT_BOOKMARKS = Path.home() / "Library/Application Support/Vivaldi/Default/Bookmarks"
DEFAULT_BLACKLIST = Path(__file__).parent / "blacklist.json"

# Domains that are search engines — skip pages that are just search queries
SEARCH_DOMAINS = {
    "kagi.com", "www.google.com", "google.com",
    "www.bing.com", "bing.com",
    "duckduckgo.com", "search.yahoo.com",
    "www.startpage.com", "search.brave.com",
}

# These domains are inherently "feeds" or high-noise routine pages — not citations
FEED_DOMAINS = {
    "www.reddit.com": lambda path: path != "/" and not path.startswith("/r/"),
    "news.ycombinator.com": lambda path: "item" in path,  # keep /item?id= links
}

# For these domains, the given query params ARE the page identity — keep them in the dedup key
IDENTITY_PARAMS: dict[str, list[str]] = {
    "www.youtube.com": ["v"],
    "youtube.com": ["v"],
    "m.youtube.com": ["v"],
    "news.ycombinator.com": ["id"],
    "en.wikipedia.org": ["title", "curid"],
    "old.reddit.com": ["id"],
}

# Tracking / session params that should be stripped from displayed URLs
STRIP_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_referrer",
    "fbclid", "gclid", "msclkid", "igshid", "mc_eid",
    "ref", "referrer", "referer",
    "token", "si", "feature", "app", "usp", "tab",
    "_ga", "_gl", "jst", "pwd",
    "source", "via",
}


def chrome_ts_to_dt(chrome_ts: int) -> datetime:
    unix_s = (chrome_ts / 1_000_000) - CHROME_EPOCH_OFFSET_S
    return datetime.fromtimestamp(unix_s, tz=timezone.utc)


def load_blacklist(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def is_blacklisted(url: str, title: str, bl: dict) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return True

    netloc = parsed.netloc.lower()
    url_lower = url.lower()
    title_lower = (title or "").lower()

    # Exact domain match
    if netloc in {d.lower() for d in bl.get("domains", [])}:
        return True

    # Domain contains substring
    for sub in bl.get("domain_contains", []):
        if sub.lower() in netloc:
            return True

    # URL prefix match
    for prefix in bl.get("url_prefixes", []):
        if url_lower.startswith(prefix.lower()):
            return True

    # URL contains
    for fragment in bl.get("url_contains", []):
        if fragment.lower() in url_lower:
            return True

    # Title contains
    for fragment in bl.get("title_contains", []):
        if fragment.lower() in title_lower:
            return True

    return False


def is_interesting(url: str, title: str) -> bool:
    """Heuristic filter: remove noise that isn't a 'citation'."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    netloc = parsed.netloc.lower()
    path = parsed.path or "/"

    # Skip bare protocol schemes
    if parsed.scheme not in ("http", "https"):
        return False

    # Skip search engine results pages
    if netloc in SEARCH_DOMAINS:
        qs = parse_qs(parsed.query)
        if any(k in qs for k in ("q", "query", "search", "s", "p")):
            return False

    # Skip root/home pages of social/feed sites unless there's a real path
    if netloc in FEED_DOMAINS:
        keep_fn = FEED_DOMAINS[netloc]
        if not keep_fn(path):
            return False

    # Must have a title that isn't just the domain
    if not title or title.strip() == netloc:
        return False

    return True


def canonical_url(url: str) -> tuple[str, str]:
    """Return (dedup_key, clean_url) for a URL.

    dedup_key  — scheme+netloc+path, plus identity params for known sites.
    clean_url  — same but with tracking params stripped (used in report).
    """
    try:
        p = urlparse(url)
    except Exception:
        return url, url

    netloc = p.netloc.lower()
    path = p.path.rstrip("/") or "/"
    qs = parse_qs(p.query, keep_blank_values=True)

    # Build clean query: strip tracking params
    clean_qs = {k: v for k, v in qs.items() if k.lower() not in STRIP_PARAMS}

    # Build dedup key: for most sites strip ALL query params; for known sites keep identity params
    identity = IDENTITY_PARAMS.get(netloc, None)
    if identity is None:
        # Generic site: dedup by path only
        key_qs: dict = {}
    else:
        # Keep only the identity params that are present
        key_qs = {k: qs[k] for k in identity if k in qs}

    key = f"{p.scheme}://{netloc}{path}"
    if key_qs:
        key += "?" + urlencode(sorted(key_qs.items()), doseq=True)
    # fragments never distinguish pages — always drop them

    clean = urlunparse((p.scheme, netloc, path, "", urlencode(sorted(clean_qs.items()), doseq=True), ""))

    return key, clean


def root_domain(netloc: str) -> str:
    """Return the registrable domain (strip subdomains) from a netloc.
    Handles common two-level TLDs like .co.uk, .com.au.
    """
    host = netloc.split(":")[0].lower()
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    two_level = {"co", "com", "org", "net", "gov", "edu", "ac", "me"}
    if len(parts[-1]) == 2 and parts[-2] in two_level:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def deduplicate(rows: list[dict]) -> list[dict]:
    """Deduplicate by canonical URL (domain+path, keeping identity params for known sites).
    When multiple URLs collapse to the same key, keep the most recent visit and the
    cleanest (shortest) URL for display.
    """
    seen: dict[str, dict] = {}
    for row in rows:
        key, clean = canonical_url(row["url"])
        if key not in seen or row["visited_at"] > seen[key]["visited_at"]:
            seen[key] = {**row, "url": clean}
        elif len(clean) < len(seen[key]["url"]):
            seen[key]["url"] = clean
    return sorted(seen.values(), key=lambda r: r["visited_at"], reverse=True)


def query_history(db_path: Path, days: int | None) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    shutil.copy2(db_path, tmp_path)

    rows = []
    try:
        conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        where = ""
        if days:
            # Chrome timestamp cutoff
            cutoff_unix = datetime.now(timezone.utc).timestamp() - (days * 86400)
            cutoff_chrome = int((cutoff_unix + CHROME_EPOCH_OFFSET_S) * 1_000_000)
            where = f"WHERE last_visit_time >= {cutoff_chrome}"

        sql = f"""
            SELECT url, title, visit_count, last_visit_time
            FROM urls
            {where}
            ORDER BY last_visit_time DESC
        """
        for r in conn.execute(sql):
            rows.append({
                "url": r["url"],
                "title": r["title"] or "",
                "visit_count": r["visit_count"],
                "visited_at": chrome_ts_to_dt(r["last_visit_time"]),
            })
        conn.close()
    finally:
        tmp_path.unlink(missing_ok=True)

    return rows


def query_url_visit_times(db_path: Path) -> dict[str, datetime]:
    """Return {url: last_visit_time} for all URLs in history DB (unfiltered)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    shutil.copy2(db_path, tmp_path)

    result: dict[str, datetime] = {}
    try:
        conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
        for row in conn.execute("SELECT url, last_visit_time FROM urls WHERE last_visit_time > 0"):
            result[row[0]] = chrome_ts_to_dt(row[1])
        conn.close()
    finally:
        tmp_path.unlink(missing_ok=True)

    return result


def query_bookmarks(path: Path) -> list[dict]:
    """Walk the Vivaldi Bookmarks JSON and return all URL bookmarks with folder paths."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    rows: list[dict] = []

    def walk(node: dict, folder_path: list[str]) -> None:
        t = node.get("type")
        name = node.get("name", "")
        if t == "folder":
            for child in node.get("children", []):
                walk(child, folder_path + [name])
        elif t == "url":
            url = node.get("url", "")
            title = node.get("name", "")
            date_added_raw = int(node.get("date_added", "0") or "0")
            # Use date_added as fallback; main() will overlay last_visit_time from history
            visited_at = chrome_ts_to_dt(date_added_raw) if date_added_raw else datetime.now(timezone.utc)
            # folder_path[0] is the root container (e.g. "Bookmarks"); skip it in display
            path_str = " › ".join(folder_path[1:]) if len(folder_path) > 1 else (folder_path[0] if folder_path else "")
            rows.append({
                "url": url,
                "title": title,
                "visited_at": visited_at,
                "bookmark_path": path_str,
            })

    # Walk the bookmark bar (contains Music, Home, Work at top level)
    walk(data["roots"].get("bookmark_bar", {}), [])

    return rows


def deduplicate_bookmarks(rows: list[dict]) -> list[dict]:
    """Deduplicate bookmarks by canonical URL, keeping most-recently-visited entry."""
    seen: dict[str, dict] = {}
    for row in rows:
        try:
            key, clean = canonical_url(row["url"])
        except Exception:
            continue
        if key not in seen or row["visited_at"] > seen[key]["visited_at"]:
            seen[key] = {**row, "url": clean}
    return sorted(seen.values(), key=lambda r: r["visited_at"], reverse=True)


def _tab_controls(tab: str) -> str:
    """Return the controls bar HTML for one tab."""
    return (
        f'<div class="controls" id="controls-{tab}">\n'
        f'  <input type="text" id="{tab}-search" placeholder="Filter by title or URL\u2026"'
        f' autocomplete="off" oninput="onSearch(\'{tab}\', this.value)">\n'
        f'  <div class="sep">|</div>\n'
        f'  <div class="btn-group">\n'
        f'    <button id="{tab}-btn-all" class="active" onclick="setQuickRange(\'{tab}\',\'all\')">All time</button>\n'
        f'    <button id="{tab}-btn-month" onclick="setQuickRange(\'{tab}\',\'month\')">Last month</button>\n'
        f'    <button id="{tab}-btn-week" onclick="setQuickRange(\'{tab}\',\'week\')">Last week</button>\n'
        f'    <button id="{tab}-btn-day" onclick="setQuickRange(\'{tab}\',\'day\')">Last day</button>\n'
        f'  </div>\n'
        f'  <div class="sep">|</div>\n'
        f'  <span class="date-range-label">From</span>\n'
        f'  <input type="date" id="{tab}-date-from" oninput="setCustomRange(\'{tab}\')">\n'
        f'  <span class="date-range-label">to</span>\n'
        f'  <input type="date" id="{tab}-date-to" oninput="setCustomRange(\'{tab}\')">\n'
        f'  <div class="sep">|</div>\n'
        f'  <div class="btn-group">\n'
        f'    <button onclick="expandAll(\'{tab}\')">Expand all</button>\n'
        f'    <button onclick="collapseAll(\'{tab}\')">Collapse all</button>\n'
        f'  </div>\n'
        f'  <div class="sep">|</div>\n'
        f'  <button id="{tab}-btn-aggregate" class="active" onclick="toggleAggregate(\'{tab}\')">'
        f'Aggregate subdomains</button>\n'
        f'  <div class="result-count">Showing <span id="{tab}-count-shown">0</span>'
        f' of <span id="{tab}-count-total">0</span></div>\n'
        f'</div>'
    )


_H_THEAD = (
    '<thead><tr>'
    '<th onclick="sortBy(\'history\',\'title\')" id="history-th-title">Title <em class="sort-icon">\u2195</em></th>'
    '<th onclick="sortBy(\'history\',\'domain\')" id="history-th-domain">Domain <em class="sort-icon">\u2195</em></th>'
    '<th onclick="sortBy(\'history\',\'url\')" id="history-th-url">URL <em class="sort-icon">\u2195</em></th>'
    '<th onclick="sortBy(\'history\',\'ts_ms\')" id="history-th-ts_ms" class="sorted">Date <em class="sort-icon">\u2193</em></th>'
    '<th onclick="sortBy(\'history\',\'visits\')" id="history-th-visits">Visits <em class="sort-icon">\u2195</em></th>'
    '</tr></thead>'
)

_B_THEAD = (
    '<thead><tr>'
    '<th onclick="sortBy(\'bookmarks\',\'title\')" id="bookmarks-th-title">Title <em class="sort-icon">\u2195</em></th>'
    '<th onclick="sortBy(\'bookmarks\',\'domain\')" id="bookmarks-th-domain">Domain <em class="sort-icon">\u2195</em></th>'
    '<th onclick="sortBy(\'bookmarks\',\'url\')" id="bookmarks-th-url">URL <em class="sort-icon">\u2195</em></th>'
    '<th onclick="sortBy(\'bookmarks\',\'ts_ms\')" id="bookmarks-th-ts_ms" class="sorted">Date <em class="sort-icon">\u2193</em></th>'
    '<th onclick="sortBy(\'bookmarks\',\'bookmark_path\')" id="bookmarks-th-bookmark_path">Path <em class="sort-icon">\u2195</em></th>'
    '</tr></thead>'
)


def render_html(history_rows: list[dict], bookmark_rows: list[dict]) -> str:
    js_history = json.dumps([
        {
            "url": r["url"],
            "title": r["title"],
            "domain": urlparse(r["url"]).netloc,
            "root_domain": root_domain(urlparse(r["url"]).netloc),
            "visits": r["visit_count"],
            "ts": r["visited_at"].isoformat(),
            "ts_ms": int(r["visited_at"].timestamp() * 1000),
        }
        for r in history_rows
    ], ensure_ascii=False)

    js_bookmarks = json.dumps([
        {
            "url": r["url"],
            "title": r["title"],
            "domain": urlparse(r["url"]).netloc,
            "root_domain": root_domain(urlparse(r["url"]).netloc),
            "bookmark_path": r["bookmark_path"],
            "ts": r["visited_at"].isoformat(),
            "ts_ms": int(r["visited_at"].timestamp() * 1000),
        }
        for r in bookmark_rows
        if urlparse(r["url"]).scheme in ("http", "https")
    ], ensure_ascii=False)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    h_count = len(history_rows)
    b_count = len(bookmark_rows)

    h_controls = _tab_controls("history")
    b_controls = _tab_controls("bookmarks")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Vivaldi Report</title>
<style>
  :root {{
    --bg: #0f1117;
    --surface: #1a1d27;
    --border: #2a2d3a;
    --accent: #6c8ebf;
    --accent2: #a78bfa;
    --text: #e2e8f0;
    --muted: #64748b;
    --link: #7dd3fc;
    --link-hover: #bae6fd;
    --row-hover: #1e2235;
    --badge-bg: #2a2d3a;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    font-size: 14px;
    line-height: 1.5;
  }}

  header {{
    padding: 20px 32px 14px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: baseline;
    gap: 16px;
  }}
  header h1 {{
    font-size: 18px;
    font-weight: 600;
    color: var(--text);
  }}
  header .meta {{
    color: var(--muted);
    font-size: 12px;
  }}

  .tab-bar {{
    display: flex;
    gap: 0;
    padding: 0 32px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }}
  .tab-btn {{
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--muted);
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    border-radius: 0;
    transition: color 0.15s, border-color 0.15s;
    margin-bottom: -1px;
  }}
  .tab-btn:hover {{ color: var(--text); border-color: var(--border); }}
  .tab-btn.active {{ color: var(--accent2); border-color: var(--accent2); }}

  .controls {{
    padding: 16px 32px;
    border-bottom: 1px solid var(--border);
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
  }}

  .controls input[type=text] {{
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 13px;
    width: 260px;
    outline: none;
  }}
  .controls input[type=text]:focus {{ border-color: var(--accent); }}
  .controls input[type=date] {{
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 10px;
    border-radius: 6px;
    font-size: 13px;
    outline: none;
  }}
  .controls input[type=date]:focus {{ border-color: var(--accent); }}

  .btn-group {{ display: flex; gap: 4px; }}
  button {{
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--muted);
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 12px;
    cursor: pointer;
    transition: all 0.15s;
  }}
  button:hover {{ border-color: var(--accent); color: var(--text); }}
  button.active {{
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
    font-weight: 600;
  }}

  .sep {{ color: var(--border); user-select: none; }}

  .result-count {{
    margin-left: auto;
    color: var(--muted);
    font-size: 12px;
    white-space: nowrap;
  }}
  .result-count span {{ color: var(--text); font-weight: 600; }}

  table {{
    width: 100%;
    border-collapse: collapse;
  }}
  thead {{
    position: sticky;
    top: 0;
    z-index: 10;
    background: var(--surface);
  }}
  th {{
    padding: 10px 16px;
    text-align: left;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
  }}
  th:hover {{ color: var(--text); }}
  th .sort-icon {{ margin-left: 4px; opacity: 0.4; font-style: normal; }}
  th.sorted .sort-icon {{ opacity: 1; color: var(--accent2); }}

  td {{
    padding: 9px 16px;
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
  }}
  tr:hover td {{ background: var(--row-hover); }}

  .col-title {{ max-width: 340px; }}
  .col-domain {{ white-space: nowrap; }}
  .col-date {{ white-space: nowrap; }}
  .col-url {{ max-width: 380px; }}
  .col-path {{ max-width: 260px; }}

  .title-cell {{
    font-weight: 500;
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 340px;
  }}
  .url-cell {{
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 380px;
  }}
  a {{ color: var(--link); text-decoration: none; }}
  a:hover {{ color: var(--link-hover); text-decoration: underline; }}

  .domain-badge {{
    display: inline-block;
    background: var(--badge-bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1px 7px;
    font-size: 11px;
    color: var(--muted);
    font-family: monospace;
  }}
  .date-text {{
    color: var(--muted);
    font-size: 12px;
    font-family: monospace;
  }}
  .visits-badge {{
    display: inline-block;
    min-width: 24px;
    text-align: center;
    background: var(--badge-bg);
    border-radius: 10px;
    padding: 1px 7px;
    font-size: 11px;
    color: var(--muted);
  }}
  .path-text {{
    font-size: 11px;
    color: var(--muted);
    font-family: monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    display: block;
    max-width: 260px;
  }}

  .empty-state {{
    padding: 80px 32px;
    text-align: center;
    color: var(--muted);
  }}
  .empty-state p {{ font-size: 16px; margin-bottom: 8px; }}

  .date-range-label {{ font-size: 11px; color: var(--muted); white-space: nowrap; }}

  tr.group-header {{ cursor: pointer; }}
  tr.group-header td {{
    background: var(--surface);
    border-top: 2px solid var(--border);
    padding: 7px 16px;
  }}
  tr.group-header:hover td {{ background: #1e2235; }}
  .group-toggle {{
    display: inline-block;
    width: 14px;
    color: var(--accent2);
    font-style: normal;
    margin-right: 6px;
    font-size: 10px;
  }}
  .group-count {{ color: var(--muted); font-size: 11px; margin-left: 8px; }}
  .group-date-range {{ float: right; color: var(--muted); font-size: 11px; font-family: monospace; }}

  mark {{
    background: #fbbf24;
    color: #0f1117;
    border-radius: 2px;
    padding: 0 1px;
  }}
</style>
</head>
<body>

<header>
  <h1>Vivaldi Report</h1>
  <div class="meta">Generated {generated_at} &bull; {h_count} history entries &bull; {b_count} bookmarks</div>
</header>

<nav class="tab-bar">
  <button class="tab-btn active" data-tab="history" onclick="switchTab('history')">History</button>
  <button class="tab-btn" data-tab="bookmarks" onclick="switchTab('bookmarks')">Bookmarks</button>
</nav>

<div id="section-history">
{h_controls}
<table>
{_H_THEAD}
<tbody id="tbody-history"></tbody>
</table>
<div class="empty-state" id="empty-history" style="display:none"><p>No results match your filters.</p></div>
</div>

<div id="section-bookmarks" style="display:none">
{b_controls}
<table>
{_B_THEAD}
<tbody id="tbody-bookmarks"></tbody>
</table>
<div class="empty-state" id="empty-bookmarks" style="display:none"><p>No results match your filters.</p></div>
</div>

<script>
const DATA = {{
  history:   {js_history},
  bookmarks: {js_bookmarks}
}};

const state = {{
  history:   {{ sortCol: 'ts_ms', sortDir: -1, filterText: '', filterFrom: null, filterTo: null, aggregate: true, groupState: new Map(), currentGroupSids: [] }},
  bookmarks: {{ sortCol: 'ts_ms', sortDir: -1, filterText: '', filterFrom: null, filterTo: null, aggregate: true, groupState: new Map(), currentGroupSids: [] }}
}};

// ── Tab switching ─────────────────────────────────────────────────────────────

function switchTab(tab) {{
  ['history', 'bookmarks'].forEach(t => {{
    document.getElementById('section-' + t).style.display = t === tab ? '' : 'none';
  }});
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab)
  );
}}

// ── Grouping ──────────────────────────────────────────────────────────────────

function groupDomain(tab, r) {{
  return state[tab].aggregate ? r.root_domain : r.domain;
}}

function stableId(tab, gd, firstTs) {{
  const s = state[tab];
  return (s.sortCol === 'domain' || s.sortCol === 'url')
    ? 'D:' + gd
    : 'C:' + gd + ':' + firstTs;
}}

function computeGroups(tab, rows) {{
  const s = state[tab];
  const groups = [];
  if (s.sortCol === 'domain' || s.sortCol === 'url') {{
    const domainMap = new Map();
    const order = [];
    rows.forEach(r => {{
      const gd = groupDomain(tab, r);
      if (!domainMap.has(gd)) {{ domainMap.set(gd, []); order.push(gd); }}
      domainMap.get(gd).push(r);
    }});
    order.forEach(gd => groups.push({{ gd, rows: domainMap.get(gd) }}));
  }} else {{
    let cur = null;
    rows.forEach(r => {{
      const gd = groupDomain(tab, r);
      if (!cur || cur.gd !== gd) {{ cur = {{ gd, rows: [r] }}; groups.push(cur); }}
      else cur.rows.push(r);
    }});
  }}
  return groups;
}}

// ── Controls ──────────────────────────────────────────────────────────────────

function toggleAggregate(tab) {{
  state[tab].aggregate = !state[tab].aggregate;
  document.getElementById(tab + '-btn-aggregate').classList.toggle('active', state[tab].aggregate);
  render(tab);
}}

function onSearch(tab, val) {{
  state[tab].filterText = val;
  render(tab);
}}

function setQuickRange(tab, range) {{
  document.querySelectorAll('#controls-' + tab + ' .btn-group button').forEach(b => b.classList.remove('active'));
  document.getElementById(tab + '-date-from').value = '';
  document.getElementById(tab + '-date-to').value = '';
  const s = state[tab];
  s.filterFrom = null; s.filterTo = null;
  const now = Date.now();
  if (range === 'day')        {{ s.filterFrom = now - 86400_000;       document.getElementById(tab + '-btn-day').classList.add('active'); }}
  else if (range === 'week')  {{ s.filterFrom = now - 7 * 86400_000;   document.getElementById(tab + '-btn-week').classList.add('active'); }}
  else if (range === 'month') {{ s.filterFrom = now - 30 * 86400_000;  document.getElementById(tab + '-btn-month').classList.add('active'); }}
  else                        {{ document.getElementById(tab + '-btn-all').classList.add('active'); }}
  render(tab);
}}

function setCustomRange(tab) {{
  document.querySelectorAll('#controls-' + tab + ' .btn-group button').forEach(b => b.classList.remove('active'));
  const from = document.getElementById(tab + '-date-from').value;
  const to   = document.getElementById(tab + '-date-to').value;
  const s = state[tab];
  s.filterFrom = from ? new Date(from).getTime() : null;
  s.filterTo   = to   ? new Date(to).getTime() + 86400_000 - 1 : null;
  render(tab);
}}

// ── Sort ──────────────────────────────────────────────────────────────────────

function sortBy(tab, col) {{
  const s = state[tab];
  if (s.sortCol === col) s.sortDir *= -1;
  else {{ s.sortCol = col; s.sortDir = col === 'ts_ms' ? -1 : 1; }}
  updateSortHeaders(tab);
  render(tab);
}}

function updateSortHeaders(tab) {{
  const s = state[tab];
  document.querySelectorAll('#section-' + tab + ' th').forEach(th => {{
    th.classList.remove('sorted');
    const icon = th.querySelector('.sort-icon');
    if (icon) icon.textContent = '\u2195';
  }});
  const th = document.getElementById(tab + '-th-' + s.sortCol);
  if (th) {{
    th.classList.add('sorted');
    const icon = th.querySelector('.sort-icon');
    if (icon) icon.textContent = s.sortDir === 1 ? '\u2191' : '\u2193';
  }}
}}

// ── Group toggle ──────────────────────────────────────────────────────────────

function toggleGroup(tab, gi) {{
  const s = state[tab];
  const sid = s.currentGroupSids[gi];
  const wasCollapsed = s.groupState.get(sid) ?? true;
  const nowCollapsed = !wasCollapsed;
  s.groupState.set(sid, nowCollapsed);
  document.querySelectorAll(`#tbody-${{tab}} tr.group-child[data-gi="${{gi}}"]`).forEach(tr => {{
    tr.style.display = nowCollapsed ? 'none' : '';
  }});
  const hdr = document.querySelector(`#tbody-${{tab}} tr.group-header[data-gi="${{gi}}"]`);
  if (hdr) hdr.querySelector('.group-toggle').textContent = nowCollapsed ? '\u25b6' : '\u25bc';
}}

function expandAll(tab) {{
  document.querySelectorAll(`#tbody-${{tab}} tr.group-child`).forEach(tr => tr.style.display = '');
  document.querySelectorAll(`#tbody-${{tab}} tr.group-header`).forEach(hdr => {{
    const gi = hdr.dataset.gi;
    const sid = state[tab].currentGroupSids[gi];
    if (sid) state[tab].groupState.set(sid, false);
    const icon = hdr.querySelector('.group-toggle');
    if (icon) icon.textContent = '\u25bc';
  }});
}}

function collapseAll(tab) {{
  document.querySelectorAll(`#tbody-${{tab}} tr.group-child`).forEach(tr => tr.style.display = 'none');
  document.querySelectorAll(`#tbody-${{tab}} tr.group-header`).forEach(hdr => {{
    const gi = hdr.dataset.gi;
    const sid = state[tab].currentGroupSids[gi];
    if (sid) state[tab].groupState.set(sid, true);
    const icon = hdr.querySelector('.group-toggle');
    if (icon) icon.textContent = '\u25b6';
  }});
}}

// ── Utilities ─────────────────────────────────────────────────────────────────

function esc(s) {{
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function highlight(text, q) {{
  if (!q || !text) return esc(text || '');
  const lower = text.toLowerCase();
  const lq = q.toLowerCase();
  let result = '', i = 0;
  while (i < text.length) {{
    const pos = lower.indexOf(lq, i);
    if (pos === -1) {{ result += esc(text.slice(i)); break; }}
    result += esc(text.slice(i, pos));
    result += '<mark>' + esc(text.slice(pos, pos + q.length)) + '</mark>';
    i = pos + q.length;
  }}
  return result;
}}

function fmtDate(iso) {{
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, '0');
  return `${{d.getFullYear()}}-${{pad(d.getMonth()+1)}}-${{pad(d.getDate())}} ${{pad(d.getHours())}}:${{pad(d.getMinutes())}}`;
}}

// ── Render ────────────────────────────────────────────────────────────────────

function render(tab) {{
  const s = state[tab];
  const q = s.filterText.toLowerCase();
  const allRows = DATA[tab];

  let rows = allRows.filter(r => {{
    if (s.filterFrom && r.ts_ms < s.filterFrom) return false;
    if (s.filterTo   && r.ts_ms > s.filterTo)   return false;
    if (q) {{
      const fields = [r.title, r.url, r.domain];
      if (tab === 'bookmarks') fields.push(r.bookmark_path || '');
      if (!fields.some(f => (f || '').toLowerCase().includes(q))) return false;
    }}
    return true;
  }});

  rows.sort((a, b) => {{
    const av = a[s.sortCol] ?? '', bv = b[s.sortCol] ?? '';
    return av < bv ? -s.sortDir : av > bv ? s.sortDir : 0;
  }});

  document.getElementById(tab + '-count-shown').textContent = rows.length;
  document.getElementById(tab + '-count-total').textContent = allRows.length;

  const tbody = document.getElementById('tbody-' + tab);
  const empty = document.getElementById('empty-' + tab);

  if (rows.length === 0) {{ tbody.innerHTML = ''; empty.style.display = ''; return; }}
  empty.style.display = 'none';

  const groups = computeGroups(tab, rows);
  s.currentGroupSids = groups.map(g => stableId(tab, g.gd, g.rows[0].ts_ms));

  const isBookmarks = tab === 'bookmarks';
  const countLabel  = isBookmarks ? 'bookmarks' : 'visits';

  let html = '';
  groups.forEach((g, gi) => {{
    const sid = s.currentGroupSids[gi];
    const isGroup = g.rows.length > 1;
    const collapsed = isGroup ? (s.groupState.get(sid) ?? true) : false;

    if (isGroup) {{
      const newest = fmtDate(g.rows[0].ts);
      const oldest = fmtDate(g.rows[g.rows.length - 1].ts);
      const dateRange = newest === oldest ? newest : `${{oldest}} \u2013 ${{newest}}`;
      html += `<tr class="group-header" data-gi="${{gi}}" onclick="toggleGroup('${{tab}}',${{gi}})">
        <td colspan="5">
          <em class="group-toggle">${{collapsed ? '\u25b6' : '\u25bc'}}</em>
          <span class="domain-badge">${{esc(g.gd)}}</span>
          <span class="group-count">${{g.rows.length}} ${{countLabel}}</span>
          <span class="group-date-range">${{esc(dateRange)}}</span>
        </td></tr>`;
    }}

    g.rows.forEach(r => {{
      const hidden = isGroup && collapsed ? ' style="display:none"' : '';
      const giAttr = isGroup ? ` class="group-child" data-gi="${{gi}}"` : '';
      const lastCol = isBookmarks
        ? `<td class="col-path"><span class="path-text" title="${{esc(r.bookmark_path)}}">${{highlight(r.bookmark_path || '', q)}}</span></td>`
        : `<td><span class="visits-badge">${{r.visits}}</span></td>`;
      html += `<tr${{giAttr}}${{hidden}}>
        <td class="col-title"><div class="title-cell" title="${{esc(r.title)}}">${{highlight(r.title, q)}}</div></td>
        <td class="col-domain"><span class="domain-badge">${{highlight(r.domain, q)}}</span></td>
        <td class="col-url"><div class="url-cell"><a href="${{esc(r.url)}}" target="_blank" rel="noopener" title="${{esc(r.url)}}">${{highlight(r.url, q)}}</a></div></td>
        <td class="col-date"><span class="date-text">${{fmtDate(r.ts)}}</span></td>
        ${{lastCol}}
      </tr>`;
    }});
  }});

  tbody.innerHTML = html;
}}

// ── Init ──────────────────────────────────────────────────────────────────────

['history', 'bookmarks'].forEach(tab => {{
  document.getElementById(tab + '-count-total').textContent = DATA[tab].length;
  updateSortHeaders(tab);
  render(tab);
  document.getElementById(tab + '-search').addEventListener('keydown', e => {{
    if (e.key === 'Escape') {{ e.target.value = ''; onSearch(tab, ''); }}
  }});
}});
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Extract interesting Vivaldi history citations.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to Vivaldi History SQLite file")
    parser.add_argument("--bookmarks", type=Path, default=DEFAULT_BOOKMARKS, help="Path to Vivaldi Bookmarks JSON file")
    parser.add_argument("--blacklist", type=Path, default=DEFAULT_BLACKLIST)
    parser.add_argument("--output", "-o", type=Path, default=Path("report.html"))
    parser.add_argument("--days", type=int, default=None, help="Only look at last N days of history (default: all)")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"Error: History DB not found at {args.db}", file=sys.stderr)
        sys.exit(1)

    bl = load_blacklist(args.blacklist)

    print(f"Querying history from {args.db}…", file=sys.stderr)
    rows = query_history(args.db, args.days)
    print(f"  {len(rows)} total URLs", file=sys.stderr)

    rows = [r for r in rows if not is_blacklisted(r["url"], r["title"], bl)]
    print(f"  {len(rows)} after blacklist", file=sys.stderr)

    rows = [r for r in rows if is_interesting(r["url"], r["title"])]
    print(f"  {len(rows)} after interest filter", file=sys.stderr)

    rows = deduplicate(rows)
    print(f"  {len(rows)} after deduplication", file=sys.stderr)

    bookmark_rows: list[dict] = []
    if args.bookmarks.exists():
        print(f"Querying bookmarks from {args.bookmarks}…", file=sys.stderr)
        # Build url→last_visit mapping from history to annotate bookmarks with real visit dates
        visit_times = query_url_visit_times(args.db)
        bookmark_rows = query_bookmarks(args.bookmarks)
        print(f"  {len(bookmark_rows)} total bookmarks", file=sys.stderr)
        for bm in bookmark_rows:
            if bm["url"] in visit_times:
                bm["visited_at"] = visit_times[bm["url"]]
        bookmark_rows = deduplicate_bookmarks(bookmark_rows)
        print(f"  {len(bookmark_rows)} after deduplication", file=sys.stderr)
    else:
        print(f"Warning: Bookmarks file not found at {args.bookmarks}", file=sys.stderr)

    html = render_html(rows, bookmark_rows)
    args.output.write_text(html, encoding="utf-8")
    print(f"Report written to {args.output}  ({len(rows)} history, {len(bookmark_rows)} bookmarks)", file=sys.stderr)


if __name__ == "__main__":
    main()
