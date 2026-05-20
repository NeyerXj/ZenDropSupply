# ZenDropSupply

Standalone distributed Zendrop catalog harvester.

This repo is separate from the main TTD app. It creates a fresh Postgres database and collects Zendrop products for a dedicated supply dataset.

## Defaults

- Target: `100000` processed products
- Requested ship-from metadata: `cn`
- Destination: `us`
- Page size: `60`
- Database: `zendrop_supply`
- Controller UI: `http://localhost:8091`

Zendrop MCP exposes destination shipping country, but it does not expose a `ship_from=china` filter in `get_catalog_products`. The app stores `requested_origin_country_code=cn` and `origin_verified=false` unless Zendrop starts returning verifiable origin data.

## Local controller

```bash
cp .env.example .env
# edit .env and set ZENDROP_API_TOKEN
docker compose up -d --build postgres controller
```

Open:

```text
http://localhost:8091
```

Start a fresh run from the UI.

## Local worker on the same machine

```bash
docker compose --profile worker up -d --build worker
```

## Remote worker over WireGuard

Recommended topology:

- Mac runs `postgres` and `controller`.
- Remote server joins the same private WireGuard network.
- Remote worker connects directly to Mac Postgres over the WireGuard IP.
- Page claiming uses Postgres row locks, so multiple workers do not process the same page.

On the remote server:

```bash
git clone https://github.com/NeyerXj/ZenDropSupply.git
cd ZenDropSupply
cp .env.example .env
```

Set `.env`:

```env
ZENDROP_API_TOKEN=your_token
DATABASE_URL=postgresql://zendrop:zendrop@10.8.0.1:5434/zendrop_supply
HARVESTER_WORKER_ID=server-1
```

Run:

```bash
docker compose -f docker-compose.worker.yml up -d --build
```

Use a unique `HARVESTER_WORKER_ID` per server.

## Exposing Postgres safely

Only expose Postgres on a private interface such as WireGuard. Do not open `5434` publicly.

The controller compose maps host port `5434` to container Postgres `5432` for remote workers:

```text
10.8.0.1:5434 -> postgres:5432
```

## Verification

```bash
PYTHONPATH=. pytest -q
docker compose config -q
```
