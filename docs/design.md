# Animal Agenda Watcher: design

Date: 2026-11-12
Status: draft, awaiting approval

## Intent

Local decisions that affect animals are often made at hearings nobody from the animal rights movement attends, because the items are hard to spot: they appear on agendas as zoning, permit or contract items. This project finds those items in Madison and Dane County agendas and tells an activist what is being decided, when, and how to weigh in.

The first reader is the author. Once it demonstrably works, it will be pitched to ASAP in Madison. The first version therefore has two jobs: be useful to one person, and produce evidence that persuades a group.

## Goals

1. **Backtest.** Classify the last three years of Madison and Dane County agenda items and report what would have been flagged: how many, what kinds, which bodies, how much notice there was.
2. **Live digest.** Run the same pipeline weekly on new and upcoming items and email a digest to the author.
3. **Pitch material.** A short, readable summary of the backtest findings that can be shown to ASAP.

## Non-goals (for this version)

- Other municipalities in the Madison area (each is a separate scraping job).
- Wisconsin DNR permit notices and state legislative hearing notices. These are the planned next feeds once the classifier is proven.
- A public website, signups, or multiple recipients.
- Matching supporters to districts or committees. If needed later, integrate an existing tool.

## Anti-goals

- A digest so noisy the reader stops opening it. A missed item is bad; ten irrelevant items every week kills the tool.
- Briefs that assert what a body can legally consider when that has not been checked. Wrong legal framing wastes a commenter's effort and damages credibility with ASAP.
- Auto-generated comments submitted in bulk. The tool informs people; people comment.

## Constraints

- Free or nearly free to run. A solo developer pays for it.
- Open source, so other groups can point it at their own city.
- Public data only.

## What was verified

On 2026-11-12, the public Legistar API answered without a key for both bodies:

- `https://webapi.legistar.com/v1/madison/...`
- `https://webapi.legistar.com/v1/dane/...`

`/events` returns meetings with body name, date and (for Dane County) a link to the agenda PDF. `/matters` returns legislative items with title, type, status, sponsoring body and dates. Madison's event list includes placeholder meetings dated years in the future, so upcoming meetings need a sanity filter on date.

Not yet verified: how many items three years covers, how agenda items link to meetings in each client, and whether attachments such as staff reports are reachable through the API.

## Approaches considered

**A. Backtest first, then go live (chosen).** Shows early how often relevant items occur locally, gives real past cases to tune against, and produces the pitch. Costs a week or two before the first live alert.

**B. Live digest straight away.** Fastest to something running, but it would take months to learn whether it catches the right things, and there would be nothing to show ASAP.

**C. Public site from the start.** The right shape once a group relies on it, but it means building for reliability and outside readers before knowing the classifier works or that anyone wants it.

Wiring together existing no-code tools was dropped: it handles Legistar, but the later feeds and the legal-framing part of the brief need custom work anyway.

The author picked A.

## How it works

1. **Fetch.** Pull matters and events for each configured Legistar client into a local database. Fetching is incremental and safe to re-run. Adding a city should be a configuration change, not new code.
2. **Classify.** For each item, decide whether it materially affects animals. Two stages keep cost down: a cheap first pass over the title and type discards the obvious majority; a second pass on survivors reads the full text and any reachable attachments. Each flagged item gets a category, a confidence, and a one-sentence reason.
3. **Brief.** For each flagged item: what is being decided, which body, when and where it meets, a link to the source, and how to comment before that body.
4. **Output.** The backtest writes a report. The live run emails a digest of new flagged items and upcoming meetings that include them.

### What counts as relevant (author to confirm)

Proposed scope is any decision that materially affects animals, labelled by category so the reader can filter:

- Animal agriculture: slaughter and meat processing, livestock facilities, manure and water permits, agricultural zoning and conditional use permits
- Wildlife management: culls of deer, geese and other animals, trapping, pesticide and rodenticide use
- Captive animals: Henry Vilas Zoo (county-owned), circuses, rodeos, fairs, exhibitions
- Sale and use: fur, foie gras, pet store sales, live markets
- Public procurement and food policy: institutional food contracts, plant-based defaults
- Animal services and control ordinances

Routine items that mention animals without a decision at stake (a dog park maintenance contract, a proclamation) should not be flagged.

### Judging the classifier

There is no labelled dataset. The builder should assemble a small hand-checked set from the backtest: known relevant items found by keyword search and local news, plus a random sample of unflagged items read by hand to estimate misses. The backtest report states these numbers plainly, including how many items were checked.

### Legal framing

The "arguments this body can act on" section is deferred until it can be done properly. Version one says how to comment and links the source documents. It does not claim what the body may legally consider. Wisconsin's livestock siting law is the main reason this matters, and it needs a per-decision-type reference written with care, ideally reviewed by someone at ASAP or a lawyer.

## Calls made by the designer (not decided by the author)

- Python, with SQLite as the local store.
- Classification by a Claude model through the API, with a small cheap model for the first pass. The builder picks models and checks current pricing.
- Three years of history for the backtest.
- Weekly cadence, run by a scheduled job (GitHub Actions or local cron), email as the delivery channel.
- Legal framing deferred, as above.
- Broad relevance scope with categories, as above.

## Left to the builder

- Database schema and module layout.
- How agenda items are linked to meetings in each client, and whether attachments can be fetched.
- Prompt design and the exact two-stage split.
- Email provider.
- Format of the backtest report.

## Risks

- **Relevant items may be rare.** If the backtest finds only a handful, Madison and Dane County alone do not justify a weekly digest, and the DNR and legislature feeds move up. Finding this out early is the point of doing the backtest first.
- **The decisive detail may be in a PDF attachment**, not the title. If attachments are unreachable, the first pass will miss zoning-framed items, which are the ones that matter most.
- **Notice periods may be short.** If items typically appear only days before a meeting, weekly is too slow. The backtest should measure this.
