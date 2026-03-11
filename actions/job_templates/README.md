# JARVIS Multi-Domain Job Templates

This folder contains production-ready `input_markdown` templates for Mongo Bridge jobs.

## Templates

- `video_social.template.md`
  - For: YouTube, TikTok, Instagram, Facebook video/content projects.
- `ecommerce_pod.template.md`
  - For: Etsy, Amazon, Redbubble, print-on-demand, product listing strategy.
- `freelance_services.template.md`
  - For: service offers, lead generation, outreach scripts, client acquisition.
- `opportunity_radar.template.md`
  - For: cross-platform opportunity discovery, scoring, ranking, and decisions.

## Quick Start (CLI)

Use the helper script to render a template and enqueue it directly:

```powershell
python tools/mongo_enqueue_template_job.py `
  --project cat_pod_us `
  --template video_social `
  --niche "cat podcast content" `
  --platforms "youtube,tiktok,instagram,facebook" `
  --target-market us
```

Opportunity Radar job:

```powershell
python tools/mongo_enqueue_template_job.py `
  --project opportunity_radar_os `
  --template opportunity_radar `
  --niche "cross-platform high-revenue opportunity discovery" `
  --target-market us
```

Dry run without enqueue:

```powershell
python tools/mongo_enqueue_template_job.py `
  --project cat_pod_us `
  --template video_social `
  --niche "cat podcast content" `
  --dry-run
```

## Placeholders

Each template supports these placeholders:

- `{{PROJECT_ID}}`
- `{{NICHE}}`
- `{{TARGET_MARKET}}`
- `{{PLATFORMS}}`
- `{{BUSINESS_GOAL}}`
- `{{CUSTOM_REQUIREMENTS}}`
- `{{GENERATED_AT_UTC}}`

## Recommended Workflow

1. Pick the closest template.
2. Fill niche/platform/goal.
3. Enqueue job.
4. Run `Start <project_id>` from Custom GPT.
5. Review stored packet and iterate.
