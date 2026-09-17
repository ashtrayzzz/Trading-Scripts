# Build sequence and acceptance criteria

This roadmap implements the architecture incrementally. No stage is deployed by creating this document, and no provider purchases or recurring automations have been initiated.

## Stage 0 — establish the first operating scope

Select one playbook, its instrument universe and horizon, one accessible price source, and one account profile. Confirm account product/stage and actual rules; decide how positions and fills will be imported. Select local or hosted operation, expected uptime, monthly data/AI budget and notification channel.

Until those selections are made, implementation can use labeled synthetic fixtures and an unverified account profile. Fixture outcomes must remain distinct from actual market results. Vendor access and rule verification are dependencies for live usefulness, not blockers to architecture design.

Acceptance: documented playbook conditions, permitted data source, explicit horizon, account fields with verification status, and a written meaning of a qualifying candidate.

Define the first account's risk policy alongside the playbook: per-trade and aggregate risk budgets, concentration and position limits, daily loss/drawdown conventions, provider-limit buffer, and conditions for reduced risk, pause and resumption. Specify calculation bases and scenario assumptions. Required thresholds need owner approval before live plan readiness; fixtures may exercise synthetic policies clearly labeled as such.

## Stage 1 — complete the smallest useful loop

Build instrument identity, one market adapter, raw retention, data validation, closed-bar features, one technical scanner, candidate records, a transparent scoring profile, account eligibility, review queue and journal. Start with a manually entered or imported account snapshot if no appropriate read-only account integration exists. The dashboard must show its age.

Acceptance: one candidate can be traced to its source data and configuration, evaluated for an account, accepted/deferred/rejected, linked to recorded fills and evaluated after its horizon. The same inputs and version reproduce the same deterministic outputs. Stale or unverified account state yields unknown eligibility. Re-importing data or fills is idempotent.

Include risk policy versions, operating levels, preliminary account screening and final plan assessment in this stage. Acceptance also requires that a high-scoring candidate cannot bypass a risk limit; missing required risk settings prevent plan readiness; concurrent plans cannot reuse reserved budgets; and reduction/pause/resume behavior follows the approved policy. Show configured limits, used/remaining budgets and reasons in the review interface.

## Stage 2 — reliable unattended collection and review

Add background schedules, dependency recomputation, job retries, data health, delivery outbox, daily review digest and backups. Add a second account to verify that intelligence is shared while policy and exposure remain account-specific.

Acceptance: a provider outage is visible, affected candidates degrade or expire, recovery fills gaps without duplicate alerts, and the database can be restored. One opportunity can be eligible in one account and blocked in another with reproducible reasons. Schedules and alert channels are explicitly configured before activation.

## Stage 3 — catalysts, sentiment and narrative velocity

Add a curated event calendar, source documents, entity matching, evidence families and a bounded extraction task. Compute velocity and source breadth in code; use AI to classify and summarize cited text.

Acceptance: a rescheduled event updates affected candidates; copied stories do not count as independent corroboration; incorrect entities and unsupported citations are caught in a labeled review set. Show the incremental value and cost of each enrichment relative to the technical baseline.

## Stage 4 — macro, geopolitics and cross-asset research

Add macro vintages and regime features, sourced exposure relationships, causal scenarios, synchronized cross-asset series and relationship-stability checks. Enable modules only for playbooks that can use them on the relevant horizon.

Acceptance: historical replay cannot see future revisions; asynchronous market closures do not create false dislocations; a geopolitical thesis shows its evidence chain, uncertainty, counter-scenario and observable invalidation. Scenarios remain distinct from confirmed effects.

## Stage 5 — measured feedback and controlled changes

Build cohort reports, actual versus hypothetical outcome separation, cost-aware walk-forward evaluation, baseline comparisons and shadow-version comparisons. AI can summarize findings and propose changes; the owner approves promotion of a new configuration.

Acceptance: results report sample size and uncertainty, preserve rejected candidates, avoid future-data leakage and attribute behavior to exact strategy versions. A new version can be compared and rolled back without rewriting old decisions.

## Implementation layout when coding begins

```text
src/
  connectors/       market, documents, calendars, account imports
  core/             identities, time, quality, shared contracts
  intelligence/     technical, narrative, geopolitical, macro, events, dislocations
  opportunities/    assembly, scoring, expiry, evidence lineage
  accounts/         rule versions, exposure, eligibility, sizing proposals
  journal/          decisions, plans, fill reconciliation, outcomes
  evaluation/       replay, cohorts, baselines, version comparisons
  orchestration/    jobs, retries, source health, outbox
  api/              dashboard and review interfaces
config/             reviewed playbooks, source settings, account profiles
migrations/         database schema evolution
tests/              contract, replay, policy and reconciliation cases
docs/               architecture and operating decisions
```

Keep this structure conceptual until the runtime and providers are selected. Avoid creating unused service infrastructure. First decide where durable records live and how a run is reproduced; defer specialist search, vector, graph and streaming infrastructure until demonstrated needs arise.

## Priority validation scenarios

| Scenario | Required result |
| --- | --- |
| Partial candle or late bar | No closed-bar signal from an unfinished interval; later corrections create revisions |
| Duplicate news and repeated ingestion | One evidence family; no repeated opportunity inflation |
| Missing required module | Candidate stays incomplete rather than acquiring a misleading neutral value |
| Rules or account snapshot missing/stale | Unknown account eligibility and clear missing-data reasons |
| Equity near a floor; existing and pending exposure | Scenario checks use remaining headroom without double counting marked losses |
| Daily reset boundary or revised rule | Apply the correct time convention and effective policy version |
| Changed price/exposure between review and plan | Recompute fit and retain the original decision snapshot |
| Strong signal exceeds owner risk budget | Block plan readiness regardless of merit or provider headroom |
| Two plans compete for remaining risk budget | Atomically reserve committed exposure; do not approve both against the same remaining budget |
| Risk level reduced, paused or missing inputs | Apply the configured reduced budget or block new-exposure readiness; retain monitoring of existing positions |
| Pause condition clears | Require configured recovery conditions and recorded owner resume approval before relaxing the state |
| AI invents a reference or follows embedded instructions | Reject unsupported output; no policy or account mutation |
| Repeated fill import and partial exits | Stable realized totals and reconciled remaining quantity |
| Macro revision or delisted instrument in replay | Only historically available values and universes enter evaluation |
| Candidate never traded | Keep it in signal evaluation, outside actual portfolio P&L |
| Job retry after uncertain delivery | Reconcile delivery status; avoid uncontrolled alert repetition |

## Decisions intentionally left open

The first build needs the initial asset universe and trading horizon; exact Breakout product/stage and other account mandates; owner-approved risk limits and operating-level rules; available data subscriptions and account exports; preferred hosting/uptime; monthly operating budget; and alert channel. These affect configuration and provider selection, while the module contracts and evidence-to-decision flow remain stable.
