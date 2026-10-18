"""Synthetic dataset at scale: the 5 pinned demo cases + N generated customers with history,
devices, payees and stored behaviour profiles. Also generates live incoming payments for the feed.
ALL DATA IS FICTIONAL. Deterministic for a given seed."""
import random, uuid
from datetime import timedelta
import data as D, rules, baseline

FIRST = ["Aarav", "Diya", "Kabir", "Ananya", "Vihaan", "Isha", "Arjun", "Meera", "Rohan", "Sara", "Karan", "Nisha",
         "Dev", "Priya", "Aditya", "Kavya", "Rahul", "Sneha", "Vikram", "Pooja"]
LAST = ["Sharma", "Iyer", "Reddy", "Nair", "Gupta", "Das", "Patel", "Rao", "Menon", "Singh", "Joshi", "Kulkarni"]
BIZ = ["Traders", "Textiles", "Foods", "Electricals", "Pharma", "Hardware", "Logistics", "Agro", "Prints", "Motors"]
SEGMENTS = {  # payee templates: (name, low, high, every_days)
    "salaried": [("Landlord", 15000, 45000, 30), ("Electricity Board", 800, 4000, 30), ("Savings - own", 5000, 40000, 15),
                 ("Family", 3000, 20000, 20), ("Credit card bill", 5000, 60000, 30)],
    "business": [("Raw Material Supplier", 150000, 800000, 15), ("Packaging Vendor", 30000, 150000, 20),
                 ("Logistics Partner", 20000, 90000, 10), ("Payroll Account", 200000, 900000, 30), ("GST Payment", 20000, 200000, 30)],
    "retired": [("Family", 5000, 30000, 30), ("Pharmacy", 1000, 6000, 15), ("Society Maintenance", 2000, 6000, 30)],
    "student": [("Hostel", 8000, 15000, 30), ("Mobile Recharge", 199, 999, 28), ("Friend", 200, 3000, 10)],
}
CHANNELS = ["Mobile App", "Internet Banking", "UPI"]

def _name(rnd, seg):
    if seg == "business": return f"{rnd.choice(LAST)} {rnd.choice(BIZ)} (MSME)"
    return f"{rnd.choice(FIRST)} {rnd.choice(LAST)[0]}"

def build(n_customers=200, seed=2026):
    rnd = random.Random(seed)
    out = {"customers": {k: dict(v) for k, v in D.CUSTOMERS.items()},
           "beneficiaries": {k: dict(v) for k, v in D.BENEFICIARIES.items()},
           "devices": {k: dict(v) for k, v in D.DEVICES.items()},
           "events": [dict(e) for e in D.ACCOUNT_EVENTS], "history": [dict(h) for h in D.HISTORY]}
    h_id = len(out["history"])
    for i in range(n_customers):
        cid = f"CUST-{2000 + i}"
        seg = rnd.choices(list(SEGMENTS), weights=[45, 25, 20, 10])[0]
        out["customers"][cid] = {"name": _name(rnd, seg), "segment": seg, "generated": True}
        devs = [f"DEV-{cid[5:]}-{k}" for k in range(rnd.randint(1, 2))]
        for d in devs:
            out["devices"][d] = {"customer_id": cid, "label": rnd.choice(["Android phone", "iPhone", "Laptop browser"]),
                                 "first_seen": D.ts(days=rnd.randint(90, 1500))}
        h0, h1 = rnd.randint(7, 11), rnd.randint(18, 22)
        for j, (pname, lo, hi, every) in enumerate(rnd.sample(SEGMENTS[seg], rnd.randint(2, len(SEGMENTS[seg])))):
            ben = f"BEN-{cid[5:]}-{j}"
            out["beneficiaries"][ben] = {"customer_id": cid, "name": pname, "added_at": D.ts(days=rnd.randint(200, 1200))}
            typ = rnd.randrange(lo, hi)
            for k in range(1, rnd.randint(4, 9)):
                h_id += 1
                at = (D.NOW - timedelta(days=every * k + rnd.randint(0, 3))).replace(hour=rnd.randint(h0, h1), minute=rnd.randint(0, 59))
                out["history"].append({"id": f"HTX-{h_id:05d}", "customer_id": cid, "beneficiary_id": ben,
                                       "device_id": rnd.choice(devs), "amount": int(typ * rnd.uniform(0.85, 1.15)),
                                       "at": at.isoformat(), "status": "COMPLETED"})
    by_c = {}
    for h in out["history"]: by_c.setdefault(h["customer_id"], []).append(h)
    out["profiles"] = {cid: baseline.from_history(cid, by_c.get(cid, [])) for cid in out["customers"]}
    return out

def make_payment(rnd, cid, cust, prof, store_put, anomaly_rate=0.10, kind=None):
    """One synthetic incoming payment. New payees/devices/contact changes are written via store_put
    (as core banking would) before the payment event is published."""
    tag = uuid.uuid4().hex[:6].upper()
    h0, h1 = prof["usual_hours"] or [9, 19]
    at = D.NOW.replace(hour=rnd.randint(h0, h1), minute=rnd.randint(0, 59))
    ben = rnd.choice(prof["known_payees"]); dev = rnd.choice(prof["known_devices"])
    amt = int(prof["payee_typical"].get(ben, prof["median_amount"]) * rnd.uniform(0.8, 1.1))
    if kind is None and rnd.random() < anomaly_rate:
        kind = rnd.choice(["new_payee", "new_device", "high_amount", "takeover"])
    side = []
    if kind in ("new_payee", "takeover"):
        ben = f"BEN-F{tag}"
        side.append(("beneficiaries", ben, {"customer_id": cid, "name": rnd.choice(["Quick Refund Desk", "R K Enterprises",
                     "Crypto Wallet Svc", "New Vendor LLP", "Prize Claims Ltd"]), "added_at": (at - timedelta(minutes=rnd.randint(3, 600))).isoformat(), "source": "feed"}))
        amt = int(prof["median_amount"] * rnd.uniform(0.5, 2.5))
    if kind in ("new_device", "takeover"):
        dev = f"DEV-F{tag}"
        side.append(("devices", dev, {"customer_id": cid, "label": "Unknown browser", "first_seen": (at - timedelta(minutes=rnd.randint(5, 300))).isoformat(), "source": "feed"}))
    if kind in ("high_amount", "takeover"):
        amt = int(prof["median_amount"] * rnd.uniform(4, 12))
    if kind == "takeover":
        side.append(("account_events", f"EVT-F{tag}", {"id": f"EVT-F{tag}", "customer_id": cid, "type": "MOBILE_NUMBER_CHANGED",
                     "at": (at - timedelta(hours=rnd.randint(1, 20))).isoformat(), "source": "feed"}))
    for coll, i, doc in side: store_put(coll, i, doc)
    return {"id": f"TXN-F{tag}", "customer_id": cid, "beneficiary_id": ben, "device_id": dev, "amount": max(amt, 100),
            "at": at.isoformat(), "channel": rnd.choice(CHANNELS), "injected": kind or "none"}
