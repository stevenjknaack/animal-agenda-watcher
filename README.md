# Animal Agenda Watcher

Local decisions that affect animals are often made at hearings nobody from the animal rights movement
attends, because the items are hard to spot: they show up on agendas as zoning, permit or contract items.
This tool reads city and county agendas, flags the items that materially affect animals, and tells you
what is being decided, when, and how to comment.

It currently watches the City of Madison and Dane County, Wisconsin. Both publish through Legistar, and
any other Legistar city or county can be added in `config.toml`.

## What it does

| Command | What happens |
|---|---|
| `aaw sync` | Copies agenda items, meetings and agenda lines from the public Legistar API into `aaw.db`. |
| `aaw classify` | Flags items that affect animals. Stage 1 triages titles in batches with a small model; stage 2 reads the full text and up to three PDF attachments of the survivors. Calls the Claude API. |
| `aaw label --source flagged\|keyword\|random` | Shows items one at a time so you can hand-check the classifier. |
| `aaw scores` | Prints how the classifier did on the hand-checked items. |
| `aaw report --out report.md` | Writes the backtest: what was flagged, by category and body, how much notice there was, and how far to trust it. |
| `aaw digest [--dry-run]` | Emails new flagged items, soonest meeting first, with how to comment. |
| `aaw weekly` | `sync`, `classify`, then `digest`. This is what the scheduled job runs. |

## Setup

Needs Python 3.12 or newer.

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest            # offline; never calls Legistar or Claude
```

`classify` needs `ANTHROPIC_API_KEY`. `digest` needs `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`
and `DIGEST_TO` (port 465 uses implicit TLS, any other port uses STARTTLS).

## Running the backtest

```
aaw sync                      # over an hour the first time: three years, one polite request at a time
aaw classify --stage 1 --since 2026-10-01 --until 2026-10-31   # try one month first and read what it kept
aaw classify                  # the rest
aaw label --source flagged    # then --source keyword, then --source random
aaw scores
aaw report --out report.md
```

Every step can be interrupted and re-run. Items are classified once; failed reviews are retried on the next run.
The start date is fixed by a database's first sync, so use a separate `--db` file for a short trial sync.

## Adding a city or county

Add a `[[clients]]` block to `config.toml`. `slug` is the name in the place's Legistar address
(`https://<slug>.legistar.com`). Write `how_to_comment` from the government's own instructions page and
put that page in `comment_source_url`. No code changes are needed.

## The weekly job

`.github/workflows/weekly.yml` runs `aaw weekly` every Monday at 12:00 UTC. Add `ANTHROPIC_API_KEY` and the
five mail settings as repository secrets.

The database is kept on a `data` branch, as one commit that each run replaces. The simplest way to start is
to run the sync and classification on your own machine, then push the result:

```
mkdir /tmp/data && cp aaw.db /tmp/data && cd /tmp/data
git init -b data && git add aaw.db && git commit -m "Database"
git push -f <your repo url> data
```

The same push is how hand labels reach the scheduled job: labels live in `aaw.db`, so label locally on a
copy fetched from the `data` branch and push it back.

Scheduled runs refuse to start from an empty database, because rebuilding means re-downloading three years
of agendas and paying to classify them again. To rebuild on purpose, start the workflow by hand with
"bootstrap" ticked.

If anything could not be checked (Legistar was down, the Claude API was unavailable, a review failed), the
email says so at the top and the run is marked failed. It never reports a quiet week when it was blind.

If the Claude API is unavailable, classification stops after three failures in a row and leaves the
affected items for the next run. A wrong key or model name stops it at once.

## Known limits

- **Agenda lines with no legislative item attached are not classified.** Most are procedural. The report
  counts them and says how many contain an animal-related keyword.
- **No legal framing.** The digest says how to comment. It makes no claim about what a body may legally
  consider. That needs a careful reference written with someone who knows Wisconsin's livestock siting
  rules, and it has not been written.
- **Notice figures are approximate.** Legistar does not record when an agenda was first posted, so notice
  is measured from an item's introduction date to its first meeting.
- **Meetings without a published agenda are skipped** until the agenda appears.
- **Scanned PDFs yield no text.** Those items are judged on their title and item text alone.
- **The tool never submits comments.** It informs people; people comment.

## Design

See `docs/design.md` and `docs/plan.md`.
