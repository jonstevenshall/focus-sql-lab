"""Build site/search.json: the index behind the search box in the page header.

It is made only from files the other scripts have already written to the site folder, so it needs no network and no
API key, and it changes only when they do:

    python -I tools/build_search.py <site dir>

Reads focus/model.json (datasets, columns, joins, attributes, conditions, appendix, metadata and schema topics, and
every requirement line), object-summaries.json, focus/spec-queries.json, practice-queries.json, focus/history.json
with thread-summaries.json, focus/data/manifest.json (the Sample data tables), quiz.json (titles and questions, not
hints or answers), focus/ai-tokens.json, evidence.json, daily.json, recent.json and, if it exists, open-requests.json.

Each thing is listed once, under the tab it lives on: the page groups results by the tab their address opens. A pull
request or issue discussed in the last two weeks is listed as its card on Most recent updates, not again from the History.
Each day's summary is listed under the Daily summary tab.

Each entry is {y: what it is, i: its ID, n: its name, t: its title, b: its text, s: a line shown under it, x: more
words to find it by, not shown (SQL, thread numbers, column IDs), h: its address on the page}, with empty fields left
out. The page ranks a match in i above one in n, n above t, and t above b, s and x. A pull request or issue also has
g: the address of the panel whose History lists it, which #thread/<number> opens. Requirement lines are the specification's, with its Markdown links and emphasis reduced to their text.
"""
import datetime
import json
import pathlib
import re
import sys

SITE = pathlib.Path(sys.argv[1])
OUT = SITE / "search.json"


def load(rel, default=None):
    p = SITE / rel
    return json.loads(p.read_text()) if p.exists() else default


model = load("focus/model.json")
summaries = load("object-summaries.json", {"summaries": {}})["summaries"]
history = load("focus/history.json")
said = load("thread-summaries.json", {"summaries": {}})["summaries"]
evidence = load("evidence.json", {"items": []})["items"]
recent = load("recent.json", {"threads": []})["threads"]
daily = load("daily.json", {"days": []})["days"]
queries = load("practice-queries.json", []) + load("focus/spec-queries.json", [])
open_req = load("open-requests.json")  # written by tools/open_requests.py, once that is in place

items = []


def add(y, h, i="", n="", t="", b="", s="", x="", g=""):
    e = {"y": y, "i": str(i), "n": n, "t": t, "b": re.sub(r"\s+", " ", b).strip(), "s": s, "x": re.sub(r"\s+", " ", x).strip(),
         "h": h, "g": g}
    items.append({k: v for k, v in e.items() if v})


def plain(md):
    """A line of the specification's Markdown as it reads when published: link and emphasis marks dropped."""
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"(?<![\w*])\*\*?([^*]+?)\*\*?(?![\w*])", r"\1", md)
    return md.replace("`", "").replace("\\*", "*").replace("\\_", "_").strip()


def req_lines(owner, path, label):
    """One entry per requirement line of a dataset, column, attribute, condition or topic page."""
    for r in owner.get("requirements") or []:
        if r.get("heading"):
            continue
        text = " · ".join(" | ".join(plain(c) for c in row) for row in r["table"]) if r.get("table") else plain(r["md"])
        if text:
            add("req", f"#{path}/line-{r['line']}", t=text, s=label, b="" if owner["id"] in label else owner["id"])


# ---- the data model and the Reference tab ------------------------------------------------------------------------
target = {}  # a specification file (or "topic:<id>") -> the address of the panel that shows its History
names = {}  # that address -> the ID and name of what it shows
ds_name = {d["id"]: d["name"] for d in model["datasets"]}

for d in model["datasets"]:
    path = f"model/{d['id']}"
    target.setdefault(d["source"], path)
    names[path] = (d["id"], d["name"])
    add("ds", f"#{path}", i=d["id"], n=d["name"], s="Dataset" + (" · preview" if d.get("preview") else ""),
        b=" ".join([d.get("description", ""), summaries.get(d["source"], ""), *d.get("relationships_text", [])]))
    req_lines(d, path, f"{d['name']} dataset")

for d in model["datasets"]:
    for c in d["columns"]:
        path = f"model/{d['id']}/{c['id']}"
        target.setdefault(c["source"], path)
        names[path] = (c["id"], c["name"])
        facts = [c.get("feature_level"), c.get("column_type"), c.get("data_type")]
        add("col", f"#{path}", i=c["id"], n=c["name"], s=" · ".join([f"Column in {d['name']}", *filter(None, facts)]),
            b=" ".join([c.get("description", ""), summaries.get(c["source"], "")]),
            x=" ".join([c.get("conditions") or "", *(" ".join(map(str, row)) if isinstance(row, list) else str(row)
                                                       for row in c.get("allowed_values") or [])]))
        req_lines(c, path, f"{c['id']} · {d['name']}")

