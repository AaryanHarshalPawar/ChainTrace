# ChainTrace

**Real-Time Identification of Fraud-Linked Cryptocurrency Exchanges from
Victim-Reported Suspect Wallet Addresses**

Smart India Hackathon — problem statement **26183**

Given a wallet address from a cybercrime complaint, this system traces the
money forward across the blockchain, identifies the nearest exchange or VASP
that received it, scores the risk, and tells the investigating officer what to
do next — with the evidence for every claim.

---

## Why the "nearest VASP" is the whole point

An exchange's hot wallet is shared by millions of customers. A preservation
notice naming it identifies nobody.

A **deposit address** is different: it belongs to exactly one customer, and it
maps to one KYC'd account. The system's core heuristic finds these — an
address that receives funds and immediately sweeps ~100% of them into a known
exchange wallet, retaining nothing, *is* that exchange's customer deposit
address. That address is what a notice must name, and it is what turns a
trace into a person.

---

## Current status

| Component | State |
|---|---|
| Address detection (TRON / EVM / Bitcoin) | Working, 72 tests |
| TRON adapter (TronGrid) | Working against live API |
| Counterfeit-token & spam filtering | Working |
| OFAC sanctions corpus | **944 real addresses**, auto-ingested |
| Deposit-address heuristic | Working |
| Behavioural VASP inference | Working |
| Forward tracer (haircut value model) | Working |
| Risk scoring + investigative actions | Working |
| REST API | Working — `/trace`, `/screen`, `/validate`, `/health` |
| Ethereum / EVM adapter | **Not built yet** |
| Bitcoin adapter | **Not built yet** |
| React dashboard + graph visualisation | **Not built yet** |
| PDF investigation report | **Not built yet** |
| Named exchange labels | **Ships empty by design — see "Known limitations"** |

---

## Setup

Requires Python 3.11+ (developed on 3.14) and Node 18+ for the dashboard.

```bash
cd backend
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copy the environment template and (optionally) add a free TronGrid key:

```bash
copy .env.example .env
```

Build the sanctions corpus — this downloads the live OFAC SDN list:

```bash
.venv\Scripts\python.exe scripts\ingest_ofac.py
```

Run the API:

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Interactive docs at <http://localhost:8000/docs>.

### Try it

```bash
curl "http://localhost:8000/api/v1/screen?address=TNiq9AXBp9EjUqhDhrwrfvAA8U3GUQZH81"
```

Returns an OFAC hit: *Bank Markazi Jomhouri Islami Iran*, programs
`IRAN, IFSR, IRGC, SDGT`.

```bash
curl -X POST http://localhost:8000/api/v1/trace ^
  -H "Content-Type: application/json" ^
  -d "{\"address\":\"TMuA6YqfCeX8EhbfYEg5y7S4DqzSJireY9\",\"max_hops\":2}"
```

### Run the tests

```bash
cd backend
.venv\Scripts\python.exe -m pytest
```

---

## Architecture

```
complaint ──► detect ──► resolve ──► trace ──► attribute ──► score ──► recommend
              chain      adapter     BFS        VASP          risk      actions
                                     haircut    3 methods
```

```
backend/app/
  core/
    chains.py      chain registry + address validation (base58check, bech32)
    models.py      normalised domain models; every claim carries its evidence
    assets.py      canonical token registry + counterfeit detection
    pricing.py     valuation, keyed on contract address (never symbol)
  chains/
    http.py        cached, rate-limited, offline-capable HTTP client
    base.py        ChainAdapter contract + profile construction
    tron.py        TronGrid adapter
    spam.py        dust / advert / counterfeit / poisoning filter
  attribution/
    labels.py      label corpus loader (OFAC + curated), strict sourcing
    behaviour.py   behavioural classification from transaction shape
    engine.py      3 attribution methods, ranked by investigative usefulness
  tracing/
    tracer.py      forward BFS with haircut value attribution
  risk/
    scoring.py     weighted signals, typology, recommended actions
  api/v1/routes.py REST endpoints
