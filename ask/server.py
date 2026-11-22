"""The MCP server tab's question box: answers plain-English questions about FOCUS with Claude Haiku 5.5, which looks
things up with the FinOps Foundation's FOCUS MCP server.

  GET  /api/ask/status   {"enabled": true or false, "model": ...}
  POST /api/ask          {"question": "...", "history": [{"q": "...", "a": "..."}, ...]}
                         -> {"answer", "tools", "cost", "model_calls", "seconds", "stop"}

"history" is the conversation so far, oldest first, sent by the page so that follow-up questions make sense. The last six
questions and answers are used, and older ones are dropped while they come to more than 12,000 characters.

In the lab's container this listens on 127.0.0.1:8081 and nginx passes /api/ to it (see ../Dockerfile and ../nginx.conf).
The API key comes from the ANTHROPIC_API_KEY environment variable, set in Coolify. With none set, /api/ask/status says
the box is off and the page keeps it hidden.

Limits, each an environment variable:
  ASK_QUESTION_CAP_USD  1.00  spend on one question, at list price. Checked before each model call, so the call that
                              crosses it still finishes.
  ASK_DAILY_CAP_USD     1.00  spend per UTC day across all visitors. New questions are refused once it is reached.
  ASK_PER_HOUR          20    questions per visitor (IP address) per hour.
  ASK_MAX_AT_ONCE       3     questions answered at the same time.
The spend counts are kept in memory, so they start again from zero when the container restarts (every deploy). The
spend limit on the API key's workspace in the Anthropic Console is the backstop.

Tool replies are kept for a day and reused, because the FOCUS server limits how often one address can call it and its
content changes rarely. Replies that were errors are not kept.

Questions are not stored or logged. The log has one line per question: its cost, model and tool call counts and time.

For local testing, --dev also serves ../site, so the page and the box run from one address:
  ANTHROPIC_API_KEY=... python ask/server.py --dev [--port 8648]
"""
import json
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MCP_URL = "https://focus.finops.org/wp-json/focus/v1/mcp"
MODEL = "claude-haiku-5-5"
EFFORT = "low"
MAX_TOKENS = 8000  # Haiku 5.5 always thinks; the thinking is billed as output and needs room here
PRICE = ((0.10, 0.50), (0.50, 2.50))  # US$ per million input and output tokens: prompts up to 100,000 tokens, then above
QUESTION_CAP = float(os.environ.get("ASK_QUESTION_CAP_USD", "1.00"))
DAILY_CAP = float(os.environ.get("ASK_DAILY_CAP_USD", "1.00"))
PER_HOUR = int(os.environ.get("ASK_PER_HOUR", "20"))
MAX_AT_ONCE = int(os.environ.get("ASK_MAX_AT_ONCE", "3"))
MAX_CALLS = 15  # model calls per question
MAX_SECONDS = 120  # per question
MAX_QUESTION = 500  # characters
MAX_HISTORY_TURNS = 6
MAX_HISTORY_CHARS = 12_000
CACHE_SECONDS = 24 * 3600
CACHE_MAX = 2000

SYSTEM = """You answer questions about FOCUS, the FinOps Open Cost and Usage Specification, in a question box on the FOCUS SQL Lab, an independent website about FOCUS.

Your tools read the FinOps Foundation's FOCUS website, focus.finops.org, and the specification published there; they don't read the lab. Answer only from what the tools return. If they don't cover the question, say so plainly instead of answering from memory. Say which FOCUS release your answer comes from. When you point to a query, use case or page, say it is on the FOCUS website and give its URL from the tool reply. Quote SQL and requirement text exactly as the tools return it. Keep answers short: a few sentences or a short list, naming the columns, attributes, queries or pages you used.

If a question isn't about FOCUS or cloud and SaaS cost data, say you can only answer questions about FOCUS, and don't call any tools.

The server's own guidance on its tools:

"""

lock = threading.Lock()
cache = {}  # (tool name, arguments as sorted JSON) -> (time, text)
spend = {"day": None, "usd": 0.0}
asked = {}  # visitor address -> times of their questions in the last hour
at_once = threading.BoundedSemaphore(MAX_AT_ONCE)
server = {"at": 0, "tools": None, "instructions": ""}
client = None


