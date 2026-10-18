# RiskRoute AI

**Event-driven, explainable payment-risk routing for banks**
Google Cloud AI Builder Cup 2026 · Theme: BFSI — Intelligent Risk, Fraud & Financial Experiences

> ⚠️ **All data is synthetic.** No real customers, banks or payments are used.
> RiskRoute decides **which checks** a payment needs; a bank's payment system would move the money. In this prototype, "release" and "block" are simulated status changes only.


---

## Contents
1. [The problem](#1-the-problem)
2. [What RiskRoute does](#2-what-riskroute-does)
3. [Try it in 5 minutes](#3-try-it-in-5-minutes)
4. [How a payment flows](#4-how-a-payment-flows)
5. [Architecture on Google Cloud](#5-architecture-on-google-cloud)
6. [How Gemini is used, and kept honest](#6-how-gemini-is-used-and-kept-honest)
7. [Rules and behaviour signals](#7-rules-and-behaviour-signals)
8. [Synthetic data](#8-synthetic-data)
9. [Repository structure](#9-repository-structure)
10. [API reference](#10-api-reference)
11. [Run locally](#11-run-locally)
12. [Deploy to your own Google Cloud project](#12-deploy-to-your-own-google-cloud-project)
13. [Configuration](#13-configuration)
14. [Scale, reliability and cost](#14-scale-reliability-and-cost)
15. [Results](#15-results)
16. [Limitations](#16-limitations)
17. [Roadmap](#17-roadmap)

---

## 1. The problem

Banks protect risky digital payments with **fixed controls**: new-payee cooling periods, transfer limits, holds and OTPs. These controls treat every payment the same:

- A routine ₹6 lakh payment to a supplier a business has paid every month for years hits the same friction as a payment to a payee added 10 minutes ago.
- When something *does* look wrong, an analyst has to collect transaction, payee, device and login history from several systems before deciding what to check.

The result is friction for genuine customers and slow, manual investigations for risky ones.

## 2. What RiskRoute does

RiskRoute screens **every** payment cheaply and spends AI and human time only on the risky few.

| Tier | Runs on | What happens |
|---|---|---|
| **Tier 1 — Rules + behaviour** | Every payment, in milliseconds | Bank policy rules + the customer's own stored behaviour profile. If no mandatory rule fires and the payment looks normal for this customer → **auto-clear. No AI call.** |
| **Tier 2 — Gemini investigation** | Only payments Tier 1 flags, asynchronously | Queued on Pub/Sub. A Gemini agent fetches evidence through tools and writes a structured investigation. Every claim must cite a record it actually fetched. |
| **Route** | Every payment | **Auto-clear** → released · **Customer verification** → customer confirms in their app, no bank staff · **Analyst review** → a person decides, with the investigation already prepared |

Two guardrails sit outside the AI:

- **Mandatory-rule floor.** Hard bank rules set the minimum route. Gemini can escalate a payment but can never route it below that floor.
- **Account-takeover safeguard.** If the customer's registered mobile number changed recently, an in-app "yes, it was me" is not trusted (it could reach the attacker), so the payment goes to an analyst.

**What this is not:** it does not remove mandatory controls such as cooling periods, and it does not claim to detect fraud with a proven accuracy. It routes payments to the right level of checking and explains why, with evidence.

## 3. Try it in 5 minutes

Open the [live app](https://project-cb6ae9d9-d1b9-40d0-9bc.web.app/). Click **Reset demo** first if earlier activity is shown.

| Step | Do this | What you should see |
|---|---|---|
| 1 | Click **TXN-1045** → **Screen payment** | ₹6 lakh to a regular supplier. **Rules only: Verify**, but **RiskRoute: Auto-clear**, with step 3 "Gemini skipped". Behaviour context removed unnecessary friction. |
| 2 | Click **TXN-1042** → **Screen payment** | New payee (40 min old), new browser, amount far above usual. Gemini investigates. Open the **Agent trace** and **Evidence** tabs: each claim shows record IDs like `[BEN-182]`. Route: **Customer verification**. Click **Open customer's phone** → **Yes**. |
| 3 | Click **TXN-1044** → **Screen payment** | Mobile number changed 2.5 h ago + new phone + new "Refund Desk" payee. Hard rule → **Analyst review**, with a warning that app confirmation is not trusted. |
| 4 | Click **TXN-1046** → **Screen payment** | Amount is normal for this business, but the payee is brand new. The mandatory floor keeps it at **Verification**, even if Gemini suggests proceed (a guardrail note appears). |
| 5 | Click **Send 25 payments** | Live synthetic traffic goes through Pub/Sub. Most cards turn **Released** immediately (Tier 1). A few show **AI investigating**, then move to their route. |
| 6 | Open **Scale & benchmark** → **Run simulation**, then **Run benchmark** | Tier 1 over 2,000 synthetic payments, and rules-only vs RiskRoute on the 5 labelled cases. |
| 7 | Open **Audit log** | Filters: *Needs attention*, *Auto-cleared (Tier 1)*, *AI pipeline*, *Customer & analyst actions*, *Blocked*. Search a case ID, e.g. `TXN-1044`. |

Daily usage caps protect cost: **150 AI investigations** and **1,000 feed payments** per day. Above the AI cap, flagged payments are routed by rules only and the app keeps working.

## 4. How a payment flows

```
             ┌─────────────────────────────── Tier 1 (every payment) ───────────────────────────────┐
payment ──►  │ static rules (rules.py)  +  stored behaviour profile (baseline.py)                    │
             └───────────────┬───────────────────────────────────────────────────┬──────────────────┘
                             │ no hard rule AND no deviation                     │ otherwise
                             ▼                                                   ▼
                     AUTO-CLEAR → Released                         INVESTIGATING (queued on Pub/Sub)
                     profile learns from it                                      │
                                                                                 ▼
                                                ┌──────────── Tier 2: Gemini agent (agent.py) ────────────┐
                                                │ 1. calls tools to fetch evidence from Firestore         │
                                                │ 2. writes a JSON report (indicators, gaps, checks)      │
                                                │ 3. citation check removes uncited claims                │
                                                │ 4. mandatory-rule guard raises any too-low route        │
                                                └───────────────────────────┬─────────────────────────────┘
                                                                            ▼
                               Auto-clear  ·  Customer verification  ·  Analyst review
                                                                            │
                               customer Yes / No, analyst Release / Block / Request verification
                                                                            ▼
                                                     Released  or  Blocked   (every step audited)
```

**Case statuses**

| Status | Meaning |
|---|---|
| `NEW` | Demo case not yet screened |
| `INVESTIGATING` | Flagged by Tier 1, waiting for or inside the Gemini worker |
| `AWAITING_CUSTOMER` | Customer must confirm in their app |
| `ANALYST_REVIEW` | With a human analyst |
| `RELEASED` | Allowed (simulated). The customer's profile learns from it. |
| `BLOCKED` | Stopped (simulated) |

## 5. Architecture on Google Cloud

```
Browser ──► Firebase Hosting (.web.app) ──rewrite all paths──► Cloud Run "riskroute" (FastAPI + UI)
                                                                   │        │           │
                                         "Send payments" publish   │        │ read/write│ Gemini
                                                                   ▼        ▼           ▼
                                               Pub/Sub payments-in    Firestore    Vertex AI (Gemini 2.5 Flash)
                                                     │ push (OIDC)        ▲
                                                     ▼                    │
                                       Cloud Run /pubsub/payments (Tier 1)┤
                                                     │ flagged → publish  │
                                                     ▼                    │
                                       Pub/Sub flagged-payments           │
                                                     │ push (OIDC)        │
                                                     ▼                    │
                                       Cloud Run /pubsub/investigate ─────┘
```

| Service | How RiskRoute uses it |
|---|---|
| **Firebase (Hosting)** | Public entry point. `firebase.json` rewrites every request to the Cloud Run service. |
| **Cloud Run** | Runs the whole application: REST API, Tier-1 screener, Gemini worker and the web UI. Scales to zero. |
| **Firestore** | All data: synthetic customers, payees, devices, history, stored behaviour profiles, cases, investigations, audit log, daily usage counters, policy thresholds. |
| **Gemini (Vertex AI)** | Tool-calling investigation agent with schema-constrained JSON output; also drafts the customer confirmation message. |
| **Pub/Sub** | Two topics decouple screening from AI investigation and give automatic retries with backoff. |
| **IAM service account** | One identity for Cloud Run and Pub/Sub push. **No API keys anywhere.** |
| Cloud Build + Artifact Registry | Build the container automatically from source on deploy. |

## 6. How Gemini is used, and kept honest

The Gemini agent receives the payment ID plus the signals Tier 1 already computed, then **decides for itself** which tools to call:

| Tool | Returns |
|---|---|
| `get_transaction(txn_id)` | The payment (benchmark labels are never exposed) |
| `get_beneficiary(beneficiary_id)` | Payee record, including when it was added |
| `get_account_activity(customer_id)` | Logins, OTP passes, payee additions, contact changes, devices |
| `get_behaviour_baseline(customer_id)` | Stored profile: typical amounts, known payees and devices, usual hours |

It then produces a JSON report (enforced by a response schema): summary, risk indicators, mitigating factors, missing information, recommended checks, a customer confirmation message, a recommendation (`PROCEED` / `VERIFY` / `HUMAN_REVIEW`) and its reasoning.

**Hallucination controls**

1. **Grounded tools only.** Tools return stored Firestore records, nothing from the open web.
2. **Citation check.** Each claim must list evidence IDs, and every ID must have been returned by a tool call in this investigation. Claims that fail are removed and counted (shown as *cited / removed*).
3. **Deterministic settings.** Temperature 0 and a strict JSON schema.
4. **Policy outside the model.** Mandatory rules are enforced in code after Gemini answers. If Gemini recommends less than the floor, the route is raised and a visible note explains why.
5. **Safe failure.** If Gemini errors, Pub/Sub retries; after 3 attempts the payment is routed by rules only and marked "Gemini unavailable".

**Who decides what:** for ~91% of payments, deterministic rules and statistics decide with no AI. For flagged payments, Gemini's recommendation sets the route *within* the limits of mandatory rules. A customer or analyst makes the final call wherever verification or review is required.

## 7. Rules and behaviour signals

**Static rules** (`rules.py`, thresholds stored in Firestore `config/policy`)

| Rule | Fires when | Type | Minimum route |
|---|---|---|---|
| `NEW_BENEFICIARY` | Payee added < 24 h before payment | **Hard** | Verify |
| `RECENT_CONTACT_CHANGE` | Mobile/email changed < 72 h before payment | **Hard** | Human review |
| `NEW_DEVICE` | Device first seen < 24 h before payment | Soft | Verify |
| `HIGH_VALUE` | Amount ≥ ₹2,00,000 | Soft | Verify |

*Hard* rules set the mandatory floor. *Soft* rules alone don't block auto-clear if behaviour is normal.

**Behaviour signals** (`baseline.py`). Each customer has a stored profile, updated every time one of their payments is released:

| Signal | Fires when |
|---|---|
| `AMOUNT_ABOVE_USUAL` | Amount ≥ 3× this payee's typical amount (or the customer median for a new payee) |
| `ABOVE_HISTORICAL_MAX` | Amount > 1.2× the customer's largest recent payment |
| `UNSEEN_PAYEE` | No previous payments to this payee |
| `UNSEEN_DEVICE` | Device never used for a previous payment |
| `UNUSUAL_HOUR` | Outside the customer's usual payment hours |

This mirrors security-monitoring practice: correlation rules (static) plus behavioural analytics (dynamic), with an investigation layer on top.

## 8. Synthetic data

All data is generated; nothing is real.

- **`backend/data.py`** — 5 storytelling cases on a fixed demo clock (17 Oct 2026, 10:00 IST):

| Case | Story | Expected route |
|---|---|---|
| TXN-1042 | ₹8L, payee added 40 min ago, new browser, far above usual | Customer verification |
| TXN-1043 | ₹25k rent to a long-standing payee | Auto-clear |
| TXN-1044 | Mobile changed 2.5 h ago, new phone, new "Refund Desk" payee, ₹4.5L | Analyst review |
| TXN-1045 | ₹6L to the business's regular supplier | Auto-clear (despite high value) |
| TXN-1046 | ₹7L, normal for the business, but payee is new | Verification (mandatory floor) |

- **`backend/generate_data.py`** — 200 more customers across salaried, business, retired and student segments (≈203 customers, ≈3,650 past payments, ≈668 payees, ≈306 devices), each with realistic payees, frequencies, hours and devices. It also generates live payments for the feed, with a configurable share of injected anomalies: new payee, new device, high amount, or full account takeover.

Expected labels exist only for the benchmark and are never sent to Gemini.

## 9. Repository structure

```
riskroute-ai/
├── README.md
├── firebase.json        Firebase Hosting: rewrite all paths to Cloud Run "riskroute"
├── .firebaserc          Firebase project binding
├── public/              Minimal Hosting folder (content is served by Cloud Run)
└── backend/             Built and deployed to Cloud Run
    ├── main.py          FastAPI app: REST API, Pub/Sub push endpoints, action state machine, serves the UI
    ├── pipeline.py      Live feed, Tier-1 ingest, Gemini worker, retries, caps, audit
    ├── agent.py         Tier-1 screen, Gemini agent and tools, citation check, mandatory-rule guard
    ├── rules.py         Static bank-policy rules
    ├── baseline.py      Behaviour profiles: build, incremental learning, deviation signals
    ├── store.py         Data access: Firestore (production) and in-memory (local, tests, simulation)
    ├── data.py          5 demo cases and their records
    ├── generate_data.py Synthetic customers, history and live payment generator
    ├── sim.py           Scale simulation of Tier 1 over thousands of payments
    ├── seed_data.py     Load the synthetic dataset into Firestore (--wipe to clear app data first)
    ├── dump_data.py     Back up every Firestore collection to backend/dumps/ (git-ignored)
    ├── requirements.txt Python dependencies
    ├── Procfile         Start command for Cloud Run buildpacks
    └── static/          Web UI: index.html, app.js, style.css (no build step)
```

**Firestore collections**

| Collection | Contents |
|---|---|
| `customers`, `beneficiaries`, `devices` | Synthetic master data |
| `transactions` | Past completed payments |
| `profiles` | Stored behaviour profile per customer |
| `account_events` | Logins, OTP, payee additions, contact changes |
| `cases` | Every payment being screened: status, route, tier |
| `investigations` | Saved investigations (cache) and optional Gemini second opinions |
| `audit` | Every routing decision and human action |
| `usage` | Daily counters for the AI and feed caps |
| `config/policy` | Rule thresholds |

## 10. API reference

| Method & path | Purpose |
|---|---|
| `GET /api/health` | App name, model, mode (`pubsub` or `local`) |
| `GET /api/stats` | KPI counts and today's usage against caps |
| `GET /api/cases?view=&limit=&cursor=` | Paginated queue: `demo`, `all`, or a status |
| `GET /api/case/{txn}` | Case, investigation, audit trail |
| `POST /api/process/{txn}` · `POST /api/process_all` | Screen demo cases |
| `POST /api/feed?n=25` | Publish 1–50 synthetic payments |
| `POST /api/case/{txn}/action` | `CUSTOMER_CONFIRMED`, `CUSTOMER_DENIED`, `ESCALATE`, `REQUEST_VERIFICATION`, `ANALYST_RELEASE`, `ANALYST_BLOCK` |
| `POST /api/ai_opinion/{txn}` | Optional Gemini second opinion on an auto-cleared case (does not change the route) |
| `GET /api/audit?txn=&limit=` | Audit log, or one case's history |
| `GET /api/simulate?n=&anomaly_pct=` | Tier-1 scale simulation |
| `GET /api/benchmark` | Rules-only vs RiskRoute on the 5 labelled cases |
| `POST /api/reset` | Remove feed payments and audit, restore demo cases (keeps successful cached investigations) |
| `POST /pubsub/payments` · `POST /pubsub/investigate` | Pub/Sub push targets (OIDC-verified) |

## 11. Run locally

Without Pub/Sub or Firestore, the app uses in-memory data and a local thread pool. Tier 1, the UI, the feed, simulation and audit all work. Gemini calls need Google Cloud credentials; without them, flagged payments fall back to rules-only routing.

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# optional, for real Gemini calls:
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=<your-project-id>

uvicorn main:app --reload      # open http://127.0.0.1:8000
```

## 12. Deploy to your own Google Cloud project

Replace the placeholders. Run commands from the folder indicated.

**1. Enable services and create resources**
```bash
export P=<your-project-id>
export SA=riskroute-sa@$P.iam.gserviceaccount.com
gcloud config set project $P
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com firestore.googleapis.com pubsub.googleapis.com
gcloud firestore databases create --location=<region>
gcloud iam service-accounts create riskroute-sa
for r in roles/aiplatform.user roles/datastore.user roles/pubsub.publisher; do
  gcloud projects add-iam-policy-binding $P --role=$r --member="serviceAccount:$SA" --condition=None -q
done
gcloud pubsub topics create payments-in flagged-payments
```
On a new project, Cloud Build may also need `roles/cloudbuild.builds.builder` on the default compute service account.

**2. Load synthetic data** (from `backend/`)
```bash
GOOGLE_CLOUD_PROJECT=$P python seed_data.py
```

**3. Deploy Cloud Run** (from `backend/`)
```bash
gcloud run deploy riskroute --source . --region <region> --allow-unauthenticated \
  --service-account $SA --memory 1Gi --timeout 300 --max-instances 3 --concurrency 40 \
  --set-env-vars USE_FIRESTORE=1,GOOGLE_CLOUD_PROJECT=$P,GOOGLE_CLOUD_LOCATION=us-central1,GEMINI_MODEL=gemini-2.5-flash,PUBSUB_PAYMENTS_TOPIC=payments-in,PUBSUB_FLAGGED_TOPIC=flagged-payments,PUSH_SA=$SA
```

**4. Connect Pub/Sub** (use the service URL printed above)
```bash
export URL=<cloud-run-url>
gcloud pubsub subscriptions create payments-in-push --topic=payments-in \
  --push-endpoint=$URL/pubsub/payments --push-auth-service-account=$SA --ack-deadline=60 \
  --min-retry-delay=10s --max-retry-delay=300s
gcloud pubsub subscriptions create flagged-push --topic=flagged-payments \
  --push-endpoint=$URL/pubsub/investigate --push-auth-service-account=$SA --ack-deadline=300 \
  --min-retry-delay=10s --max-retry-delay=300s
```

**5. Firebase Hosting** (from the repo root; update `firebase.json` region and `.firebaserc` project)
```bash
npm i -g firebase-tools
firebase login
firebase projects:addfirebase $P
firebase deploy --only hosting
```

## 13. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `USE_FIRESTORE` | off | `1` = Firestore, otherwise in-memory data |
| `GOOGLE_CLOUD_PROJECT` | — | Project for Firestore, Pub/Sub and Vertex AI |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | Vertex AI location for Gemini |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model |
| `PUBSUB_PAYMENTS_TOPIC`, `PUBSUB_FLAGGED_TOPIC` | — | Both set = asynchronous Pub/Sub mode |
| `PUSH_SA` | — | Service-account email expected in Pub/Sub push tokens |
| `AI_DAILY_CAP` | `150` | Max Gemini investigations per day |
| `FEED_DAILY_CAP` | `1000` | Max synthetic feed payments per day |
| `MAX_PARALLEL_AI` | `6` | Concurrent Gemini calls per instance |
| `N_CUSTOMERS` | `200` | Generated synthetic customers |
| `APP_NAME` | `RiskRoute AI` | Name shown in the UI |

## 14. Scale, reliability and cost

- **Cheap Tier 1.** One profile read plus payee and device lookups per payment; no history scan.
- **Asynchronous AI.** Pub/Sub separates screening from Gemini, so a slow model call never blocks intake.
- **Idempotent.** Duplicate Pub/Sub deliveries are ignored; each investigation is claimed once via a Firestore transaction.
- **Back-pressure and retries.** Limited parallel Gemini calls; quota or availability errors return HTTP 503 so Pub/Sub retries with backoff; after 3 attempts, rules-only routing.
- **Secure push.** Pub/Sub OIDC tokens are verified against the service account.
- **Cost.** Cloud Run scales to zero; Gemini runs on ~9% of payments; results are cached; daily caps on AI and feed usage.
- **Scalable reads.** Paginated queues and Firestore count aggregations for dashboards.

## 15. Results

Measured on the deployed prototype with synthetic data.

**Scale simulation** — 2,000 payments from 200 customers, 10% injected anomalies:

| Metric | Result |
|---|---|
| Auto-cleared at Tier 1 (no AI) | 1,818 (90.9%) |
| Sent to Gemini | 182 |
| Mandatory analyst review | 44 |
| Tier-1 compute per payment | ≈0.017 ms (excludes database I/O) |
| Injected anomalies flagged | 98.9% |
| Rules-only frictions removed by behaviour context | 158 of 311 |

**Benchmark** — 5 demo cases with team-set expected routes:

| Approach | Matches expected route |
|---|---|
| Static rules only | 80% |
| RiskRoute | 100% |
| Mandatory-rule violations | 0 |

These show workload, speed and behaviour on synthetic data. They are **not** a claim of real-world fraud-detection accuracy.

## 16. Limitations

- Synthetic data only; benchmark labels were set by the team on 5 cases.
- Simulated anomalies use the same signal types the rules check; real fraud is more varied.
- Gemini runs in `us-central1` while the app runs in India; a real deployment would need data-residency review.
- Concurrent profile updates for the same customer are last-write-wins.
- One shared demo: pressing **Reset demo** or **Send payments** affects what other viewers see.
- Firebase Hosting ends requests after 60 seconds; a first benchmark run with uncached Gemini calls is best started on the Cloud Run URL.
- Pub/Sub token checks verify the sender identity but not the token audience (acceptable for a prototype).

## 17. Roadmap

- Analyst feedback loop to tune rule thresholds and profile sensitivity
- Payee name-verification as an additional signal
- Ingestion of real core-banking payment events
- Bank-grade IAM, PII masking and in-region model hosting
- Automated evaluation of prompts and model output in CI
- Pilot on historical, anonymised alerts with a bank risk team

---

Built for the Google Cloud AI Builder Cup 2026 with **Gemini (Vertex AI), Cloud Run, Firestore, Firebase Hosting and Pub/Sub**.
