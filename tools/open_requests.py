"""Open requests: the open FOCUS_Spec issues and pull requests that concern each dataset and column of the data model.

    GH_TOKEN=... python -I tools/open_requests.py <site dir>                        dry run: the matches, what's due, its cost
    GH_TOKEN=... ANTHROPIC_API_KEY=... python -I tools/open_requests.py <site dir> --write [--limit N]
        [--model M] [--effort E] [--fallback M]     as in summarise.py (default model: claude-haiku-5-5)
    GH_TOKEN=... python -I tools/open_requests.py <site dir> --write --no-ai         write without calling the API
        [--force]                                   write even though the counts fell sharply since the last run

Reads <site dir>/focus/model.json, focus/history.json (each definition file's earlier names), thread-summaries.json,
recent.json and tools/summary-basis.json; reads and writes <site dir>/open-requests.json. The daily workflow
(.github/workflows/daily.yml) runs it after recent.py.

A thread is a strong match for a dataset or column when it is a pull request that changes the definition's file (under
its current name or an earlier one), or when its title names the column's ID (ListCost) or the dataset's name or ID
(SKU Price, SkuPrice). A pull request that adds a page under a dataset's columns/ folder proposes a column that doesn't
exist yet, and is a strong match for that dataset; so is a title that names a column the model doesn't have
("Add ProcessingTier column"), for the datasets the thread names. A name that appears only in the description is a weak
match. A column ID that several datasets share counts for the datasets the thread names, or for the first that has it
(Cost and Usage) if it names none. Pull requests that change more than 15 files ("spec-wide", as in the History) and
match only by their files are kept apart. Only datasets and columns are matched: they are the panels that show these.

Each matched thread gets a summary of the whole thread. One written here is kept until the thread gains comments or
reviews. Otherwise the History summary in thread-summaries.json is reused if it was written from the same number of
comments and reviews (tools/summary-basis.json), or else the thread's brief in recent.json. Failing those, one is
written with the History brief (common.THREAD_RULES from common.thread_text) and checked with common.problems; a text
that fails twice goes to the fallback model. At most --limit are written a run (default 30): strong matches first,
then the most recently active. A thread that is due but not written keeps its earlier summary, marked stale.

Before writing, the counts are compared with the last run's: if the open threads or the matched threads fell by more
than half, nothing is written and the script exits with an error (--force writes anyway).
"""
import datetime
import json
import pathlib
import re
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).parent))  # python -I leaves the script's folder off the path
from common import (CAMEL, THREAD_RULES, Spend, escalated_item, escalated_share, escalation, estimate,  # noqa: E402
                    estimate_line, fallback_from, gh_all, graphql, log_run, model_from, model_index, thread_limit,
                    thread_text, write_text)

SITE, FLAGS = pathlib.Path(sys.argv[1]), sys.argv[2:]
OUT = SITE / "open-requests.json"
NOW = datetime.datetime.now(datetime.timezone.utc)
LIMIT = int(FLAGS[FLAGS.index("--limit") + 1]) if "--limit" in FLAGS else 30
MODEL, SETTINGS = model_from(FLAGS, default="claude-haiku-5-5")
FALLBACK, FALLBACK_SETTINGS = fallback_from(FLAGS, MODEL)

model = json.loads((SITE / "focus" / "model.json").read_text())
history = json.loads((SITE / "focus" / "history.json").read_text())
WIDE = history.get("wide", 15)
before = json.loads(OUT.read_text()) if OUT.exists() else {"threads": {}}
whole = json.loads((SITE / "thread-summaries.json").read_text())
whole_basis = json.loads((pathlib.Path(__file__).parent / "summary-basis.json").read_text())["threads"]
recent = json.loads((SITE / "recent.json").read_text()) if (SITE / "recent.json").exists() else {"threads": []}
briefs = {t["number"]: t for t in recent["threads"] if t.get("summary") and not t.get("stale")}

# ---- the data model: files (with their earlier names) and names ------------------------------------------------
by_file, by_word, WORDS = model_index(model)
wanted = lambda key: key.startswith(("dataset:", "column:"))
alias = defaultdict(set)  # a file's name now or earlier -> the definition files it is now
for path in list(by_file):
    for name in [path, *history.get("names", {}).get(path, [])]:
        alias[name].add(path)
COLUMN_DIRS = {d["source"].rsplit("/", 1)[0] + "/columns/": d["id"] for d in model["datasets"]}
datasets_of = defaultdict(list)  # a column ID -> the datasets that have it, in the model's order (Cost and Usage first)
for d in model["datasets"]:
    for c in d["columns"]:
        datasets_of[c["id"]].append(d["id"])
