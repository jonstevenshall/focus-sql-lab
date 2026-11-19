# FOCUS SQL Lab

A web page for exploring SQL queries on FOCUS sample data and exploring the FOCUS data model. DuckDB-WASM runs the
queries in the browser, so the server only serves static files: no database, no login, no API.

## What it does

- **Query:** run the specification's 66 example queries exactly as published, or the lab's 11 practice queries.
  62 of the 66 run in DuckDB as published; the page explains why the other 4 don't.
- **Response explorer:** pivot and chart any query result by any column, using Perspective (FINOS).
- **Data model:** the five FOCUS 1.5 datasets with lines between the key columns of every documented join. Click a
  join to see the spec's wording, the lab's join SQL and how well the keys match in the chosen data source. Click a
  dataset, column or attribute to see a plain-language summary, the spec's definition, its requirements exactly as
  written (each line marked with the pull request that last changed it), and a profile of its values. Attributes,
  such as StringHandling or CorrectionHandling, are the shared rule sets the requirements point to. Operating model conditions, such as Includes Payable Invoices, open the same way: what they mean, which columns and rules depend on them, and their history. The spec's supporting material opens there too: the appendix (worked examples and guidance), metadata (how a delivery of FOCUS data describes itself) and the JSON schemas, each with a summary, what links to it and its history. Links to them inside the requirements open them in place. The **Reference** tab lists the conditions, attributes and supporting material as cards or as a sortable list, with where each applies; clicking a name previews it in a panel alongside, the same panel the Data model uses. The Evidence tab has the same cards-or-list switch. Each dataset, column and join also shows its **history**:
  the pull requests that changed its definition file, newest first, with an excerpt of each one's description, the
  issue it addressed, and how much discussion each had, with a summary of the conversation in each pull request and
  issue that had one. Each entry shows its status on GitHub (merged, open, or closed and why), and a "Not merged" list
  shows pull requests that proposed changes to the definition but were closed without merging or are still open (on a
  dataset or column, the open ones are under open requests instead). Above the history, each
  dataset and column lists its **open requests**: the open FOCUS_Spec issues and pull requests that concern it, with
  each one's kind (feature request, action item, pull request), status, milestone, when it was opened and last active,
  how it was matched and a summary of its conversation. Threads that only name it in their description are in a
  collapsed list. The history and the open requests are rebuilt every day. The 11 join-key columns also have a note on
  the debate behind them.
- **Data sources:** switch between the accepted 1.0 sample, two samples proposed in open FOCUS-Sample-Data pull
  requests (1.3 and 1.4), and the specification's 1.5 worked examples. The Data model shows which joins work in each.
- **Sample data:** one card per data source, with its status, a link to its pull request or folder and the commit the
  files are pinned to, and its tables. Clicking a table shows its row and column counts, links to the files it was
  read from, and its first 50 rows, with the columns the lab added underlined. The same tables are also available as
  a sortable list.
- **AI tokens:** how FOCUS 1.5 records charges for using AI models: the columns, and the model and token properties
  inside SkuPriceDetails, quoted from the specification; the worked examples and the four token queries; the threads
  that shaped it, with their conversations; and where the specification stops today, with the open requests behind
  each gap and their status on GitHub.
- **Quiz:** 21 SQL questions on the FOCUS data, at two levels. "Write it" asks for a query that answers a common cost
  question; "Change it" gives a query and asks for a specific change. A check runs your query and the model answer in
  the browser and compares their results. Hints and model answers are there when you want them. A data model panel
  beside the questions (under the question on a phone) lists the five datasets, their columns and the lab's join keys
  for the data the question runs on. Clicking a dataset, column or join shows the same detail as the Data model tab.
- **Recent** (also at `#recent`): the FOCUS_Spec issues and pull requests people opened, commented on or reviewed in
  the last two weeks, newest activity first, rebuilt every morning. Each card has a summary of the fortnight, the
  comments and reviews as a reply tree with a one-line gist of each and a link to it on GitHub, the status changes the
  repository's automation posted, and links to the parts of the data model the thread touches (from a pull request's
  changed files, and the column and dataset names in the text). Each of those parts lists its recent threads in turn.
