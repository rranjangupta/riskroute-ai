import base64, json, os
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from store import get_store
import agent, pipeline, rules, sim

APP_NAME = os.getenv("APP_NAME", "RiskRoute AI")
PUSH_SA = os.getenv("PUSH_SA")
app = FastAPI(title=APP_NAME)

def S(): return get_store()

def case_or_404(txn):
    c = S().case(txn)
    if not c: raise HTTPException(404, "unknown transaction")
    return c

def view(txn):
    s = S(); c = case_or_404(txn); inv = s.get("investigations", txn)
    if not inv and c.get("tier") == 1 and c["status"] != "NEW":
        inv = agent.investigate(txn, s)          # Tier-1 reports are cheap: recomputed, not stored
    return {"case": c, "inv": inv, "status": c["status"], "audit": s.audit(txn), "ai_second_opinion": s.get("investigations", txn + ":ai")}

@app.get("/api/health")
def health(): return {"ok": True, "app": APP_NAME, "model": agent.MODEL, "mode": pipeline.mode(), "data": "synthetic"}

@app.get("/api/stats")
def stats():
    st = S().stats(); u = S().usage()
    return {**st, "usage": {"ai_investigations": u.get("ai_investigations", 0), "ai_cap": pipeline.AI_CAP,
                            "feed_payments": u.get("feed_payments", 0), "feed_cap": pipeline.FEED_CAP}, "mode": pipeline.mode()}

@app.get("/api/cases")
def cases(view: str = "demo", limit: int = 20, cursor: str | None = None):
    items, nxt = S().list_cases(view, max(1, min(limit, 50)), cursor)
    keep = ("id", "customer_name", "payee_name", "amount", "channel", "at", "rules", "rules_only_decision",
            "status", "route", "tier", "ai_used", "pinned")
    return {"items": [{k: c.get(k) for k in keep} for c in items], "next": nxt}

@app.get("/api/case/{txn}")
def get_case(txn: str): return view(txn)

@app.post("/api/process/{txn}")
def process_one(txn: str):
    c = case_or_404(txn)
    if not c.get("pinned"): raise HTTPException(400, "live-feed payments are processed by the pipeline")
    pipeline.process_pinned(S(), txn); return view(txn)

@app.post("/api/process_all")
def process_all():
    ids = [c["id"] for c in S().list_cases("demo")[0] if c["status"] == "NEW"]
    with ThreadPoolExecutor(5) as ex: list(ex.map(lambda i: pipeline.process_pinned(S(), i), ids))
    return {"processed": len(ids)}

@app.post("/api/feed")
def feed(n: int = 25):
    r = pipeline.feed(S(), max(1, min(n, 50)))
    if "error" in r: raise HTTPException(429, r["error"])
    return r

# ---------- Pub/Sub push endpoints ----------
def _push_payload(req_body: dict, request: Request):
    if PUSH_SA:
        from google.oauth2 import id_token
        from google.auth.transport import requests as greq
        tok = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        try: info = id_token.verify_oauth2_token(tok, greq.Request())
        except Exception: raise HTTPException(401, "invalid push token")
        if info.get("email") != PUSH_SA or not info.get("email_verified"): raise HTTPException(403, "wrong push identity")
    return json.loads(base64.b64decode(req_body["message"]["data"]))

@app.post("/pubsub/payments")
def push_payments(body: dict, request: Request):
    return {"result": pipeline.ingest(S(), _push_payload(body, request))}

@app.post("/pubsub/investigate")
def push_investigate(body: dict, request: Request):
    p = _push_payload(body, request)
    try: return {"result": pipeline.investigate(S(), p["txn_id"])}
    except pipeline.Retry: return Response(status_code=503)   # Pub/Sub redelivers with backoff

# ---------- analyst / customer actions ----------
@app.post("/api/ai_opinion/{txn}")
def ai_opinion(txn: str):
    s = S(); case_or_404(txn); key = txn + ":ai"
    if not s.get("investigations", key):
        if not s.usage_take("ai_investigations", pipeline.AI_CAP): raise HTTPException(429, "Daily AI budget reached")
        r = agent.investigate(txn, s, force_ai=True)
        if r.get("ai_available"): s.put("investigations", key, r)
    return view(txn)

class Action(BaseModel):
    action: str
    note: str = ""