known = set(by_word) | set(datasets_of)
# A column the model doesn't have, named in a title that proposes it: two or more capitalised parts (QuantityTierCategory,
# not SaaS), not a condition (IncludesAccountNaming) and not a product name.
NEW_NAME = re.compile(r"(?:[A-Z][a-z0-9]+){2,}")
NOT_COLUMNS = {"FinOps", "GitHub", "OpenAI", "DuckDB", "BigQuery", "PowerBi", "JsonSchema", "OpenTelemetry", "CloudWatch"}
PROPOSES = re.compile(r"\b(add|adding|new|introduce|introducing|propose|proposed)\b", re.I)


def names_in(text, named):
    """The datasets and columns a text names: {key: the name as written}. named: the datasets the whole thread names."""
    found = {}
    for w in WORDS.findall(text or ""):
        key = by_word[w]
        if key.startswith("column:"):
            col = key.split(".", 1)[1]
            for ds in [d for d in datasets_of[col] if d in named] or datasets_of[col][:1]:
                found.setdefault(f"column:{ds}.{col}", w)
        elif key.startswith("dataset:"):
            found.setdefault(key, w)
    return found


# ---- every open issue and pull request -----------------------------------------------------------------------
COMMENTS = "comments(last: 20) { totalCount nodes { createdAt author { __typename login } } }"
ISSUES = """issues(states: OPEN, first: 100{after}) {{ totalCount pageInfo {{ hasNextPage endCursor }}
  nodes {{ number title body url createdAt author {{ login }} issueType {{ name }} milestone {{ title }} {comments} }} }}"""
PULLS = """pullRequests(states: OPEN, first: 50{after}) {{ totalCount pageInfo {{ hasNextPage endCursor }}
  nodes {{ number title body url createdAt isDraft changedFiles author {{ login }} milestone {{ title }} {comments}
    reviews(last: 20) {{ totalCount nodes {{ submittedAt author {{ __typename login }} }} }}
    files(first: 100) {{ pageInfo {{ hasNextPage }} nodes {{ path changeType }} }} }} }}"""


def every(template, field):
    out, cursor, total = [], None, None
    while True:
        conn = graphql(template.format(after=f', after: "{cursor}"' if cursor else "", comments=COMMENTS))[field]
        out += conn["nodes"]
        total = conn["totalCount"]
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    if len(out) != total:
        sys.exit(f"GitHub listed {total} open {field} but returned {len(out)}: not written")
    return out


people = lambda nodes, at: [x[at] for x in nodes if x.get(at) and (x.get("author") or {}).get("__typename") != "Bot"
                            and not (x.get("author") or {}).get("login", "").endswith("[bot]")]
TYPE_KIND = {"Feature": "feature request", "Action Item": "action item"}
threads, matches = {}, defaultdict(dict)  # matches: key -> {number: [level, how]}
issues, pulls = every(ISSUES, "issues"), every(PULLS, "pullRequests")
for x in issues + pulls:
    n, pr = x["number"], "isDraft" in x
    reviews = x.get("reviews") or {"totalCount": 0, "nodes": []}
    files = []
    if pr:
        if x["files"]["pageInfo"]["hasNextPage"]:  # more than 100 files: the REST API lists up to 3,000
            files = [(f["filename"], f["status"].upper()) for f in gh_all(f"pulls/{n}/files")]
        else:
            files = [(f["path"], f["changeType"]) for f in x["files"]["nodes"]]
    threads[n] = {
        "number": n, "kind": "pull request" if pr else TYPE_KIND.get((x.get("issueType") or {}).get("name"),
                                                                       ((x.get("issueType") or {}).get("name") or "issue").lower()),
        "title": x["title"], "url": x["url"], "state": "draft" if x.get("isDraft") else "open",
        "milestone": (x.get("milestone") or {}).get("title"), "author": (x.get("author") or {}).get("login"),
        "opened": x["createdAt"][:10],
        "last": max([x["createdAt"], *people(x["comments"]["nodes"], "createdAt"), *people(reviews["nodes"], "submittedAt")]),
        "comments": x["comments"]["totalCount"], "reviews": reviews["totalCount"],
        **({"files": x["changedFiles"]} if pr else {}),
    }

    # ---- what it concerns ----
    title, body = x["title"], x.get("body") or ""
    named = {k.split(":")[1] for k in names_in(title + "\n" + body, set()) if k.startswith("dataset:")}
    reasons = defaultdict(list)  # key -> [(strong, how)]
    for path, change in files:
        if alias.get(path):
            for current in alias[path]:
                for key in filter(wanted, by_file[current]):
                    reasons[key].append((True, f"changes {path.rsplit('/', 1)[-1]}" + ("" if path == current else f" (the page's earlier name, {path})")))
        elif change == "ADDED":
            for folder, ds in COLUMN_DIRS.items():
                if path.startswith(folder) and path.endswith(".md"):
                    reasons[f"dataset:{ds}"].append((True, f"adds {path.rsplit('/', 1)[-1]}, a new column"))
    in_title = names_in(title, named)
    for key, w in in_title.items():
        reasons[key].append((True, f"title names {w}"))
    for key, w in names_in(body, named).items():
        if key not in in_title:
            reasons[key].append((False, f"description names {w}"))
    # A title that proposes a column the model doesn't have ("Add QuantityTierCategory to SkuPrice") concerns the
    # datasets the thread names. One that also names an existing column or rule is about that instead ("Add
    # ProcessingTier as a SkuPriceDetails property").
    new = [w for w in CAMEL.findall(title) if NEW_NAME.fullmatch(w) and w not in known and w not in NOT_COLUMNS
           and not w.startswith("Includes")]
    if new and PROPOSES.search(title) and all(k.startswith("dataset:") for k in map(by_word.get, WORDS.findall(title))):
        for ds in named:
            reasons[f"dataset:{ds}"].append((True, f"title proposes {new[0]}, a new column"))
    wide = pr and x["changedFiles"] > WIDE
    for key, rs in reasons.items():
        hows = list(dict.fromkeys(h for s, h in sorted(rs, key=lambda r: not r[0])))
        strong = [h for s, h in rs if s]
        by_files_only = all(h.startswith("changes ") for h in strong)
        level = "weak" if not strong else "wide" if wide and by_files_only else "strong"
        matches[key][n] = [level, "; ".join(hows)]

