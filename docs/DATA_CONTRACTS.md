# Data contracts — version 0.1

These are design specifications, not implemented schemas. IDs are opaque stable identifiers; timestamps are UTC with timezone offsets. Money and quantities use decimal representations with explicit units/currencies. Versioned snapshots are immutable; corrections reference prior versions.

## Shared envelope

Every derived record carries `id`, `schema_version`, `created_at`, `as_of`, `available_at`, `producer`, `producer_version`, `run_id`, `input_ids`, `quality_status`, and optional `expires_at`. `as_of` describes the observed state; `available_at` is when this system could first use it. Replay uses both and never exposes later records prematurely.

Statuses distinguish `valid`, `degraded`, `unknown`, `quarantined` and `expired`. A missing numeric value is null with a reason, never a fabricated zero. Producers must declare required fields, units, allowed values and dependencies. Referenced input versions must remain resolvable.

## Core records

| Record | Required domain fields | Key invariant |
| --- | --- | --- |
| Instrument | instrument_id, asset_id, venue, symbol, instrument_type, base/quote currencies, contract specifications, valid interval | Venue and contract identity are explicit; symbol alone is not a key |
| SourceDocument | source_id, provider_record_id, URL/reference, publication/observed/ingested times, content hash, rights metadata, raw reference | Repeated and syndicated documents can share one evidence family |
| MarketObservation | instrument_id, source_id, observation type, event time, values with units; interval/closure for bars | Unique source/instrument/type/time/version; revisions preserved |
| Claim | document_id, evidence location/excerpt where permitted, entity_ids, claim type, extracted text, extraction version, verification status | Fact, inference, scenario and rumor are distinct; every claim has provenance |
| Event | event_id, event type, entities, scheduled time or time range, time confidence, status, source_ids, revision | A reschedule revises the same event; cancellation does not delete history |
| ExposureEdge | from_entity, to_entity, relationship, direction, magnitude if known, valid interval, evidence_ids | Inferred transmission scenarios do not masquerade as measured exposure |
| FeatureSnapshot | instrument/entity, horizon, feature definition/version, values, source input IDs, window | Units, sampling frequency and historical availability are explicit |
| ModuleSignal | module, instrument/entity, direction or contextual stance, horizon, feature_ids, evidence_ids, conditions, confidence details, expiry | A signal may add context without suggesting a trade direction |
| OpportunityVersion | opportunity_id, revision, playbook/version, instrument(s), direction, horizon, signal/evidence IDs, thesis, counterevidence, trigger/invalidation, score breakdown, expiry | Same thesis revisions retain identity; materially different direction/horizon is a separate candidate |
| AccountRuleVersion | account_id, product/stage, effective interval, verified_at, source references, permissions, equity-floor/reset mechanics, portfolio limits | Unverified required rules cannot yield eligible status |
| RiskPolicyVersion | account_id, effective interval, approved_by/at, limits with units/calculation bases, scenario definitions, related-asset groups, buffers, reduction/pause/resume rules | Owner-approved limits apply alongside provider rules; score changes cannot relax them |
| RiskStateSnapshot | account_id, risk_policy_version_id, account_snapshot_id, prior state reference, normal/reduced/paused/unknown, reason codes, measured limits/budgets, transition time, resume approval reference if required | State transitions are reproducible; missing required inputs cannot authorize new exposure |
| AccountSnapshot | account_id, observation time, equity/balance/currencies, position/pending-order IDs, source, reconciliation status | Missing positions or pending orders make relevant risk checks unknown |
| EligibilitySnapshot | opportunity_version, account_snapshot_id, rule_version_id, risk_policy_version_id, risk_state_snapshot_id, preliminary/final assessment stage, plan_version reference for final assessments, reservation snapshot references, eligible/blocked/unknown, reason codes, exposure/scenario results, validity window | Final eligibility requires an exact plan and current risk inputs; preliminary screening is not plan approval |
| Decision | opportunity_version, eligibility_snapshot_id, actor, timestamp, accepted/deferred/rejected, reason, optional plan_id | Acceptance records an intention, not a fill |
| TradePlan | plan_id, revision, decision_id, account_id, instruments, direction, entry assumptions, stop/invalidation, quantity, horizon, estimated costs, initial risk definition | Plan readiness requires a valid final assessment of this exact immutable revision |
| RiskReservation | account_id, plan_version, final_eligibility_snapshot_id, committed quantity and scenario-risk amounts, status, created/released times, linked order/fill IDs | Readiness and reservation commit atomically against current account/reservation versions; fills replace reserved exposure without double counting |
| Fill | account_id, external order/fill ID, instrument_id, timestamp, side, quantity, price, fees/currency, optional plan link | Import idempotency prevents counting a fill twice; unmatched fills remain reconcilable |
| Outcome | plan/opportunity reference, actual/hypothetical, evaluation definition/version, horizon end, net results, excursion metrics, data completeness | Hypothetical marks never enter realized account P&L |
| JobRun / AlertDelivery | job/event key, input version, attempts, status, timestamps, error; recipient/channel for alerts | Retries do not create duplicate candidates or uncontrolled notifications |