```

### Design decisions worth knowing

**Valuation is keyed on contract address, never on token symbol.** A live
probe of one TRON exchange wallet found tokens presenting as `U S D T` (name
`T e t h e r`) and `USDTT` (`USDT Teller`, 16 decimals) sitting beside genuine
Tether. Pricing by symbol would let a counterfeit inflate a reported loss
without limit. Only contracts in the canonical registry are priced; anything
else is left explicitly *unvalued* rather than guessed.

**Nothing is silently discarded.** Spam, dust and counterfeits are *flagged*,
counted, and reported. The tracer records why it stopped at every node, so a
rate-limit gap can never be mistaken for the end of the money trail.

**Value uses the haircut method.** If an address receives $10,000 and sends
$6,000 to B, B is credited with 60% of traced value. Traced value can never
exceed what entered the graph — the property that makes the number safe to put
in a report.

**Risk scoring is rules, not ML — deliberately.** See "Known limitations".

**Hybrid data.** Every upstream response is cached to SQLite. Entries can be
*pinned* into an offline snapshot, so `OFFLINE_MODE=true` gives a complete
live-looking demo with the network unplugged. A warm trace runs in ~0.1s with
zero upstream calls.

---

## Known limitations

These are real and stated plainly — they matter more than the feature list.

**1. Named exchange attribution is sparse.** The system reliably detects
*that* an address is an exchange deposit address or omnibus wallet, but often
cannot name the operator. Building a named corpus needs sources we do not
have for free.

During development, four widely-cited "exchange hot wallets" were profiled
against the live chain. One showed **0 senders and 198 receivers** — a payout
wallet, not a deposit target. Two showed ordinary retail activity. Only one
behaved like an exchange at all. **Addresses repeated in blog posts do not
survive verification**, so none of them were committed.

The corpus is therefore built from what can be verified:
- **OFAC SDN** — 944 addresses, authoritative, dated, auto-ingested
- **Behavioural inference** — works with no label at all
- **Curated entries** — must pass `scripts/vet_labels.py` before commit

To close this gap: obtain addresses from VASP proof-of-reserves publications,
regulator filings, or direct written confirmation from the exchange. Never
from an unsourced list.

**2. Risk scoring is rule-based, not machine-learned.** The problem statement
asks for ML-assisted detection, and that is the right long-term answer — but a
supervised model needs labelled outcomes, and no labelled corpus of Indian
cyber-fraud wallets exists to train on. A model trained on invented labels
would produce confident, unexplainable output, which is worse than useless
when an officer must justify a freeze to a magistrate.

The architecture leaves the path open: the current signals are exactly the
feature vector such a model would use, and the weights become learned once
NCRP case outcomes provide labels. This is a data problem, not an
architecture problem.

**3. NCRP / SAHYOG integration is a contract, not a connection.** Neither
platform exposes a public API. `POST /api/v1/trace` accepts a wallet plus case
metadata in the shape such an integration would use, so the adapter can be
written without reshaping anything underneath — but it is not connected, and
the demo should say so.

**4. Prices are spot, not historical.** Valuing a two-year-old payment at
today's price is wrong. Stablecoins (most Indian fraud proceeds) are pegged
and therefore exact; volatile assets are flagged as approximate.

**5. Single-chain today.** TRON only. EVM and Bitcoin adapters are the next
build, and the `ChainAdapter` contract is already in place for them.

---

## Operational notes

**Get a free TronGrid key.** Keyless, TronGrid rate-limits at roughly 1
request/second and a cold multi-hop trace runs slowly; an early test returned
a trace with *zero nodes* because every request was throttled. Two minutes at
<https://www.trongrid.io/dashboard> fixes it. The client also self-tunes,
widening its interval permanently after a 429.

**Before a demo, seed the snapshot:**

```bash
.venv\Scripts\python.exe scripts\seed_snapshot.py TMuA6YqfCeX8EhbfYEg5y7S4DqzSJireY9
```

Then set `OFFLINE_MODE=true`.

**Refresh sanctions data** before any live use — `ingest_ofac.py` is dated,
and a stale sanctions list is worse than an absent one.