matched = {n for m in matches.values() for n in m}
level_of = {n: min((m[n][0] for m in matches.values() if n in m), key=["strong", "wide", "weak"].index) for n in matched}
counts = {"open": len(threads), "prs": len(pulls), "issues": len(issues), "matched": len(matched),
          "strong": sum(v == "strong" for v in level_of.values()), "wide": sum(v == "wide" for v in level_of.values()),
          "weak": sum(v == "weak" for v in level_of.values())}
print(f"{counts['open']} open threads ({counts['prs']} pull requests, {counts['issues']} issues). {counts['matched']} concern a "
      f"dataset or column: {counts['strong']} strongly, {counts['wide']} only as spec-wide pull requests changing more than "
      f"{WIDE} files, {counts['weak']} only by a name in the description. Objects with matches: {len(matches)}.")

# ---- is this a sane run? -------------------------------------------------------------------------------------
last = before.get("counts")
if last and "--force" not in FLAGS:
    fell = [f"{k} fell from {last[k]} to {counts[k]}" for k in ("open", "matched") if counts[k] < last[k] / 2]
    if fell:
        sys.exit(f"Not written: {'; '.join(fell)} since the last run ({before.get('checked_at')}). Check GitHub, then rerun with --force.")
if not counts["open"]:
    sys.exit("Not written: GitHub listed no open threads.")

# ---- summaries ----------------------------------------------------------------------------------------------
SUMMARY = ("summary", "summary_from", "summary_to", "basis", "written_by")
due = []
for n in sorted(matched):
    t, old = threads[n], before["threads"].get(str(n)) or {}
    basis = f"{t['comments']}/{t['reviews']}"
    if old.get("summary_from") == "written" and old.get("basis") == basis:
        t.update({k: old[k] for k in SUMMARY if k in old})
    elif str(n) in whole["summaries"] and whole_basis.get(str(n)) == basis:
        t.update(summary=whole["summaries"][str(n)], summary_from="history", summary_to=whole.get("written"))
    elif n in briefs:
        t.update(summary=briefs[n]["summary"], summary_from="recent", summary_to=recent["generated_at"][:10],
                 recent_since=recent["since"][:10])
    else:
        due.append(n)
        if old.get("summary"):  # yesterday's, until a new one is written
            t.update({k: old[k] for k in SUMMARY + ("recent_since",) if k in old}, stale=True)
        elif str(n) in whole["summaries"]:
            t.update(summary=whole["summaries"][str(n)], summary_from="history", summary_to=whole.get("written"), stale=True)
due = sorted(sorted(due, key=lambda n: threads[n]["last"], reverse=True), key=lambda n: level_of[n] != "strong")  # strong first, then the most recently active
todo = due[:LIMIT]

# The estimate, from the text each thread to write will send (fetched once here, and sent as it is when writing).
texts, chars, unread = {}, [], []
for n in todo:
    try:
        texts[n] = thread_text(n)
        chars.append(len(THREAD_RULES) + len(texts[n]) + 30)
    except Exception as e:  # GitHub's rate limit, say: estimate without it, and fetch it again when writing
        unread.append(f"#{n} ({type(e).__name__})")
