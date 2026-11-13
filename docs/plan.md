# Animal Agenda Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find Madison and Dane County agenda items that materially affect animals, report what the last three years would have flagged, and email a weekly digest of new ones.

**Architecture:** A Python CLI (`aaw`) over one SQLite file. `sync` copies Legistar matters, meetings and agenda items into the database; `classify` runs a two-stage LLM judgment and stores verdicts; `report` and `digest` only read the database. The LLM sits behind a small `Judge` interface so every test runs offline with a fake.

**Tech Stack:** Python 3.12, uv, httpx, pypdf, the `anthropic` SDK, pytest, SQLite (stdlib), smtplib (stdlib), GitHub Actions for the weekly run.

**Spec:** `docs/design.md` in the new repo (copied in Task 1 from `/Users/stevenknaack/Projects/brainstorming/2026-11-12-animal-agenda-watcher-design.md`). Read it before starting.

## Global Constraints

- Public data only. No scraping behind logins.
- Free or nearly free to run: classify each matter once per stage and never re-send a stored verdict to the model.
- Adding a city is a `config.toml` change, not new code.
- No output may claim what a body can legally consider. Briefs say what is being decided, when, where, and how to comment.
- The tool never submits comments.
- Be polite to Legistar: one request at a time, at least 0.2 s apart, at most 3 retries with backoff.
- Tests never touch the network or the Claude API.
- New repo at `/Users/stevenknaack/Projects/animal-agenda-watcher`, MIT licence, package name `agenda_watcher`.

## Facts measured on 2026-11-12

- Base URL `https://webapi.legistar.com/v1/{client}`; clients `madison` and `dane`; no key needed.
- `/matters` accepts OData `$top` (max 1000), `$skip`, `$filter`, `$orderby`. One year is about 3,700 matters for Madison and 2,300 for Dane, so three years is roughly 18,000.
- `/events/{id}/eventitems?Attachments=1` lists agenda lines. Only some carry `EventItemMatterId` (5 of 13 and 8 of 31 in the meetings sampled); the rest are procedural or free-text lines.
- `/matters/{id}/versions` returns `[{"Key": "<textId>", "Value": "<n>"}]`; `/matters/{id}/texts/{textId}` returns `MatterTextPlain`.
- `/matters/{id}/attachments` returns `MatterAttachmentName` and `MatterAttachmentHyperlink` (direct PDF links).
- A bad request returns HTTP 200-or-4xx with a JSON object `{"Message": ...}` instead of a list.
- Madison has placeholder meetings dated 2030 and 2050, and `EventAgendaStatusName` takes values including `Approved`, `Amended`, `Cancelled`.

## Review Focus

1. Legistar returns an object with `Message` where a list is expected: the client raises `LegistarError` naming the URL, and `sync` logs it and carries on with the next matter. (Task 2, Task 3)
2. A matter has a null title or no text version: it is stored, skipped by stage 1 only if both title and name are empty, and never crashes a batch. (Task 3, Task 4)
3. An attachment is a scanned image, 404s, or is huge: it contributes no text, and stage 2 still judges on title and matter text. (Task 5)
4. The model returns malformed output or omits an item from a batch: stage 1 treats the item as a survivor; stage 2 retries once, then records an error row, never a "not relevant" verdict. (Task 4, Task 5)
5. A week with nothing flagged, or `digest` run twice: the reader still gets a short "nothing new" email, and no item is emailed twice. Cancelled and far-future placeholder meetings never appear as upcoming. (Task 8)

---

## File Structure

