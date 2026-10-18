"""Tier 1: deterministic rules + stored behaviour profile (no AI).
Tier 2: Gemini (Vertex AI) tool-calling investigation, then citation check + mandatory-rule guard."""
import json, os, time
import rules, baseline

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
LEVEL = rules.LEVEL
ROUTE = {"PROCEED": "AUTO_CLEAR", "VERIFY": "CUSTOMER_VERIFY", "HUMAN_REVIEW": "ANALYST_REVIEW"}
TXN_FIELDS = ("id", "customer_id", "beneficiary_id", "device_id", "amount", "at", "channel")
_client = None

def client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    return _client

SYSTEM = """You are a payment-risk investigation assistant for a bank risk analyst.
Investigate ONE pending payment. Always use the tools to fetch evidence before concluding.
Only state facts present in tool results. Cite record IDs exactly as returned (e.g. BEN-182, EVT-701, BASELINE:CUST-102).
Never say fraud is confirmed. State what information is missing. You recommend; you never approve or block.
PROCEED = consistent with history, no concerns. VERIFY = needs a customer confirmation. HUMAN_REVIEW = strong account-takeover/scam indicators."""

def _fn(name, desc, param):
    return {"name": name, "description": desc,
            "parameters": {"type": "OBJECT", "properties": {param: {"type": "STRING"}}, "required": [param]}}

