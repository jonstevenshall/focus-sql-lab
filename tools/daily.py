"""Daily summary: a short account, in prose, of what the FOCUS working group did on GitHub each day.

    python -I tools/daily.py <site dir>                    dry run: the period, what happened in it, the estimated cost
    GH_TOKEN=... ANTHROPIC_API_KEY=... python -I tools/daily.py <site dir> --write [--model M] [--effort E] [--fallback F]
        --backfill N   also write the N days before the earliest summary, as far back as recent.json reaches
        --redo         write the latest day again (to try a change to the brief)
        --rewrite D,D  write these days again, by id (2026-11-19), latest or earlier
        --show         print the material sent to the model for each day, and stop

Reads <site dir>/recent.json, which tools/recent.py rebuilds just before this in the daily workflow, and
focus/model.json; reads and writes <site dir>/daily.json, newest day first.

A day runs from the end of the previous day's summary to the time recent.json was built, so each comment, review and
status change is in exactly one day, even when GitHub starts the workflow late. A day is named by its last date in
Brisbane (UTC+10, no daylight saving), where the workflow is scheduled for 05:30; a second run on the same date
rewrites that day to cover both. Pull requests merged and threads closed in the period come from GitHub (GH_TOKEN),
since recent.json doesn't record when they happened.

The material for a day: every thread with activity in it, its comments, reviews and status changes in the period
(with each one's gist from recent.json and the start of its text), and, as background, the thread's two-week summary.
A day before the latest has no two-week summary as background, since that was written later and would tell the model
what happened next: it gets the thread's earlier gists instead, each thread's state as it was at the end of the day
(a pull request merged later is still open in it), and no thread opened after the day, even where a body edited later
names one.

Each summary is written by Claude Haiku 5.5 (high effort) and must pass the checks every AI-written text in the lab
passes (common.problems), and those of a daily summary: every thread named by its number, no thread number that isn't
in the material, a length that fits the day. A text that passes is then fact-checked against the material by a second
call, which returns only errors of substance, each quoting the summary. A text that fails either gets one retry naming
what failed; one that fails twice goes to Claude Sonnet 5.5 for two more tries. A day that fails on both is listed
with its threads and no summary, and is tried again on the next run while recent.json still covers it.
"""
import datetime
import json
import os
import pathlib
import re
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).parent))  # python -I leaves the script's folder off the path
from common import (MODELS, Spend, call_cost, escalated_item, escalated_share, escalation, estimate,  # noqa: E402
                    estimate_line, fallback_from, gh, gh_all, log_run, model_from, norm, problems)

SITE, FLAGS = pathlib.Path(sys.argv[1]), sys.argv[2:]
OUT = SITE / "daily.json"
MODEL, SETTINGS = model_from(FLAGS, default="claude-haiku-5-5")
FALLBACK, FALLBACK_SETTINGS = fallback_from(FLAGS, MODEL)
# A daily summary is longer than a thread's, and Haiku thinks at length before writing it: room for both (16,000 is the
# most the SDK allows without streaming).
SETTINGS["max_tokens"] = 16000 if MODEL == "claude-haiku-5-5" else 4000
if FALLBACK:
    FALLBACK_SETTINGS["max_tokens"] = 16000 if FALLBACK == "claude-haiku-5-5" else 4000
REVIEWER = "claude-haiku-5-5"
LIMIT = 400
KEEP_DAYS = 120
BRISBANE = datetime.timezone(datetime.timedelta(hours=10))
BACKFILL = int(FLAGS[FLAGS.index("--backfill") + 1]) if "--backfill" in FLAGS else 0
REWRITE = FLAGS[FLAGS.index("--rewrite") + 1].split(",") if "--rewrite" in FLAGS else []
RETRY_FAILED = 2  # at most this many earlier days that failed are tried again in a run

ts = lambda s: datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)  # noqa: E731
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
day_id = lambda d: d.astimezone(BRISBANE).date().isoformat()  # noqa: E731
long_date = lambda d: f"{d.day} {d:%B %Y}"  # noqa: E731

recent = json.loads((SITE / "recent.json").read_text())
model = json.loads((SITE / "focus" / "model.json").read_text())
before = json.loads(OUT.read_text()) if OUT.exists() else {"days": []}
days = list(before["days"])
earlier_cost = {d["id"]: (d.get("cost", 0), d.get("runs", 0)) for d in days}  # a day written again adds to its cost
UNTIL, WINDOW = ts(recent["generated_at"]), ts(recent["since"])
by_n = {t["number"]: t for t in recent["threads"]}
ds_name = {d["id"]: d["name"] for d in model["datasets"]}


