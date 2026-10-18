"""Seed Firestore with the synthetic dataset (5 pinned demo cases + ~200 generated customers).
  python seed_data.py            # N_CUSTOMERS env var to change size (default 200)
  python seed_data.py --wipe     # first delete app-generated data from earlier versions"""
import os, sys
from google.cloud import firestore
import data as D, generate_data as G
from store import MemoryStore, seed_cases

db = firestore.Client()
if "--wipe" in sys.argv:
    bw = db.bulk_writer(); n = 0
    for coll in ("cases", "investigations", "case_state", "audit", "usage", "profiles"):
        for d in db.collection(coll).stream(): bw.delete(d.reference); n += 1
    bw.close(); print("wiped", n, "docs")

ds = G.build(int(os.getenv("N_CUSTOMERS", "200")))
bw = db.bulk_writer()
for coll in ("customers", "beneficiaries", "devices", "profiles"):
    for k, v in ds[coll].items(): bw.set(db.collection(coll).document(k), {"id": k, **v} if coll != "profiles" else v)
for e in ds["events"]: bw.set(db.collection("account_events").document(e["id"]), e)
for h in ds["history"]: bw.set(db.collection("transactions").document(h["id"]), h)
for c in seed_cases(MemoryStore(ds)): bw.set(db.collection("cases").document(c["id"]), c)
bw.set(db.collection("config").document("policy"), D.POLICY)
bw.close()
print(f"Seeded {len(ds['customers'])} customers, {len(ds['history'])} history payments, {len(ds['beneficiaries'])} payees, "
      f"{len(ds['devices'])} devices, {len(ds['profiles'])} profiles, 5 demo cases.")
