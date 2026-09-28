# PULSE: KOSPI/KOSDAQ universe and resumable quote sweeps

## Listing source and migration

`KISListingSync` downloads the two **separate market masters** published by Korea
Investment & Securities. Market membership comes from the file, not ticker ranges,
company-name heuristics, a news link, or OpenDART's corpCode response.

Primary source/layout references:
- https://github.com/koreainvestment/open-trading-api/blob/main/stocks_info/kis_kospi_code_mst.py
- https://github.com/koreainvestment/open-trading-api/blob/main/stocks_info/kis_kosdaq_code_mst.py
- https://github.com/koreainvestment/open-trading-api/tree/main/stocks_info
- https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip
- https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip

The parser uses CP949 names, fixed ASCII trailer lengths 227/221 (excluding newline),
security group, ETP, SPAC and preferred-share fields. TLS verification remains enabled.
A layout change fails validation; do not silently disable validation to accept it.

An additive SQLite migration preserves company IDs, news links and old snapshots.
New metadata: `market_source`, `market_source_url`, `market_verified_at` (retrieval
verification time, **not an exchange-provided as-of date**), `listing_generation`,
`listing_active`, `security_group`, `security_type`, `corp_code`.
Existing unverified rows, including old `market='KRX'`, start inactive/UNKNOWN and do
not enter the automatic universe until the listing sync matches their ticker.
OpenDART enriches name/corp_code only; newly seen DART tickers have market UNKNOWN.
Neither DART nor generic registration overwrites verified listing markets.

Both masters must download and parse before an atomic replacement. Empty/one-market,
duplicate or malformed snapshots fail. Production also requires >=500 rows per market
and rejects a drop over 25% from the prior active membership. These are completeness
safeguards, not a claim about today's exact listing counts. Omitted old members become
inactive only on successful replacement; history is retained. Market transfers keep IDs.
A sync failure preserves the last verified universe and is reported by the runner.

## Explicit inclusion policy

`UniversePolicy` is independent of sync and quote execution:

| Security | Default | Control |
| --- | --- | --- |
| Ordinary company shares, REITs, investment companies, DRs, foreign company shares | Include | KOSPI/KOSDAQ active verified membership |
| SPAC | Include | `include_spac` |
| Preferred shares | Include | `include_preferred` |
| ETF | Exclude | `include_etf` |
| ETN | Exclude | `include_etn` |
| ELW, warrants/rights, other certificates, unknown classification | Exclude | Always excluded |
| KONEX, other exchanges, unverified or inactive listings | Exclude | Always excluded |

The universe is security/ticker-level, so one issuer can have ordinary and preferred
shares. Six-character alphanumeric codes are supported. No 500/5000 company-list cap,
news requirement, watchlist requirement or market-cap filter applies. Eligible enabled
TICKER watchlist entries come first, in P0/P1/P2 order; then verified recent event links;
then every remaining member in ticker order. Watchlist entries cannot bypass policy.
The order/membership is frozen for one sweep; changes enter the next sweep. Even a
watchlist larger than one batch cannot repeatedly reset the cursor or starve the rest.
An in-progress frozen sweep may finish attempts for a member removed by a later sync.

## Batches, pacing and recovery

| Setting | Default | Range / behavior |
| --- | --- | --- |
| `PULSE_BATCH_SIZE` / constructor `batch_size` | 100 | 1..500 **attempts**, including retries |
| `PULSE_REQUESTS_PER_SECOND` / `requests_per_second` | 1 | 0.1..10, sequential; configure within account allowance |
| `batch_seconds` | 45 | 1..60; stop scheduling new calls at deadline |
| `max_attempts` | 3 | 1..5, persisted for each sweep |
| transient retry delay | 2^attempt seconds | capped at 300; never blocks untouched members |
| HTTP 429 / KIS `EGW00201` | stop batch | durable cooldown >=60s, Retry-After seconds respected up to 3600s |
| HTTP 401/403 | stop batch | durable 300s cooldown |
| provider initialization failure | stop batch | no item attempts consumed; 60s cooldown |

