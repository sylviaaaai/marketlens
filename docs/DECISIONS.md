# Architecture Decision Records

## ADR-001 — Consumer-first product
Status: Accepted; 2026-10-09
Decision: Prioritize consumer buying research rather than seller sourcing or margin optimization.

## ADR-002 — CSV-first boundary
Status: Accepted; 2026-10-09
Decision: Start with permission-aware CSV and deterministic analysis; official API adapters only after data access verification. Benefit: reliable MVP independent of social platform crawling.

## ADR-003 — Lightweight stack
Status: Accepted; 2026-10-09
Decision: Python + SQLite + Streamlit + pytest; optional LLM added later, no early multi-agent architecture.

## ADR-004 — Evidence and uncertainty as first-class product features
Status: Accepted; 2026-10-09
Decision: Product matches, review assertions, prices and recommendations include source, time, coverage, and unknown handling. No black-box composite value score in V0.1.

## ADR-005 — Superseding merchant MVP planning
Status: Accepted; 2026-10-09
Decision: Keep early merchant/price-tracker docs under `docs/legacy_v00/` strictly for historical reference; the Consumer Edition spec is the sole implementation target.
