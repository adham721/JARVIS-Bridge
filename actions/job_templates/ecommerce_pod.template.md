[JARVIS Production Job]
project_id: {{PROJECT_ID}}
vertical: ecommerce_pod
niche: {{NICHE}}
target_market: {{TARGET_MARKET}}
platforms: {{PLATFORMS}}
generated_at_utc: {{GENERATED_AT_UTC}}

BUSINESS_GOAL:
{{BUSINESS_GOAL}}

CUSTOM_REQUIREMENTS:
{{CUSTOM_REQUIREMENTS}}

RESEARCH_SCOPE:
- Demand signals by keyword and product angle.
- Competition density and differentiation gaps.
- Pricing and margin assumptions.
- Listing optimization patterns by marketplace.
- Creative directions for print-on-demand products.

OUTPUT_JSON_SCHEMA:
{
  "type": "object",
  "required": [
    "project_id",
    "packet_type",
    "generated_at",
    "market_analysis",
    "product_opportunities",
    "listing_assets",
    "creative_prompts",
    "sources"
  ],
  "properties": {
    "project_id": { "type": "string" },
    "packet_type": { "type": "string" },
    "generated_at": { "type": "string" },
    "market_analysis": {
      "type": "object",
      "required": ["top_keywords", "demand_summary", "competition_summary"],
      "properties": {
        "top_keywords": {
          "type": "array",
          "items": { "type": "string" }
        },
        "demand_summary": { "type": "string" },
        "competition_summary": { "type": "string" }
      }
    },
    "product_opportunities": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "marketplace",
          "product_type",
          "niche_angle",
          "demand_signal",
          "competition_signal",
          "pricing_range",
          "differentiator"
        ],
        "properties": {
          "marketplace": { "type": "string" },
          "product_type": { "type": "string" },
          "niche_angle": { "type": "string" },
          "demand_signal": { "type": "string" },
          "competition_signal": { "type": "string" },
          "pricing_range": { "type": "string" },
          "differentiator": { "type": "string" }
        }
      }
    },
    "listing_assets": {
      "type": "object",
      "required": ["title_prompt", "bullets_prompt", "description_prompt", "tags_keywords"],
      "properties": {
        "title_prompt": { "type": "string" },
        "bullets_prompt": { "type": "string" },
        "description_prompt": { "type": "string" },
        "tags_keywords": {
          "type": "array",
          "items": { "type": "string" }
        }
      }
    },
    "creative_prompts": {
      "type": "object",
      "required": ["design_prompt", "mockup_prompt", "variation_prompt"],
      "properties": {
        "design_prompt": { "type": "string" },
        "mockup_prompt": { "type": "string" },
        "variation_prompt": { "type": "string" }
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
- Keep recommendations practical and immediately executable.