ACTIONS = {"CUSTOMER_CONFIRMED": ({"AWAITING_CUSTOMER"}, "customer (simulated)"),
           "CUSTOMER_DENIED": ({"AWAITING_CUSTOMER"}, "customer (simulated)"),
           "ESCALATE": ({"AWAITING_CUSTOMER"}, "analyst"),
           "REQUEST_VERIFICATION": ({"ANALYST_REVIEW"}, "analyst"),
           "ANALYST_RELEASE": ({"ANALYST_REVIEW"}, "analyst"),
           "ANALYST_BLOCK": ({"ANALYST_REVIEW"}, "analyst")}

@app.post("/api/case/{txn}/action")
def act(txn: str, a: Action):
    s = S(); c = case_or_404(txn); cur = c["status"]
    if a.action not in ACTIONS: raise HTTPException(400, "unknown action")
    allowed, actor = ACTIONS[a.action]
    if cur not in allowed: raise HTTPException(409, f"{a.action} not allowed while case is {cur}")
    r = s.get("investigations", txn) or {}
    contact_changed = any(h["rule"] == "RECENT_CONTACT_CHANGE" for h in r.get("rule_hits", []))
    note = a.note[:300]
    if a.action == "CUSTOMER_CONFIRMED":
        if contact_changed:
            to = "ANALYST_REVIEW"; note = (note + " · " if note else "") + "Registered mobile changed recently: confirmation not trusted, sent to analyst"
        else: to = "RELEASED"; note = note or "Customer confirmed intent; payment released (simulated)"
    elif a.action == "CUSTOMER_DENIED": to = "BLOCKED"; note = note or "Customer did not recognise the payment; blocked and flagged"
    elif a.action in ("ESCALATE",): to = "ANALYST_REVIEW"
    elif a.action == "REQUEST_VERIFICATION": to = "AWAITING_CUSTOMER"
    elif a.action == "ANALYST_RELEASE": to = "RELEASED"
    else: to = "BLOCKED"
    pipeline.set_status(s, c, to)
    pipeline.audit(s, txn, a.action, actor, cur, to, note)
    return view(txn)

@app.get("/api/audit")
def get_audit(txn: str | None = None, limit: int = 300):
    return S().audit(txn=txn.strip().upper() if txn else None, limit=max(1, min(limit, 500)))

@app.post("/api/reset")
def reset():
    n = S().reset()
    return {"ok": True, "deleted": n}

@app.get("/api/simulate")
def simulate(n: int = 2000, anomaly_pct: float = 10):
    return sim.run(max(100, min(n, 10000)), max(0.0, min(anomaly_pct, 50)) / 100)

@app.get("/api/benchmark")
def benchmark():
    s = S(); ids = [c["id"] for c in s.list_cases("demo")[0]]
    def inv(i):
        r = s.get("investigations", i)
        if r: return r
        sc = agent.screen(s.case(i), s)
        allowed = sc["tier1_clear"] or s.usage_take("ai_investigations", pipeline.AI_CAP)
        with pipeline.AI_SEM: r = agent.investigate(i, s, ai_allowed=allowed)
        if r.get("ai_available"): s.put("investigations", i, r)
        return r
    with ThreadPoolExecutor(5) as ex: res = dict(zip(ids, ex.map(inv, ids)))
    rows = []
    for i in ids:
        r = res[i]; g = r.get("grounding") or {}; c = s.case(i)
        rows.append({"id": i, "expected": c["expected"], "rules_only": r["rules_only_decision"], "result": r["final_recommendation"],
                     "route": r.get("route"), "tier": r.get("tier"), "floor": r["mandatory_floor"], "ai_used": r.get("ai_used"),
                     "cited_claims": g.get("supported"), "uncited_removed": g.get("unsupported"), "latency_ms": r["latency_ms"]})
    n = len(rows)
    return {"rows": rows, "agreement": round(sum(x["expected"] == x["result"] for x in rows) / n, 2),
            "rules_only_agreement": round(sum(x["expected"] == x["rules_only"] for x in rows) / n, 2),
            "mandatory_rule_violations": sum(rules.LEVEL[x["result"]] < rules.LEVEL[x["floor"]] for x in rows),
            "gemini_calls": sum(bool(x["ai_used"]) for x in rows),
            "note": "Expected labels were set by the project team on synthetic cases; this is a demonstration, not an accuracy claim."}

static = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static):
    app.mount("/", StaticFiles(directory=static, html=True), name="ui")
