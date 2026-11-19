"""Keep the AI-written summaries up to date: write them only for spec pages and GitHub threads that are new or changed.

    python tools/summarise.py <FOCUS_Spec checkout> <site dir>                 dry run: list what's due, with an estimated cost
    python tools/summarise.py <FOCUS_Spec checkout> <site dir> --write [--limit N]   write up to N summaries (default 50)
        [--model M]       claude-sonnet-5-5 (default) or claude-haiku-5-5
        [--effort E]      low, medium or high (default: medium for Sonnet, low for Haiku)
        [--fallback M]    the model for a summary the first one fails twice (default claude-sonnet-5-5; none to skip)
        [--only K1,K2]    write exactly these page keys or thread numbers, due or not (to compare models, say)
    python tools/summarise.py <FOCUS_Spec checkout> <site dir> --stamp         only write site/focus/refresh.json
    python tools/summarise.py <FOCUS_Spec checkout> <site dir> --init          record the current pages and threads as summarised

Summaries live in site/object-summaries.json (one per dataset, column, attribute, condition and supporting topic page)
and site/thread-summaries.json (one per FOCUS_Spec issue and pull request in history.json with three or more comments or
reviews). tools/summary-basis.json records what each was written from: a hash of the page, or the thread's comment and
review counts. A page whose hash changes, or a thread that gains comments or reviews, is due again.

--write calls the Claude API (ANTHROPIC_API_KEY) and costs money; nothing else here does. It also needs GH_TOKEN to read
threads. Every summary must pass the checks in problems() (word limit, they/them, no URLs or Markdown, quotes verbatim).
One that fails twice is escalated to the fallback model for two more tries, and skipped and reported if it fails those.
Each summary records the model that wrote it ("written_by"), and each --write and --stamp --data adds an entry to
<site dir>/update-log.json, with the share of summaries escalated and the cost per model.
"""
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys

SPEC, SITE = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
FLAGS = sys.argv[3:]
LIMIT = int(FLAGS[FLAGS.index("--limit") + 1]) if "--limit" in FLAGS else 50
BASIS = pathlib.Path(__file__).parent / "summary-basis.json"
OBJ, THR = SITE / "object-summaries.json", SITE / "thread-summaries.json"
sys.path.insert(0, str(pathlib.Path(__file__).parent))  # python -I leaves the script's folder off the path
from common import (THREAD_RULES, Spend, escalated_item, escalated_share, escalation, estimate, estimate_line,  # noqa: E402
                    fallback_from, log_run, model_from, thread_limit, thread_text, write_text)

MODEL, SETTINGS = model_from(FLAGS)
FALLBACK, FALLBACK_SETTINGS = fallback_from(FLAGS, MODEL)
ONLY = [k.strip() for k in (FLAGS[FLAGS.index("--only") + 1] if "--only" in FLAGS else "").split(",") if k.strip()]
TODAY = datetime.date.today().isoformat()

model = json.loads((SITE / "focus" / "model.json").read_text())
history = json.loads((SITE / "focus" / "history.json").read_text())
objs, threads = json.loads(OBJ.read_text()), json.loads(THR.read_text())
basis = json.loads(BASIS.read_text()) if BASIS.exists() else {"pages": {}, "threads": {}}

# ---- what should have a summary -------------------------------------------------------------------------------
LIMITS = {"dataset": 200, "column": 100, "attribute": 200, "condition": 100, "appendix": 160, "metadata": 160, "schema": 160}
pages = {}  # key -> (kind, [files], what it defines)
for d in model["datasets"]:
    pages[d["source"]] = ("dataset", [d["source"]], f"dataset {d['id']} ({d['name']})")
    for c in d["columns"]:
        pages[c["source"]] = ("column", [c["source"]], f"column {d['id']}.{c['id']} ({c['name']})")
for a in model["attributes"]:
    pages[a["source"]] = ("attribute", [a["source"]], f"attribute {a['id']}")
for c in model["conditions"]:
    pages[c["source"]] = ("condition", [c["source"]], f"operating model condition {c['id']}")
for t in model["topics"]:
    pages["topic:" + t["id"]] = (t["kind"], t["files"] or [t["source"]], f"{t['kind']} topic {t['name']}")


def page_hash(files):
    h = hashlib.sha256()
    for f in files:
        h.update(f.encode() + b"\0" + (SPEC / f).read_bytes())
    return h.hexdigest()[:16]


everything = {**history["prs"], **history["issues"]}
weight = {n: v.get("comments", 0) + v.get("reviews", 0) for n, v in everything.items()}
due_threads_all = {n for n, w in weight.items() if w >= 3}
counts = lambda n: f"{everything[n].get('comments', 0)}/{everything[n].get('reviews', 0)}"

