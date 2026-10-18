# RiskRoute AI — explainable, event-driven payment-risk routing for banks (AI Builder Cup · BFSI)

> **All data is synthetic.** No real customers, banks or payments. The AI recommends; it never moves money.

Fixed controls (cooling periods, limits) treat every payment the same. RiskRoute screens **every** payment in
milliseconds and spends AI and people only on the risky few.

| Tier | Runs on | What happens |
|---|---|---|
| 1 · Rules + behaviour profile | Every payment | Static bank rules + the customer's **stored** behaviour profile (1 read; updated incrementally on each released payment). No mandatory rule and normal behaviour → **auto-clear, no AI call** |
| 2 · Gemini agent (async) | Flagged payments | Queued on Pub/Sub. Gemini (Vertex AI) calls 4 tools and writes a structured investigation; a citation check drops claims citing records it never fetched |
| Route | — | **Auto-clear** · **Customer self-verification** (no staff) · **Analyst review** (investigation pre-built) |

## Architecture (Google Cloud)
```
payment events ─► Pub/Sub payments-in ─► Cloud Run /pubsub/payments  (Tier 1)
                                              │ flagged
                                              ▼
                  Pub/Sub flagged-payments ─► Cloud Run /pubsub/investigate (Gemini on Vertex AI)
                                              ▼
                  Firestore: cases · profiles · investigations · audit · usage
```
- **Idempotent**: duplicate deliveries ignored (`create()`); investigations claimed in a Firestore transaction.
- **Back-pressure**: max parallel Gemini calls per instance; 429/503 → HTTP 503 so Pub/Sub retries with backoff; after 3 attempts, rules-only routing.
- **Push auth**: Pub/Sub OIDC token verified against the push service account.
- **Cost guard**: daily caps on AI investigations (`AI_DAILY_CAP`) and feed volume (`FEED_DAILY_CAP`).
- **Scale-friendly reads**: Tier 1 = profile + payee + device point reads and one small query; paginated queue; aggregation counts.

## Synthetic data
- `backend/data.py` — 5 pinned storytelling cases (TXN-1042…1046).
- `backend/generate_data.py` — ~200 generated customers (salaried, business, retired, student), ~3,600 history payments, stored profiles, and a live payment generator with injected anomalies (new payee, new device, high amount, account takeover).

## Demo walkthrough
1. **TXN-1045** — ₹6L: rules alone say *Verify*; profile shows a routine supplier payment → auto-cleared, no AI call.
2. **TXN-1042** — new payee + new device + far above usual → Gemini → customer verification (open the simulated phone).
3. **TXN-1044** — mobile changed + new device + new payee → analyst; a customer confirmation is not trusted.
4. **TXN-1046** — normal amount but new payee → mandatory rule keeps it at verification even if the AI says proceed.
5. **Send 50 payments** — watch Tier 1 clear most of them instantly while flagged ones move through the AI queue.
6. **Scale & benchmark** — real Tier-1 code over 2,000+ synthetic payments with measured speed; rules-only vs RiskRoute on labelled cases.

## Run locally (no Pub/Sub; uses a thread pool)
```bash
cd backend && pip install -r requirements.txt
GOOGLE_CLOUD_PROJECT=<project> uvicorn main:app --reload
```
Limitations: synthetic data; benchmark labels set by the team; simulation shows workload and speed, not real-world detection accuracy;
concurrent profile updates for the same customer are last-write-wins.