def obj_label(key):
    kind, name = key.split(":", 1)
    if kind == "dataset":
        return f"the {ds_name.get(name, name)} dataset"
    if kind == "column":
        ds, col = name.split(".", 1)
        return f"the {col} column ({ds_name.get(ds, ds)})"
    return f"the {name} {kind}" if kind in ("attribute", "condition") else f"the {name} section"


# ---- which days to write ----------------------------------------------------------------------------------------
# The latest day runs from the end of the last summary (or a day before recent.json was built) to when recent.json was
# built. With --backfill, the days before the earliest summary are added, a day at a time, while recent.json covers them.
plan = []  # (from, to, mode): mode "latest" has the two-week summaries as background; "earlier" has earlier gists
for d in REWRITE:
    if d not in {x["id"] for x in days}:
        sys.exit(f"--rewrite: there is no day {d} in {OUT}.")
if days and (days[0]["id"] == day_id(UNTIL) or "--redo" in FLAGS or days[0]["id"] in REWRITE):
    start = ts(days[0]["from"])  # rewrite the latest day, to now
    days.pop(0)
else:
    start = ts(days[0]["to"]) if days else UNTIL - datetime.timedelta(days=1)
if UNTIL > start:
    plan.append((start, UNTIL, "latest"))
elif not days or not (BACKFILL or REWRITE):
    sys.exit(f"Nothing new: recent.json was built at {recent['generated_at']}, and the last summary runs to {iso(start)}.")
first = ts(days[-1]["from"]) if days else start
for _ in range(BACKFILL):
    if first - datetime.timedelta(days=1) < WINDOW:
        break
    plan.append((first - datetime.timedelta(days=1), first, "earlier"))
    first -= datetime.timedelta(days=1)
# Earlier days that failed, while recent.json still covers them.
for d in [d for d in days if d.get("failed") and ts(d["from"]) >= WINDOW][:RETRY_FAILED]:
    plan.append((ts(d["from"]), ts(d["to"]), "earlier"))
    days.remove(d)
# Earlier days named with --rewrite, over the same period as before.
for d in [d for d in days if d["id"] in REWRITE and ts(d["from"]) >= WINDOW]:
    plan.append((ts(d["from"]), ts(d["to"]), "earlier"))
    days.remove(d)

# ---- what changed state in each period, from GitHub ------------------------------------------------------------
changes = {}  # number -> (when, what, issue) for pull requests merged and threads closed since the earliest day
created = {}  # number -> when it was opened, for every thread updated since the earliest day
gh_titles = {}  # number -> title, for the same threads
if os.environ.get("GH_TOKEN"):
    for i in gh_all("issues", state="all", since=iso(min(p[0] for p in plan))):
        created[i["number"]] = i["created_at"]
        gh_titles[i["number"]] = i["title"]
        pr = i.get("pull_request") or {}
        if pr.get("merged_at"):
            changes[i["number"]] = (pr["merged_at"], "merged", i)
        elif i.get("closed_at") and i["state"] == "closed":
            what = "closed without merging" if pr else {"completed": "closed as completed", "not_planned": "closed as not planned"}.get(
                i.get("state_reason"), "closed")
            changes[i["number"]] = (i["closed_at"], what, i)
else:
    print("No GH_TOKEN: merges and closures are left out.")


def newest_by(to):
    """The highest thread number opened by this time (GitHub numbers threads in order, so a higher one came later), or
    None without GitHub. An earlier day mustn't mention a thread opened after it, even where a body edited later does."""
    return max((n for n, c in created.items() if c <= iso(to)), default=None)


def short_title(t):
    """A thread's title without the repository's prefixes ("[AI] ", "[FR] ", "FR #2437: ", "Editorial #2833: ") or
    backticks: the form a summary gives in brackets after the number. The page's shortTitle does the same."""
    t = re.sub(r"^\s*\[[A-Za-z ]+\]\s*", "", t or "")
    return re.sub(r"^[A-Za-z]+ #\d+(?:-\w+)?:\s*", "", t).replace("`", "").strip()


def src_titles(threads):
    """Each thread number whose title the material gives (the day's threads and the threads they mention): its short title."""
    out = {}
    for r in threads:
        out[r["n"]] = short_title(r["title"])
        out.update({x["n"]: short_title(x["title"]) for x in r["refs"] if x.get("title") and x["n"] not in out})
    return out


def without_titles(text, titles):
    """The text with each "(title)" after a thread number taken out: what the length and wording checks count."""
    for n, t in titles.items():
        text = text.replace(f"#{n} ({t})", f"#{n}")
    return text


