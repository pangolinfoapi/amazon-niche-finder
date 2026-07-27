# amazon-niche-finder — Free & Open Source Amazon Niche Research

[![Track](https://github.com/pangolinfoapi/amazon-niche-finder/actions/workflows/track.yml/badge.svg)](https://github.com/pangolinfoapi/amazon-niche-finder/actions/workflows/track.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org)
[![Built with Pangolinfo](https://img.shields.io/badge/built%20with-Pangolinfo-blue)](https://www.pangolinfo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Free & open-source Amazon niche research tool — discover blue-ocean niches with
> low competition.** `amazon-niche-finder` queries real Amazon demand and competition
> data through the [Pangolinfo Niche Data API](https://www.pangolinfo.com/amazon-niche-data-api/)
> and scores each niche with a simple **blue-ocean score** (search volume ÷ brand count).

Part of the [Pangolinfo open-source ecosystem](related-projects.md). Looking for where
to sell on Amazon without fighting 1,000 established brands? This tool surfaces niches
with real search demand but few competing sellers.

---

## Table of contents

- [Why use it?](#why-use-it)
- [Features](#features)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [How the blue-ocean score works](#how-the-blue-ocean-score-works)
- [Configuration](#configuration)
- [Automate it (GitHub Actions)](#automate-it-github-actions)
- [🌐 Pangolinfo ecosystem](#-pangolinfo-ecosystem)
- [FAQ](#faq)
- [Roadmap](#roadmap)

---

## Why use it?

- **Real data, not guesses.** Pulls live Amazon niche metrics (search volume, price
  bands, brand counts, return rates) via the Pangolinfo API.
- **Blue-ocean scoring.** Every niche gets a `score = search_volume_T90 ÷ (brand_count + 1)`
  so high-demand / low-competition niches float to the top.
- **Zero dependencies.** Pure Python standard library — no `pip install` headaches.
- **Daily automation.** A GitHub Action re-runs the scan every day and commits the
  report, so the repo doubles as a public, self-updating opportunity dataset.
- **Free to start.** Pangolinfo gives **200 free API calls** — enough to run this daily
  for weeks. [Get a free key →](https://tool.pangolinfo.com)

---

## Features

- 🔎 **Live niche metrics** — search volume (T90/T360), price bands, brand count,
  return rate, product count per niche
- 🌊 **Blue-ocean scoring** — `search_volume ÷ (brand_count + 1)` ranks opportunities
- 🚩 **Opportunity flags** — set hard floors (min search volume, price band, max
  brand count) so only genuinely attractive niches get flagged ★
- 🗄️ **SQLite history** — first-seen / last-seen tracking across daily scans
- 🤖 **Free daily automation** — GitHub Actions commits `reports/latest.md` every day
- 🧩 **Zero dependencies** — Python standard library only

---

## Architecture

```
        ┌─────────────────────────────────────────────┐
        │  amazon-niche-finder (this repo)             │
        │  niche_finder.py  ·  SQLite (data/niches.db) │
        └───────────────────┬─────────────────────────┘
                            │  streamable-HTTP (MCP)
                            │  tools/call → filter_niches
                            ▼
        ┌─────────────────────────────────────────────┐
        │   Pangolinfo MCP server                       │
        │   mcp.pangolinfo.com/mcp  (Bearer JWT)        │
        └───────────────────┬─────────────────────────┘
                            │  proxy → Amazon niche data
                            ▼
               Amazon niche metrics (JSON)
```

---

## Quick start

```bash
git clone https://github.com/pangolinfoapi/amazon-niche-finder.git
cd amazon-niche-finder

# 1. Create your config from the example
python niche_finder.py init

# 2. Set your free API key
export PANGOLIN_TOKEN="your_token_from_tool.pangolinfo.com"

# 3. Run a scan
python niche_finder.py run

# 4. See the top opportunities
python niche_finder.py report

# 5. Browse the full history
python niche_finder.py history
```

---

## How the blue-ocean score works

```
score = search_volume_T90 / (brand_count + 1)
```

A niche with **50,000 searches/90d** and only **40 brands** scores ~1,220 — a strong
candidate. The same search volume with **800 brands** scores ~62 — saturated. The
`opportunity` block in `niches.json` lets you set hard floors (minimum search volume,
price band, maximum brand count) so only genuinely attractive niches get flagged ★.

---

## Configuration

Edit `niches.json` to target your marketplaces and criteria:

```json
[
  {
    "label": "wireless-high-demand",
    "marketplace_id": "ATVPDKIKX0DER",
    "filters": {
      "search_volume_t90_min": 5000,
      "minimum_price": 15,
      "maximum_price": 80,
      "sort_field": "searchVolumeT90",
      "sort_order": "desc",
      "size": 10
    },
    "opportunity": {
      "min_search_volume_t90": 5000,
      "max_brand_count": 300,
      "min_price": 15,
      "max_price": 80
    }
  }
]
```

Common `marketplace_id` values: `ATVPDKIKX0DER` (US), `A2EUQ1WTGCTBG2` (CA),
`A1F83G8C2ARO7P` (UK), `A1PA6795UKMFR9` (DE), `A1VC38T7YXB528` (JP).

---

## Automate it (GitHub Actions)

The included workflow runs daily and commits `reports/latest.md`:

1. Fork / clone this repo.
2. Add your key as a **repository secret** named `PANGOLIN_TOKEN`
   (Settings → Secrets → Actions).
3. Enable Actions. That's it — a fresh niche report appears every day.

Full walkthrough + troubleshooting: [docs/SETUP.md](docs/SETUP.md).

---

## 🌐 Pangolinfo ecosystem

### 🛰️ More free tools by [@pangolinfoapi](https://github.com/pangolinfoapi)

- [amazon-keyword-rank-tracker](https://github.com/pangolinfoapi/amazon-keyword-rank-tracker) —
  track where your ASINs rank for target keywords
- [google-trends-tracker](https://github.com/pangolinfoapi/google-trends-tracker) —
  monitor keyword interest with Google Trends, daily
- [amazon-review-analyzer](https://github.com/pangolinfoapi/amazon-review-analyzer) —
  Amazon review sentiment + complaint/praise theme mining

### 🏗️ Built on the official Pangolinfo projects ([by @Pangolin-spg](https://github.com/Pangolin-spg))

- [pangolinfo-amazon-scraper](https://github.com/Pangolin-spg/pangolinfo-amazon-scraper)
  — official Python SDK for the Pangolinfo Scrape API (this tool's data source)
- [amazon-walmart-shopify-scrape-api](https://github.com/Pangolin-spg/amazon-walmart-shopify-scrape-api)
  ⭐ 56 — the underlying Scrape API for Amazon, Walmart, Shopify, Shopee, eBay
- [pangolinfo-amazon-scraper-cli](https://github.com/Pangolin-spg/pangolinfo-amazon-scraper-cli)
  ⭐ 8 — Agent/AI-friendly CLI for Amazon data collection
- [openclaw-skill-pangolinfo](https://github.com/Pangolin-spg/openclaw-skill-pangolinfo)
  ⭐ 8 — real-time web scraper skill for OpenClaw & AI agents

> Full map of official Pangolinfo projects, skills and the live MCP endpoint:
> [related-projects.md](related-projects.md).

---

## FAQ

**Is this affiliated with Amazon?** No. It is an independent open-source tool that reads
public Amazon marketplace data via the Pangolinfo API.

**Is my API key safe?** Yes. The key lives only in your GitHub Actions secret (or your
local shell env). It is never committed to the repo.

**How many niches can I scan for free?** Pangolinfo includes **200 free calls**. Each
`run` makes one API call per query in `niches.json`, so 2 queries ≈ 2 calls/day — the
free tier lasts a long time.

**Where do I get a key?** [tool.pangolinfo.com](https://tool.pangolinfo.com) — free, no
credit card.

---

## Roadmap

- [ ] Email digest of new ★ opportunities
- [ ] Historical score trend charts
- [ ] Cross-marketplace comparison view
- [ ] "Watchlist" of niches you're validating

---

## Contributing

Ideas and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Ecosystem map:
[related-projects.md](related-projects.md).

## License

MIT © pangolinfo