```
pyproject.toml            entry point: aaw = agenda_watcher.cli:main
config.toml               clients, how-to-comment text, model names
docs/design.md, docs/plan.md
src/agenda_watcher/
  config.py               load config.toml into dataclasses
  store.py                SQLite schema and all queries
  legistar.py             HTTP client: paging, retry, error detection
  sync.py                 Legistar -> store
  judge.py                Judge protocol, ClaudeJudge, prompts, categories
  classify.py             stage 1 and stage 2 orchestration
  attachments.py          fetch PDF, extract capped text
  evalset.py              labelling and recall/precision numbers
  report.py               backtest report (markdown)
  digest.py               build and send the weekly email
  cli.py                  argparse subcommands
tests/                    one test file per module, fixtures in tests/fixtures/
.github/workflows/weekly.yml
```

---

### Task 1: Repo, config and store

**Files:**
- Create: `pyproject.toml`, `config.toml`, `LICENSE`, `README.md`, `docs/design.md`, `docs/plan.md`, `src/agenda_watcher/config.py`, `src/agenda_watcher/store.py`
- Test: `tests/test_store.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `config.load(path: Path) -> Config` with `Config.clients: list[Client]`, `Client(slug: str, name: str, how_to_comment: str, comment_source_url: str)`, `Config.triage_model: str`, `Config.review_model: str`, `Config.backtest_start: date`
  - `store.connect(path: Path) -> sqlite3.Connection` (creates the schema, `row_factory = sqlite3.Row`)
  - `store.upsert_matter(conn, client: str, m: dict)`, `upsert_event(conn, client, e: dict)`, `upsert_event_item(conn, client, event_id: int, item: dict)`, `upsert_attachment(conn, client, matter_id: int, a: dict)`. Each takes the raw Legistar JSON object.
  - `store.set_matter_text(conn, client, matter_id: int, text: str)`
  - `store.get_meta(conn, key: str) -> str | None`, `set_meta(conn, key, value)`

Schema (primary keys in brackets):

| Table | Columns |
|---|---|
| `matters` [client, matter_id] | file, type, status, title, name, body_name, intro_date, last_modified, text, text_fetched (0/1) |
| `events` [client, event_id] | body_name, date, time, location, agenda_url, site_url, agenda_status |
| `event_items` [client, event_item_id] | event_id, matter_id (nullable), title |
| `attachments` [client, attachment_id] | matter_id, name, url |
| `verdicts` [client, matter_id, stage] | relevant (0/1/NULL for error), category, confidence, reason, summary, model, created_at |
| `labels` [client, matter_id] | relevant, category, source, note |
| `digested` [client, matter_id, event_id] | sent_at |
| `meta` [key] | value |

Dates are stored as ISO strings (`YYYY-MM-DD`), taken from the first 10 characters of Legistar's timestamps.

- [ ] **Step 1:** `mkdir` the repo, `git init`, `uv init --package`, add dependencies (`httpx`, `pypdf`, `anthropic`; dev: `pytest`). Copy the design and this plan into `docs/`.
- [ ] **Step 2:** Write `config.toml` with clients `madison` ("City of Madison") and `dane` ("Dane County"), `backtest_start = 2023-11-01`, `triage_model = "claude-haiku-5-5"`, `review_model = "claude-sonnet-5-5"`. For `how_to_comment` and `comment_source_url`, read each government's own public-comment instructions page and paraphrase it in two or three sentences; put the page URL in `comment_source_url`. Do not write these from memory.
- [ ] **Step 3: Write failing tests**

```python
def test_upsert_matter_is_idempotent_and_updates(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    m = {"MatterId": 7, "MatterFile": "90001", "MatterTypeName": "Resolution",
         "MatterStatusName": "Passed", "MatterTitle": "A", "MatterName": None,
         "MatterBodyName": "COMMON COUNCIL", "MatterIntroDate": "2025-03-04T00:00:00",
         "MatterLastModifiedUtc": "2025-03-05T10:00:00.1"}
    store.upsert_matter(conn, "madison", m)
    store.upsert_matter(conn, "madison", {**m, "MatterTitle": "B"})
    rows = conn.execute("select title, intro_date, text_fetched from matters").fetchall()
    assert [tuple(r) for r in rows] == [("B", "2025-03-04", 0)]

def test_upsert_matter_keeps_fetched_text(tmp_path):
    # set_matter_text then upsert_matter again -> text and text_fetched=1 survive

def test_same_matter_id_in_two_clients_are_separate_rows(tmp_path): ...
def test_event_item_without_matter_stores_null_matter_id(tmp_path): ...
def test_config_loads_two_clients_and_backtest_start(): ...
```

- [ ] **Step 4:** Run `uv run pytest -q`; expect failures on missing modules.
- [ ] **Step 5:** Implement `config.py` and `store.py`.
- [ ] **Step 6:** Run `uv run pytest -q`; expect all pass.
- [ ] **Step 7:** Commit: `feat: project skeleton, config and sqlite store`.

---

### Task 2: Legistar client

**Files:**
- Create: `src/agenda_watcher/legistar.py`
- Test: `tests/test_legistar.py` (use `httpx.MockTransport`)

**Interfaces:**
- Produces:
  - `class LegistarError(Exception)`
  - `class Legistar(client: str, http: httpx.Client | None = None, pause: float = 0.2)`
  - `.paged(path: str, filter: str | None = None, orderby: str | None = None, params: dict | None = None) -> Iterator[dict]` (pages with `$top=1000` and `$skip` until a short page)
  - `.get(path: str, params: dict | None = None) -> list | dict`
  - `.matter_text(matter_id: int) -> str` (latest version's `MatterTextPlain`, `""` when there are no versions)
  - `odata_date(d: date) -> str` returning `datetime'2025-11-01'`

- [ ] **Step 1: Write failing tests**

```python
def test_paged_stops_on_short_page():
    # transport returns 1000, 1000, 12 items; assert 2012 yielded and
    # the three requests carried $skip = 0, 1000, 2000

def test_message_object_raises_legistar_error():
    # transport returns {"Message": "bad"} for a list endpoint
    with pytest.raises(LegistarError, match="bad"): list(lg.paged("/matters"))

def test_retries_on_503_then_succeeds():   # 503, 503, 200 -> result, 3 calls
def test_gives_up_after_three_retries():   # four 503s -> LegistarError
def test_matter_text_uses_last_version():  # versions [{"Key":"5"},{"Key":"9"}] -> requests /texts/9
def test_matter_text_empty_when_no_versions(): assert lg.matter_text(1) == ""
```

- [ ] **Step 2:** Run `uv run pytest tests/test_legistar.py -q`; expect fail.
- [ ] **Step 3:** Implement. Retry on 429 and 5xx and on `httpx.TransportError`, sleeping 1, 2, 4 s (inject the sleep function so tests do not wait). Timeout 60 s.
- [ ] **Step 4:** Run tests; expect pass.
- [ ] **Step 5:** Commit: `feat: legistar api client with paging and retry`.

---

### Task 3: Sync

**Files:**
- Create: `src/agenda_watcher/sync.py`, `src/agenda_watcher/cli.py`
- Test: `tests/test_sync.py` (fake Legistar object returning canned lists)

**Interfaces:**
- Consumes: `Legistar`, `store.*` from Tasks 1 and 2.
- Produces:
  - `sync.sync_matters(conn, lg, client: str, since: date) -> int`: filter `MatterIntroDate ge <since>` on first run; on later runs `MatterLastModifiedUtc gt <watermark>`, with the watermark kept in `meta` under `matters_watermark:{client}`.
  - `sync.sync_events(conn, lg, client: str, start: date, end: date) -> int`: events with `EventDate` in the range, then each event's items with `Attachments=1`, storing attachments found on items.
  - `sync.fetch_text(conn, lg, client: str, matter_id: int) -> str`: fetches once, stores, sets `text_fetched`.
  - CLI: `aaw sync [--since YYYY-MM-DD]` runs both for every configured client. Default event range is `backtest_start` to today + 90 days on first run, and today - 14 to today + 90 afterwards.

- [ ] **Step 1: Write failing tests**

```python
def test_first_sync_filters_by_intro_date_and_sets_watermark(): ...
def test_second_sync_filters_by_last_modified_watermark(): ...
def test_sync_events_stores_items_and_their_attachments(): ...
def test_event_items_failure_for_one_event_does_not_stop_sync():
    # fake raises LegistarError for event 2 of 3; events 1 and 3 have items, count returned is 3
def test_matter_with_null_title_is_stored(): ...
def test_fetch_text_calls_api_once():
    # two calls to fetch_text -> lg.matter_text called once
```

- [ ] **Step 2:** Run; expect fail.
- [ ] **Step 3:** Implement `sync.py` and the `sync` subcommand.
- [ ] **Step 4:** Run tests; expect pass.
- [ ] **Step 5: Live check.** Run `uv run aaw sync --since 2026-10-01`. Expect it to finish in a few minutes, then `sqlite3 aaw.db "select client, count(*) from matters group by 1"` shows several hundred rows per client.
- [ ] **Step 6:** Run the full three-year sync (`uv run aaw sync`) and note the row counts in the README. Expect about 18,000 matters.
- [ ] **Step 7:** Commit: `feat: sync legistar matters, meetings and agenda items`.

---

### Task 4: Judge and stage 1 triage

**Before starting:** load the `claude-api` skill and use it for the SDK call shape, structured output, and current model names and prices. If the two model names in `config.toml` are wrong or a cheaper fit exists, change the config, not the code.

**Files:**
- Create: `src/agenda_watcher/judge.py`, `src/agenda_watcher/classify.py`
- Modify: `src/agenda_watcher/cli.py`
- Test: `tests/test_triage.py`

**Interfaces:**
- Produces:
  - `CATEGORIES = ("animal_agriculture", "wildlife_management", "captive_animals", "sale_and_use", "food_policy", "animal_services")`
  - `@dataclass TriageItem(matter_id: int, title: str, type: str, body_name: str)`
  - `@dataclass Review(relevant: bool, category: str | None, confidence: float, reason: str, summary: str)`
  - `class Judge(Protocol)`: `triage(items: list[TriageItem]) -> set[int]` (ids worth a closer look) and `review(title: str, type: str, body_name: str, text: str, attachment_names: list[str], attachment_text: str) -> Review`
  - `class ClaudeJudge(triage_model: str, review_model: str)` implementing it
  - `class JudgeError(Exception)`
  - `classify.run_triage(conn, judge: Judge, batch_size: int = 50) -> tuple[int, int]` returning (judged, survivors); writes `verdicts` rows with `stage = 1`
  - CLI: `aaw classify --stage 1`

The triage prompt must state the scope from the spec's "What counts as relevant" section verbatim, including the exclusion of routine items with no decision at stake, and must tell the model to favour recall: when unsure, keep the item. It must also name the disguises: conditional use permits, rezoning, agricultural or meat processing, animal feeding operations, manure storage, nutrient management, wildlife "management" contracts, and zoo contracts.

- [ ] **Step 1: Write failing tests** (with a `FakeJudge`)

```python
def test_triage_writes_stage1_verdicts_for_all_items():
    # 3 matters, fake returns {2}; verdicts: relevant = 0, 1, 0; returns (3, 1)

def test_triage_skips_matters_already_judged():
    # second run_triage makes zero judge calls

def test_triage_batches_by_batch_size():   # 120 matters, batch 50 -> 3 calls

def test_judge_error_marks_whole_batch_as_survivors():
    # fake raises JudgeError -> all items in that batch get relevant = 1, reason "triage failed"

def test_matter_with_empty_title_and_name_is_skipped_not_sent(): ...
```

And one for `ClaudeJudge` parsing, with the SDK client replaced by a stub:

```python
def test_claude_triage_ignores_ids_not_in_the_batch():
    # model returns [2, 999]; result == {2}
```

- [ ] **Step 2:** Run; expect fail.
- [ ] **Step 3:** Implement `judge.py` and `run_triage`. Use the matter's title, falling back to its name.
- [ ] **Step 4:** Run tests; expect pass.
- [ ] **Step 5: Live check on a small slice.** Run stage 1 on one month only (add `--since/--until` to `classify`). Read every survivor and 100 random non-survivors by eye. Expect survivors to be a few percent of items. If obviously relevant items were dropped, fix the prompt before spending on the full run.
- [ ] **Step 6:** Run stage 1 over the full database; record judged, survivors and cost in the README.
- [ ] **Step 7:** Commit: `feat: judge interface and stage 1 triage`.

---

### Task 5: Attachments and stage 2 review

**Files:**
- Create: `src/agenda_watcher/attachments.py`
- Modify: `src/agenda_watcher/classify.py`, `src/agenda_watcher/judge.py`, `src/agenda_watcher/cli.py`
- Test: `tests/test_attachments.py`, `tests/test_review.py`, `tests/fixtures/small.pdf`

**Interfaces:**
- Consumes: `Judge.review`, `sync.fetch_text`, `store` attachments table.
- Produces:
  - `attachments.extract_text(url: str, http: httpx.Client, max_bytes: int = 15_000_000, max_pages: int = 15, max_chars: int = 12_000) -> str` (returns `""` on any failure)
  - `classify.run_review(conn, lg, judge: Judge, http: httpx.Client, max_attachments: int = 3) -> tuple[int, int]` returning (reviewed, flagged); writes `verdicts` rows with `stage = 2`
  - CLI: `aaw classify --stage 2`, and `aaw classify` with no stage runs 1 then 2

`run_review` takes every matter with a stage 1 verdict of relevant and no stage 2 verdict. For each it fetches the matter text if not yet fetched, fetches the matter's attachments from Legistar if none are stored, extracts text from the first `max_attachments` PDFs, and calls `judge.review`. Total attachment text passed to the judge is capped at 30,000 characters.

The review prompt asks for: relevant or not under the spec's scope; one category from `CATEGORIES`; confidence from 0 to 1; a one-sentence reason; and a two-sentence plain-language summary of what is being decided. It must forbid any statement about what the body may legally consider.

- [ ] **Step 1: Write failing tests**

```python
def test_extract_text_reads_fixture_pdf():        # contains a known phrase
def test_extract_text_returns_empty_on_404(): assert extract_text(...) == ""
def test_extract_text_returns_empty_on_non_pdf_bytes(): ...
def test_extract_text_stops_download_over_max_bytes(): ...
def test_extract_text_truncates_to_max_chars(): ...

def test_review_only_touches_stage1_survivors(): ...
def test_review_stores_category_confidence_reason_summary(): ...
def test_review_retries_once_then_records_error_row():
    # fake raises JudgeError twice -> verdict row with relevant IS NULL, reason starts "review failed"
def test_error_rows_are_retried_on_next_run(): ...
def test_review_runs_when_all_attachments_fail():  # judge still called, attachment_text == ""
def test_unknown_category_from_model_is_stored_as_null_category(): ...
```

- [ ] **Step 2:** Run; expect fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run tests; expect pass.
- [ ] **Step 5:** Run stage 2 over the full database. Record reviewed, flagged, error count and cost in the README.
- [ ] **Step 6:** Commit: `feat: attachment text and stage 2 review`.

---

### Task 6: Hand-checked evaluation set

**Files:**
- Create: `src/agenda_watcher/evalset.py`
- Modify: `src/agenda_watcher/cli.py`
- Test: `tests/test_evalset.py`

**Interfaces:**
- Produces:
  - `evalset.keyword_hits(conn, terms: list[str]) -> list[Row]`: matters, and separately agenda lines with no matter, whose title matches any term (case-insensitive)
  - `evalset.sample_unflagged(conn, n: int, seed: int) -> list[Row]`: random matters whose final verdict is not relevant
  - `evalset.record_label(conn, client, matter_id, relevant: bool, category: str | None, source: str, note: str = "")`
  - `evalset.scores(conn) -> Scores` with `labelled: int`, `true_pos`, `false_pos`, `false_neg`, `precision: float | None`, `recall: float | None`, `miss_rate_in_random_sample: float | None`, `random_sample_size: int`
  - CLI: `aaw label --source keyword|random|flagged [--n 200]` shows one item at a time (title, body, date, link) and reads `y`, `n` or `s` to skip; `aaw scores` prints the numbers

Default keyword list: slaughter, meat processing, livestock, feedlot, animal feeding, CAFO, manure, dairy, poultry, hog, swine, mink, fur, foie gras, rodeo, circus, zoo, Vilas, deer, geese, goose, coyote, beaver, trapping, cull, rodenticide, pesticide, pet store, puppy, kennel, animal control, humane society, hunting, fishing, plant-based.

- [ ] **Step 1: Write failing tests**

```python
def test_scores_precision_and_recall():
    # labels: 4 relevant of which 3 flagged; 2 flagged-but-labelled-irrelevant
    s = evalset.scores(conn)
    assert (s.true_pos, s.false_pos, s.false_neg) == (3, 2, 1)
    assert s.precision == pytest.approx(0.6) and s.recall == pytest.approx(0.75)

def test_scores_none_when_nothing_labelled(): assert evalset.scores(conn).precision is None
def test_miss_rate_uses_only_random_source_labels(): ...
def test_keyword_hits_include_agenda_lines_without_matter(): ...
def test_sample_unflagged_is_deterministic_for_a_seed(): ...
```

- [ ] **Step 2:** Run; expect fail. **Step 3:** Implement. **Step 4:** Run; expect pass.
- [ ] **Step 5: Labelling session (a person does this, about two hours).** Label all flagged items, all keyword hits that were not flagged, and 200 random unflagged items. Add to the keyword list any local fights you know of from news coverage and confirm they are in the database.
- [ ] **Step 6:** Run `aaw scores`. If recall on labelled items is below 0.9 or more than half of flagged items are irrelevant, revise the prompts, delete the affected `verdicts` rows, re-run, and re-score. Record the final numbers.
- [ ] **Step 7:** Commit: `feat: labelling tool and evaluation scores`.

---

### Task 7: Backtest report

**Files:**
- Create: `src/agenda_watcher/report.py`
- Modify: `src/agenda_watcher/cli.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `evalset.scores`, verdicts, events, event_items.
- Produces:
  - `report.notice_days(conn, client, matter_id) -> int | None`: days from the matter's intro date to the first non-cancelled meeting whose agenda lists it; `None` if it never appeared on an agenda
  - `report.build(conn, config) -> str` (markdown)
  - CLI: `aaw report [--out report.md]`

Report sections, in this order: (1) headline: N flagged items across the period, per year and per client; (2) by category; (3) by body, top ten; (4) notice: median and quartiles of `notice_days`, and the share with fewer than 7 days, labelled as an approximation because Legistar does not record when an agenda was first posted; (5) how good is the classifier: the `Scores` numbers with the count of items hand-checked, stated plainly; (6) what was not looked at: the count of agenda lines with no matter attached, and how many of those matched a keyword; (7) the full list of flagged items by date, each with title, body, summary and link.

The verdict used everywhere is: a human label if one exists, otherwise the stage 2 verdict.

- [ ] **Step 1: Write failing tests**

```python
def test_notice_days_uses_first_non_cancelled_meeting():
    # intro 2025-03-01; meetings 2025-03-05 (Cancelled), 2025-03-12 (Approved) -> 11
def test_notice_days_none_when_never_on_agenda(): ...
def test_human_label_overrides_model_verdict_in_counts(): ...
def test_report_states_number_hand_checked(): assert "hand-checked" in md and "212" in md
def test_report_with_zero_flagged_items_renders(): ...
```

- [ ] **Step 2:** Run; expect fail. **Step 3:** Implement. **Step 4:** Run; expect pass.
- [ ] **Step 5:** Run `aaw report --out report.md` on the real database and read it end to end. This is the pitch document for ASAP and the decision point from the spec: if flagged items are too few to justify a weekly digest, stop here and raise it with the author before Task 8.
- [ ] **Step 6:** Commit: `feat: backtest report`.

---

### Task 8: Weekly digest

**Files:**
- Create: `src/agenda_watcher/digest.py`, `.github/workflows/weekly.yml`
- Modify: `src/agenda_watcher/cli.py`, `README.md`
- Test: `tests/test_digest.py`

**Interfaces:**
- Produces:
  - `digest.pending(conn, today: date, horizon_days: int = 90) -> list[DigestEntry]`: flagged matters on a meeting dated from `today` to `today + horizon_days`, not cancelled, not already in `digested` for that meeting; plus flagged matters introduced in the last 14 days with no meeting yet (`event_id = 0`)
  - `@dataclass DigestEntry(client, matter_id, event_id, title, category, summary, body_name, meeting_date: date | None, meeting_time, location, link, how_to_comment, comment_source_url)`
  - `digest.render(entries: list[DigestEntry], today: date) -> tuple[str, str]` (subject, plain-text body), sorted by meeting date, soonest first
  - `digest.send(subject, body, env: Mapping[str, str])` using `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `DIGEST_TO`
  - `digest.mark_sent(conn, entries, now: datetime)`
  - CLI: `aaw digest [--dry-run]` prints instead of sending and does not mark anything sent; `aaw weekly` runs sync, classify, digest

Subject is `Animal agenda watch: N items` or `Animal agenda watch: nothing new this week`. Each entry shows the summary, body, date, time, location, the Legistar link, then the client's how-to-comment text with its source URL. Entries are marked sent only after `send` returns without error.

- [ ] **Step 1: Write failing tests**

```python
def test_pending_excludes_cancelled_and_far_future_meetings():
    # meetings: today+3 Approved, today+5 Cancelled, 2050-09-24 -> only the first

def test_pending_excludes_already_digested_pairs(): ...
def test_same_matter_on_a_new_meeting_is_pending_again(): ...
def test_recent_flagged_matter_without_meeting_is_included_once(): ...
def test_render_empty_says_nothing_new():
    subject, body = digest.render([], date(2026, 11, 16))
    assert subject == "Animal agenda watch: nothing new this week"
def test_render_orders_by_meeting_date_and_includes_comment_source(): ...
def test_dry_run_does_not_mark_sent(): ...
def test_failed_send_does_not_mark_sent(): ...
def test_body_never_contains_legal_claims_field():
    # DigestEntry has no legal-framing field; body contains only the fields listed above
```

- [ ] **Step 2:** Run; expect fail. **Step 3:** Implement. **Step 4:** Run; expect pass.
- [ ] **Step 5:** Write `weekly.yml`: cron Mondays 12:00 UTC plus manual trigger; restore `aaw.db` with `actions/cache` (key by run id, restore from the latest), run `uv run aaw weekly`, save the cache. Secrets: `ANTHROPIC_API_KEY` and the five mail variables. If the cache is lost the run re-syncs and re-classifies from `backtest_start`, and the only visible effect is that upcoming items are emailed once more; say this in the README.
- [ ] **Step 6:** Run `uv run aaw digest --dry-run` locally and read the output. Then set the mail variables and run `uv run aaw digest` once; confirm the email arrives and a second run sends "nothing new".
- [ ] **Step 7:** Write the README: what it does, how to add a city, how to run the backtest, costs measured in Tasks 4 and 5, known limits (agenda lines without a matter are not classified; no legal framing; notice figures are approximate).
- [ ] **Step 8:** Commit: `feat: weekly digest email and scheduled run`.