def real_names(threads):
    """Words that read as a participant's real name in the start of an item's text: a "First Last" pair or a word before
    a verb such as "requested" that matches part of a username in recent.json ("Shawn Alpay" and shawnalpay, "Jerry" and
    jerrysr99), and any name after a greeting ("Thanks, Irena"). The caller drops any that also appear outside those
    excerpts, where the material uses them as ordinary words."""
    excerpts = " ".join(e.get("excerpt") or "" for r in threads for e in r["events"])
    users = {u.lower() for t in recent["threads"] for u in [t["author"]] + [e.get("author") or "" for e in t["events"]] if u}
    user_part = lambda w: len(w) >= 4 and any(w.lower() in u for u in users)  # noqa: E731
    found = {p for p in re.findall(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b", excerpts) if any(map(user_part, p.split()))}
    found |= {w for w in re.findall(r"(?<![\w-])([A-Z][a-z]{2,}) (?:requested|backed|suggested|asked|said|found|took|"
                                    r"raised|noted|proposed|agreed|flagged|wrote)\b", excerpts) if user_part(w)}
    found |= set(re.findall(r"\b(?:Thanks|thanks|Hi|hi|Hey|Dear),? ([A-Z][a-z]{2,})\b", excerpts))
    return found


def titles_for(text, threads):
    """The title of every thread the text names, which the page shows in brackets after its number: from the day's
    threads and the threads they mention, then GitHub, asking for any still missing one at a time."""
    known = {r["n"]: r["title"] for r in threads}
    for r in threads:
        known.update({x["n"]: x["title"] for x in r["refs"] if x.get("title") and x["n"] not in known})
    known.update({n: t for n, t in gh_titles.items() if n not in known})
    out = {}
    for n in sorted({int(x) for x in NUMBER.findall(text)}):
        if n not in known and os.environ.get("GH_TOKEN"):
            try:
                known[n] = gh(f"issues/{n}")["title"]
            except Exception:  # a title is a nicety: the number still links to GitHub
                continue
        if n in known:
            out[str(n)] = known[n]
    return out


def state_at(n, now, to):
    """A thread's state at the end of a period: one merged or closed after the period was still open in it."""
    c = changes.get(n)
    return "open" if c and c[0] > iso(to) else now


def material(frm, to, mode):
    """The day's threads, as the page lists them, and the text the model writes from."""
    a, b = iso(frm), iso(to)
    newest = newest_by(to) if mode == "earlier" else None
    threads = []
    numbers = set()
    for t in recent["threads"]:
        if any(a < e["at"] <= b for e in t["events"]):
            numbers.add(t["number"])
    numbers |= {n for n, (when, _, _) in changes.items() if a < when <= b}
    for n in numbers:
        t = by_n.get(n)
        change = changes.get(n) if n in changes and a < changes[n][0] <= b else None
        evs = [e for e in (t["events"] if t else []) if a < e["at"] <= b]
        people = [e for e in evs if e["kind"] != "status"]
        if t:
            row = {"n": n, "kind": t["kind"], "title": t["title"], "state": state_at(n, t["state"], to), "author": t["author"],
                   "opened": t["opened"], "objects": t["objects"], "summary": t.get("summary"),
                   "refs": [x for x in t["refs"] if newest is None or x["n"] <= newest]}
        else:  # closed or merged with no comment from a person in the last two weeks
            i = change[2]
            row = {"n": n, "kind": "pr" if "pull_request" in i else "issue", "title": i["title"],
                   "state": change[1].split(" ")[0] if change[1] != "closed without merging" else "closed",
                   "author": i["user"]["login"], "opened": i["created_at"][:10], "objects": [], "refs": [], "summary": None}
        row.update({"events": evs, "people": sorted({e["author"] for e in people}), "items": len(people),
                    "statuses": len(evs) - len(people), "change": change and {"what": change[1], "at": change[0]},
                    "opened_now": any(e["kind"] == "opened" for e in evs),
                    "earlier_gists": [] if not t else [(e, t.get("gists", {}).get(e["id"])) for e in t["events"]
                                                       if e["at"] <= a and e["kind"] != "status"][-6:]})
        threads.append(row)
    # The busiest threads first, then those that changed state.
    threads.sort(key=lambda r: (-r["items"], not r["change"], -r["n"]))
    return threads


ZERO_COUNT = re.compile(r"Initialized PR (Approvals|Change Requests): 0$")


def source_text(frm, to, mode, threads):
    kinds = Counter(r["change"]["what"] for r in threads if r["change"])
    people = {p for r in threads for p in r["people"]}
    lines = [f"The day: {long_date(frm)}, {frm:%H:%M} UTC, to {long_date(to)}, {to:%H:%M} UTC.",
             f"Altogether: {sum(r['items'] for r in threads)} comments, reviews and new threads from {len(people)} people "
             f"on {len(threads)} threads"
             + "".join(f"; {v} {k}" for k, v in kinds.items()) + "."]
    newest = newest_by(to) if mode == "earlier" else None
    if newest:  # a body edited later can name a thread that didn't exist yet
        lines.append(f"Threads numbered above {newest} were opened after this day: don't mention them.")
    gists = {}
    for r in threads:
        gists.update(by_n[r["n"]].get("gists", {}) if r["n"] in by_n else {})
    for r in threads:
        what = "pull request" if r["kind"] == "pr" else "issue"
        out = [f"\n=== #{r['n']} ({short_title(r['title'])}): {what}, {r['state']}; opened by {r['author']} on {r['opened']}"]
        if r["objects"]:
            out.append("It concerns " + ", ".join(obj_label(k) for k in r["objects"][:6]) + ".")
        if r["refs"]:
            out.append("It mentions " + "; ".join(f"#{x['n']}" + (f" ({short_title(x['title'])})" if x.get("title") else "")
                                                  for x in r["refs"][:8]) + ".")
        if mode == "latest" and r["summary"]:
            out.append("Background, for context only: an AI model's summary of this thread's last two weeks, written before "
                       f"this summary (it covers more than this day):\n{r['summary']}")
        elif mode == "earlier" and r["earlier_gists"]:
            out.append("Background, for context only: earlier items in this thread, before this day:")
            out += [f"- {e['author']} on {e['at'][:10]}: {g or e.get('excerpt', '')}" for e, g in r["earlier_gists"]]
        out.append("New:" if r["events"] or r["change"] else "")
        for e in r["events"]:
            reply = f" (reply to {e['parent']})" if e.get("parent") else ""
            text = gists.get(e["id"])
            body = (f"{text} Its text begins: \"{e['excerpt']}\"" if text and e.get("excerpt") else text or
                    (f"Its text begins: \"{e['excerpt']}\"" if e.get("excerpt") else "(no text)"))
            if e["kind"] == "status":
                # A new pull request's zero approvals and change requests say nothing, so they are left out.
                lines_ = [x for x in e["lines"] if not ZERO_COUNT.match(x)]
                if lines_:
                    out.append(f"[{e['id']}] Field update on {e['at'][:10]}, recorded by the repository's automation: "
                               + "; ".join(lines_))
            elif e["kind"] == "opened":
                out.append(f"[{e['id']}] {what.capitalize()} opened by {e['author']} on {e['at'][:10]}: {body}")
            elif e["kind"] == "review":
                out.append(f"[{e['id']}] Review by {e['author']} ({e['state']}) on {e['at'][:10]}{reply}: {body}")
            elif e["kind"] == "inline":
                out.append(f"[{e['id']}] Inline comment by {e['author']} on {e['path'].split('/')[-1]}, {e['at'][:10]}{reply}: {body}")
            else:
                out.append(f"[{e['id']}] Comment by {e['author']} on {e['at'][:10]}{reply}: {body}")
        if r["change"]:
            out.append(f"[x{r['n']}] {what.capitalize()} {r['change']['what']} on {r['change']['at'][:10]}.")
        lines += [x for x in out if x]
    return "\n".join(lines)


RULES = """You write the account of one day's activity in the GitHub repository of the FOCUS specification (FinOps Open Cost and Usage Specification), for a website that follows it. The reader follows the FOCUS working group and reads it in two minutes to learn what happened: what moved forward, what was decided, what is being discussed or disputed, and what is waiting on someone. The page heads your text with the date and the day's counts, and lists every thread below it with each of its comments, so you don't need to account for every comment.

Write it the way someone who follows the repository closely would tell a colleague what happened: plain written English that starts with the news and states it matter-of-factly.
- The first sentence is the most important thing that happened, said directly: who did what, on which thread (for example "#1234 merged after five approvals." or "someone-else opened seven issues listing what the dataset needs before general availability."). Don't open with a description of the day, of where the activity went or of your text: no "Most of the activity went to", "The day was dominated by", "Since the last update".
- Then the rest, grouping threads that belong together (the same release, column, dataset or theme) rather than going thread by thread: what was proposed, asked, decided, merged or closed; who said what, by GitHub username; where people agree or disagree; and what a thread is now waiting on, in plain words ("this is waiting on a reply from the author"). Don't forecast what the group will decide or when.
- End with the smaller items in a sentence, without a label (for example "kk09v also approved #101 and #102, and #103 to #105 were closed as completed."). If there are none, stop after the last main thread rather than saying what didn't happen.
- Mention every thread in the material by its number (#123) at least once, so the reader can open it. Don't mention a thread number that isn't in the material.
- The first time you mention a thread, write its number followed by its title in brackets, exactly as the material gives it (for example "#1234 (Add the 1.6 changelog) has six approvals"); after that, the number alone. The title already says what the thread is, so build the sentence around it rather than restating it ("#1234 (Add the 1.6 changelog), the 1.6 changelog, has" says it twice). Titles don't count toward the length. Mention a thread that is only referred to by the day's threads when it matters to what happened, not to list it. Don't put a thread number itself in brackets, and use # only for thread numbers (write "rows 3 and 8", "line 43").
- Write two to four short paragraphs, separated by a blank line. On a quiet day (one to three threads) a few sentences are enough; on a busy day aim for 250 to 350 words. 400 words is a hard limit, so on the busiest days give a minor thread a clause rather than a sentence, and leave the detail to the page.

Rules:
- Say only what the material shows. The day's items are under "New", each with its ID in brackets; a gist is a one-line summary of that item written earlier by an AI model, followed by the start of the item's own text. The background is context only: use it to explain why something matters, never report it as something that happened on this day.
- Each thread's heading gives its state at the end of the day. Nothing after the day is in the material, so don't say what happened next.
- A review's GitHub state is in brackets. Say a review approved or requested changes only when that is its state; for any other review, say in plain words what it asked for, suggested or proposed, however firmly. Don't name review states as such ("commented review", "in the commented state").
- The "It concerns" and "It mentions" lines under a thread's heading are context: use them to explain a thread, not as lists to repeat.
- Only FOCUS_Spec threads have numbers here. A number written after another repository's name (such as "foundation #38") belongs to that repository: leave it out.
- Field updates are recorded by the repository's automation (task force, due date, status, approval counts). Where an update names the person who made it, credit that person, not the automation ("Matt-Cowsert moved #2770 to TF-Supporting"). Use them only where they show where a thread stands, such as approvals rising, a new task force or a due date; a new thread's first settings are worth a mention only when they say who is to do what by when.
- Write about the threads and the people in them, not about your sources: never mention "the material", "the background", gists or the IDs in brackets.
- Name people only by their GitHub username exactly as shown, with the same capital and small letters even at the start of a sentence. The start of an item's text sometimes calls people by their real names: never repeat those, and never guess real names. Refer to people by username or with they/them, never he or she.
- Don't describe the day itself ("since the last update", "in this period", "today", "yesterday"): the page gives the date. Give a date only where it matters, written as 22 November.
- Refer to issues and pull requests as #123. No URLs, no Markdown, no headings, no labels such as "Smaller items:" or "Where things stand:", no lists, no emoji.
- Keep the tone plain and matter-of-fact, as the working group would describe itself: no dramatic framing ("the real debate", "the crux", "a flurry", "battle", "clash") and no filler ("notably", "it is worth noting", "overall", "in summary", "remains open", "at the end of the day"). Say what a thread is waiting on, or nothing; don't close by saying it is still open. Leave out the automation's own entries (Review Bot, fields being initialised) except as an approval count or a due date.
- Paraphrase. Quote at most 10 words, word for word from an item's text, and only when the exact wording matters.
- Don't write in the first person or address the reader.
- Reply with the summary text only."""

REVIEW = """You check a daily summary of activity in the GitHub repository of the FOCUS specification against the material it was written from. The material lists, for each thread, background (context only) and the day's items, under "New", each with an ID in brackets.

Report only clear errors of substance that a careful reader would want fixed:
- a statement the material doesn't support or contradicts: the wrong person, the wrong thread number, the wrong outcome (approved but not merged, proposed but not decided, open but called closed, a "commented" review called an approval or a change request), or a position someone didn't take;
- a forecast of what the group will decide or when;
- a number from another repository (such as "foundation #38") presented as one of this repository's threads;
- a person called by a real name instead of their GitHub username;
- something reported as happening on this day that the material shows only in the background;
- a reading of the mood or of where things stand that the items clearly don't bear out;
- the day's most important development missing.

Don't report style, word choice, length, order or emphasis, a reasonable reading of the items, or a thread that is merely mentioned briefly. For each finding, quote the summary's words exactly (a short stretch), say what is wrong, and cite the item IDs that show it. If there is nothing of this kind, return an empty list: a summary with no errors of substance is the expected case."""

REVIEW_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["findings"], "properties": {
    "findings": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["quote", "problem"],
                                            "properties": {"quote": {"type": "string"}, "problem": {"type": "string"}}}}}}

