# Phase 13: Server-Side CPU Utilization Optimization - Implementation Prompts

Priority: 🟠 HIGH  
Duration: 1-1.5 weeks  
Goal: Reduce steady-state and peak CPU utilization of the server under typical and worst-case workloads, without sacrificing correctness or real-time behavior.

---

Scope

Focus exclusively on backend hot paths:
- Event ingestion and parsing
- Database read/write patterns
- Aggregations and analytics
- WebSocket broadcasting and JSON serialization
- Background tasks overlap and scheduling
- Logging overhead
- Caching and memoization

Reference modules to optimize
- Events/log pipeline: [storj_monitor/log_processor.py](storj_monitor/log_processor.py)
- Database layer: [storj_monitor/database.py](storj_monitor/database.py)
- Financial: [storj_monitor/financial_tracker.py](storj_monitor/financial_tracker.py)
- Storage: [storj_monitor/storage_tracker.py](storj_monitor/storage_tracker.py)
- Reputation: [storj_monitor/reputation_tracker.py](storj_monitor/reputation_tracker.py)
- Analytics: [storj_monitor/analytics_engine.py](storj_monitor/analytics_engine.py)
- Server/WS: [storj_monitor/server.py](storj_monitor/server.py), [storj_monitor/websocket_utils.py](storj_monitor/websocket_utils.py)
- Tasks orchestration: [storj_monitor/tasks.py](storj_monitor/tasks.py)
- State and broadcast: [storj_monitor/state.py](storj_monitor/state.py)

---

Acceptance Criteria

Performance targets (on representative node data with 2-4 nodes):
- 30-40% reduction in average CPU during steady-state (baseline vs optimized)
- ≤ 50% CPU at 200 events/sec while maintaining real-time WS updates
- 95th percentile WS broadcast latency ≤ 200 ms under burst (2x steady-state)
- Aggregation endpoints return in ≤ 300 ms for the last 24h window
- No regression in correctness (all tests pass)

Verification
- Side-by-side A/B profiling report attached to docs/completed/PHASE_13_COMPLETE.md
- Benchmark scripts and results checked into tests/perf/ with instructions

---

Step 1: Measurement and Baseline

Tools
- py-spy to capture function-level CPU hot spots (sampling)
- scalene for CPU vs. memory attribution (optional)
- cProfile + snakeviz for deterministic runs of heavy code paths
- pytest-benchmark to capture micro/mid-level improvements

Add scripts
- tests/perf/profile_ingestion.py: feeds sample logs at configurable rate; measures CPU and throughput
- tests/perf/profile_ws.py: spins up app, simulates N clients, measures broadcast latency and CPU
- tests/perf/profile_db.py: runs key DB queries/aggregations in loops to capture hotspots

Benchmark scenarios
1) Ingestion-only: parse + write events at 100/200/400 events/sec
2) WS-only: broadcast synthetic updates at 1/2/5 updates/sec with 3-10 clients
3) Mixed: ingestion + periodic analytics and financial updates
4) Aggregation load: historical queries (24h window), repeated

Artifacts
- Store raw reports in tests/perf/reports/{timestamp}/
- Summarize flamegraphs and top-20 functions by cumulative CPU in markdown

---

Step 2: Quick Wins (Low-Risk Optimizations)

A. Logging
- Demote non-critical info logs to debug in hot loops: [storj_monitor/log_processor.py](storj_monitor/log_processor.py:460), [storj_monitor/database.py](storj_monitor/database.py:441), [storj_monitor/financial_tracker.py](storj_monitor/financial_tracker.py:1486)
- Add rate-limited logging helper and wrap hot-path logs

B. JSON serialization
- Pre-serialize stable payload frames for WS where possible
- Use orjson if available (feature-flag) for faster dumps in [storj_monitor/server.py](storj_monitor/server.py:1289) and [storj_monitor/websocket_utils.py](storj_monitor/websocket_utils.py:1)

C. WebSocket broadcast coalescing
- Coalesce multiple updates within a 50-100 ms window tick in [storj_monitor/websocket_utils.py](storj_monitor/websocket_utils.py:1)
- Debounce high-frequency UI updates (earnings, analytics) to 1-2 Hz

D. Caching small pure computations
- Memoize repeated string/formatting and percentile calculations where inputs repeat (e.g., top-k latency buckets)
- Cache satellite id-to-name mapping

E. Avoid unnecessary copies/allocations
- Replace list(...) in hot loops with iterators where safe
- Use local variable binding for repeated attribute lookups

---

Step 3: Database CPU Reduction

A. Write paths
- Batch inserts (already optimized) — verify batch sizes and commit frequency in [storj_monitor/database.py](storj_monitor/database.py:401)
- Ensure PRAGMA settings and connection reuse in high-frequency contexts
- Confirm WAL is set and fsync frequency acceptable

B. Read paths
- Add missing composite indexes for frequent filters and ranges (verify via EXPLAIN QUERY PLAN)
- Clamp query windows aggressively (e.g., events >= cutoff_iso)
- Add LIMIT in UI that doesn’t require full scans (e.g., for charts and tables)