TOOL_DECLS = [
    _fn("get_transaction", "Get the pending payment under review.", "txn_id"),
    _fn("get_beneficiary", "Get the payee record, including when it was added.", "beneficiary_id"),
    _fn("get_account_activity", "Get recent account events (logins, OTP, contact changes, payee additions) and known devices.", "customer_id"),
    _fn("get_behaviour_baseline", "Get the customer's stored behavioural profile (typical amounts, known payees/devices, usual hours).", "customer_id"),
]
CLAIM = {"type": "OBJECT", "properties": {"claim": {"type": "STRING"},
         "evidence": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["claim", "evidence"]}
REPORT_SCHEMA = {"type": "OBJECT", "properties": {
    "summary": {"type": "STRING"},
    "risk_indicators": {"type": "ARRAY", "items": CLAIM},
    "mitigating_factors": {"type": "ARRAY", "items": CLAIM},
    "missing_information": {"type": "ARRAY", "items": {"type": "STRING"}},
    "recommended_checks": {"type": "ARRAY", "items": {"type": "STRING"}},
    "customer_verification_message": {"type": "STRING"},
    "recommendation": {"type": "STRING", "enum": ["PROCEED", "VERIFY", "HUMAN_REVIEW"]},
    "reasoning": {"type": "STRING"}},
    "required": ["summary", "risk_indicators", "mitigating_factors", "missing_information",
                 "recommended_checks", "customer_verification_message", "recommendation", "reasoning"]}

def run_tool(name, args, store):
    if name == "get_transaction":
        c = store.case(args.get("txn_id", ""))
        return {k: c[k] for k in TXN_FIELDS if k in c} if c else {"error": "not found"}
    if name == "get_beneficiary":
        b = store.get("beneficiaries", args.get("beneficiary_id", ""))
        return {"id": args["beneficiary_id"], **{k: v for k, v in b.items() if k != "source"}} if b else {"error": "not found"}
    if name == "get_account_activity":
        cid = args.get("customer_id", "")
        strip = lambda d: {k: v for k, v in d.items() if k != "source"}
        return {"customer_id": cid, "events": [strip(e) for e in store.events(cid)], "devices": [strip(d) for d in store.devices(cid)]}
    if name == "get_behaviour_baseline":
        p = store.profile(args.get("customer_id", ""))
        return baseline.public(p) if p else {"error": "not found"}
    return {"error": "unknown tool"}

def collect_ids(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("id", "beneficiary_id", "device_id", "customer_id", "ref") and isinstance(v, str):
                out.add(v)
            collect_ids(v, out)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, str) and v.startswith(("BEN-", "DEV-", "CUST-")):
                out.add(v)
            collect_ids(v, out)

def validate(report, known):
    """Citation check: keep only claims whose cited IDs ALL exist in records the agent fetched."""
    st = {"supported": 0, "unsupported": 0, "removed": []}
    for key in ("risk_indicators", "mitigating_factors"):
        kept = []
        for c in report.get(key, []):
            ev = c.get("evidence") or []
            if ev and all(e in known for e in ev):
                kept.append(c); st["supported"] += 1
            else:
                st["unsupported"] += 1; st["removed"].append(c)
        report[key] = kept
    return st

def apply_guard(ai_rec, floor):
    if LEVEL.get(ai_rec, 0) < LEVEL[floor]:
        return floor, f"AI suggested {ai_rec}; raised to {floor} by a mandatory bank rule."
    return ai_rec, None

def screen(case, store):
    """Tier 1. Cost: profile + payee + device point reads and one small events query."""
    hits = rules.evaluate(case, store)
    base = store.profile(case["customer_id"])
    devs, ok = baseline.deviations(case, base)
    tier1 = not any(h["hard"] for h in hits) and not devs
    return {"hits": hits, "base": base, "devs": devs, "ok": ok, "floor": rules.mandatory_floor(hits), "tier1_clear": tier1}

def tier1_report(case, hits, ok, base):
    ev = f"BASELINE:{case['customer_id']}"
    n = base.get("payee_counts", {}).get(case["beneficiary_id"], 0)
    soft = [h["rule"] for h in hits]
    mit = [{"claim": o["detail"], "evidence": [ev]} for o in ok]
    if n:
        mit.append({"claim": f"{n} previous completed payments to this payee", "evidence": [ev, case["beneficiary_id"]]})
    if soft:
        typ = base.get("payee_typical", {}).get(case["beneficiary_id"], base["median_amount"])
        mit.append({"claim": f"Soft rule {', '.join(soft)} fired, but the amount is in line with this payee's usual "
                             f"₹{typ:,.0f} (customer max ₹{base['max_amount']:,})", "evidence": [ev, case["id"]]})
    return {"summary": ("Tier-1 clear: no mandatory rule fired and the payment matches the customer's own history"
                        + (f"; the {', '.join(soft)} flag is explained by past behaviour." if soft else ".")
                        + " Gemini was not needed."),
            "risk_indicators": [], "mitigating_factors": mit, "missing_information": [], "recommended_checks": [],
            "customer_verification_message": "", "recommendation": "PROCEED",
            "reasoning": "Deterministic Tier-1 decision: behavioural profile shows no deviation and no hard policy rule applies."}

def investigate(txn_id, store, force_ai=False, ai_allowed=True):
    t0 = time.time()
    case = store.case(txn_id)
    s = screen(case, store); hits = s["hits"]
    res = {"txn_id": txn_id, "source": case.get("source", "feed"), "case": {k: case[k] for k in TXN_FIELDS if k in case},
           "customer": store.get("customers", case["customer_id"]), "payee": store.get("beneficiaries", case["beneficiary_id"]),
           "rule_hits": hits, "behaviour_signals": s["devs"] + s["ok"], "mandatory_floor": s["floor"],
           "rules_only_decision": rules.rules_only_decision(hits), "trace": [], "model": MODEL}
    if s["tier1_clear"] and not force_ai:
        res.update(tier=1, ai_used=False, ai_available=True, report=tier1_report(case, hits, s["ok"], s["base"]),
                   ai_recommendation=None, final_recommendation="PROCEED", guard_note=None,
                   route=ROUTE["PROCEED"], latency_ms=int((time.time() - t0) * 1000))
        return res
    res.update(tier=2, ai_used=True)
    if not ai_allowed:
        res.update(ai_available=False, error="AI_BUDGET", report=None, ai_recommendation=None,
                   final_recommendation=res["rules_only_decision"], route=ROUTE[res["rules_only_decision"]],
                   guard_note="Daily AI budget reached: routed by rules only.", latency_ms=int((time.time() - t0) * 1000))
        return res
    try:
        from google.genai import types
        tool_cfg = types.GenerateContentConfig(system_instruction=SYSTEM, temperature=0,
            tools=[types.Tool(function_declarations=TOOL_DECLS)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        prompt = (f"Investigate pending payment {txn_id}.\nDeterministic signals already computed "
                  f"(confirm them with tools):\nRULE_HITS={json.dumps(hits, ensure_ascii=False)}\n"
                  f"BEHAVIOUR_SIGNALS={json.dumps(s['devs'] + s['ok'], ensure_ascii=False)}")
        contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
        known = set()
        for _ in range(6):
            resp = client().models.generate_content(model=MODEL, contents=contents, config=tool_cfg)
            calls = resp.function_calls or []
            if not calls:
                break
            contents.append(resp.candidates[0].content)
            parts = []
            for fc in calls:
                args = dict(fc.args or {})
                out = run_tool(fc.name, args, store)
                before = set(known); collect_ids(out, known)
                res["trace"].append({"tool": fc.name, "args": args, "new_evidence": sorted(known - before)})
                parts.append(types.Part.from_function_response(name=fc.name, response={"result": out}))
            contents.append(types.Content(role="user", parts=parts))
        contents.append(types.Content(role="user", parts=[types.Part(text=
            "Write the investigation report as JSON. Every claim must cite IDs from the tool results. "
            "customer_verification_message: a short, plain message the bank app could show the customer to confirm "
            "this payment (amount, payee, device/time); never ask for OTP, PIN or passwords.")]))
        rep = client().models.generate_content(model=MODEL, contents=contents,
            config=types.GenerateContentConfig(system_instruction=SYSTEM, temperature=0,
                response_mime_type="application/json", response_schema=REPORT_SCHEMA))
        report = json.loads(rep.text)
        res["grounding"] = validate(report, known)
        final, note = apply_guard(report["recommendation"], s["floor"])
        res.update(report=report, ai_recommendation=report["recommendation"],
                   final_recommendation=final, guard_note=note, ai_available=True)
    except Exception as e:
        res.update(ai_available=False, error=str(e)[:300], report=None, ai_recommendation=None,
                   final_recommendation=res["rules_only_decision"],
                   guard_note="Gemini unavailable: routed by rules only.")
    res["route"] = ROUTE[res["final_recommendation"]]
    res["latency_ms"] = int((time.time() - t0) * 1000)
    return res

def retryable(res):
    e = (res.get("error") or "").upper()
    return any(k in e for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "DEADLINE"))