def mcp(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method, **({"params": params} if params is not None else {})}
    req = urllib.request.Request(MCP_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                                          "User-Agent": "focus-sql-lab"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def tools():
    """The server's tools, as Claude tool definitions, and its instructions. Fetched again after an hour."""
    with lock:
        if server["tools"] and time.time() - server["at"] < 3600:
            return server["tools"], server["instructions"]
    init = mcp("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "focus-sql-lab", "version": "1"}})["result"]
    defs = []
    for t in mcp("tools/list", rid=2)["result"]["tools"]:
        schema = dict(t.get("inputSchema") or {})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        defs.append({"name": t["name"], "description": t.get("description", ""), "input_schema": schema})
    with lock:
        server.update(at=time.time(), tools=defs, instructions=init.get("instructions", ""))
    return defs, server["instructions"]


def run_tool(name, args):
    """The tool's text, whether the server marked it an error, and whether it came from the cache."""
    key = (name, json.dumps(args, sort_keys=True))
    with lock:
        hit = cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1], False, True
    try:
        res = mcp("tools/call", {"name": name, "arguments": args}, rid=3)
    except Exception as e:
        return f"Could not reach the FOCUS MCP server: {type(e).__name__}", True, False
    if "error" in res:
        return f"JSON-RPC error {res['error'].get('code')}: {res['error'].get('message')}", True, False
    r = res.get("result", {})
    text = "\n".join(c.get("text", json.dumps(c)) for c in r.get("content", []))
    err = bool(r.get("isError"))
    if not err:
        with lock:
            if len(cache) >= CACHE_MAX:
                del cache[min(cache, key=lambda k: cache[k][0])]
            cache[key] = (time.time(), text)
    return text, err, False


def call_cost(usage):
    prompt = usage.input_tokens + (usage.cache_creation_input_tokens or 0) + (usage.cache_read_input_tokens or 0)
    pin, pout = PRICE[prompt > 100_000]
    return usage.input_tokens * pin / 1e6 + usage.output_tokens * pout / 1e6


def today_spent():
    """US$ spent today (UTC). Call with the lock held."""
    day = datetime.now(timezone.utc).date()
    if spend["day"] != day:
        spend.update(day=day, usd=0.0)
    return spend["usd"]


def history_messages(history):
    """The conversation so far as alternating user and assistant messages, or None if it isn't well formed."""
    if not isinstance(history, list):
        return None
    pairs = []
    for h in history[-MAX_HISTORY_TURNS:]:
        if not isinstance(h, dict) or not isinstance(h.get("q"), str) or not isinstance(h.get("a"), str):
            return None
        q, a = h["q"].strip()[:MAX_QUESTION], h["a"].strip()
        if q and a:
            pairs.append((q, a))
    while pairs and sum(len(q) + len(a) for q, a in pairs) > MAX_HISTORY_CHARS:
        pairs.pop(0)
    return [m for q, a in pairs for m in ({"role": "user", "content": q}, {"role": "assistant", "content": a})]


def ask(question, earlier=()):
    import anthropic
    defs, instructions = tools()
    messages = [*earlier, {"role": "user", "content": question}]
    spent, calls, log, answer, stop, t0 = 0.0, 0, [], "", None, time.time()
    while True:
        if spent >= QUESTION_CAP:
            stop = "question_cap"
            break
        if calls >= MAX_CALLS:
            stop = "call_limit"
            break
        if time.time() - t0 > MAX_SECONDS:
            stop = "time_limit"
            break
        try:
            r = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM + instructions, tools=defs,
                                       messages=messages, thinking={"type": "adaptive"}, output_config={"effort": EFFORT})
        except anthropic.APIStatusError as e:
            stop = f"api_error_{e.status_code}"
            break
        except anthropic.APIConnectionError:
            stop = "api_connection_error"
            break
        calls += 1
        cost = call_cost(r.usage)
        spent += cost
        with lock:
            today_spent()
            spend["usd"] += cost
        text = "".join(b.text for b in r.content if b.type == "text").strip()
        if text:
            answer = text
        if r.stop_reason != "tool_use":
            stop = r.stop_reason
            break
        messages.append({"role": "assistant", "content": r.content})
        results = []
        for b in r.content:
            if b.type != "tool_use":
                continue
            out, err, cached = run_tool(b.name, b.input)
            log.append({"name": b.name, "input": b.input, "chars": len(out), "is_error": err, "cached": cached})
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": out, **({"is_error": True} if err else {})})
        messages.append({"role": "user", "content": results})
    return {"answer": answer, "stop": stop, "model": MODEL, "model_calls": calls, "tools": log,
            "cost": round(spent, 5), "seconds": round(time.time() - t0, 1)}


