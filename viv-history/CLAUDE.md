# viv-history

Extracts "interesting" citations from Vivaldi browser history and all bookmarks, rendered as a two-tab filterable/sortable HTML report. Includes synchronized history from all devices.

## Files

- `extract_history.py` — main script
- `blacklist.json` — editable list of domains/patterns to exclude
- `report.html` — generated output (add to `.gitignore`, regenerate as needed)

## Running

```bash
cd ~/src/viv-history
python3 extract_history.py -o report.html
open report.html
```

The script copies the Vivaldi DB to a temp file before reading, so it works whether or not Vivaldi is running. macOS only — the default DB path assumes `~/Library/Application Support/Vivaldi/Default/History`.

### Options

```
--db PATH          Path to Vivaldi History SQLite file (default: ~/Library/Application Support/Vivaldi/Default/History)
--bookmarks PATH   Path to Vivaldi Bookmarks JSON file (default: ~/Library/Application Support/Vivaldi/Default/Bookmarks)
--output PATH      Output HTML file (default: report.html)
--days N           Only include history from the last N days (default: all time)
```

## How it filters

1. **Blacklist** (`blacklist.json`) — removes domains, URL substrings, and title substrings. Edit this file to tune noise.
2. **Interest filter** — removes bare search-engine result pages (URLs with `?q=` etc.), non-http(s) URLs, and pages with no meaningful title.
3. **Deduplication** — collapses URLs to canonical form: same domain+path = one entry. Fragments (`#anchor`) are always stripped. For YouTube the `?v=` param is kept as the page identity; for HN `?id=` is kept. Tracking params (`utm_*`, `fbclid`, etc.) are stripped from displayed URLs.

## Report UI features

The report has two tabs: **History** and **Bookmarks**. Both share the same UI controls.

- **Sort** by Title, Domain, URL, Date, or Visits/Path — click column headers to toggle asc/desc
- **Quick date filters**: All time / Last month / Last week / Last day
- **Custom date range** pickers
- **Text search** with live highlighting — matches title, URL, domain, and (bookmarks) path. Escape clears.
- **Collapsible domain groups**: consecutive same-domain rows (date sort) or all same-domain rows (URL/domain sort) collapse into a group header showing visit count and date range. Groups start collapsed. Expand/Collapse All buttons in toolbar.
- **Aggregate subdomains** toggle (default: on) — treats `blog.example.com` and `www.example.com` as the same group under `example.com`. Child rows still show the actual subdomain.

### Bookmarks tab specifics

- **Path column** — shows the bookmark folder hierarchy (e.g. `Music › Digital Art`, `Work › Fiuturx`). Top three roots are Music, Home, Work.
- **Date column** — last visited date from history DB; falls back to bookmark creation date if never visited.
- No blacklist or interest filter applied — all bookmarks are shown (deduplicated by URL).

## Blacklist

`blacklist.json` has five sections:

- `domains` — exact netloc match (e.g. `"mail.google.com"`)
- `domain_contains` — substring match on netloc (e.g. `"fiuturx"` catches all subdomains)
- `url_contains` — substring match on full URL
- `url_prefixes` — prefix match (e.g. `"file://"`)
- `title_contains` — substring match on page title

Currently blacklisted categories: Gmail/Google Workspace, Kagi/Google search, YouTube, Wikipedia, NYTimes, Facebook/Instagram/LinkedIn/X, Nextdoor, fiuturx employer tooling (Atlassian, Zoom, GitLab paths, Datadog), Auth flows (Auth0, Okta, Cognito/AWS), HR tooling (TriNet), PayPal, Dreamhost panel, local addresses.