DRAMA = re.compile(r"\b(crux|battles?|clash(?:es|ed)?|showdown|heated|firestorm|flurry|drama(?:tic)?|bombshell|real debate)\b", re.I)
RELATIVE = re.compile(r"\b(today|yesterday|tomorrow|tonight)\b", re.I)
FIRST_PERSON = re.compile(r"\b(?:I|[Ww]e|[Oo]ur|us)\b")
SOURCES = re.compile(r"\b(the material|the background|gists?)\b", re.I)
# Openings and labels that describe the day or the text instead of saying what happened.
STOCK = re.compile(r"\b(most (?:of )?(?:the )?activity|the (?:day|period) was (?:dominated|taken up)|since the last update|"
                   r"in (?:this|the) period|this (?:update|summary)|smaller items:|other items:|where things stand:|"
                   r"overall,|in summary|notably|it is worth noting)", re.I)
FILLER = re.compile(r"\b(?:remains?|is still) (?:open|a draft)\.|\bat the end of the day\b|\bReview Bot\b|"
                    r"\binitiali[sz]ed\b", re.I)
REVIEW_STATE = re.compile(r"\b(commented (?:review|state)|state: ?commented|\(commented\))", re.I)
NUMBER = re.compile(r"(?<![\w&/])#(\d{2,5})\b")


