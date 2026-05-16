# TTD Pipeline

Standalone admin panel for sourcing Shopify competitor products, matching them against Zendrop, approving candidates, generating product assets, and uploading Shopify products as drafts.

The project is designed to run on a VPS with Docker. It does not require Shopify plugins.

## What It Does

TTD Pipeline automates the operational flow for a dropshipping store:

1. Collect products from competitor Shopify stores.
2. Filter products by gender, season, and excluded categories.
3. Search Zendrop for supplier candidates.
4. Build match preview cards for manual approval.
5. Generate product content with OpenAI.
6. Generate model product images with Gemini image generation.
7. Upload approved products to Shopify Admin API as `DRAFT`.

Shopify products are never auto-published.

## Current Pipeline

```text
Admin login
  -> Sources and filters
  -> Competitor scraping
  -> Zendrop search
  -> Match preview
  -> Manual approval
  -> Product enhancer
  -> Image enhancer
  -> Shopify draft upload
```

## Stack

- FastAPI admin web app
- Python worker for long-running pipeline jobs
- PostgreSQL for durable pipeline state
- Redis for infrastructure readiness
- Docker Compose for local and VPS deployment
- Playwright-compatible scraping structure
- Zendrop MCP API
- OpenAI Responses API for product content
- Gemini image generation for model photos
- Shopify Admin GraphQL API for draft products

## Services

| Service | Purpose |
| --- | --- |
| `web` | Admin panel and API |
| `worker` | Background pipeline executor |
| `postgres` | Persistent pipeline state |
| `redis` | Queue-ready infrastructure |
| `pipeline` | CLI/debug container |

## Quick Start

```bash
cp .env.example .env
docker compose up -d --build web worker
```

Open:

```text
http://localhost:8080
```

Default local login:

```text
admin / admin
```

Change the admin credentials in `.env` before exposing the app on a VPS.

## Environment

Required for full production flow:

```env
ZENDROP_API_TOKEN=
OPENAI_API_KEY=
GEMINI_API_KEY=
SHOPIFY_STORE=
SHOPIFY_ACCESS_TOKEN=
SHOPIFY_API_VERSION=2026-04
ADMIN_USERNAME=
ADMIN_PASSWORD=
ADMIN_SESSION_SECRET=
```

PostgreSQL is configured by Docker Compose:

```env
DATABASE_URL=postgresql://ttd:ttd@postgres:5432/ttd_pipeline
```

## Admin Panel Flow

### 1. Sources and Filters

Paste Shopify competitor store URLs or upload HTML/TXT exports. Configure:

- women keywords
- male skip keywords
- summer keywords
- excluded categories
- pages per store
- product limit

### 2. Competitor Scraping

The worker reads `/collections/all?sort_by=best-selling`, stores raw products, prices, tags, descriptions, and downloads source images.

### 3. Zendrop Search

For every filtered product, the worker searches Zendrop and stores supplier candidates with shipping estimates for Canada.

### 4. Match Preview

The UI shows competitor and Zendrop products side by side. No generation starts before manual approval.

Actions:

- `Approve`: queue content generation
- `Skip`: keep product out of generation
- `Reject`: mark as rejected
- `Manual URL`: attach a manually selected supplier URL

### 5. Product Enhancer

For approved cards, OpenAI generates:

- product title
- Shopify description
- SEO-ready content
- size chart structure
- price
- compare-at price

Pricing guardrails:

- price must be at least `total_cost * 3`
- compare-at price must be 30-45% above price

### 6. Image Enhancer

The worker generates model product images with consistent styling and stores final image sets locally before upload.

### 7. Shopify Draft Upload

Products are created through Shopify Admin GraphQL with:

- status `DRAFT`
- generated media
- price
- compare-at price
- generated product content

## CLI

Run a Zendrop search:

```bash
docker compose run --rm pipeline python -m app.cli zendrop-search "maxi dress" --limit 5 --country-code ca
```

Run a competitor scrape:

```bash
docker compose run --rm pipeline python -m app.cli competitor-scrape https://lozendafashion.com --pages 2 --limit 50
```

## Tests

```bash
docker compose run --rm --build \
  -e ZENDROP_API_TOKEN=dummy \
  -e OPENAI_API_KEY=sk-test \
  -e GEMINI_API_KEY=gemini-test \
  pipeline python -m pytest -q
```

Current suite:

```text
38 passed
```

## Security

Do not commit `.env`.

Runtime output is ignored:

- `storage/`
- `output/`
- `.playwright-cli/`
- caches and bytecode

The app should be protected behind HTTPS and a private admin password before VPS use.

