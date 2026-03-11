# JARVIS Opportunity Radar - Operating Policy v1

Status: Approved  
Owner: JARVIS Core  
Applies To: Project-1 (Master Opportunity Radar) and all future project onboarding

## 1) Mission

Build a single, reliable system that continuously discovers and prioritizes high-potential opportunities across:

- YouTube
- TikTok
- Instagram
- Facebook
- Amazon
- Etsy
- Redbubble
- Freelance platforms/channels

Project-1 does not produce final content at scale.  
Project-1 produces ranked opportunities and decisions (`GO`, `PILOT`, `WATCH`, `DROP`, `SUNSET`) for downstream execution projects.

## 2) Scope Boundaries

In scope:

- Signal collection
- Opportunity normalization
- Scoring and ranking
- Weekly prioritization
- Portfolio decisions
- Handoff payloads for execution projects

Out of scope:

- Full production pipelines per channel
- Long creative execution loops per idea
- Manual editing and publishing operations

## 3) Core Architecture

### Cloud Layer (Always-on)

- Render Web Service (`jarvis-mongo-bridge`) on `starter` plan
- MongoDB (single source of truth)

### Local Control Layer (Device-managed)

- Telegram bot process
- Scheduler and supervisor processes
- Watchdog and alerting scripts

### GPT Layer

- Custom GPT actions through Bridge API only
- No direct database writes outside Bridge endpoints

## 4) Data Contracts

### Required collections (conceptual)

- `raw_signals`
- `opportunity_scores`
- `weekly_rankings`
- `approved_opportunities`
- `active_projects`
- `execution_feedback`
- `incidents`

### Per-project context pack (required before execution)

- Project identity and objective
- Templates (title/script/thumbnail/SEO/prompts)
- Competitor baseline
- Style guide
- Latest data snapshot
- Decision history

## 5) Scoring Model (v1)

Each factor is scored from 0 to 100:

- `R`: Revenue Potential (weight 0.30)
- `G`: Growth Momentum (weight 0.20)
- `S`: Speed to Result (weight 0.15)
- `D`: Durability / Long-term stability (weight 0.15)
- `C`: Competition Gap (weight 0.10)
- `E`: Execution Ease (weight 0.10)

Modifiers:

- `Confidence`: [0.60 - 1.00] (signal quality confidence)
- `RiskPenalty`: [0 - 15] (policy, ban-risk, volatility, operational risk)

Formula:

`FinalScore = ((0.30R + 0.20G + 0.15S + 0.15D + 0.10C + 0.10E) * Confidence) - RiskPenalty`

## 6) Decision Thresholds

- `GO`: `FinalScore >= 75` and `R >= 70` and `Confidence >= 0.75`
- `PILOT`: `65 <= FinalScore <= 74` (timeboxed test)
- `WATCH`: `50 <= FinalScore <= 64`
- `DROP`: `FinalScore < 50`

`SUNSET` (for active projects):

- Score below 60 for 3 consecutive weekly cycles, or
- Negative ROI trend for 4 weeks, or
- Growth stagnation for 6 weeks

## 7) Cadence and Timeboxes

- Every 30 minutes: fast social signals collection
- Every 3 hours: marketplace/freelance signals collection
- Every 6 hours: scoring refresh
- Daily: top opportunities summary
- Weekly (Friday): full prioritization filter and next-week queue
- Monthly: portfolio review and capacity rebalance

No open-ended research loops are allowed.  
Each cycle must end with a decision state.

## 8) Weekly Friday Filter

For each platform and global portfolio:

- Re-rank all weekly opportunities
- Mark movement (`up`, `down`, `stable`)
- Output:
  - Top opportunities (immediate queue)
  - Watchlist
  - Deprioritized
  - Dropped

Mandatory output artifact:

- `Next Week Queue` ready for enqueue and execution

## 9) Capacity Management Rules

- Maximum new project activations per week: 2
- Target concurrently active execution projects: 8-10
- Any opportunity above capacity enters `WATCH` until a slot is available
- Capacity reallocation is driven by monthly portfolio review

## 10) Reliability and Observability

### Minimum runtime standards

- Bridge API is critical path and must be always available
- Telegram bot can be local-managed with auto-start and watchdog restart

### Incident levels

- `P1`: Bridge unavailable or auth broken
- `P2`: Queue stuck / repeated job failures
- `P3`: Non-critical degradation (latency, delayed summaries)

### Required telemetry

- Request success/failure rates
- Queue depth by project/status
- Processing time distributions
- Retry counts and failure reasons
- Weekly decision conversion (`GO -> Active -> ROI`)

## 11) Alerts and Operator UX

Alerts must include:

- Project ID
- Stage that failed
- Error class and short reason
- Last successful step
- Recommended operator action

Telegram operator messages must stay simple:

- What happened
- Why it matters
- Exact next command to run

## 12) Security and Access

- All writes go through Bridge API with `x-jarvis-key`
- Keep API keys in environment variables only
- No secrets in job payloads
- Limit write surface to documented endpoints

## 13) Change Management

Policy changes require:

- Version bump (`v1`, `v1.1`, etc.)
- Change summary
- Effective date
- Rollback path

No production architecture changes without an update to this policy.

## 14) Definition of Ready (Project Activation)

A new execution project is `Ready` only if:

- Context pack is complete
- Template set is defined
- Competitor baseline exists
- Scoring baseline exists
- Decision state is `GO` or `PILOT`

## 15) Definition of Done (Cycle)

A cycle is done when:

- Opportunity decisions are recorded
- Queue is updated
- Summary is delivered
- Errors (if any) are logged with owner and next action