def day_problems(text, src, threads, newest=None):
    """The checks a daily summary must pass, beyond those for every AI-written text. newest is the highest thread
    number opened by the end of an earlier day. Lengths and wording are checked with the titles in brackets taken out."""
    found = []
    titles = src_titles(threads)
    plain = without_titles(text, titles)
    bare = re.sub(r"[\"“][^\"”]*[\"”]", " ", plain)  # outside quotes
    named = {int(n) for n in NUMBER.findall(plain)}
    # The day's own threads must carry their titles; a thread they only refer to may (the page adds a missing one).
    untitled = [r["n"] for r in threads if f"#{r['n']} ({titles[r['n']]})" not in text]
    if untitled:
        found.append("give the title in brackets the first time you mention " + "; ".join(
            f"#{n}, as \"#{n} ({titles[n]})\"" for n in untitled))
    if re.search(r"\b(?:rows?|lines?|items?|findings?|steps?|options?) #\d", bare, re.I):
        found.append("it writes a row, line or item number with #; only thread numbers take #, so write \"rows 3 and 8\"")
    outside = re.sub(r'Its text begins: "[^\n]*', "", src)
    names = sorted(n for n in real_names(threads) if not re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", outside)
                   and re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", bare))
    if names:
        found.append("it names " + ", ".join(f'"{n}"' for n in names) + ", a real name from someone's comment; refer to "
                     "people only by GitHub username")
    known = {int(n) for n in NUMBER.findall(src)}
    missing = [r["n"] for r in threads if r["n"] not in named]
    if missing:
        found.append("it doesn't mention " + ", ".join(f"#{n}" for n in missing) + "; mention every thread in the material by its number")
    if named - known:
        found.append("it mentions " + ", ".join(f"#{n}" for n in sorted(named - known)) + ", which isn't in the material")
    if re.search(r"\(#\d", bare):
        found.append("it puts a thread number in brackets; write it as #123 (title), with the number outside")
    later = sorted(n for n in named & known if newest and n > newest)
    if later:
        found.append("it mentions " + ", ".join(f"#{n}" for n in later) + ", opened after this day; leave it out")
    words = len(plain.split())
    if len(threads) >= 5 and words < 180:
        found.append(f"it has {words} words; on a day with {len(threads)} threads, write at least 200")
    long_para = [len(p.split()) for p in re.split(r"\n\s*\n", plain) if len(p.split()) > 170]
    if long_para:
        found.append(f"a paragraph has {long_para[0]} words; keep each paragraph under 170 words")
    for pattern, what in ((DRAMA, "dramatic framing"), (RELATIVE, "a relative date"), (FIRST_PERSON, "the first person"),
                          (SOURCES, "a word about its sources"), (STOCK, "a stock phrase; say what happened directly"),
                          (REVIEW_STATE, "a review's state by name; say what the review did instead"),
                          (FILLER, "a filler phrase; say what a thread is waiting on, or nothing")):
        m = pattern.search(bare)
        if m:
            found.append(f"it uses {what} (\"{m.group(0)}\")")
    return found


# ---- the dry run ------------------------------------------------------------------------------------------------
work = []
for frm, to, mode in plan:
    threads = material(frm, to, mode)
    work.append((frm, to, mode, threads, source_text(frm, to, mode, threads) if threads else ""))
chars = [len(RULES) + len(src) for *_, src in work if src]
# The daily summaries' own rate once a run of them is logged: they escalate far more often than a thread's summary.
share = (escalated_share(SITE, MODEL, ("daily",)) or escalated_share(SITE, MODEL)) if FALLBACK else 0.0
est = estimate(chars, (MODEL, SETTINGS), (FALLBACK, FALLBACK_SETTINGS), share)
# A fact check for each text that passes the checks: about the same input again, on the reviewer at high effort.
review_est = estimate(chars, (REVIEWER, {**MODELS[REVIEWER], "effort": "high"}), (None, None), 0)
for k in ("input", "output", "cost"):
    est[k] = round(est[k] + review_est[k], 4) if k == "cost" else est[k] + review_est[k]
for frm, to, mode, threads, src in work:
    print(f"{day_id(to)} ({mode}): {iso(frm)} to {iso(to)}, {len(threads)} threads, "
          f"{sum(r['items'] for r in threads)} items, {len(src):,} characters of material")
print(estimate_line(est, len(chars), share, FALLBACK).replace(" In all", f" With a fact check by {REVIEWER} for each, in all")
      if est["models"] else "Nothing to write.")
# The estimate counts one try per model; retries and failed fact checks add to it, so the logged runs' cost a day is
# given beside it.
logged = [r for r in (json.loads((SITE / "update-log.json").read_text())["runs"] if (SITE / "update-log.json").exists() else [])
          if r.get("task") == "daily" and r.get("attempted")]
if logged and chars:
    per_day = sum(r["cost"] for r in logged) / sum(r["attempted"] for r in logged)
    print(f"Logged daily runs cost US${per_day:.4f} a day written, retries, escalations and fact checks included: "
          f"about US${per_day * len(chars):.4f} for these {len(chars)}.")
if "--show" in FLAGS:
    for frm, to, mode, threads, src in work:
        print(f"\n{'#' * 100}\n{day_id(to)}\n{src}")
    sys.exit()
if "--write" not in FLAGS:
    sys.exit()

import anthropic  # noqa: E402

client = anthropic.Anthropic()
spend = Spend()


def fact_check(text, src):
    """The reviewer's findings that quote the summary exactly, as problems to fix, with its tokens and cost."""
    r = client.messages.create(model=REVIEWER, max_tokens=16000, system=REVIEW,
                               messages=[{"role": "user", "content": f"The material:\n\n{src}\n\n---\n\nThe summary:\n\n{text}"}],
                               thinking={"type": "adaptive"},
                               output_config={"effort": "high", "format": {"type": "json_schema", "schema": REVIEW_SCHEMA}})
    spend.add(REVIEWER, r.usage)
    cost = {"model": REVIEWER, "input": r.usage.input_tokens, "output": r.usage.output_tokens,
            "cost": round(call_cost(REVIEWER, r.usage), 5)}
    if r.stop_reason != "end_turn":
        return [f"the fact check stopped early ({r.stop_reason})"], cost
    reply = json.loads(next(b.text for b in r.content if b.type == "text"))
    # A finding whose quote isn't in the summary can't be acted on, so it is dropped (and printed).
    real = [f for f in reply["findings"] if f["quote"].strip() and norm(f["quote"].strip(" .\"")) in norm(text)]
    for f in reply["findings"]:
        if f not in real:
            print(f"  fact check finding dropped (its quote isn't in the summary): {f}")
    return [f"fact check: \"{f['quote'].strip()}\": {f['problem'].strip()}" for f in real], cost


def attempt(model_, settings, src, threads, tries, newest):
    """One model's try at a day, with one retry naming what failed. Returns (text, problems)."""
    busy = len(threads) > 3 or sum(r["items"] for r in threads) > 8
    length = (f"aim for 250 to 350 words, and never more than {LIMIT}" if busy
              else f"a few sentences, as many as the day needs, and never more than {LIMIT} words")
    messages = [{"role": "user", "content": f"{src}\n\nLength: {length}."}]
    for n in (1, 2):
        r = client.messages.create(model=model_, max_tokens=settings["max_tokens"], system=RULES, messages=messages,
                                   thinking=settings["thinking"], output_config={"effort": settings["effort"]})
        spend.add(model_, r.usage)
        text = "".join(b.text for b in r.content if b.type == "text").strip()
        entry = {"model": model_, "try": n, "input": r.usage.input_tokens, "output": r.usage.output_tokens,
                 "cost": call_cost(model_, r.usage)}
        if r.stop_reason != "end_turn":
            found = [f"the reply stopped early ({r.stop_reason})"]
        else:
            found = (problems(without_titles(text, src_titles(threads)), LIMIT, src, thread=True)
                     + day_problems(text, src, threads, newest))
            if not found:  # the fact check's call is kept beside the writer's, under its own model
                found, entry["review"] = fact_check(text, src)
        entry["problems"] = found
        entry["cost"] = round(entry["cost"], 5)
        tries.append(entry)
        print(f"  {model_} try {n}: " + ("passed" if not found else "; ".join(found)[:400]))
        if not found or n == 2:
            return text, found
        # A model asked to come under a limit tends to land just over it, so a long text is given a target below it.
        words = len(without_titles(text, src_titles(threads)).split())
        cut = (f" It has {words} words: bring it to about 320 by shortening the minor threads to a clause each, keeping "
               "every thread number." if words > LIMIT else "")
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "That summary fails these checks: " + "; ".join(found)
                      + "." + cut + " Rewrite it so it passes, following the same rules. Reply with the summary text only."}]


