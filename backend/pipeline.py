"""Event-driven pipeline.
  payments-in (Pub/Sub) -> ingest(): Tier 1 screen, auto-clear or mark INVESTIGATING
  flagged-payments (Pub/Sub) -> investigate(): Gemini agent, route, audit
Without PUBSUB_* env vars the same functions run on a local thread pool (dev/tests)."""
import json, os, random, threading, time
from concurrent.futures import ThreadPoolExecutor
import agent, baseline, rules
from store import qkey, now_iso

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
T_PAYMENTS = os.getenv("PUBSUB_PAYMENTS_TOPIC")
T_FLAGGED = os.getenv("PUBSUB_FLAGGED_TOPIC")
AI_CAP = int(os.getenv("AI_DAILY_CAP", "150"))
FEED_CAP = int(os.getenv("FEED_DAILY_CAP", "1000"))
AI_SEM = threading.BoundedSemaphore(int(os.getenv("MAX_PARALLEL_AI", "6")))
INITIAL = {"AUTO_CLEAR": "RELEASED", "CUSTOMER_VERIFY": "AWAITING_CUSTOMER", "ANALYST_REVIEW": "ANALYST_REVIEW"}
ROUTE_MSG = {"AUTO_CLEAR": "Auto-cleared by policy — payment released (simulated)",
             "CUSTOMER_VERIFY": "Verification request sent to customer's registered channel (simulated)",
             "ANALYST_REVIEW": "Routed to analyst queue with AI investigation attached"}
_pool = ThreadPoolExecutor(8)
_publisher = None
_dataset = None

class Retry(Exception): pass

def mode(): return "pubsub" if T_PAYMENTS and T_FLAGGED else "local"

def dataset():
    global _dataset
    if _dataset is None:
        import generate_data as G
        _dataset = G.build(int(os.getenv("N_CUSTOMERS", "200")))
    return _dataset

def publish(topic, payloads):
    global _publisher
    from google.cloud import pubsub_v1
    if _publisher is None: _publisher = pubsub_v1.PublisherClient()
    path = _publisher.topic_path(PROJECT, topic)
    futs = [_publisher.publish(path, json.dumps(p).encode()) for p in payloads]
    return [f.result(timeout=30) for f in futs]

def audit(s, txn, action, actor, frm, to, note=""):
    s.add_audit({"at": now_iso(), "txn": txn, "action": action, "actor": actor, "from": frm, "to": to, "note": note})

def set_status(s, case, to, extra=None):
    f = {"status": to, "qkey": qkey(to, case), "updated_at": now_iso(), **(extra or {})}
    s.update("cases", case["id"], f); case.update(f)
    if to == "RELEASED":  # incremental behaviour learning
        p = s.profile(case["customer_id"])
        if p: s.save_profile(case["customer_id"], baseline.learn(p, case))

# ---------- live feed ----------
def feed(s, n):
    import generate_data as G
    if not s.usage_take("feed_payments", FEED_CAP, n): return {"error": f"Daily feed cap of {FEED_CAP} payments reached"}
    ds, rnd = dataset(), random.Random()
    cids = [c for c, v in ds["customers"].items() if v.get("generated")]
    pays = []
    for _ in range(n):
        cid = rnd.choice(cids)
        pays.append(G.make_payment(rnd, cid, ds["customers"][cid], ds["profiles"][cid], s.put))
    if mode() == "pubsub": publish(T_PAYMENTS, pays)
    else:
        for p in pays: _pool.submit(_local_ingest, s, p)
    return {"published": n, "mode": mode()}

def _local_ingest(s, p):
    try:
        if ingest(s, p) == "flagged": _pool.submit(_local_investigate, s, p["id"])
    except Exception as e: print("ingest error", e)

def _local_investigate(s, txn):
    for _ in range(3):
        try: investigate(s, txn); return
        except Retry: time.sleep(5)

# ---------- Tier 1 ----------
def ingest(s, p):
    if s.case(p["id"]): return "duplicate"
    doc = {k: p[k] for k in ("id", "customer_id", "beneficiary_id", "device_id", "amount", "at", "channel")}
    doc.update(source="feed", pinned=False, received_at=now_iso(),
               customer_name=(s.get("customers", p["customer_id"]) or {}).get("name", p["customer_id"]),
               payee_name=(s.get("beneficiaries", p["beneficiary_id"]) or {}).get("name", p["beneficiary_id"]))
    sc = agent.screen(doc, s)
    doc.update(rules=[h["rule"] for h in sc["hits"]], rules_only_decision=rules.rules_only_decision(sc["hits"]))
    if sc["tier1_clear"]:
        doc.update(status="RELEASED", route="AUTO_CLEAR", tier=1, ai_used=False)
    else:
        doc.update(status="INVESTIGATING", route=None, tier=2, ai_used=None, ai_attempts=0)
    doc["qkey"] = qkey(doc["status"], doc)
    if not s.create("cases", doc["id"], doc): return "duplicate"
    if sc["tier1_clear"]:
        s.save_profile(doc["customer_id"], baseline.learn(sc["base"], doc))
        audit(s, doc["id"], "SCREENED_AND_ROUTED", "system", "NEW", "RELEASED", "Tier 1 · " + ROUTE_MSG["AUTO_CLEAR"])
        return "cleared"
    audit(s, doc["id"], "SCREENED_AND_QUEUED", "system", "NEW", "INVESTIGATING",
          "Tier 1 flagged: " + ", ".join(doc["rules"] + [d["signal"] for d in sc["devs"]]) + " · queued for Gemini")
    if mode() == "pubsub": publish(T_FLAGGED, [{"txn_id": doc["id"]}])
    return "flagged"

# ---------- Tier 2 ----------
def investigate(s, txn):
    if not s.claim(txn): return "skipped"
    if not AI_SEM.acquire(timeout=240):
        s.update("cases", txn, {"claimed_at": 0}); raise Retry("busy")
    try:
        allowed = s.usage_take("ai_investigations", AI_CAP)
        r = agent.investigate(txn, s, ai_allowed=allowed)
    finally:
        AI_SEM.release()
    case = s.case(txn)
    if not r["ai_available"] and agent.retryable(r) and case.get("ai_attempts", 0) < 2:
        s.update("cases", txn, {"ai_attempts": case.get("ai_attempts", 0) + 1, "claimed_at": 0}); raise Retry(r["error"])
    finalize(s, case, r)
    return r["route"]

def finalize(s, case, r):
    s.put("investigations", case["id"], r)
    to = INITIAL[r["route"]]; frm = case["status"]
    set_status(s, case, to, {"route": r["route"], "tier": r["tier"], "ai_used": r["ai_used"]})
    audit(s, case["id"], "INVESTIGATED_AND_ROUTED" if r["ai_used"] else "SCREENED_AND_ROUTED", "system", frm, to,
          f"Tier {r['tier']} · {ROUTE_MSG[r['route']]}" + (f" · {r['guard_note']}" if r.get("guard_note") else ""))

def process_pinned(s, txn):
    """Synchronous path used by the demo's 'Screen payment' button."""
    case = s.case(txn)
    if case["status"] != "NEW": return
    r = s.get("investigations", txn)
    if not r:
        sc = agent.screen(case, s)
        allowed = sc["tier1_clear"] or s.usage_take("ai_investigations", AI_CAP)
        with AI_SEM: r = agent.investigate(txn, s, ai_allowed=allowed)
    finalize(s, case, r)