- **Search:** a box in the page header, on every tab (Ctrl-K, or Cmd-K on a Mac, goes to it). It searches the
  columns, datasets, joins, attributes, conditions and supporting material, the specification's requirement lines, the
  example queries, the pull requests and issues in the history, the evidence and the Recent threads, and groups the
  results by kind. Every word typed must match the start of a word, and CamelCase IDs count as their parts, so
  "contracted unit price" finds ContractedUnitPrice. An exact ID comes first, then an exact name, then matches in IDs,
  names, titles and the rest of the text, in that order. It runs in the browser on `site/search.json`; nothing typed is
  sent anywhere.
- **Addresses:** everything a link or a search result opens has an address to bookmark or share:
  `#model/CostAndUsage/BilledCost` (or `#model/BilledCost`), `#model/CostAndUsage`, `#model/join/cau-sku`,
  `#ref/CorrectionHandling` (attributes, conditions and supporting material), `#query/<example id>`,
  `#evidence/<evidence id>`, `#recent/<number>` and `#thread/<number>` (the panel whose history lists that pull
  request or issue). A panel address can end in `/line-<n>` to point at one requirement line. Each tab has its own
  address too (`#ref`, `#quiz`, ...).
  Anything older links out to the thread on GitHub.

## How the spec's queries run unchanged

The specification writes its queries in ANSI SQL, using BigQuery's JSON functions where it reads JSON, and marks each
value the reader supplies with `?`.

- **Inputs:** the page shows one input for each `?`, with a type (text, number or timestamp) and a value the lab
  suggests from the data. When the query runs, each value takes the place of its `?` as a typed SQL literal, the way a
  database client binds a parameter. The query text itself is never changed.
- **BigQuery names:** [site/lab-setup.sql](site/lab-setup.sql) runs before any query. It gives DuckDB BigQuery's
  `JSON_VALUE`, `JSON_EXTRACT_ARRAY`, `JSON_VALUE_ARRAY` and `FLOAT64`, with BigQuery's behaviour, and defines the
  `focus_data` table name that the version migration guide uses without defining. The page shows this file.
- **The 4 that don't run:** one uses BigQuery's `value IN UNNEST(array)` form, one has a `GROUP BY` name that
  DuckDB resolves to a table column rather than the SELECT alias, one uses `JSON_TABLE`, and one uses a stand-in
  column name (`x_CustomColumn`).

## What the lab made

Everything the lab made is labelled on the page:

- **Names:** every table name (`cau_sample`, `cau_pr7`, `cc_examples` and so on), and the view `focus_data`.
  `CostAndUsage`, `SkuPrice`, `InvoiceDetail`, `ContractCommitment` and `BillingPeriod` are FOCUS dataset names: on
  the page they are views onto the chosen data source's tables.
- **Columns:** `x_SampleRowId`, `x_Dataset` and `x_ExampleFile`. In `cau_sample`, `ServiceProviderName` and
  `HostProviderName`, derived using the spec's migration guidance, and the FOCUS 1.5 columns that 1.0 lacks, added
  empty. The sample is FOCUS 1.0 data, not 1.5 data. The proposed 1.3 and 1.4 samples also have the 1.5 columns they
  lack added empty.
- **Queries:** the 11 practice queries, written in DuckDB SQL.
- **Data model:** the relationship names, key pairs and join SQL. The specification describes relationships only in
  prose, which the page quotes. The history's selection rules (which pull requests changed a file, and calling one that
  changed more than 15 files "spec-wide") are the lab's; its summaries are excerpts the lab cut, not text it wrote.
- **Debate notes:** [site/column-notes.json](site/column-notes.json) holds notes on the 11 join-key columns, written
  by an AI model from the GitHub threads each note lists. They are the lab's reading of those discussions.
- **Quiz:** the questions, hints, notes and model answers in [tools/quiz.json](tools/quiz.json), written in DuckDB SQL.
  `extract_sql.py` tests every model answer and writes `site/quiz.json`.
