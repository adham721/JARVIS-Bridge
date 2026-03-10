[JARVIS Production Job]
project_id: {{PROJECT_ID}}
vertical: freelance_services
niche: {{NICHE}}
target_market: {{TARGET_MARKET}}
platforms: {{PLATFORMS}}
generated_at_utc: {{GENERATED_AT_UTC}}

BUSINESS_GOAL:
{{BUSINESS_GOAL}}

CUSTOM_REQUIREMENTS:
{{CUSTOM_REQUIREMENTS}}

RESEARCH_SCOPE:
- High-intent client segments and pain points.
- Offer positioning vs competitors.
- Outreach channels and message-market fit.
- Pricing anchors and packaging strategy.
- Content-led lead generation opportunities.

OUTPUT_JSON_SCHEMA:
{
  "type": "object",
  "required": [
    "project_id",
    "packet_type",
    "generated_at",
    "service_offer",
    "lead_channels",
    "outreach_prompts",
    "pricing_framework",
    "sources"
  ],
  "properties": {
    "project_id": { "type": "string" },
    "packet_type": { "type": "string" },
    "generated_at": { "type": "string" },
    "service_offer": {
      "type": "object",
      "required": ["headline", "target_client", "problem_solved", "outcome"],
      "properties": {
        "headline": { "type": "string" },
        "target_client": { "type": "string" },
        "problem_solved": { "type": "string" },
        "outcome": { "type": "string" }
      }
    },
    "lead_channels": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["channel", "approach", "why_effective"],
        "properties": {
          "channel": { "type": "string" },
          "approach": { "type": "string" },
          "why_effective": { "type": "string" }
        }
      }
    },
    "outreach_prompts": {
      "type": "object",
      "required": ["instagram_dm_prompt", "facebook_dm_prompt", "email_prompt", "proposal_prompt"],
      "properties": {
        "instagram_dm_prompt": { "type": "string" },
        "facebook_dm_prompt": { "type": "string" },
        "email_prompt": { "type": "string" },
        "proposal_prompt": { "type": "string" }
      }
    },
    "pricing_framework": {
      "type": "object",
      "required": ["starter_offer", "core_offer", "premium_offer"],
      "properties": {
        "starter_offer": { "type": "string" },
        "core_offer": { "type": "string" },
        "premium_offer": { "type": "string" }
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
- Include at least 5 real source URLs with direct links.
- Prompts must be actionable and ready to copy-paste.

