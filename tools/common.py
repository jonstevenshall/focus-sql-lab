"""Shared by summarise.py, recent.py and open_requests.py: the models they can use, the checks every AI-written text
must pass, writing a text with one retry and a fallback model, the summary brief for a whole thread, the parts of the
data model by file and by name, and reading FOCUS_Spec from GitHub's REST and GraphQL APIs (GH_TOKEN)."""
import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict

REPO = "FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec"

# Per model: US$ per million input and output tokens (list price), and the request settings. Sonnet 5.5 turns thinking
# off with "between_tools", which no other model accepts. Haiku 5.5 always thinks, so it has room in max_tokens for the
# thinking (billed as output).
MODELS = {
    "claude-sonnet-5-5": {"price": (2.0, 10.0), "thinking": {"type": "between_tools"}, "effort": "medium", "max_tokens": 1024},
    "claude-haiku-5-5": {"price": (0.10, 0.50), "thinking": {"type": "adaptive"}, "effort": "low", "max_tokens": 8000},
}


def model_from(flags, default="claude-sonnet-5-5"):
    """The model and its settings from --model and --effort (low, medium or high)."""
    name = flags[flags.index("--model") + 1] if "--model" in flags else default
    if name not in MODELS:
        sys.exit(f"--model must be one of {', '.join(MODELS)}")
    settings = dict(MODELS[name])
    if "--effort" in flags:  # how hard Haiku thinks; on Sonnet (thinking off) it only shapes the reply
        settings["effort"] = flags[flags.index("--effort") + 1]
        if settings["effort"] not in ("low", "medium", "high"):
            sys.exit("--effort must be low, medium or high")
    return name, settings


# A text the first model fails twice gets two more tries on the fallback (--fallback MODEL, or none). The default
# fallback is Claude Sonnet 5.5, unless that is already the first model.
FALLBACK = "claude-sonnet-5-5"


def fallback_from(flags, primary):
    name = flags[flags.index("--fallback") + 1] if "--fallback" in flags else (FALLBACK if primary != FALLBACK else "none")
    if name == "none":
        return None, None
    if name not in MODELS:
        sys.exit(f"--fallback must be none or one of {', '.join(MODELS)}")
    return name, dict(MODELS[name])


class Spend:
    """Tokens used and their cost at list price, per model."""

    def __init__(self):
        self.tokens = {}

    def add(self, model, usage):
        t = self.tokens.setdefault(model, [0, 0])
        t[0] += usage.input_tokens
        t[1] += usage.output_tokens

    def cost(self, model=None):
        return sum(i * MODELS[m]["price"][0] / 1e6 + o * MODELS[m]["price"][1] / 1e6
                   for m, (i, o) in self.tokens.items() if model in (None, m))

    def report(self):
        """Per model: tokens, cost, and the list prices it was costed at (US$ per million input and output tokens)."""
        return {m: {"input": i, "output": o, "cost": round(self.cost(m), 4), "price": list(MODELS[m]["price"])}
                for m, (i, o) in self.tokens.items()}

    def line(self):
        parts = [f"{m}: {i:,} input and {o:,} output tokens, US${self.cost(m):.4f}" for m, (i, o) in self.tokens.items()]
        return ("; ".join(parts) or "no API calls") + f". In all US${self.cost():.4f} at list price."


def escalation(attempted, first_pass, escalated, escalated_passed, failed):
    """The counts the update log shows for a batch of AI-written texts."""
    return {"attempted": attempted, "first_pass": first_pass, "escalated": escalated,
            "escalated_passed": escalated_passed, "failed": failed,
            "escalated_pct": round(100 * escalated / attempted, 1) if attempted else 0.0}


def call_cost(model, usage):
    """What one API call cost at list price."""
    p_in, p_out = MODELS[model]["price"]
    return usage.input_tokens * p_in / 1e6 + usage.output_tokens * p_out / 1e6


def escalated_item(key, title, tries, result):
    """The working for one item the first model failed twice, for the update log: each try's model, the checks' exact
    findings (none on the try that passed), its tokens and cost; and the outcome (passed, partly or failed)."""
    return {"key": str(key), "title": title, "result": result, "tries": tries,
            "cost": round(sum(t["cost"] for t in tries), 5)}


