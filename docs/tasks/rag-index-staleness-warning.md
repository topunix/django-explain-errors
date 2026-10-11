Parked 2026-10-11 pending the retrieval-arms eval (vector vs frames+Jedi vs hybrid).

# Task: rag-index-staleness-warning

Source: docs/roadmap.md entry `rag-index-staleness-warning`. Branch: `rag-index-staleness-warning`.
This file is deleted in the final commit.

## Behavior (decided)

Build time:
- Store every indexed file's path, mtime (`st_mtime_ns`), and SHA-256 of content in a new
  table in the index DB: `indexed_files(path PRIMARY KEY, mtime_ns, sha256)`.
- Hash the same bytes that were read for indexing, not a second read.
- Paths use the same convention as existing chunk records.

Retrieval time:
- Stat each indexed file. If mtime differs, hash the file and compare to the stored hash.
  An mtime-only change (git checkout, editor touch) is not stale. A missing file is stale.
- If any indexed file is changed or deleted, log one WARNING on the `explain_errors` logger
  naming the count and the rebuild command `python manage.py build_error_index`.
- Count changed and deleted indexed files only. No directory walk, no new-file detection.
- Detect and warn only. No re-embedding, no file watching.
- Warn once per process. After the warning fires, skip the check for the rest of the
  process. Until it fires, check on every retrieval.
- Legacy index (no file-metadata table): unknown freshness. Log one WARNING asking to
  rebuild with `build_error_index`. Counts toward the once-per-process limit.
- The check never breaks explanation generation. Any exception inside it is caught and
  logged at DEBUG; retrieval proceeds normally.
- No new settings.

## Tests

Sync and async paths where retrieval differs; `assertLogs("explain_errors")`.
- Fresh index, no edits: no warning.
- Content edit to an indexed file: one warning, count 1, rebuild command.
- mtime bump with identical content: no warning.
- Deleted indexed file: counted.
- Two changed files: count 2.
- Second retrieval after a warning: no second warning, no stat/hash work (mock).
- Legacy index without the table: one rebuild warning, results still returned.
- Exception inside the check (patched `os.stat` raising PermissionError): no crash,
  results unchanged.
- RAG disabled or no index: no-op.
- `build_index` writes one metadata row per indexed file with the correct hash.
- Existing suite stays green.

## Docs (separate commits)
- README RAG section: short paragraph on the warning and when to rerun the build command.
  Note the known gap: template edits do not trigger runserver autoreload, so a stale
  template does not re-warn until the next Python edit. No em dashes.
- CHANGELOG.md entry under Unreleased.
- docs/roadmap.md: delete this entry; reword the "no per-file mtime or hash" text in
  pinned-frame-retrieval; flag (do not rewrite) working-tree-diff-context's "Unproven" note.
