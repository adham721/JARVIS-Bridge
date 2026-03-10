[JARVIS Production Job]
project_id: {{PROJECT_ID}}
vertical: video_social
niche: {{NICHE}}
target_market: {{TARGET_MARKET}}
platforms: {{PLATFORMS}}
generated_at_utc: {{GENERATED_AT_UTC}}

BUSINESS_GOAL:
{{BUSINESS_GOAL}}

CUSTOM_REQUIREMENTS:
{{CUSTOM_REQUIREMENTS}}

RESEARCH_SCOPE:
- Audience pain points and search intent in this niche.
- Current trend angles suitable for short and long-form content.
- Platform-specific format opportunities (hook, retention, CTA).
- Monetization and conversion opportunities from content.

OUTPUT_JSON_SCHEMA:
{
  "type": "object",
  "required": [
    "project_id",
    "packet_type",
    "generated_at",
    "strategy",
    "content_ideas",
    "prompts",
    "sources"
  ],
  "properties": {
    "project_id": { "type": "string" },
    "packet_type": { "type": "string" },
    "generated_at": { "type": "string" },
    "strategy": {
      "type": "object",
      "required": ["positioning", "audience_segment", "content_pillars"],
      "properties": {
        "positioning": { "type": "string" },
        "audience_segment": { "type": "string" },
        "content_pillars": {
          "type": "array",
          "items": { "type": "string" }
        }
      }
    },
    "content_ideas": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "title",
          "hook",
          "format",
          "platform_fit",
          "why_now",
          "cta"
        ],
        "properties": {
          "title": { "type": "string" },
          "hook": { "type": "string" },
          "format": { "type": "string" },
          "platform_fit": {
            "type": "array",
            "items": { "type": "string" }
          },
          "why_now": { "type": "string" },
          "cta": { "type": "string" }
        }
      }
    },
    "prompts": {
      "type": "object",
      "required": [
        "master_prompt",
        "script_prompt",
        "caption_prompt",
        "thumbnail_or_cover_prompt",
        "seo_prompt"
      ],
      "properties": {
        "master_prompt": { "type": "string" },
        "script_prompt": { "type": "string" },
        "caption_prompt": { "type": "string" },
        "thumbnail_or_cover_prompt": { "type": "string" },
        "seo_prompt": { "type": "string" }
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
- All prompts must be production-ready and directly usable.

