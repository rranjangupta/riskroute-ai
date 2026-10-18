"""Static, deterministic bank-policy rules: source of truth for mandatory controls."""
from datetime import datetime
LEVEL = {"PROCEED": 0, "VERIFY": 1, "HUMAN_REVIEW": 2}

def fmt(h): return f"{h*60:.0f} min" if h < 1 else f"{h:.1f} h"
def _h(a, b): return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()) / 3600

def evaluate(case, store):
    p = store.policy(); hits = []
    ben = store.get("beneficiaries", case["beneficiary_id"]); dev = store.get("devices", case["device_id"])
    if ben and _h(case["at"], ben["added_at"]) < p["new_beneficiary_hours"]:
        hits.append({"rule": "NEW_BENEFICIARY", "hard": True, "min_action": "VERIFY",
                     "detail": f"Payee added {fmt(_h(case['at'], ben['added_at']))} before payment", "evidence": [case["beneficiary_id"]]})
    if dev and _h(case["at"], dev["first_seen"]) < p["new_device_hours"]:
        hits.append({"rule": "NEW_DEVICE", "hard": False, "min_action": "VERIFY",
                     "detail": f"Device first seen {fmt(_h(case['at'], dev['first_seen']))} before payment", "evidence": [case["device_id"]]})
    for e in store.events(case["customer_id"]):
        if e["type"] in ("MOBILE_NUMBER_CHANGED", "EMAIL_CHANGED") and _h(case["at"], e["at"]) < p["contact_change_hours"]:
            hits.append({"rule": "RECENT_CONTACT_CHANGE", "hard": True, "min_action": "HUMAN_REVIEW",
                         "detail": f"{e['type'].replace('_', ' ').capitalize()} {fmt(_h(case['at'], e['at']))} before payment", "evidence": [e["id"]]})
    if case["amount"] >= p["high_value_amount"]:
        hits.append({"rule": "HIGH_VALUE", "hard": False, "min_action": "VERIFY",
                     "detail": f"Amount ₹{case['amount']:,} ≥ threshold ₹{p['high_value_amount']:,}", "evidence": [case["id"]]})
    return hits

def mandatory_floor(hits):
    return max((h["min_action"] for h in hits if h["hard"]), key=LEVEL.get, default="PROCEED")

def rules_only_decision(hits):
    return max((h["min_action"] for h in hits), key=LEVEL.get, default="PROCEED")
