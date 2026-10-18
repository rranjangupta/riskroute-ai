"""Scale simulation: real Tier-1 code over synthetic traffic from ~200 generated customers.
Measures in-process throughput (excludes Firestore I/O). Traffic mix and anomaly rate are assumptions."""
import random, time
import agent, rules, generate_data as G
from store import MemoryStore

_ds = None
def run(n=2000, anomaly_rate=0.10, seed=7):
    global _ds
    if _ds is None: _ds = G.build()
    st = MemoryStore(_ds); rnd = random.Random(seed)
    cids = [c for c, v in _ds["customers"].items() if v.get("generated")]
    res, secs = [], 0.0
    for _ in range(n):
        cid = rnd.choice(cids)
        p = G.make_payment(rnd, cid, _ds["customers"][cid], _ds["profiles"][cid], st.put, anomaly_rate)
        t0 = time.perf_counter(); sc = agent.screen(p, st); secs += time.perf_counter() - t0
        res.append((p, sc))
        st._events_by_c[cid] = [e for e in st._events_by_c.get(cid, []) if e.get("source") != "feed"]  # anomaly scoped to its own payment
    out = {"screened": n, "customers": len(cids), "tier1_clear": 0, "to_ai": 0, "floor_verify": 0, "floor_human": 0,
           "rules_only_friction": 0, "friction_avoided": 0, "anomalies_injected": 0, "anomalies_flagged": 0, "normal_flagged": 0}
    for p, sc in res:
        anom = p["injected"] != "none"; out["anomalies_injected"] += anom
        ro = rules.rules_only_decision(sc["hits"]) != "PROCEED"; out["rules_only_friction"] += ro
        if sc["tier1_clear"]:
            out["tier1_clear"] += 1; out["friction_avoided"] += ro
        else:
            out["to_ai"] += 1; out["anomalies_flagged"] += anom; out["normal_flagged"] += not anom
            out["floor_verify"] += sc["floor"] == "VERIFY"; out["floor_human"] += sc["floor"] == "HUMAN_REVIEW"
    out["no_ai_pct"] = round(100 * out["tier1_clear"] / n, 1)
    out["anomaly_flag_rate"] = round(100 * out["anomalies_flagged"] / max(1, out["anomalies_injected"]), 1)
    out["tier1_ms_per_payment"] = round(1000 * secs / n, 3)
    out["tier1_per_sec_one_cpu"] = int(n / secs) if secs else None
    out["assumptions"] = (f"{n} synthetic payments across {len(cids)} generated customers; {anomaly_rate:.0%} injected anomalies "
                          "(new payee, new device, high amount, takeover), each affecting only its own payment. Throughput is in-process Tier-1 compute on one CPU, "
                          "excluding Firestore reads (~3 point reads + 1 small query per payment in production).")
    return out