if "--init" in FLAGS:
    basis = {"pages": {k: page_hash(f) for k, (_, f, _) in pages.items() if k in objs["summaries"]},
             "threads": {n: counts(n) for n in due_threads_all if n in threads["summaries"]}}
    BASIS.write_text(json.dumps(basis, indent=0, sort_keys=True))
    print(f"recorded {len(basis['pages'])} pages and {len(basis['threads'])} threads as summarised")
    sys.exit()


def stamp():
    """site/focus/refresh.json: which spec commit the site was built from, and when things were last refreshed."""
    out = SITE / "focus" / "refresh.json"
    old = json.loads(out.read_text()) if out.exists() else {}
    # The time of the rebuild, in UTC; the page shows it in the reader's own time zone.
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    refreshed = now if "--data" in FLAGS else old.get("refreshed_at", now)
    out.write_text(json.dumps({"spec": model["spec"], "refreshed_at": refreshed,
                               "summaries_written": {"pages": objs.get("written"), "threads": threads.get("written")}}, indent=1))


if "--stamp" in FLAGS:
    stamp()
    if "--data" in FLAGS:  # a rebuild from the specification (tools/refresh.sh)
        log_run(SITE, {"task": "rebuild", "spec": model["spec"].get("ref"), "spec_date": model["spec"].get("date")})
    sys.exit()

new_pages = [k for k in pages if k not in objs["summaries"]]
changed_pages = [k for k in pages if k in objs["summaries"] and basis["pages"].get(k) != page_hash(pages[k][1])]
new_threads = sorted((n for n in due_threads_all if n not in threads["summaries"]), key=int)
changed_threads = sorted((n for n in due_threads_all if n in threads["summaries"] and basis["threads"].get(n) != counts(n)), key=int)
due = [("page", k) for k in new_pages + changed_pages] + [("thread", n) for n in new_threads + changed_threads]
if ONLY:
    unknown = [k for k in ONLY if k not in pages and k not in due_threads_all]
    if unknown:
        sys.exit(f"--only: not a page key or a thread with three or more comments or reviews: {', '.join(unknown)}")
    due = [("page" if k in pages else "thread", k) for k in ONLY]


PAGE_RULES = """You write plain-language summaries of pages from the FOCUS specification (FinOps Open Cost and Usage Specification) for a learning website's data model view. The reader is a technical product manager who wants to understand each part of the model without reading the page itself. Accuracy matters more than style: every statement must come from the page.

What to cover:
- A dataset page: what the dataset records and what it's for; what one row represents and what makes a row unique, if the page says; when a data generator must or may provide it; how it connects to other datasets; anything notable, such as preview status or a key rule.
- A column page: what the value means in practice; its data type and format, and its allowed values (summarise a long list and name a few); its feature level as the page gives it and when it is required, may be null, or must be null; how it relates to other columns; any rule a user could easily get wrong.
- An attribute, operating model condition, appendix, metadata or schema page: what it is, what it governs or shows, and the rules or examples a reader most needs.

Rules:
- Use only what the page says. Don't add background knowledge, examples or rules the page doesn't contain.
- Translate the normative language faithfully: MUST and MUST NOT become "must" and "must not", SHOULD becomes "should", MAY becomes "may". Don't strengthen or weaken them.
- Plain, precise English in the present tense, as continuous prose: no Markdown, no bullet lists, no URLs, no links. Write column and dataset IDs exactly as the spec does, and spell out other abbreviations.
- Start with what the thing is.

Reply with the summary text only."""


def page_text(key):
    kind, files, what = pages[key]
    body = "\n\n".join(f"--- {f} ---\n{(SPEC / f).read_text()}" for f in files)
    return f"This page defines: {what}.\n\n{body[:120_000]}"


texts = {}  # the text each item sends, built once: measured for the estimate, then sent


def text_of(kind, key):
    if (kind, key) not in texts:
        texts[(kind, key)] = page_text(key) if kind == "page" else thread_text(key)
    return texts[(kind, key)]


def sent_chars(kind, key):
    """The characters an item sends: its instructions, its text and the length line."""
    return len(PAGE_RULES if kind == "page" else THREAD_RULES) + len(text_of(kind, key)) + 30


print(f"Due: {len(new_pages)} new and {len(changed_pages)} changed pages, {len(new_threads)} new and "
      f"{len(changed_threads)} changed threads ({len(new_pages + changed_pages + new_threads + changed_threads)} summaries).")
if ONLY:
    print(f"--only: writing just these {len(due)}.")
todo = due[:LIMIT]
chars, unread = [], []
for kind, key in todo:
    try:
        chars.append(sent_chars(kind, key))
    except Exception as e:  # GitHub's rate limit, say: estimate without it, and try it again when writing
        unread.append(f"{kind} {key} ({type(e).__name__})")