for r in model["relationships"]:
    keys = ", ".join(f"{r['a']}.{k[0]} = {r['b']}.{k[1]}" for k in r["keys"])
    add("join", f"#model/join/{r['id']}", n=r["name"], s=f"Join written by the lab · {ds_name[r['a']]} → {ds_name[r['b']]}",
        b=" ".join([r.get("cardinality", ""), *r.get("also", [])]), x=keys)

for kind, label, rows in [("attr", "Attribute", model.get("attributes", [])), ("cond", "Operating model condition", model.get("conditions", []))]:
    for a in rows:
        path = f"ref/{a['id']}"
        target.setdefault(a["source"], path)
        names[path] = (a["id"], a["name"])
        add(kind, f"#{path}", i=a["id"], n=a["name"], s=label + (f" · introduced in {a['introduced']}" if a.get("introduced") else ""),
            b=" ".join([a.get("description", ""), summaries.get(a["source"], "")]))
        req_lines(a, path, f"{a['name']} {label.lower()}")

TOPIC_KIND = {"appendix": "Appendix", "metadata": "Metadata", "schema": "JSON schema"}
for t in model.get("topics", []):
    path = f"ref/{t['id']}"
    for f in [f"topic:{t['id']}", t["source"], *t.get("files", [])]:
        target.setdefault(f, path)
    names[path] = (t["id"], t["name"])
    add("topic", f"#{path}", i=t["id"], n=t["name"], s=TOPIC_KIND.get(t["kind"], t["kind"]),
        b=" ".join([t.get("description", ""), summaries.get(f"topic:{t['id']}", ""), *t.get("pages", [])]))
    req_lines(t, path, f"{t['name']} ({TOPIC_KIND.get(t['kind'], t['kind']).lower()})")

# ---- example queries ---------------------------------------------------------------------------------------------
for q in queries:
    add("query", f"#query/{q['id']}", n="" if q.get("lab") else q.get("feature", ""), t=q["title"],
        s="Lab practice query, DuckDB SQL" if q.get("lab") else "Example query in the specification",
        b=" ".join([q.get("intro") or "", q.get("note") or ""]), x=q.get("sql") or "")

# ---- pull requests and issues: the History, and the open requests -------------------------------------------------
# Each opens the panel whose History lists it: a page it changed whose ID or name its title names ("Reconcile
# ContractedUnitPrice null semantics", "Add `SKU Price` dataset"), or else the page it changed most, by lines. An issue
# is listed under the pull requests that name it: a page one of them changed whose ID or name the issue's title names,
# or else the first merged one's page.
pages = {}  # pull request number -> {address of a page it changed: lines changed}
for f, x in history["files"].items():
    for n, a, d in x["prs"] + x.get("unmerged", []) if f in target else []:
        c = pages.setdefault(str(n), {})
        c[target[f]] = max(c.get(target[f], 0), a + d)


def named(title, cands):
    """The pages among cands whose ID (ContractedUnitPrice) or name (SKU Price, in any case) the title has as words."""
    def says(word):
        return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", title, re.I if " " in word else 0)
    return [p for p in cands if any(says(w) for w in names.get(p, ()) if w)]


def pick(title, cands):
    return max(named(title, cands) or cands, key=lambda p: cands[p])


best = {n: pick(history["prs"][n]["title"], c) for n, c in pages.items() if n in history["prs"]}
issue_at = {}
for n, p in sorted(history["prs"].items(), key=lambda kv: (kv[1].get("state") != "merged", int(kv[0]))):
    for i in map(str, p.get("issues", [])):
        if n not in best or i not in history["issues"]:
            continue
        hit = named(history["issues"][i]["title"], pages[n])
        if hit and not named(history["issues"][i]["title"], [issue_at.get(i, "")]):
            issue_at[i] = max(hit, key=lambda q: pages[n][q])
        issue_at.setdefault(i, best[n])

open_threads = (open_req or {}).get("threads", {})
open_target = {}
for key, rows in (open_req or {}).get("objects", {}).items():
    kind, rest = key.split(":", 1)
    path = f"model/{rest}" if kind == "dataset" else ("model/" + rest.replace(".", "/", 1) if kind == "column" else None)
    for n, level, _how in rows:
        rank = {"strong": 0, "wide": 1}.get(level, 2)
        if path and rank < open_target.get(str(n), (9, ""))[0]:
            open_target[str(n)] = (rank, path)


def state_of(x):
    if x.get("unmerged"):
        return "open draft" if x.get("draft") else "open" if x.get("state") == "open" else "closed, not merged"
    if x.get("state") == "closed" and x.get("reason"):
        return f"closed as {x['reason']}"
    return x.get("state", "")


in_recent = {str(r["number"]) for r in recent}


def thread(y, n, x, goal, extra=""):
    if str(n) in in_recent:
        return  # listed as its Recent card instead
    when = x.get("merged") or x.get("closed") or x.get("opened") or ""
    sub = " · ".join(filter(None, ["Pull request" if y == "pr" else "Issue", state_of(x), when, x.get("author")]))
    add(y, f"#thread/{n}", i=n, t=x["title"], s=sub, g=f"#{goal}",
        b=" ".join(filter(None, [x.get("summary") if x.get("summary") != x["title"] else "", said.get(str(n), ""), extra])))