- **AI tokens view:** the explanations in the AI tokens tab, and which threads it lists under each gap, are the lab's.
  They live in [tools/extract_ai.py](tools/extract_ai.py). Specification text in the view is quoted as published, and
  the script stops if a quoted sentence is no longer in the specification.
- **Evidence:** [site/evidence.json](site/evidence.json), shown in the Evidence tab: pain points, who asked, how the working
  group found out, use cases and trade-offs, curated by an AI model from the FOCUS_Spec issues and conversation summaries.
  Quotes are verbatim.
- **Plain-language summaries:** [site/object-summaries.json](site/object-summaries.json) summarises each dataset,
  column, attribute, appendix, metadata and schema page of the specification, written by an AI model from that page.
- **Conversation summaries:** [site/thread-summaries.json](site/thread-summaries.json) summarises the conversation in
  each FOCUS_Spec issue and pull request in the history that has three or more comments or reviews, written by an AI model
  from each thread. Threads with less discussion show only their own description.
- **Open requests:** [site/open-requests.json](site/open-requests.json) matches the open FOCUS_Spec issues and pull
  requests to the datasets and columns they concern; the matching rules are the lab's. Each thread's summary is written
  by an AI model from the thread, or reused from the conversation summaries or the Recent tab.

## Findings

[findings/spec-inconsistencies.md](findings/spec-inconsistencies.md): inconsistencies in the specification's examples
found while building the lab, stress-tested, with proposed fixes.

## Licence

Code is under the [Apache License 2.0](LICENSE); the lab's writing and curated content are under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); everything in `site/focus/` is derived from FOCUS and is under
CC BY 4.0. [NOTICE.md](NOTICE.md) lists which files are which.

## What's next

See [BACKLOG.md](BACKLOG.md) for the next things to build.

## Repository layout

Everything under `site/focus/` is derived from FOCUS project material (CC BY 4.0). Everything else is original to
this project. [NOTICE.md](NOTICE.md) lists each derived file's source, upstream version and changes.

```
README.md, NOTICE.md
Dockerfile, nginx.conf        serve site/ with nginx
tools/                        build scripts, plus practice.json (the practice queries)
site/
  index.html                  the page
  search.json                 the index behind the header's search box, rebuilt from the other files by tools/build_search.py
  lab-setup.sql               what the lab adds to DuckDB so the spec's queries run as published
  column-notes.json           the lab's notes on the debate behind each join-key column, with the threads each draws on
  thread-summaries.json       summaries of the conversation in each discussed FOCUS_Spec issue and pull request
  object-summaries.json       plain-language summaries of each dataset, column and attribute page
  practice-queries.json       the practice queries, with test results (built from tools/practice.json)
  quiz.json                   the quiz, with each model answer's tested row count (built from tools/quiz.json)
  recent.json                 the last two weeks of FOCUS_Spec discussion, rebuilt daily by tools/recent.py
  open-requests.json          the open FOCUS_Spec issues and pull requests behind each dataset and column, rebuilt daily
  update-log.json             every rebuild and batch of AI-written text: what it did, escalations and cost
  focus/                      FOCUS-derived, rebuilt by the tools; not edited by hand
    data/*.parquet            sample data and the specification's worked examples
    data/manifest.json        table list, row counts, column types, the columns the lab added, and each table's source files
    spec-queries.json         the specification's example queries, verbatim, with suggested inputs and test results
    model.json                datasets, columns and relationships from the specification
    history.json              the pull requests and issues behind each definition, with excerpts of their descriptions
    ai-tokens.json            the AI tokens view: specification extracts, thread status and the lab's notes on the gaps
    refresh.json              the specification commit the site was built from, and when it was last refreshed
```

## Data sources

The page has one data source switch. It points all five FOCUS dataset names at that source's tables. A dataset the
source doesn't have points at an empty table with the specification's columns, so queries and joins still run.

