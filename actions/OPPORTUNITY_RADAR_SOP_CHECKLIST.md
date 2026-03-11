# Opportunity Radar OS - SOP Checklist (Print Version)

Owner: Operator  
System: JARVIS Opportunity Radar OS  
Timezone: Africa/Cairo  
Objective: Discover, score, rank, and decide high-value opportunities across all target platforms.

## A) Daily Startup Checklist (once after device/server availability)

- [ ] Confirm Render bridge is healthy (`/healthz` and `/api/v1/health`).
- [ ] Confirm MongoDB connectivity is healthy.
- [ ] Confirm Telegram bot is online and receiving commands.
- [ ] Confirm queue flow works (`create -> next -> complete/fail` sanity check).
- [ ] Confirm active API key is valid (`x-jarvis-key`).
- [ ] Confirm no stuck incidents from previous day.

## B) Repeating Runtime Checklist (every cycle)

- [ ] Collect latest signals from target platforms.
- [ ] Normalize signals into comparable opportunity candidates.
- [ ] Recalculate scores using Policy v1 formula.
- [ ] Update platform-level rankings.
- [ ] Emit decisions: `GO`, `PILOT`, `WATCH`, `DROP`.
- [ ] Enqueue top approved opportunities for execution projects.
- [ ] Log changes and movement (`up`, `down`, `stable`).

## C) Midday Control Check (1 time/day)

- [ ] Review queue depth by status (`queued`, `processing`, `completed`, `failed`).
- [ ] Review failed jobs and classify error type (schema/auth/network/logic).
- [ ] Re-run or quarantine repeated failures.
- [ ] Verify that no project exceeds capacity policy.

## D) End of Day Checklist

- [ ] Publish daily summary:
  - Top opportunities per platform
  - Global top opportunities
  - New `GO`/`PILOT` decisions
  - Dropped/deprioritized items
- [ ] Save daily snapshot for audit.
- [ ] Confirm incident queue is empty or assigned with ETA.
- [ ] Prepare next-day run queue.

## E) Friday Weekly Filter Checklist (Mandatory)

- [ ] Recompute all weekly scores with latest data and confidence.
- [ ] Apply weekly prioritization:
  - `Top`
  - `Watch`
  - `Deprioritize`
  - `Drop`
- [ ] Build and publish `Next Week Queue`.
- [ ] Execute portfolio decisions:
  - Promote high performers
  - Move weak performers to `Maintenance`/`Sunset`
- [ ] Freeze policy exceptions and document rationale.

## F) Monthly Portfolio Review (once/month)

- [ ] Evaluate project-level ROI trend.
- [ ] Identify projects to `Scale`.
- [ ] Identify projects to `Sunset`.
- [ ] Rebalance capacity for next month.
- [ ] Update assumptions and scoring calibration if needed.

## G) Incident SOP (when errors happen)

- [ ] Record incident (`time`, `project_id`, `stage`, `error`, `impact`).
- [ ] Categorize severity:
  - `P1`: bridge/auth/down
  - `P2`: queue stuck/repeated failures
  - `P3`: degraded non-critical behavior
- [ ] Apply immediate containment (retry, restart, isolate).
- [ ] Send Telegram alert with exact next action.
- [ ] Escalate to maintenance if unresolved in SLA window.
- [ ] Document root cause and prevention action.

## H) Hard Rules (Do Not Skip)

- [ ] Never run open-ended research loops without a decision.
- [ ] Never bypass Bridge API for writes.
- [ ] Never activate more than 2 new projects per week.
- [ ] Never keep low-performing projects active without review.
- [ ] Always keep summaries operator-friendly and action-oriented.

## I) Minimum Daily Output

At minimum, every day must produce:

- [ ] Updated ranked opportunities
- [ ] Clear decision list (`GO/PILOT/WATCH/DROP`)
- [ ] Action queue for execution projects
- [ ] Operator summary (simple language + next commands)

