"""Data access. FirestoreStore (USE_FIRESTORE=1) or MemoryStore (local dev, tests, simulation).
Designed for scale: point reads per payment, stored profiles, paginated queue, aggregation counts."""
import os, time, threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import data as D

STATUSES = ["NEW", "INVESTIGATING", "AWAITING_CUSTOMER", "ANALYST_REVIEW", "RELEASED", "BLOCKED"]
ROUTES = ["AUTO_CLEAR", "CUSTOMER_VERIFY", "ANALYST_REVIEW"]
END = "\uf8ff"

def now_iso(): return datetime.now(timezone.utc).isoformat()
def qkey(status, case): return f"{status}#{case.get('received_at', '')}#{case['id']}"

class MemoryStore:
    def __init__(self, dataset=None):
        import generate_data as G
        self.ds = dataset or G.build(int(os.getenv("N_CUSTOMERS", "200")))
        self.c = {"customers": self.ds["customers"], "beneficiaries": dict(self.ds["beneficiaries"]),
                  "devices": dict(self.ds["devices"]), "profiles": dict(self.ds["profiles"]),
                  "account_events": {e["id"]: e for e in self.ds["events"]}, "cases": {}, "investigations": {}}
        self._events_by_c = {}
        for e in self.c["account_events"].values(): self._events_by_c.setdefault(e["customer_id"], []).append(e)
        self._dev_by_c = {}
        for k, v in self.c["devices"].items(): self._dev_by_c.setdefault(v["customer_id"], []).append({"id": k, **v})
        self._audit, self._usage, self._lock = [], {}, threading.Lock()
        for c in seed_cases(self): self.c["cases"][c["id"]] = c
    def policy(self): return D.POLICY
    def get(self, coll, i):
        v = self.c[coll].get(i); return dict(v) if isinstance(v, dict) else v
    def put(self, coll, i, doc):
        self.c[coll][i] = dict(doc)
        if coll == "account_events": self._events_by_c.setdefault(doc["customer_id"], []).append(doc)
        if coll == "devices": self._dev_by_c.setdefault(doc["customer_id"], []).append({"id": i, **doc})
    def update(self, coll, i, fields): self.c[coll].setdefault(i, {}).update(fields)
    def create(self, coll, i, doc):
        with self._lock:
            if i in self.c[coll]: return False
            self.c[coll][i] = dict(doc); return True
    def case(self, i): return self.get("cases", i)
    def events(self, cid): return list(self._events_by_c.get(cid, []))
    def devices(self, cid): return list(self._dev_by_c.get(cid, []))
    def profile(self, cid): return self.c["profiles"].get(cid)
    def save_profile(self, cid, p): self.c["profiles"][cid] = p
    def list_cases(self, view="demo", limit=20, cursor=None):
        cs = list(self.c["cases"].values())
        if view == "demo": return sorted([c for c in cs if c.get("pinned")], key=lambda c: c["id"]), None
        key = "received_at" if view == "all" else "qkey"
        if view != "all": cs = [c for c in cs if c["status"] == view]
        cs.sort(key=lambda c: c[key], reverse=True)
        if cursor: cs = [c for c in cs if c[key] < cursor]
        page = cs[:limit]
        return page, (page[-1][key] if len(cs) > limit else None)
    def stats(self):
        cs = list(self.c["cases"].values())
        return {"total": len(cs), "status": {s: sum(c["status"] == s for c in cs) for s in STATUSES},
                "route": {r: sum(c.get("route") == r for c in cs) for r in ROUTES},
                "tier1": sum(c.get("tier") == 1 for c in cs), "ai": sum(bool(c.get("ai_used")) for c in cs)}
    def add_audit(self, e): self._audit.append(e)
    def audit(self, txn=None, limit=100):
        return [a for a in reversed(self._audit) if not txn or a["txn"] == txn][:limit]
    def claim(self, txn_id, secs=300):
        with self._lock:
            c = self.c["cases"].get(txn_id)
            if not c or c["status"] != "INVESTIGATING" or time.time() - c.get("claimed_at", 0) < secs: return False
            c["claimed_at"] = time.time(); return True
    def usage_take(self, field, cap, n=1):
        with self._lock:
            k = (datetime.now(timezone.utc).strftime("%Y%m%d"), field)
            if self._usage.get(k, 0) + n > cap: return False
            self._usage[k] = self._usage.get(k, 0) + n; return True
    def usage(self):
        d = datetime.now(timezone.utc).strftime("%Y%m%d")
        return {f: v for (day, f), v in self._usage.items() if day == d}
    def reset(self):
        n = len(self.c["cases"]) - 5 + len(self._audit)
        for coll in ("beneficiaries", "devices", "account_events"):
            for k in [k for k, v in self.c[coll].items() if v.get("source") == "feed"]: del self.c[coll][k]
        self._events_by_c = {}
        for e in self.c["account_events"].values(): self._events_by_c.setdefault(e["customer_id"], []).append(e)
        self._dev_by_c = {}
        for k, v in self.c["devices"].items(): self._dev_by_c.setdefault(v["customer_id"], []).append({"id": k, **v})
        self.c["investigations"] = {k: v for k, v in self.c["investigations"].items() if v.get("source") == "demo" and v.get("ai_available")}
        self.c["profiles"] = dict(self.ds["profiles"]); self._audit.clear()
        self.c["cases"] = {c["id"]: c for c in seed_cases(self)}
        return n