C. Aggregations
- Pre-aggregate where cost-effective (hourly or minutely) to reduce CPU at query time
- Validate existing hourly_stats coverage and expand if missing chart windows
- Use temp tables for repeated heavy calculations within a single request cycle if needed

D. Concurrency
- Ensure thread pool for DB work (app["db_executor"]) has right size: CPU-bound parts should remain small; IO-bound can be higher
- Convert obvious blocking sequences to run_in_executor where safe

Deliverable
- Documented EXPLAIN QUERY PLAN comparisons for key queries in docs/completed/PHASE_13_COMPLETE.md

---

Step 4: Background Task Scheduling and Overlap

A. Stagger background tasks
- Offset starts for reputation_polling_task, storage_polling_task, financial_polling_task to avoid synchronized CPU bursts in [storj_monitor/tasks.py](storj_monitor/tasks.py:1)
- Introduce jitter (±10-15%) on repeat intervals

B. Analytics cadence
- Reduce analytics frequency if no material change (gate by input deltas)
- Cache baselines and re-use between runs in [storj_monitor/analytics_engine.py](storj_monitor/analytics_engine.py:17)

C. Backpressure
- If ingestion queue grows, temporarily pause or downsample WS updates (publish system load state to clients)

---

Step 5: WebSocket Efficiency

A. Differential payloads
- Send only changed keys vs. full payloads for heavy cards
- For large arrays, transmit bounded top-N or hashed deltas

B. Batching and chunking
- Combine multiple message types into a single frame when under load
- Avoid concurrent dumps per client; serialize on a broadcast worker

C. Client-driven throttling
- Respect client-sent hints for lower frequency updates when in background tab

---

Step 6: Micro-Optimizations

- Replace repeated datetime.now() calls in tight loops with precomputed values
- Hoist constant computations out of loops (e.g., 1024**3)
- Prefer tuple over list when creating fixed small sequences repeatedly
- Use list pre-allocation where lengths are known (e.g., building tuples for executemany)

---

Step 7: Feature Flags and Config

Add CPU optimization toggles to [storj_monitor/config.py](storj_monitor/config.py:1):
- PERF_WS_COALESCE_MS = 75
- PERF_USE_ORJSON = False
- PERF_DB_EXECUTOR_MAX_WORKERS = None  # auto-tune
- PERF_ANALYTICS_JITTER_PCT = 0.15
- PERF_MAX_BROADCAST_HZ = 2
- PERF_ENABLE_DIFF_PAYLOADS = True
- PERF_ENABLE_LOG_RATE_LIMIT = True

Make all optimizations opt-in initially; roll forward after validation.

---

Step 8: Test Plan

Unit tests
- Validate diff payloads and coalescing logic (no lost updates)
- Assert logging suppression does not hide errors (mock logger)
- Verify caches invalidate on config changes

Integration tests
- WS under load: N clients, assert average and p95 latency targets
- Ingestion → DB → broadcast pipeline at 200 events/sec: throughput and correctness
- Aggregations return within 300 ms with warm caches and indexes

Benchmarks
- pytest-benchmark for hot functions (e.g., latency histogram calculation, percentile calculation)
- Standalone perf scripts producing CSV of CPU% over time (psutil) and throughput

Acceptance
- Attach before/after graphs for CPU% and latency
- All tests pass with no flakiness increases

---

Prompts for Code Mode

Profiling and Baseline
1) Add tests/perf tooling and capture baseline CPU% and latency
2) Generate py-spy flamegraphs for mixed workload; commit assets under tests/perf/reports/

Low-Risk Optimizations
3) Implement WS coalescing and diff payloads in [storj_monitor/websocket_utils.py](storj_monitor/websocket_utils.py:1) and [storj_monitor/server.py](storj_monitor/server.py:1289); gate via config flags
4) Introduce orjson (optional) guarded by PERF_USE_ORJSON in [storj_monitor/server.py](storj_monitor/server.py:1289)
5) Apply logging rate-limits in hot loops (log_processor, database, financial_tracker)

Database
6) Run EXPLAIN on hot queries; add or adjust indexes in [storj_monitor/database.py](storj_monitor/database.py:130); clamp windows and add LIMIT where safe
7) Validate batch sizes and commit cadence; avoid unnecessary connection churn

Tasks and Analytics
8) Add jitter and staggering in [storj_monitor/tasks.py](storj_monitor/tasks.py:1); reduce redundant analytics and reuse cached baselines in [storj_monitor/analytics_engine.py](storj_monitor/analytics_engine.py:17)

Verification
9) Re-run perf scenarios; compare CPU/latency vs baseline; commit results
10) Draft docs/completed/PHASE_13_COMPLETE.md with A/B graphs and configuration changes

---

Documentation/Tracking

- Update [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) and [docs/MASTER_ROADMAP.md](docs/MASTER_ROADMAP.md) when Phase 13 begins and completes
- Create docs/completed/PHASE_13_COMPLETE.md on completion, including:
  - Summary of changes
  - Before/after CPU and latency charts
  - Config flags adopted as defaults