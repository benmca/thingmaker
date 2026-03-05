#!/usr/bin/env python3
"""
extract_history.py — Pull interesting citations from Vivaldi browser history.

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


def render_html(rows: list[dict]) -> str:
    # Serialize rows as JSON for JS
    js_rows = json.dumps([
        {
            "url": r["url"],
            "title": r["title"],
            "domain": urlparse(r["url"]).netloc,
            "root_domain": root_domain(urlparse(r["url"]).netloc),
            "visits": r["visit_count"],
            "ts": r["visited_at"].isoformat(),
            "ts_ms": int(r["visited_at"].timestamp() * 1000),
        }
        for r in rows
    ], ensure_ascii=False)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    count = len(rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Vivaldi History — Interesting Citations</title>
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
    --danger: #f87171;
    --success: #4ade80;
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
    padding: 24px 32px 16px;
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
  .controls input[type=text]:focus {{
    border-color: var(--accent);
  }}
  .controls input[type=date] {{
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 10px;
    border-radius: 6px;
    font-size: 13px;
    outline: none;
  }}
  .controls input[type=date]:focus {{
    border-color: var(--accent);
  }}

  .btn-group {{
    display: flex;
    gap: 4px;
  }}
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
  .col-url {{ max-width: 420px; }}

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
    max-width: 420px;
  }}
  a {{
    color: var(--link);
    text-decoration: none;
  }}
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

  .empty-state {{
    padding: 80px 32px;
    text-align: center;
    color: var(--muted);
  }}
  .empty-state p {{ font-size: 16px; margin-bottom: 8px; }}

  .date-range-label {{
    font-size: 11px;
    color: var(--muted);
    white-space: nowrap;
  }}

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
  .group-count {{
    color: var(--muted);
    font-size: 11px;
    margin-left: 8px;
  }}
  .group-date-range {{
    float: right;
    color: var(--muted);
    font-size: 11px;
    font-family: monospace;
  }}
</style>
</head>
<body>

<header>
  <h1>Vivaldi History — Interesting Citations</h1>
  <div class="meta">Generated {generated_at} &bull; {count} entries</div>
</header>

<div class="controls">
  <input type="text" id="search" placeholder="Filter by title or URL…" autocomplete="off">

  <div class="sep">|</div>

  <div class="btn-group">
    <button id="btn-all" class="active" onclick="setQuickRange('all')">All time</button>
    <button id="btn-month" onclick="setQuickRange('month')">Last month</button>
    <button id="btn-week" onclick="setQuickRange('week')">Last week</button>
    <button id="btn-day" onclick="setQuickRange('day')">Last day</button>
  </div>

  <div class="sep">|</div>

  <span class="date-range-label">From</span>
  <input type="date" id="date-from" oninput="setCustomRange()">
  <span class="date-range-label">to</span>
  <input type="date" id="date-to" oninput="setCustomRange()">

  <div class="sep">|</div>

  <div class="btn-group">
    <button onclick="expandAll()">Expand all</button>
    <button onclick="collapseAll()">Collapse all</button>
  </div>

  <div class="sep">|</div>

  <button id="btn-aggregate" class="active" onclick="toggleAggregate()">Aggregate subdomains</button>

  <div class="result-count">Showing <span id="count-shown">0</span> of <span id="count-total">0</span></div>
</div>

<table id="results-table">
  <thead>
    <tr>
      <th onclick="sortBy('title')" id="th-title">Title <em class="sort-icon">↕</em></th>
      <th onclick="sortBy('domain')" id="th-domain">Domain <em class="sort-icon">↕</em></th>
      <th onclick="sortBy('url')" id="th-url">URL <em class="sort-icon">↕</em></th>
      <th onclick="sortBy('ts_ms')" id="th-ts_ms" class="sorted">Date <em class="sort-icon">↓</em></th>
      <th onclick="sortBy('visits')" id="th-visits">Visits <em class="sort-icon">↕</em></th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>
<div class="empty-state" id="empty" style="display:none">
  <p>No results match your filters.</p>
</div>

<script>
const ALL_ROWS = {js_rows};

let sortCol = 'ts_ms';
let sortDir = -1; // -1 = desc, 1 = asc
let filterText = '';
let filterFrom = null; // ms
let filterTo = null;   // ms
let aggregateByRootDomain = true;

// groupState: stableId -> true (collapsed) | false (expanded)
// Stable IDs survive re-renders so collapse state is preserved across filter/sort changes.
const groupState = new Map();
// Populated each render so toggleGroup(gi) can look up the stableId by index.
let currentGroupSids = [];

// ── Grouping ─────────────────────────────────────────────────────────────────

function groupDomain(r) {{
  return aggregateByRootDomain ? r.root_domain : r.domain;
}}

function stableId(gd, firstTs) {{
  // When sorted by domain/url every entry for a domain is adjacent → one group per domain.
  // For all other sorts group consecutive runs, keyed by first timestamp.
  return (sortCol === 'domain' || sortCol === 'url')
    ? 'D:' + gd
    : 'C:' + gd + ':' + firstTs;
}}

function computeGroups(rows) {{
  const groups = [];
  if (sortCol === 'domain' || sortCol === 'url') {{
    const domainMap = new Map();
    const order = [];
    rows.forEach(r => {{
      const gd = groupDomain(r);
      if (!domainMap.has(gd)) {{ domainMap.set(gd, []); order.push(gd); }}
      domainMap.get(gd).push(r);
    }});
    order.forEach(gd => groups.push({{ gd, rows: domainMap.get(gd) }}));
  }} else {{
    let cur = null;
    rows.forEach(r => {{
      const gd = groupDomain(r);
      if (!cur || cur.gd !== gd) {{ cur = {{ gd, rows: [r] }}; groups.push(cur); }}
      else cur.rows.push(r);
    }});
  }}
  return groups;
}}

function toggleAggregate() {{
  aggregateByRootDomain = !aggregateByRootDomain;
  document.getElementById('btn-aggregate').classList.toggle('active', aggregateByRootDomain);
  render();
}}

// ── Toggle ────────────────────────────────────────────────────────────────────

function toggleGroup(gi) {{
  const sid = currentGroupSids[gi];
  const wasCollapsed = groupState.get(sid) ?? true;
  const nowCollapsed = !wasCollapsed;
  groupState.set(sid, nowCollapsed);
  document.querySelectorAll(`tr.group-child[data-gi="${{gi}}"]`).forEach(tr => {{
    tr.style.display = nowCollapsed ? 'none' : '';
  }});
  const hdr = document.querySelector(`tr.group-header[data-gi="${{gi}}"]`);
  if (hdr) hdr.querySelector('.group-toggle').textContent = nowCollapsed ? '▶' : '▼';
}}

function expandAll() {{
  document.querySelectorAll('tr.group-child').forEach(tr => tr.style.display = '');
  document.querySelectorAll('tr.group-header').forEach(hdr => {{
    const gi = hdr.dataset.gi;
    const sid = currentGroupSids[gi];
    if (sid) groupState.set(sid, false);
    const icon = hdr.querySelector('.group-toggle');
    if (icon) icon.textContent = '▼';
  }});
}}

function collapseAll() {{
  document.querySelectorAll('tr.group-child').forEach(tr => tr.style.display = 'none');
  document.querySelectorAll('tr.group-header').forEach(hdr => {{
    const gi = hdr.dataset.gi;
    const sid = currentGroupSids[gi];
    if (sid) groupState.set(sid, true);
    const icon = hdr.querySelector('.group-toggle');
    if (icon) icon.textContent = '▶';
  }});
}}

// ── Utilities ─────────────────────────────────────────────────────────────────

function setQuickRange(range) {{
  document.querySelectorAll('.btn-group button').forEach(b => b.classList.remove('active'));
  document.getElementById('date-from').value = '';
  document.getElementById('date-to').value = '';
  filterFrom = null; filterTo = null;
  const now = Date.now();
  if (range === 'day')   {{ filterFrom = now - 86400_000;        document.getElementById('btn-day').classList.add('active'); }}
  else if (range === 'week')  {{ filterFrom = now - 7 * 86400_000;   document.getElementById('btn-week').classList.add('active'); }}
  else if (range === 'month') {{ filterFrom = now - 30 * 86400_000;  document.getElementById('btn-month').classList.add('active'); }}
  else {{ document.getElementById('btn-all').classList.add('active'); }}
  render();
}}

function setCustomRange() {{
  document.querySelectorAll('.btn-group button').forEach(b => b.classList.remove('active'));
  const from = document.getElementById('date-from').value;
  const to   = document.getElementById('date-to').value;
  filterFrom = from ? new Date(from).getTime() : null;
  filterTo   = to   ? new Date(to).getTime() + 86400_000 - 1 : null;
  render();
}}

function sortBy(col) {{
  if (sortCol === col) sortDir *= -1;
  else {{ sortCol = col; sortDir = col === 'ts_ms' ? -1 : 1; }}
  updateSortHeaders();
  render();
}}

function updateSortHeaders() {{
  document.querySelectorAll('th').forEach(th => {{
    th.classList.remove('sorted');
    const icon = th.querySelector('.sort-icon');
    if (icon) icon.textContent = '↕';
  }});
  const th = document.getElementById('th-' + sortCol);
  if (th) {{
    th.classList.add('sorted');
    const icon = th.querySelector('.sort-icon');
    if (icon) icon.textContent = sortDir === 1 ? '↑' : '↓';
  }}
}}

function esc(s) {{
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function fmtDate(iso) {{
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, '0');
  return `${{d.getFullYear()}}-${{pad(d.getMonth()+1)}}-${{pad(d.getDate())}} ${{pad(d.getHours())}}:${{pad(d.getMinutes())}}`;
}}

// ── Render ────────────────────────────────────────────────────────────────────

function render() {{
  const q = filterText.toLowerCase();

  let rows = ALL_ROWS.filter(r => {{
    if (filterFrom && r.ts_ms < filterFrom) return false;
    if (filterTo   && r.ts_ms > filterTo)   return false;
    if (q && !r.title.toLowerCase().includes(q) && !r.url.toLowerCase().includes(q) && !r.domain.toLowerCase().includes(q)) return false;
    return true;
  }});

  rows.sort((a, b) => {{
    const av = a[sortCol] ?? '', bv = b[sortCol] ?? '';
    return av < bv ? -sortDir : av > bv ? sortDir : 0;
  }});

  document.getElementById('count-shown').textContent = rows.length;
  document.getElementById('count-total').textContent = ALL_ROWS.length;

  const tbody = document.getElementById('tbody');
  const empty = document.getElementById('empty');

  if (rows.length === 0) {{ tbody.innerHTML = ''; empty.style.display = ''; return; }}
  empty.style.display = 'none';

  const groups = computeGroups(rows);
  currentGroupSids = groups.map(g => stableId(g.gd, g.rows[0].ts_ms));

  let html = '';
  groups.forEach((g, gi) => {{
    const sid = currentGroupSids[gi];
    const isGroup = g.rows.length > 1;
    const collapsed = isGroup ? (groupState.get(sid) ?? true) : false;

    if (isGroup) {{
      const newest = fmtDate(g.rows[0].ts);
      const oldest = fmtDate(g.rows[g.rows.length - 1].ts);
      const dateRange = newest === oldest ? newest : `${{oldest}} – ${{newest}}`;
      html += `<tr class="group-header" data-gi="${{gi}}" onclick="toggleGroup(${{gi}})">
        <td colspan="5">
          <em class="group-toggle">${{collapsed ? '▶' : '▼'}}</em>
          <span class="domain-badge">${{esc(g.gd)}}</span>
          <span class="group-count">${{g.rows.length}} visits</span>
          <span class="group-date-range">${{esc(dateRange)}}</span>
        </td></tr>`;
    }}

    g.rows.forEach(r => {{
      const hidden = isGroup && collapsed ? ' style="display:none"' : '';
      const giAttr = isGroup ? ` class="group-child" data-gi="${{gi}}"` : '';
      html += `<tr${{giAttr}}${{hidden}}>
        <td class="col-title"><div class="title-cell" title="${{esc(r.title)}}">${{esc(r.title)}}</div></td>
        <td class="col-domain"><span class="domain-badge">${{esc(r.domain)}}</span></td>
        <td class="col-url"><div class="url-cell"><a href="${{esc(r.url)}}" target="_blank" rel="noopener" title="${{esc(r.url)}}">${{esc(r.url)}}</a></div></td>
        <td class="col-date"><span class="date-text">${{fmtDate(r.ts)}}</span></td>
        <td><span class="visits-badge">${{r.visits}}</span></td>
      </tr>`;
    }});
  }});

  tbody.innerHTML = html;
}}

document.getElementById('search').addEventListener('input', e => {{
  filterText = e.target.value;
  render();
}});

document.getElementById('count-total').textContent = ALL_ROWS.length;
updateSortHeaders();
render();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Extract interesting Vivaldi history citations.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to Vivaldi History SQLite file")
    parser.add_argument("--blacklist", type=Path, default=DEFAULT_BLACKLIST)
    parser.add_argument("--output", "-o", type=Path, default=Path("report.html"))
    parser.add_argument("--days", type=int, default=None, help="Only look at last N days (default: all)")
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

    html = render_html(rows)
    args.output.write_text(html, encoding="utf-8")
    print(f"Report written to {args.output}  ({len(rows)} entries)", file=sys.stderr)


if __name__ == "__main__":
    main()
