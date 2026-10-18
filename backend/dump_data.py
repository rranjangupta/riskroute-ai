"""Dump every Firestore collection to dumps/firestore-<time>.json"""
import json, os
from datetime import datetime
from google.cloud import firestore
db = firestore.Client()
out = {c.id: {d.id: d.to_dict() for d in c.stream()} for c in db.collections()}
os.makedirs("dumps", exist_ok=True)
f = f"dumps/firestore-{datetime.now():%Y%m%d-%H%M%S}.json"
json.dump(out, open(f, "w"), indent=2, default=str, ensure_ascii=False)
print(f, {k: len(v) for k, v in out.items()})