written = failed = first_pass = escalated = escalated_passed = 0
escalations = []
new_days = []
for frm, to, mode, threads, src in work:
    counts = {"items": sum(r["items"] for r in threads), "threads": len(threads),
              "people": len({p for r in threads for p in r["people"]}),
              "opened": sum(r["opened_now"] for r in threads),
              "merged": sum(bool(r["change"]) and r["change"]["what"] == "merged" for r in threads),
              "closed": sum(bool(r["change"]) and r["change"]["what"] != "merged" for r in threads)}
    day = {"id": day_id(to), "from": iso(frm), "to": iso(to), "counts": counts,
           "threads": [{"n": r["n"], "kind": r["kind"], "title": r["title"], "state": r["state"], "items": r["items"],
                        "comments": sum(e["kind"] in ("comment", "review", "inline") for e in r["events"]),
                        "statuses": r["statuses"], "people": r["people"], "objects": r["objects"][:6],
                        "change": r["change"]["what"] if r["change"] else None, "opened": r["opened_now"]} for r in threads]}
    if mode == "earlier":
        day["background"] = "earlier items"  # written later, without the two-week summaries (see the module's notes)
    if not threads:
        day["text"] = ""  # nothing happened: the page says so
        new_days.append(day)
        continue
    print(f"{day['id']}: {len(threads)} threads")
    tries, up = [], False
    newest = newest_by(to) if mode == "earlier" else None
    try:
        text, found = attempt(MODEL, SETTINGS, src, threads, tries, newest)
        writer = MODEL
        if found and FALLBACK:
            up = True
            print(f"  escalated to {FALLBACK}")
            text, found = attempt(FALLBACK, FALLBACK_SETTINGS, src, threads, tries, newest)
            writer = FALLBACK
    except anthropic.AuthenticationError:
        raise
    except Exception as e:  # one day's error doesn't lose the others
        text, found, writer = "", [f"error: {type(e).__name__}: {str(e)[:200]}"], None
        print(f"  {found[0]}")
        up = any(t["model"] != MODEL for t in tries)
    # What writing this day cost, its fact checks included, added to any earlier run's cost for the same day.
    spent, runs = earlier_cost.get(day["id"], (0, 0))
    spent += sum(t["cost"] + (t["review"]["cost"] if t.get("review") else 0) for t in tries)
    day.update({"cost": round(spent, 5), "runs": runs + 1})
    if up:
        item = escalated_item(day["id"], f"Daily summary for {long_date(to.astimezone(BRISBANE))}", tries, "passed" if not found else "failed")
        item["cost"] = round(item["cost"] + sum(t["review"]["cost"] for t in tries if t.get("review")), 5)  # with the fact checks
        escalations.append(item)
    first_pass += not found and not up
    escalated += up
    escalated_passed += up and not found
    if found:
        failed += 1
        day.update({"text": None, "failed": True})
    else:
        written += 1
        day.update({"text": text, "written_by": writer, "titles": titles_for(text, threads)})
    new_days.append(day)