for n, x in history["prs"].items():
    if n in best:
        thread("pr", n, x, best[n], open_threads.get(n, {}).get("summary", ""))
for n, x in history["issues"].items():
    if n in issue_at and n not in history["prs"]:
        thread("issue", n, x, issue_at[n], open_threads.get(n, {}).get("summary", ""))
for n, x in open_threads.items():
    if n in history["prs"] or n in history["issues"] or n not in open_target:
        continue
    y = "pr" if x.get("kind") == "pull request" else "issue"
    thread(y, n, {**x, "state": "open draft" if x.get("state") == "draft" else "open"}, open_target[n][1],
           " ".join(filter(None, [x.get("summary"), said.get(n, "")])))

# ---- the Sample data, Quiz and AI tokens tabs ---------------------------------------------------------------------
manifest = load("focus/data/manifest.json", {"tables": {}, "sources": {}})
for name, m in manifest["tables"].items():
    src = manifest["sources"].get(m.get("source"), {})
    labs = m.get("lab_columns") or {}
    add("table", f"#data/{name}", i=name, b=m.get("description", ""),
        s=" · ".join(filter(None, [ds_name.get(m.get("dataset"), m.get("dataset")), src.get("label"), f"{m.get('rows', 0):,} rows"])),
        x=" ".join([*labs, *labs.values()]))

QUIZ_LEVELS = {1: "Write it", 2: "Change it"}  # as the Quiz tab heads its two lists
for q in load("quiz.json", []):
    add("quiz", f"#quiz/{q['id']}", t=q["title"], b=q.get("ask", ""),
        s=" · ".join(filter(None, [QUIZ_LEVELS.get(int(q.get("level", 0))), *q.get("concepts", [])])))

ai = load("focus/ai-tokens.json")
if ai:  # the parts of the AI tokens tab, under the headings the tab gives them
    add("ai", "#ai/how", t="How a token charge is recorded",
        b=" ".join([*ai.get("how", []), ai.get("feature", {}).get("description", "")]))
    for r in ai.get("properties", {}).get("rows", []):
        add("ai", f"#ai/{r['key']}", i=r["key"], b=r.get("description", ""), x=r.get("values", ""),
            s=f"The model and token properties · {r.get('type', '')}")
    for g in ai.get("gaps", []):
        add("ai", f"#ai/{g['id']}", t=g["title"], b=g.get("text", ""), s="Where it stops",
            x=" ".join(q.get("text", "") for q in g.get("quotes", [])))

# ---- evidence, the daily summaries and the most recent updates ---------------------------------------------------------------------------------
EV_KINDS = {"pain": "Pain point", "demand": "Who asked", "discovery": "How they found out", "use_case": "Use case",
            "tradeoff": "Trade-off"}
obj_label = lambda k: k.split(":", 1)[1]  # noqa: E731
for e in evidence:
    threads = ", ".join(f"#{n}" for n in e.get("threads") or [])
    add("ev", f"#evidence/{e['id']}", t=e["statement"], b=e.get("quote") or "",
        s=" · ".join(filter(None, [EV_KINDS.get(e["category"], e["category"]), e.get("date"), threads and f"threads {threads}"])),
        x=" ".join([*(e.get("who") or []), *[obj_label(o) for o in e.get("objects") or []]]))

BRISBANE = datetime.timezone(datetime.timedelta(hours=10))  # a day is named by its date there (tools/daily.py)
for d in daily:
    if not d.get("text"):
        continue
    day = datetime.date.fromisoformat(d["id"])
    c = d["counts"]
    add("daily", f"#daily/{d['id']}", i=d["id"], t=f"{day:%A} {day.day} {day:%B %Y}", b=d["text"],
        s=f"Daily summary · {c['threads']} threads · {c['items']} comments, reviews and new threads",
        x=" ".join([*(f"#{t['n']} {t['n']}" for t in d["threads"]), *{p for t in d["threads"] for p in t["people"]}]))

for r in recent:
    add("recent", f"#recent/{r['number']}", i=r["number"], t=r["title"],
        s=" · ".join(["Pull request" if r["kind"] == "pr" else "Issue", r["state"], f"last activity {r['last'][:10]}", r["author"]]),
        b=r.get("summary") or "", x=" ".join([*r.get("labels", []), *[obj_label(o) for o in r.get("objects", [])]]))

OUT.write_text(json.dumps({"about": "The site search index, built by tools/build_search.py from the other files in this folder.",
                           "items": items}, separators=(",", ":"), ensure_ascii=False))
counts = {}
for e in items:
    counts[e["y"]] = counts.get(e["y"], 0) + 1
print(f"Wrote {OUT}: {len(items)} entries ({', '.join(f'{v} {k}' for k, v in counts.items())}), {OUT.stat().st_size:,} bytes")