def log_run(site, entry):
    """Add an entry to <site>/update-log.json, newest first: what ran, what it did and what it cost, and how it was
    started (on GitHub: schedule or workflow_dispatch)."""
    path = site / "update-log.json"
    log = json.loads(path.read_text()) if path.exists() else {"runs": []}
    run = os.environ.get("GITHUB_RUN_ID")
    url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{run}" if run else None
    trigger = {"trigger": os.environ["GITHUB_EVENT_NAME"]} if os.environ.get("GITHUB_EVENT_NAME") else {}
    log["runs"].insert(0, {"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **entry, **trigger, "run": url})
    log["runs"] = log["runs"][:400]
    path.write_text(json.dumps(log, indent=1, ensure_ascii=False))


# ---- checks -----------------------------------------------------------------------------------------------------
GENDERED = re.compile(r"\b(he|she|him|her|his|hers|himself|herself)\b", re.I)
norm = lambda s: re.sub(r"\s+", " ", s.replace("’", "'").replace("“", '"').replace("”", '"')).strip().lower()
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
# The people in a thread, from the headers the thread text is written with ("Comment by X on ...").
PEOPLE = re.compile(r"^(?:\[\w+\] )?(?:Comment|Review|Inline comment) by ([A-Za-z0-9][A-Za-z0-9-]*)"
                    r"|^(?:\[\w+\] )?(?:Pull request|Issue) opened by ([A-Za-z0-9][A-Za-z0-9-]*)", re.M)


def problems(text, limit, source, thread=False):
    """What's wrong with an AI-written text: an empty list if it passes."""
    found = []
    if thread:
        for user in {a or b for a, b in PEOPLE.findall(source)}:
            for m in re.finditer(rf"(?<![A-Za-z0-9-]){re.escape(user)}(?![A-Za-z0-9-])", text, re.I):
                if m.group(0) != user:
                    found.append(f"it writes the username {user} as {m.group(0)}; write it exactly as shown")
                    break
        if re.search(rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b(?:{MONTHS}) \d{{1,2}}(?:st|nd|rd|th)?\b", text):
            found.append("write dates as 29 October 2026, or as a month and year")
    if not text:
        found.append("the reply was empty")
    if len(text.split()) > limit:
        found.append(f"it has {len(text.split())} words; the limit is {limit}")
    if GENDERED.search(text):
        found.append("it uses he or she; use the username or they/them")
    if re.search(r"https?://|www\.", text):
        found.append("it contains a URL")
    if re.search(r"\*\*|`|^\s*[-*•] |\[[^\]]+\]\(|^#+ ", text, re.M):
        found.append("it contains Markdown or a list")
    # Every quoted stretch, taken in pairs from the start, so the words between two short quotes ("row" ... "line
    # item") are never read as a quote. Only quotes of 12 characters or more are checked.
    for q in re.findall(r"[\"“]([^\"”]+)[\"”]", text):
        if len(q) >= 12 and norm(q) not in norm(source):
            found.append(f"the quote \"{q[:60]}\" isn't in the source word for word")
    return found


# ---- GitHub -----------------------------------------------------------------------------------------------------
def gh(path, **params):
    query = ("&" if "?" in path else "?") + urllib.parse.urlencode(params) if params else ""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}{query}",
                                 headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def gh_all(path, **params):
    out, page = [], 1
    while True:
        batch = gh(path, per_page=100, page=page, **params)
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def graphql(query):
    """FOCUS_Spec's repository object from a GraphQL query written inside repository(...) { }. Stops on any error."""
    owner, name = REPO.split("/")
    req = urllib.request.Request("https://api.github.com/graphql", headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}"},
                                 data=json.dumps({"query": f'query {{ repository(owner: "{owner}", name: "{name}") {{ {query} }} }}'}).encode())
    with urllib.request.urlopen(req) as r:
        out = json.load(r)
    if out.get("errors"):
        raise RuntimeError("GraphQL: " + "; ".join(e.get("message", "") for e in out["errors"][:3]))
    return out["data"]["repository"]


def cut(s, n=3000):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + " […cut]"


def is_bot(user):
    return (user or {}).get("type") == "Bot" or (user or {}).get("login", "").endswith("[bot]")


# ---- the parts of the data model, by file and by the words that name them -----------------------------------------
CAMEL = re.compile(r"[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+")  # BilledCost, SkuPriceDetails: not plain English words


def model_index(model):
    """by_file: each definition file -> the model keys it defines ("column:CostAndUsage.BilledCost"). by_word: each name
    that identifies a part (a column ID in CamelCase, a dataset's ID or name, an attribute or condition ID; four letters
    or more) -> its key. words: a pattern that finds those names in text."""
    by_file, by_word = defaultdict(list), {}
    for d in model["datasets"]:
        by_file[d["source"]].append(f"dataset:{d['id']}")
        by_word.setdefault(d["id"], f"dataset:{d['id']}")
        by_word.setdefault(d["name"], f"dataset:{d['id']}")  # "Cost and Usage", "SKU Price"
        for c in d["columns"]:
            by_file[c["source"]].append(f"column:{d['id']}.{c['id']}")
            if CAMEL.fullmatch(c["id"]):
                by_word.setdefault(c["id"], f"column:{d['id']}.{c['id']}")  # the first dataset that has it (Cost and Usage first)
    for kind in ("attributes", "conditions"):
        for a in model.get(kind, []):
            if not a.get("id"):  # the conditions overview page has no ID of its own
                continue
            key = f"{kind[:-1]}:{a['id']}"
            by_file[a["source"]].append(key)
            by_word.setdefault(a["id"], key)
    for t in model.get("topics", []):
        for f in t["files"] or [t["source"]]:
            by_file[f].append(f"topic:{t['id']}")
    by_word = {w: k for w, k in by_word.items() if len(w) >= 4}
    words = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(sorted(map(re.escape, by_word), key=len, reverse=True)) + r")(?![A-Za-z0-9_])")
    return by_file, by_word, words