days = sorted(new_days + days, key=lambda d: d["to"], reverse=True)[:KEEP_DAYS]
OUT.write_text(json.dumps({
    "about": ("What the FOCUS working group did on GitHub each day. Written each morning by an AI model from the day's "
              "comments, reviews, merges and closures, then fact-checked against them by a second call. "
              "It interprets, so check the threads before relying on it."),
    "generated_at": recent["generated_at"],
    "days": days,
}, indent=1, ensure_ascii=False))
attempted = len([w for w in work if w[3]])
counts_ = escalation(attempted, first_pass, escalated, escalated_passed, failed)
if attempted:
    log_run(SITE, {"task": "daily", "model": MODEL, "effort": SETTINGS["effort"], "fallback": FALLBACK, "reviewer": REVIEWER,
                   "days": len(work), "threads": sum(len(w[3]) for w in work), "items": sum(r["items"] for w in work for r in w[3]),
                   **counts_, "spend": spend.report(), "cost": round(spend.cost(), 4),
                   "estimate": {k: est[k] for k in ("input", "output", "cost")}, "escalations": escalations})
print(f"Wrote {OUT}: {len(days)} days. {written} written, {failed} failed; escalated {escalated} of {attempted} "
      f"({counts_['escalated_pct']}%) to {FALLBACK or 'no fallback'}, {escalated_passed} of them passed. {spend.line()}")