| Source | Status | Cost and Usage | Contract Commitment | Invoice Detail | Billing Period | SKU Price |
| --- | --- | --- | --- | --- | --- | --- |
| FOCUS-Sample-Data 1.0 | accepted | `cau_sample`, 100,000 | – | – | – | – |
| FOCUS 1.3, [Sample-Data PR #7](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data/pull/7) | proposed, not accepted | `cau_pr7`, 3,000 | `cc_pr7`, 66 | – | – | – |
| FOCUS 1.4, [Sample-Data PR #5](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data/pull/5) | draft, not accepted | `cau_pr5`, 1,000 | `cc_pr5`, 1,000 | `inv_pr5`, 1,000 | `bp_pr5`, 1,000 | – |
| Spec 1.5 worked examples | in the specification | `cau_examples`, 493 | `cc_examples`, 9 | `inv_examples`, 8 | – | `sku_examples`, 62 |

`CorrectionExamples` (114 rows, the specification's correction-handling examples) sits outside the switch.

Decimal columns are stored as 64-bit floating point (DOUBLE), one of the precisions the spec's NumericFormat attribute
allows. Sums of charges and credits can therefore leave tiny remainders instead of exactly zero.

## AI feedback for the quiz (not switched on)

The quiz marks answers without AI, by comparing results. It's ready for an AI explanation of what's wrong, but none is
switched on.

- Set `QUIZ_FEEDBACK_URL` in `site/index.html` to an endpoint. After a wrong answer the page then shows "Ask for
  feedback", sends the attempt as a JSON POST and shows the `feedback` string from the JSON reply. The attempt is
  `{version, at, source, question: {id, level, title, ask, start, answer, ordered}, sql, error, verdict, problems:
  [{kind, message}], yours: {columns, rowCount, firstRows}, expected: {columns, rowCount, firstRows}}`.
- The endpoint holds the API key and calls the model. The key can't go in the page, which anyone can read. Anyone can
  also press the button, so the endpoint needs a rate limit and a daily spending cap.
- The page keeps the same records in the browser (the last 200), and "Download your attempts" saves them as JSON.
- One feedback request is roughly 2,000 input tokens and 250 output tokens. At list prices that's about US$0.007 with
  Claude Sonnet 5.5 ($2 / $10 per million input / output tokens), US$0.013 with Opus 5.5 ($4 / $20) and US$0.0003 with
  Haiku 5.5 ($0.10 / $0.50).

## Rebuild

You need Python with `duckdb==1.5.4` (see `tools/requirements.txt`), a clone of `FOCUS_Spec` (branch
`working_draft`), and a raw data folder holding `focus_sample_100000.csv.gz` from FOCUS-Sample-Data, plus `pr7-1.3/`
and `pr5-1.4/` with the CSV files from each pull request's `FOCUS-1.3/` and `FOCUS-1.4/` folder at the commits listed
in [NOTICE.md](NOTICE.md).

The quickest way is the refresh script, which runs the five build scripts in order (`extract_model.py` must run before
`extract_sql.py`, `extract_history.py` and `extract_ai.py`, which read `model.json`) and then lists the AI summaries that
are due:

```bash
GH_TOKEN=<GitHub token> PYTHON=<python with duckdb 1.5.4> tools/refresh.sh <FOCUS_Spec clone> <raw data folder> [history cache]
```

Or one at a time:

```bash
python -I tools/build_data.py <FOCUS_Spec clone> <raw data folder> site/focus/data
python -I tools/extract_model.py <FOCUS_Spec clone> site
python -I tools/extract_sql.py <FOCUS_Spec clone> site
GH_TOKEN=<GitHub token> python -I tools/extract_history.py <FOCUS_Spec clone with full history> <commit> site
GH_TOKEN=<GitHub token> python -I tools/extract_ai.py <FOCUS_Spec clone> site
```

`extract_history.py` needs a clone with full history (if yours is shallow, set `HISTORY` to a full or bare clone for
`refresh.sh`), and a GitHub token to read pull requests and issues through the API.

### Refreshing on GitHub

The workflow [.github/workflows/refresh.yml](.github/workflows/refresh.yml) does the same on GitHub, against the latest
`working_draft`. It runs every day at 18:30 UTC, and can be started by hand: Actions, then "Refresh from FOCUS_Spec",
then "Run workflow". It downloads the sample data at the pinned commits, rebuilds, writes the summaries that are due
(below), and pushes to `main` only if something changed, which redeploys the site. So the definitions, each one's
history, and the status of every pull request and issue in it are at most a day old. The page header shows when the site was last updated by any run (a refresh or the daily Recent
rebuild), in the reader's own time zone, with that update's cost in API calls and the total to date; both come from
`site/update-log.json`. The footer adds the specification commit and when the AI summaries were last written, from
`site/focus/refresh.json`, which records the refresh time in UTC.

Untick "publish" for a trial run. It rebuilds and lists the files that changed, and keeps the rebuilt JSON as a
download (`trial-run`, under the run's Artifacts) for 7 days. It pushes nothing. With "summaries" ticked it still
writes them (and spends on the API), but only into that download: a way to try a model on a few pages or threads
before publishing.

The history doesn't use `git log --follow`, because its answer depends on the git version: git 2.56, on GitHub's
runners, walks branching history differently from 2.50. Instead, each file's earlier names come from the renames git
finds in each commit, and `git log` lists the commits that changed any of them.

### AI summaries

The page and conversation summaries are the only part that needs an AI model, and they're written only for what's new
or changed. [tools/summarise.py](tools/summarise.py) compares each specification page and each GitHub thread with what
its summary was written from (recorded in `tools/summary-basis.json`):

```bash
python -I tools/summarise.py <FOCUS_Spec clone> site                         # dry run: what's due, with an estimated cost
GH_TOKEN=... ANTHROPIC_API_KEY=... python -I tools/summarise.py <FOCUS_Spec clone> site --write --limit 50
```

The dry run's estimate is measured from the text each item will send (the thread or page, and the instructions),
using input tokens per character and output tokens per item calibrated on the logged runs, plus the share of items
that escalated to the fallback in those runs. Each run that writes records its estimate next to what it spent.
`--write` calls the Claude API and prints what it spent. `--model` picks the model: `claude-sonnet-5-5` (the default,
US$2 / $10 per million input / output tokens at list price) or `claude-haiku-5-5` (US$0.10 / $0.50). Haiku always
thinks, and its thinking is billed as output; `--effort` (low, the default for Haiku, medium or high) sets how much. `--only` names the pages or threads to write,
whether due or not, for comparing models on the same items. Each summary must pass automatic checks (word limit,
they/them pronouns, usernames and dates as written, no URLs or Markdown, quotes found word for word in the source).
One that fails twice is escalated to the fallback model (`--fallback`, Claude Sonnet 5.5 by default, `none` to turn it
off) for two more tries, and skipped if it fails those too; each summary records the model that wrote it
(`written_by`). The checks can't tell whether a summary is accurate. The daily run of the workflow writes up to 40 a
day with Claude Haiku 5.5 at high effort, using the `ANTHROPIC_API_KEY` repository secret. Started by hand, it writes
them only when its "summaries" box is ticked, up to the "limit" given, and offers the same model and "only" choices.
The debate notes and evidence are curated and aren't refreshed.

### Recent discussion, daily

[tools/recent.py](tools/recent.py) builds `site/recent.json` from GitHub: every FOCUS_Spec issue and pull request that
someone opened, commented on or reviewed in the last 14 days. For each thread it makes one call to the model (Claude
Haiku 5.5 at high effort by default, with JSON output) for a summary of the fortnight and a gist of each comment and
review, checked like the other summaries. A thread with any part that fails twice goes to the fallback model (Claude
Sonnet 5.5) for two more tries, keeping whichever parts pass; an item with no passing gist shows a plain excerpt. A
thread is summarised again only when it gains comments or reviews from people, or still has no summary. The workflow
[.github/workflows/daily.yml](.github/workflows/daily.yml) runs it every day at 19:30 UTC (05:30 in Brisbane) and
pushes the result; it can also be started by hand. It shares a queue with the refresh, so the two never push at once.

### Open requests, daily

[tools/open_requests.py](tools/open_requests.py) builds `site/open-requests.json` from GitHub, in the same daily
workflow, after the Recent tab: every open FOCUS_Spec issue and pull request, matched to the datasets and columns it
concerns. A strong match is a pull request that changes the definition's file (under its current name or an earlier
one, from `history.json`), or a title that names the column's ID or the dataset's name or ID. A pull request that adds
a page under a dataset's `columns/` folder, or a title that proposes a column the model doesn't have, counts for the
dataset it names. A name that appears only in the description is a weak match. Pull requests that change more than 15
files and match only by their files are kept apart, as in the history. Each matched thread gets a summary of the whole
thread: the one written here before, until the thread gains comments or reviews; otherwise the conversation summary
from `thread-summaries.json` if it is up to date, or the thread's Recent brief; otherwise a new one, written and
checked like the conversation summaries (Claude Haiku 5.5 at high effort, Claude Sonnet 5.5 as the fallback), at most
30 a run, strong matches first. Before writing, it compares its counts with the last run's and stops if the open
threads or the matches fell by more than half (`--force` writes anyway). If the step fails, the Recent tab is still
published.

```bash
GH_TOKEN=... python -I tools/open_requests.py site                          # dry run: the matches, what's due, its cost
GH_TOKEN=... ANTHROPIC_API_KEY=... python -I tools/open_requests.py site --write --effort high [--limit 30]
GH_TOKEN=... python -I tools/open_requests.py site --write --no-ai          # write it without calling the API
```

### Update log

Every rebuild (`summarise.py --stamp --data`) and every batch of AI-written text (`summarise.py --write`,
`recent.py --write`, `open_requests.py --write`) adds an entry to `site/update-log.json`: when it ran, the GitHub run, what it did, how many items
passed first time, how many (and what share) were escalated to the fallback model and how many of those passed, and the
tokens and cost per model at list price, with the list prices used and how the run started (scheduled or by hand). Each
entry's "Show the working" gives each model's tokens times its list price, the run total, the estimate made before the
run, and for every item the first model failed twice: what the checks found on each try (their exact messages), which
try the fallback passed on or that it failed, and the item's tokens and cost per model. Entries from before
20 November 2026 recorded counts only and show them as they are; their four escalations are filled in from the runs'
GitHub logs, which printed only the findings that sent each item to the fallback. The page shows it when you click the update time in the header (or the
"Updated" time on the Recent tab), with the total spend and the overall share escalated. Clicking the cost in the header
opens a breakdown by model for the latest update and to date: what the main model wrote and how much passed first time,
what went to the fallback, and the tokens and cost of each.

```bash
GH_TOKEN=... python -I tools/recent.py site                                   # dry run: what's in the window, what's due
GH_TOKEN=... ANTHROPIC_API_KEY=... python -I tools/recent.py site --write     # summarise and write site/recent.json
GH_TOKEN=... python -I tools/recent.py site --write --no-ai                   # write it without calling the API
```

### Search index

[tools/build_search.py](tools/build_search.py) writes `site/search.json` from files already in `site/`: the
definitions, joins and requirement lines in `focus/model.json` with the plain-language summaries, the example queries,
the pull requests and issues in `focus/history.json` with their conversation summaries, the evidence, the Recent
threads and, where it exists, `open-requests.json`. It needs no network and no API key. `tools/refresh.sh` runs it, the
refresh workflow runs it again after writing summaries, and the daily workflow runs it after the Recent tab, so the
index always matches what the panels show. The page loads it the first time someone uses the search box.

```bash
python -I tools/build_search.py site
```

## Run locally

```bash
python3 -m http.server 8644 --directory site
```

## Deploy

On Coolify, add this repository as a Public Repository resource using the Dockerfile build pack and port 80.
A GitHub webhook redeploys it on every push to `main`.