# ---- writing a text, checked, with a fallback model -----------------------------------------------------------
def write_text(client, spend, system, content, limit, thread, primary, fallback, log=print, tries=None):
    """A text from the first model, checked with problems(), with one retry naming the problems. One that fails twice
    goes to the fallback model for two more tries. primary and fallback are (model, settings) pairs; the fallback's
    model may be None. Each call is added to tries, if given: its model, try number, the checks' findings, tokens and
    cost. Returns (text, problems, the model that wrote it, whether it was escalated)."""

    def attempt(model, settings):
        messages = [{"role": "user", "content": f"{content}\n\nLength: at most {limit} words."}]
        for n in (1, 2):
            # The retry sends back only the text of the first reply, not its thinking, which the API allows.
            r = client.messages.create(model=model, max_tokens=settings["max_tokens"], system=system, messages=messages,
                                       thinking=settings["thinking"], output_config={"effort": settings["effort"]})
            spend.add(model, r.usage)
            text = "".join(b.text for b in r.content if b.type == "text").strip()
            found = problems(text, limit, content, thread) if r.stop_reason == "end_turn" else [f"the reply stopped early ({r.stop_reason})"]
            if tries is not None:
                tries.append({"model": model, "try": n, "problems": found, "input": r.usage.input_tokens,
                              "output": r.usage.output_tokens, "cost": round(call_cost(model, r.usage), 5)})
            if not found or n == 2:
                return text, found
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "That summary fails these checks: " + "; ".join(found) + ". Rewrite it so it passes, following the same rules. Reply with the summary text only."}]

    text, found = attempt(*primary)
    if not found or not fallback[0]:
        return text, found, primary[0], False
    log(f"  escalated to {fallback[0]}: {'; '.join(found)[:200]}")
    text, found = attempt(*fallback)
    return text, found, fallback[0], True


# ---- what a batch of summaries will cost --------------------------------------------------------------------------
# Calibrated on the logged summary runs in site/update-log.json, against the text each of their items sent (measured
# again from the thread or page on 20 November 2026). Input: tokens per character sent (instructions, text and the
# length line), which includes the retries that failed first tries needed: most first tries fail a check (often the word
# limit), and a retry sends the text again. Output: tokens per item, which for Haiku is mostly its thinking, billed as
# output. Haiku 5.5 at high effort: 202 items in two runs (12 and 13 November 2026), 781,806 characters sent, 341,190
# input and 357,049 output tokens. Sonnet 5.5 at medium effort: 15 items in one run (19 November 2026), 232,829
# characters, 135,054 input and 5,421 output tokens. Settings with no logged run borrow the nearest that has one, and
# the estimate says so.
INPUT_PER_CHAR = {"claude-haiku-5-5": 0.436, "claude-sonnet-5-5": 0.580}
OUTPUT_PER_ITEM = {("claude-haiku-5-5", "high"): 1768, ("claude-sonnet-5-5", "medium"): 361}


def escalated_share(site, model, tasks=("summaries", "open-requests", "recent")):
    """The share of items the first model failed twice and passed to the fallback, in logged runs with this first model
    and a fallback. Every task's runs count: they use the same checks, and each has few escalations on its own."""
    path = site / "update-log.json"
    runs = [r for r in (json.loads(path.read_text())["runs"] if path.exists() else [])
            if r.get("task") in tasks and r.get("model") == model and r.get("fallback") and r.get("attempted")]
    attempted = sum(r["attempted"] for r in runs)
    return sum(r.get("escalated", 0) for r in runs) / attempted if attempted else 0.0


