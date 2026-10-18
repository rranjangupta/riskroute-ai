"""Behavioural baseline. At scale the baseline is a STORED profile per customer (one read per payment),
built once from history and updated incrementally whenever a payment is released."""
from datetime import datetime
from statistics import median

RECENT = 60  # amounts kept per profile

def from_history(cid, hist):
    hist = sorted(hist, key=lambda h: h["at"])
    amts = [h["amount"] for h in hist][-RECENT:]
    hrs = [datetime.fromisoformat(h["at"]).hour for h in hist]
    by_payee = {}
    for h in hist: by_payee.setdefault(h["beneficiary_id"], []).append(h["amount"])
    return finish({"id": f"BASELINE:{cid}", "customer_id": cid, "txn_count": len(hist), "recent_amounts": amts,
                   "known_payees": sorted(by_payee), "known_devices": sorted({h["device_id"] for h in hist}),
                   "usual_hours": [min(hrs), max(hrs)] if hrs else None,
                   "payee_counts": {b: len(v) for b, v in by_payee.items()},
                   "payee_typical": {b: int(median(v)) for b, v in by_payee.items()}})

def finish(p):
    a = p["recent_amounts"]
    p["median_amount"] = int(median(a)) if a else 0
    p["max_amount"] = max(a) if a else 0
    return p

def learn(p, case):
    """Incremental update after a payment is released (no history scan)."""
    p = dict(p); b = case["beneficiary_id"]
    p["recent_amounts"] = (list(p["recent_amounts"]) + [case["amount"]])[-RECENT:]
    p["txn_count"] = p.get("txn_count", 0) + 1
    p["known_payees"] = sorted(set(p["known_payees"]) | {b})
    p["known_devices"] = sorted(set(p["known_devices"]) | {case["device_id"]})
    cnt = dict(p.get("payee_counts", {})); n = cnt.get(b, 0); cnt[b] = n + 1; p["payee_counts"] = cnt
    typ = dict(p.get("payee_typical", {})); typ[b] = int((typ.get(b, case["amount"]) * n + case["amount"]) / (n + 1)); p["payee_typical"] = typ
    return finish(p)

def compute(cid, store):
    return store.profile(cid)

def public(p):
    """Compact view for the AI tool / UI."""
    return {k: v for k, v in p.items() if k != "recent_amounts"}

def deviations(case, base):
    out = []; ev = f"BASELINE:{case['customer_id']}"; ben = case["beneficiary_id"]
    ref = base.get("payee_typical", {}).get(ben) or base["median_amount"]
    label = "this payee's usual" if ben in base.get("payee_typical", {}) else "customer median"
    ratio = case["amount"] / ref if ref else None
    if ratio and ratio >= 3:
        out.append({"signal": "AMOUNT_ABOVE_USUAL", "detail": f"{ratio:.1f}x {label} (₹{ref:,.0f})", "evidence": [ev]})
    if base["max_amount"] and case["amount"] > base["max_amount"] * 1.2:
        out.append({"signal": "ABOVE_HISTORICAL_MAX", "detail": f"Above max ₹{base['max_amount']:,}", "evidence": [ev]})
    if ben not in base["known_payees"]:
        out.append({"signal": "UNSEEN_PAYEE", "detail": "No previous payments to this payee", "evidence": [ev, ben]})
    if case["device_id"] not in base["known_devices"]:
        out.append({"signal": "UNSEEN_DEVICE", "detail": "Device not used in previous payments", "evidence": [ev, case["device_id"]]})
    h = datetime.fromisoformat(case["at"]).hour
    if base["usual_hours"] and not (base["usual_hours"][0] <= h <= base["usual_hours"][1]):
        out.append({"signal": "UNUSUAL_HOUR", "detail": f"Hour {h} outside usual {base['usual_hours']}", "evidence": [ev]})
    ok = [] if out else [{"signal": "CONSISTENT_WITH_HISTORY", "detail": "Known payee, known device, amount within usual range", "evidence": [ev]}]
    return out, ok
