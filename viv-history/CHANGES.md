# Changes

## 2026-03-05 — Bookmarks tab + search highlighting

### Bookmarks tab

The report now has two tabs: **History** (existing) and **Bookmarks** (new).

The bookmarks tab reads `~/Library/Application Support/Vivaldi/Default/Bookmarks` (JSON) and presents all bookmarked URLs with the same UI as the history tab: sortable columns, date filters, collapsible domain groups, and aggregate-subdomains toggle.

Differences from history tab:
- **Path column** instead of Visits — shows the folder hierarchy within the bookmark file (e.g. `Music › Digital Art`, `Work › Fiuturx`).
- **Date column** shows last visited date cross-referenced from the history DB; falls back to date-added if the URL has no history entry.
- Blacklist and interest filters are **not** applied — bookmarks are user-curated.
- Duplicates are collapsed (canonical URL dedup), keeping the most-recently-visited entry.

New CLI option: `--bookmarks PATH` (default: `~/Library/Application Support/Vivaldi/Default/Bookmarks`).

New Python functions: `query_url_visit_times`, `query_bookmarks`, `deduplicate_bookmarks`.

### Search highlighting

Typing in the search box now highlights matching substrings in amber (`<mark>`) within the Title, Domain, URL, and Path cells of each visible row. The href on links is unaffected.

Bookmarks search also matches against the Path column.

**Escape** clears the search box and resets the filter.