SHARE = escalated_share(SITE, MODEL)
EST = estimate(chars, (MODEL, SETTINGS), (FALLBACK, FALLBACK_SETTINGS), SHARE)
kept = {k: sum(threads[n].get("summary_from") == k and not threads[n].get("stale") for n in matched) for k in ("written", "history", "recent")}
print(f"Summaries: {kept['written']} written here before and still current, {kept['history']} reused from History, "
      f"{kept['recent']} from Recent; {len(due)} due, {len(todo)} to write in this run (--limit {LIMIT}), measured at "
      f"{sum(chars):,} characters sent. " + estimate_line(EST, len(todo), SHARE, FALLBACK)
      + (f" Could not read {', '.join(unread)}." if unread else ""))
for n in todo[:40]:
    t = threads[n]
    print(f"  #{n} [{level_of[n]}] {t['title'][:80]} ({t['comments']} comments, {t['reviews']} reviews)")
if len(todo) > 40:
    print(f"  … and {len(todo) - 40} more")

if "--write" not in FLAGS:
    sys.exit()

spend = Spend()
written = failed = first_pass = escalated = escalated_passed = 0
escalations = []
if "--no-ai" not in FLAGS and todo:
    import anthropic

    client = anthropic.Anthropic()
    for n in todo:
        t = threads[n]
        up, tries = False, []
        try:
            text, found, writer, up = write_text(client, spend, THREAD_RULES, texts.get(n) or thread_text(n), thread_limit(t["comments"] + t["reviews"]),
                                                 True, (MODEL, SETTINGS), (FALLBACK, FALLBACK_SETTINGS), tries=tries)
        except anthropic.AuthenticationError:
            raise
        except Exception as e:  # one thread's error (the API, or GitHub's rate limit) doesn't lose the run
            text, found = "", [f"error: {type(e).__name__}: {str(e)[:200]}"]
            up = any(x["model"] != MODEL for x in tries)
        if up:
            escalations.append(escalated_item(n, t["title"], tries, "failed" if found else "passed"))
        first_pass += not found and not up
        escalated += up
        escalated_passed += up and not found
        if found:
            failed += 1
            print(f"#{n}: not written: {'; '.join(found)[:300]}")
            continue
        for k in SUMMARY + ("recent_since", "stale"):
            t.pop(k, None)
        t.update(summary=text, summary_from="written", summary_to=NOW.strftime("%Y-%m-%d"),
                 basis=f"{t['comments']}/{t['reviews']}", written_by=writer)
        written += 1
        print(f"#{n}: written" + (f" (with {writer})" if up else ""))

# ---- write ------------------------------------------------------------------------------------------------------
order = {"strong": 0, "wide": 1, "weak": 2}
OUT.write_text(json.dumps({
    "checked_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "wide": WIDE, "counts": counts,
    "about": ("The open FOCUS_Spec issues and pull requests that concern each dataset and column, matched by the files "
              "they change and the names in their titles and descriptions, rebuilt daily from GitHub. Each thread's "
              "summary is written by an AI model from the thread, or reused from the History or Recent summaries, and "
              "rewritten when the thread gains comments or reviews. The summaries paraphrase those discussions and name "
              "participants by their GitHub usernames."),
    "open": sorted(threads),
    "threads": {str(n): threads[n] for n in sorted(matched)},
    "objects": {key: [[n, *m[n]] for n in sorted(sorted(m, key=lambda n: threads[n]["last"], reverse=True), key=lambda n: order[m[n][0]])]
                for key, m in sorted(matches.items())},
}, indent=1, ensure_ascii=False))
attempted = len(todo) if "--no-ai" not in FLAGS else 0
if attempted:
    log_run(SITE, {"task": "open-requests", "model": MODEL, "effort": SETTINGS["effort"], "fallback": FALLBACK,
                   "threads": len(matched), "kept": len(matched) - len(due),
                   **escalation(attempted, first_pass, escalated, escalated_passed, failed),
                   "left": len(due) - written, "spend": spend.report(), "cost": round(spend.cost(), 4),
                   "estimate": {k: EST[k] for k in ("input", "output", "cost")}, "escalations": escalations})
print(f"Wrote {OUT}: {len(matched)} threads for {len(matches)} datasets and columns. Summaries: {written} written, {failed} "
      f"failed, {len(due) - len(todo)} left for the next run. Escalated {escalated} of {attempted} to "
      f"{FALLBACK or 'no fallback'}, {escalated_passed} of them written. {spend.line()}")