class Handler(SimpleHTTPRequestHandler):
    dev = False

    def log_message(self, fmt, *args):  # no request log: it would hold visitors' addresses
        pass

    def send_json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def visitor(self):
        # Behind Coolify's proxy and nginx, the last X-Forwarded-For entry is the address the proxy saw.
        fwd = [a.strip() for a in self.headers.get("X-Forwarded-For", "").split(",") if a.strip()]
        return fwd[-1] if fwd else self.client_address[0]

    def do_GET(self):
        if self.path.split("?")[0] == "/api/ask/status":
            return self.send_json(200, {"enabled": client is not None, "model": MODEL, "effort": EFFORT})
        if self.dev and not self.path.startswith("/api/"):
            return super().do_GET()
        self.send_json(404, {"error": "Not found."})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/ask":
            return self.send_json(404, {"error": "Not found."})
        if client is None:
            return self.send_json(503, {"error": "The question box isn't switched on here."})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(min(size, 65536)) or b"{}")
            question = str(body.get("question", "")).strip()
            earlier = history_messages(body.get("history") or [])
        except (ValueError, AttributeError):
            return self.send_json(400, {"error": "The request wasn't understood."})
        if earlier is None:
            return self.send_json(400, {"error": "The request wasn't understood."})
        if not question:
            return self.send_json(400, {"error": "Type a question first."})
        if len(question) > MAX_QUESTION:
            return self.send_json(400, {"error": f"Questions can be up to {MAX_QUESTION} characters."})
        now, who = time.time(), self.visitor()
        with lock:
            if today_spent() >= DAILY_CAP:
                return self.send_json(429, {"error": "The lab's allowance for questions today is used up. It starts again at midnight UTC."})
            mine = [t for t in asked.get(who, []) if now - t < 3600]
            if len(mine) >= PER_HOUR:
                return self.send_json(429, {"error": f"That's {PER_HOUR} questions in the last hour, the most the lab takes from one visitor. Try again later."})
            asked[who] = mine + [now]
            for k in [k for k, v in asked.items() if not any(now - t < 3600 for t in v)]:
                del asked[k]
        if not at_once.acquire(blocking=False):
            return self.send_json(503, {"error": "The lab is answering other questions just now. Try again in a minute."})
        try:
            result = ask(question, earlier)
        except Exception as e:
            print(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "error": type(e).__name__}), flush=True)
            return self.send_json(502, {"error": "Something went wrong answering that. Try again in a minute."})
        finally:
            at_once.release()
        print(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "earlier": len(earlier) // 2,
                          "cost": result["cost"],
                          "model_calls": result["model_calls"], "tool_calls": len(result["tools"]),
                          "cached": sum(t["cached"] for t in result["tools"]), "stop": result["stop"],
                          "seconds": result["seconds"]}), flush=True)
        self.send_json(200, result)


def main():
    global client
    flags = sys.argv[1:]
    port = int(flags[flags.index("--port") + 1]) if "--port" in flags else (8648 if "--dev" in flags else 8081)
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        client = anthropic.Anthropic(timeout=60, max_retries=2)
    Handler.dev = "--dev" in flags
    host = "127.0.0.1"
    site = Path(__file__).resolve().parent.parent / "site"
    httpd = ThreadingHTTPServer((host, port), lambda *a: Handler(*a, directory=str(site)))
    print(f"Question box {'on' if client else 'off (no ANTHROPIC_API_KEY)'}; listening on {host}:{port}"
          + (f", serving {site}" if Handler.dev else ""), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