SHARE = escalated_share(SITE, MODEL)
EST = estimate(chars, (MODEL, SETTINGS), (FALLBACK, FALLBACK_SETTINGS), SHARE)
print(f"This run writes {len(todo)} (--limit {LIMIT}), measured at {sum(chars):,} characters sent. "
      + estimate_line(EST, len(todo), SHARE, FALLBACK) + (f" Could not read {', '.join(unread)}." if unread else ""))
for kind, key in due[:30]:
    print(f"  {kind}: {key}")
if len(due) > 30:
    print(f"  … and {len(due) - 30} more")
if "--write" not in FLAGS or not due:
    sys.exit()

# ---- writing ---------------------------------------------------------------------------------------------------
import anthropic  # only needed to write

client = anthropic.Anthropic()
spend = Spend()


def write_one(system, content, limit, thread=False, tries=None):
    """A summary from the first model, or from the fallback if the first fails twice. Returns (text, problems, model, escalated)."""
    return write_text(client, spend, system, content, limit, thread, (MODEL, SETTINGS), (FALLBACK, FALLBACK_SETTINGS), tries=tries)


skipped, tally, escalations = [], {"first_pass": 0, "escalated": 0, "escalated_passed": 0}, []
for kind, key in due[:LIMIT]:
    # An error on one item (the API, or GitHub's rate limit) skips it rather than losing the run; a bad key stops it.
    escalated, tries = False, []
    try:
        if kind == "page":
            text, found, writer, escalated = write_one(PAGE_RULES, text_of(kind, key), LIMITS.get(pages[key][0], 160), tries=tries)
            if not found:
                objs["summaries"][key] = text
                objs.setdefault("written_by", {})[key] = writer
                basis["pages"][key] = page_hash(pages[key][1])
        else:
            text, found, writer, escalated = write_one(THREAD_RULES, text_of(kind, key), thread_limit(weight[key]), thread=True, tries=tries)
            if not found:
                threads["summaries"][key] = text
                threads.setdefault("written_by", {})[key] = writer
                basis["threads"][key] = counts(key)
    except anthropic.AuthenticationError:
        raise
    except Exception as e:
        text, found = "", [f"error: {type(e).__name__}: {str(e)[:200]}"]
        escalated = escalated or any(t["model"] != MODEL for t in tries)
    if escalated:
        escalations.append(escalated_item(key, pages[key][2] if kind == "page" else everything[key]["title"], tries,
                                          "failed" if found else "passed"))
    tally["first_pass"] += not found and not escalated
    tally["escalated"] += escalated
    tally["escalated_passed"] += escalated and not found
    print(("skipped " if found else "wrote ") + f"{kind} {key}" + (f" (with {writer})" if escalated and not found else "")
          + (": " + "; ".join(found) if found else ""))
    if found:
        skipped.append(key)
        print(f"  draft: {text}")  # kept in the log only, so a skipped draft can still be read

objs["written"] = threads["written"] = TODAY
objs["about"] = ("Plain-language summaries of the FOCUS specification's dataset, column, attribute, condition, appendix, metadata "
                 "and schema pages, written by an AI model from each page, and rewritten when the page changes.")
threads["about"] = ("Summaries of the conversation in each FOCUS_Spec issue and pull request in the Data model's history that has "
                    "three or more comments or reviews, written by an AI model from each thread, and rewritten when it "
                    "gains comments or reviews. They paraphrase the discussion and name participants by GitHub username.")
OBJ.write_text(json.dumps(objs, indent=1, ensure_ascii=False))
THR.write_text(json.dumps(threads, indent=1, ensure_ascii=False))
BASIS.write_text(json.dumps(basis, indent=0, sort_keys=True))
stamp()
done = due[:LIMIT]
counts_ = escalation(len(done), tally["first_pass"], tally["escalated"], tally["escalated_passed"], len(skipped))
log_run(SITE, {"task": "summaries", "model": MODEL, "effort": SETTINGS["effort"], "fallback": FALLBACK,
               "pages": sum(1 for k, _ in done if k == "page"), "threads": sum(1 for k, _ in done if k == "thread"),
               **counts_, "left": max(0, len(due) - LIMIT), "spend": spend.report(), "cost": round(spend.cost(), 4),
               "estimate": {k: EST[k] for k in ("input", "output", "cost")}, "escalations": escalations})
print(f"Wrote {len(done) - len(skipped)}, skipped {len(skipped)}, {max(0, len(due) - LIMIT)} left for the next run. "
      f"Escalated {tally['escalated']} of {len(done)} ({counts_['escalated_pct']}%) to {FALLBACK or 'no fallback'}, "
      f"{tally['escalated_passed']} of them written. {spend.line()}")