## Module interface

`evaluate(context, input_snapshot_ids, module_config_version) → observations + diagnostics`

Context includes instrument/entity universe, horizon, decision cutoff and run ID. Modules read their declared dependencies and write their own outputs. They do not update another module's records, rules or decisions. AI enrichment follows the same contract and is validated before persistence.

Each run returns success, partial or failed; dependency freshness; inputs used; records produced; and diagnostics. The orchestrator propagates dependency changes to affected downstream records. An unavailable optional module is visible as missing context, while a required module failure prevents qualification under that playbook.

## Event contracts and states

Operational events include `evidence.ingested`, `evidence.corrected`, `feature.updated`, `signal.created`, `signal.expired`, `opportunity.revised`, `account.updated`, `eligibility.changed`, `decision.recorded`, `fill.imported`, and `outcome.ready`. Each carries an event ID, entity/version reference, occurred/recorded times, correlation ID and idempotency key. Events announce committed records; a transaction/outbox boundary prevents announcing writes that never committed.

Candidate lifecycle: detected → incomplete or scored → active → invalidated or expired. A candidate can be revised while active. Review decisions attach to specific versions and do not erase earlier decisions. Account eligibility is a separate lifecycle and can change without changing opportunity merit. Trade plans and fills have their own states so partial fills, cancellations and manual trades are representable.

Risk policy and state changes invalidate affected eligibility assessments. An initial review decision may reference preliminary screening; a subsequent plan requires final assessment before readiness. Readiness references the immutable plan and assessment rather than rewriting the original review decision. Preserve reservations for exposure that may still execute; release or replace them only when cancellation, expiry before submission, or reconciled orders/fills establishes the remaining commitment.

## Initial configuration boundaries

- Playbook: universe, horizons, required/optional modules, feature definitions, component weights/transforms, evidence-family caps, missing-input policy, expiry and alert conditions.
- Account: provider/product/stage, base currency, verified rules and effective dates, user mandate, source freshness budget, portfolio constraints and operational buffer.
- Risk policy: approved per-trade and aggregate budgets, concentration groups/caps, loss/drawdown conventions, position limits, scenario assumptions, reservation handling and operating-level transition/resume rules. Each control is explicitly configured or marked not applicable with a reason; missing required settings remain unknown.
- Source: provider, permitted storage/use, instruments/entities, identity mapping, expected delay, retry/rate limits and health threshold.
- AI task: model/prompt/schema version, approved evidence access, timeout/spend cap, cache policy and extraction evaluation set.
- Delivery: selected channels, time zone, review schedule, quiet hours, material-change criteria, cooldown and retry policy.

Unknown account values remain unset. Initial project configuration must not contain invented live credentials, account balances, risk percentages or provider entitlements.
