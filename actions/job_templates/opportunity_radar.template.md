[JARVIS Production Job]
project_id: {{PROJECT_ID}}
vertical: opportunity_radar_os
niche_scope: {{NICHE}}
target_market: {{TARGET_MARKET}}
platforms: {{PLATFORMS}}
generated_at_utc: {{GENERATED_AT_UTC}}

MISSION:
Continuously discover and prioritize high-potential opportunities across the listed platforms.
Focus on both:
- short-term opportunities (fast wins)
- long-term opportunities (durable growth and revenue)

BUSINESS_GOAL:
{{BUSINESS_GOAL}}

CUSTOM_REQUIREMENTS:
{{CUSTOM_REQUIREMENTS}}

RESEARCH_SCOPE:
- Find niches/topics/offers with strong revenue and growth signals.
- Identify markets/regions with better monetization potential.
- Separate opportunities by horizon: `short_term`, `mid_term`, `long_term`.
- Detect risk factors (policy risk, volatility, execution risk).
- Include competitor context and saturation cues.

SCORING_POLICY_V1:
Use and report these dimensions (0-100):
- revenue_potential (R)
- growth_momentum (G)
- speed_to_result (S)
- durability (D)
- competition_gap (C)
- execution_ease (E)
- confidence (0.60-1.00)
- risk_penalty (0-15)

Formula:
final_score = ((0.30*R + 0.20*G + 0.15*S + 0.15*D + 0.10*C + 0.10*E) * confidence) - risk_penalty

Decision thresholds:
- GO: final_score >= 75 and revenue_potential >= 70 and confidence >= 0.75
- PILOT: 65-74
- WATCH: 50-64
- DROP: < 50

OUTPUT_JSON_SCHEMA:
{
  "type": "object",
  "required": [
    "project_id",
    "packet_type",
    "generated_at",
    "time_window",
    "platform_summary",
    "top_opportunities",
    "watchlist",
    "dropped",
    "sources"
  ],
  "properties": {
    "project_id": { "type": "string" },
    "packet_type": { "type": "string" },
    "generated_at": { "type": "string" },
    "time_window": {
      "type": "object",
      "required": ["from", "to"],
      "properties": {
        "from": { "type": "string" },
        "to": { "type": "string" }
      }
    },
    "platform_summary": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["platform", "signals_count", "top_signal"],
        "properties": {
          "platform": { "type": "string" },
          "signals_count": { "type": "number" },
          "top_signal": { "type": "string" }
        }
      }
    },
    "top_opportunities": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "opportunity_id",
          "platform",
          "title",
          "horizon",
          "reason",
          "score_components",
          "final_score",
          "decision",
          "execution_brief"
        ],
        "properties": {
          "opportunity_id": { "type": "string" },
          "platform": { "type": "string" },
          "title": { "type": "string" },
          "horizon": { "type": "string" },
          "reason": { "type": "string" },
          "score_components": {
            "type": "object",
            "required": [
              "revenue_potential",
              "growth_momentum",
              "speed_to_result",
              "durability",
              "competition_gap",
              "execution_ease",
              "confidence",
              "risk_penalty"
            ],
            "properties": {
              "revenue_potential": { "type": "number" },
              "growth_momentum": { "type": "number" },
              "speed_to_result": { "type": "number" },
              "durability": { "type": "number" },
              "competition_gap": { "type": "number" },
              "execution_ease": { "type": "number" },
              "confidence": { "type": "number" },
              "risk_penalty": { "type": "number" }
            }
          },
          "final_score": { "type": "number" },
          "decision": { "type": "string" },
          "execution_brief": {
            "type": "object",
            "required": ["core_angle", "first_action", "expected_outcome"],
            "properties": {
              "core_angle": { "type": "string" },
              "first_action": { "type": "string" },
              "expected_outcome": { "type": "string" }
            }
          }
        }
      }
    },
    "watchlist": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["title", "platform", "reason", "next_review_in_days"],
        "properties": {
          "title": { "type": "string" },
          "platform": { "type": "string" },
          "reason": { "type": "string" },
          "next_review_in_days": { "type": "number" }
        }
      }
    },
    "dropped": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["title", "platform", "reason"],
        "properties": {
          "title": { "type": "string" },
          "platform": { "type": "string" },
          "reason": { "type": "string" }
        }
      }
    },
    "sources": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["title", "url"],
        "properties": {
          "title": { "type": "string" },
          "url": { "type": "string" }
        }
      }
    }
  }
}

STRICT_OUTPUT_RULES:
- Return JSON object only (no markdown fences, no prose outside JSON).
- Must satisfy OUTPUT_JSON_SCHEMA exactly.
- Include at least 8 real source URLs.
- Ensure each `top_opportunities` item has a valid computed `final_score` and decision.