class FirestoreStore:
    def __init__(self):
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter
        self.fs, self.FF = firestore, FieldFilter
        self.db = firestore.Client()
        self._policy = None
    def policy(self):
        if self._policy is None: self._policy = self.get("config", "policy") or D.POLICY
        return self._policy
    def get(self, coll, i):
        s = self.db.collection(coll).document(i).get()
        return s.to_dict() if s.exists else None
    def put(self, coll, i, doc): self.db.collection(coll).document(i).set(doc)
    def update(self, coll, i, fields): self.db.collection(coll).document(i).set(fields, merge=True)
    def create(self, coll, i, doc):
        from google.api_core.exceptions import AlreadyExists
        try: self.db.collection(coll).document(i).create(doc); return True
        except AlreadyExists: return False
    def _where(self, coll, f, v): return self.db.collection(coll).where(filter=self.FF(f, "==", v))
    def case(self, i): return self.get("cases", i)
    def events(self, cid): return [d.to_dict() for d in self._where("account_events", "customer_id", cid).stream()]
    def devices(self, cid): return [{"id": d.id, **d.to_dict()} for d in self._where("devices", "customer_id", cid).stream()]
    def profile(self, cid): return self.get("profiles", cid)
    def save_profile(self, cid, p): self.put("profiles", cid, p)
    def list_cases(self, view="demo", limit=20, cursor=None):
        col = self.db.collection("cases"); D_ = self.fs.Query.DESCENDING
        if view == "demo": return sorted((d.to_dict() for d in self._where("cases", "pinned", True).stream()), key=lambda c: c["id"]), None
        if view == "all": q, key = col.order_by("received_at", direction=D_), "received_at"
        else:
            q = col.where(filter=self.FF("qkey", ">=", view + "#")).where(filter=self.FF("qkey", "<", view + "#" + END)).order_by("qkey", direction=D_)
            key = "qkey"
        if cursor: q = q.start_after({key: cursor})
        page = [d.to_dict() for d in q.limit(limit + 1).stream()]
        return page[:limit], (page[limit - 1][key] if len(page) > limit else None)
    def _count(self, q): return int(q.count(alias="c").get()[0][0].value)
    def stats(self):
        col = self.db.collection("cases")
        jobs = {"total": col, **{f"s:{s}": self._where("cases", "status", s) for s in STATUSES},
                **{f"r:{r}": self._where("cases", "route", r) for r in ROUTES},
                "tier1": self._where("cases", "tier", 1), "ai": self._where("cases", "ai_used", True)}
        with ThreadPoolExecutor(8) as ex: res = dict(zip(jobs, ex.map(self._count, jobs.values())))
        return {"total": res["total"], "status": {s: res[f"s:{s}"] for s in STATUSES},
                "route": {r: res[f"r:{r}"] for r in ROUTES}, "tier1": res["tier1"], "ai": res["ai"]}
    def add_audit(self, e): self.db.collection("audit").add(e)
    def audit(self, txn=None, limit=100):
        if txn: return sorted((d.to_dict() for d in self._where("audit", "txn", txn).stream()), key=lambda a: a["at"], reverse=True)
        return [d.to_dict() for d in self.db.collection("audit").order_by("at", direction=self.fs.Query.DESCENDING).limit(limit).stream()]
    def claim(self, txn_id, secs=300):
        ref = self.db.collection("cases").document(txn_id)
        @self.fs.transactional
        def tx_claim(tx):
            d = ref.get(transaction=tx).to_dict() or {}
            if d.get("status") != "INVESTIGATING" or time.time() - d.get("claimed_at", 0) < secs: return False
            tx.update(ref, {"claimed_at": time.time()}); return True
        return tx_claim(self.db.transaction())
    def usage_take(self, field, cap, n=1):
        ref = self.db.collection("usage").document(datetime.now(timezone.utc).strftime("%Y%m%d"))
        @self.fs.transactional
        def tx_take(tx):
            d = ref.get(transaction=tx).to_dict() or {}
            if d.get(field, 0) + n > cap: return False
            tx.set(ref, {field: d.get(field, 0) + n}, merge=True); return True
        return tx_take(self.db.transaction())
    def usage(self): return self.get("usage", datetime.now(timezone.utc).strftime("%Y%m%d")) or {}
    def reset(self):
        import generate_data as G
        refs = {}
        for coll in ("beneficiaries", "devices", "account_events", "cases", "investigations"):
            for d in self._where(coll, "source", "feed").stream(): refs[d.reference.path] = d.reference
        for d in self._where("investigations", "ai_available", False).stream(): refs[d.reference.path] = d.reference
        for d in self.db.collection("audit").stream(): refs[d.reference.path] = d.reference
        bw = self.db.bulk_writer(); n = len(refs)
        for r in refs.values(): bw.delete(r)
        bw.flush()
        ds = G.build(int(os.getenv("N_CUSTOMERS", "200")))
        for cid, p in ds["profiles"].items(): bw.set(self.db.collection("profiles").document(cid), p)
        mem = MemoryStore(ds)
        for c in seed_cases(mem): bw.set(self.db.collection("cases").document(c["id"]), c)
        bw.close(); return n

def seed_cases(store):
    """Pinned demo cases in status NEW with their rule hits pre-computed for the queue."""
    import rules
    out = []
    for i, c in enumerate(D.CASES):
        hits = rules.evaluate(c, store)
        doc = {**c, "pinned": True, "source": "demo", "status": "NEW", "route": None, "tier": None, "ai_used": None,
               "received_at": f"2026-10-17T04:{30 + i:02d}:00+00:00", "rules": [h["rule"] for h in hits],
               "rules_only_decision": rules.rules_only_decision(hits),
               "customer_name": D.CUSTOMERS[c["customer_id"]]["name"], "payee_name": D.BENEFICIARIES[c["beneficiary_id"]]["name"]}
        doc["qkey"] = qkey("NEW", doc); out.append(doc)
    return out

_store = None
def get_store():
    global _store
    if _store is None:
        _store = FirestoreStore() if os.getenv("USE_FIRESTORE") == "1" else MemoryStore()
    return _store