These are conservative application settings, **not an asserted KIS contractual rate
limit**. Account/environment quotas and other applications still share the provider's
allowance. Pacing covers Worker calls on the **same database**; direct quote API calls,
other databases/processes outside this worker, and token issuance are not globally
coordinated. Keep a shared DB and reserve account capacity for those consumers.
An in-flight HTTP call can finish after the scheduling deadline (KIS token and quote
requests each have a 15s timeout). `collect()` never drains an entire universe in one call.

`market_sweeps` stores the immutable ordered membership's hash and retry limit;
`market_sweep_items` stores each ticker's ordinal, attempts, status, error class,
next retry time, and snapshot ID. `market_call_gate` persists inter-call spacing and
cooldown. Every invocation gets a unique SQLite lease owner; a 120s lease is renewed
before each request. Competing invocations return SKIPPED_LOCKED. A dead process's
lease expires; resumed IN_PROGRESS items consume their reserved attempt and retry
within budget. A lease fence rejects a late response from the former owner.

Snapshot insertion and the OK checkpoint share **one SQLite transaction**. A crash
cannot commit one without the other. An uncommitted remote read may be repeated;
remote exactly-once delivery is not claimed. Committed OK items are not re-requested
within that sweep. Unattempted items are scheduled before retries. Once complete,
the next collection starts a fresh sweep. Explicit `collect(tickers=[...])` remains a
bounded diagnostic mode (may bypass automatic membership policy); it has its own
stable scope and uses the same lock/rate gate, leaving the universe sweep intact.

Report `status` distinguishes SUCCESS, IN_PROGRESS, DEGRADED, FAILED, NO_TARGETS and
SKIPPED_LOCKED. `sweep_status` is RUNNING, COMPLETE, PARTIAL_FAILED or FAILED.
`attempted_count`, `saved_count`, `error_count` are **this batch**;
`target_count`, `completed_count`, `failed_count`, `remaining_count`, `retry_count`
are **the whole sweep**. Reports also expose ID/hash, retry limit, next retry time,
next-call time, cooldown and per-item batch results. Timestamps in pacing fields are
UTC Unix seconds. Only exception classes, not provider/credential-bearing messages,
are persisted. No-target automatic runs explicitly report NO_VERIFIED_UNIVERSE.

## Operation

From `AI_NEWSROOM_GITHUB_RC`:

```bash
python scripts/sync_company_master.py
python scripts/market_cycle.py --batch-size 100 --requests-per-second 1
python scripts/market_cycle.py --interval-minutes 1
python scripts/production_runner.py
```

The sync command always refreshes listing masters and optionally enriches DART when
configured. The production runner refreshes listing masters daily (60-minute retry
on failure), independently of the DART key/disable flag, **before** collecting quotes.
`AI_NEWSROOM_DISABLE_KIS` disables both automatic listing download and quote collection.
DART's existing disable flag only disables its enrichment. For offline/custom runners,
`listing_sync=False` explicitly disables automatic listing downloads.

The runner retains `market_progress` in its persistent status. Pending sweeps resume
on each polling cycle rather than waiting the normal five-minute fresh-sweep interval.
The standalone command runs **one batch** by default; rerun or use repeat mode to finish.
One request/sec requires at least N seconds for N tickers, plus polling, timeouts and
retries. This implements full-market **coverage over successive batches**, not
simultaneous real-time quotes for all companies. Size/rate/batch duration are reported
so operators can tune measured latency without silently exceeding their KIS allowance.
Completed sweep/item history is retained for audit; long-running installations should
add a retention policy before allowing indefinite growth.

## Offline verification

```bash
python -m pytest -q
```

`tests/test_pulse_universe.py` prohibits socket/request network calls. It covers CP949
market layouts, product classification, migration/DART protection, atomic sync rejection,
market transfers/delistings, 6,200 tickers with reconstruction between every batch,
watchlist priority, no starvation, deadline/rate pacing, bounded failures, credentials,
429/EGW cooldown, concurrent/lost leases, rollback after snapshot insertion, and an
actual child-process `os._exit` followed by DB recovery. Runtime integration verifies
listing-first operation without DART and continuation on the next poll. Mock success
is not live KIS credential/quota, market freshness, or Windows production validation.