def estimate(chars, primary, fallback, share):
    """What writing items that send these many characters each should cost: every item on the first model, and the
    share that escalate again on the fallback. primary and fallback are (model, settings) pairs; the fallback's model
    may be None. Returns tokens and cost per model, the totals, and notes on any setting that wasn't measured."""
    notes, per = [], {}

    def per_item(model, settings):
        key = (model, settings["effort"])
        if key in OUTPUT_PER_ITEM:
            return OUTPUT_PER_ITEM[key]
        near = next(k for k in OUTPUT_PER_ITEM if k[0] == model)
        notes.append(f"{model} at {settings['effort']} effort has no logged run; its output is estimated from {near[1]} effort")
        return OUTPUT_PER_ITEM[near]

    for (model, settings), weight in ((primary, 1.0), (fallback, share)):
        if not model or not weight:
            continue
        i, o = sum(chars) * INPUT_PER_CHAR[model] * weight, len(chars) * per_item(model, settings) * weight
        p_in, p_out = MODELS[model]["price"]
        per[model] = {"input": round(i), "output": round(o), "cost": i * p_in / 1e6 + o * p_out / 1e6}
    return {"models": per, "input": sum(v["input"] for v in per.values()), "output": sum(v["output"] for v in per.values()),
            "cost": round(sum(v["cost"] for v in per.values()), 4), "notes": notes}


def estimate_line(est, n, share, fallback):
    """The estimate as one plain sentence."""
    parts = "; ".join(f"{m}: {v['input']:,} input and {v['output']:,} output tokens, US${v['cost']:.4f}" for m, v in est["models"].items())
    esc = f", with {share:.1%} escalating to {fallback} as in the logged runs" if fallback and share else ""
    total = f"{est['cost']:.{2 if est['cost'] >= 0.1 else 4}f}"
    return (f"Estimated cost of these {n}{esc}: {parts or 'nothing'}. In all about US${total} at list price."
            + "".join(f" Note: {x}." for x in est["notes"]))


# ---- a whole thread, and the brief for summarising it ----------------------------------------------------------
THREAD_RULES = """You write summaries of GitHub discussion threads from the FOCUS specification repository (FinOps Open Cost and Usage Specification) for a learning website. The reader wants to know what each conversation was about and how it ended.

Cover, in plain English and the past tense:
1. What was proposed or asked, and why.
2. The main points raised or disputed: who held which position (by GitHub username), alternatives considered, concerns, compromises.
3. How it ended: merged, approved by members, closed as complete, deferred, cut from a release, superseded by another issue or pull request, or still open. Give the month and year when the thread states it.

Rules:
- State only what the thread says. Don't speculate, evaluate or add background knowledge. If the thread doesn't record the outcome, say "The thread doesn't record the outcome."
- Name people only by their GitHub username exactly as shown, with the same capital and small letters even at the start of a sentence. Never guess real names. Refer to people by username or with they/them, never he or she.
- Write dates as 29 October 2026, or as a month and year (October 2026).
- Refer to other issues and pull requests as #123. No URLs, no Markdown, no emoji, no bullet lists.
- Paraphrase. Quote at most 10 words, and only when the exact wording matters.
- Skip bot comments, CLA notices and routine housekeeping (scheduling, moving to member review, reassigning) unless they record a decision or the outcome.
- If a thread is mostly editorial or mechanical, say so briefly.

Reply with the summary text only."""


def thread_limit(weight):
    """The word limit for a thread's summary, from its comments and reviews."""
    return 70 if weight < 20 else 130


def thread_text(n):
    """A FOCUS_Spec issue or pull request as text to summarise: its description, comments, reviews and inline comments."""
    i = gh(f"issues/{n}")
    is_pr = "pull_request" in i
    head = [f"Title: #{n} {i['title']}", f"{'Pull request' if is_pr else 'Issue'} opened by {i['user']['login']} on {i['created_at'][:10]}."]
    if is_pr:
        p = gh(f"pulls/{n}")
        head.append(f"Merged on {p['merged_at'][:10]}." if p.get("merged_at") else f"State: {p['state']}.")
    else:
        head.append(f"State: {i['state']}{' (' + i['state_reason'] + ')' if i.get('state_reason') else ''}.")
    head.append("Description:\n" + cut(i.get("body"), 6000))
    events = []
    bot = lambda u: u.get("type") == "Bot" or u["login"].endswith("[bot]")
    for c in gh_all(f"issues/{n}/comments"):
        if not bot(c["user"]):
            events.append((c["created_at"], f"Comment by {c['user']['login']} on {c['created_at'][:10]}:\n{cut(c['body'])}"))
    if is_pr:
        for r in gh_all(f"pulls/{n}/reviews"):
            if r.get("submitted_at") and not bot(r["user"]) and (r.get("body") or r["state"] != "COMMENTED"):
                events.append((r["submitted_at"], f"Review by {r['user']['login']} ({r['state'].lower()}) on {r['submitted_at'][:10]}:\n{cut(r.get('body'))}"))
        for c in gh_all(f"pulls/{n}/comments"):
            if not bot(c["user"]):
                events.append((c["created_at"], f"Inline comment by {c['user']['login']} on {c['path']}, {c['created_at'][:10]}:\n{cut(c['body'], 1500)}"))
    text = "\n\n".join(head + [e for _, e in sorted(events)])
    return text[:600_000]  # about 150,000 tokens: the longest thread so far (#2424) is about 400,000 characters
