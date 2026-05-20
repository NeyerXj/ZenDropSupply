# Zendrop Supply Harvester Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone distributed Zendrop catalog harvester for a fresh 100k US-destination database with China-origin metadata.

**Architecture:** One controller owns Postgres, UI, run creation, worker status, ETA, and product storage. One or more worker containers connect to the controller Postgres over a private network such as WireGuard and atomically claim page jobs with row locks so duplicate downloads are avoided.

**Tech Stack:** Python 3.11, FastAPI, psycopg, httpx, Pydantic settings, Docker Compose, Postgres 16, pytest.

---

### Task 1: Standalone Project Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `docker-compose.worker.yml`
- Create: `.env.example`

- [x] Create a small Python package with only dependencies needed for controller, worker, tests, and Zendrop MCP calls.
- [x] Add controller and worker Docker profiles.
- [x] Keep the database separate from TTD by using `zendrop_supply` and a dedicated Docker volume.

### Task 2: DB Schema And Coordination

**Files:**
- Create: `app/database.py`
- Create: `app/services/harvester.py`
- Test: `tests/test_harvester.py`

- [x] Create tables for runs, pages, products, product images, shipping estimates, and workers.
- [x] Implement atomic page claiming with `FOR UPDATE SKIP LOCKED`.
- [x] Track worker heartbeat and per-page durations for ETA.

### Task 3: Zendrop Client And Worker

**Files:**
- Create: `app/providers/zendrop.py`
- Create: `app/worker.py`
- Test: `tests/test_harvester.py`

- [x] Implement Zendrop MCP client.
- [x] Store products with `requested_origin_country_code=cn` and `destination_country_code=us`.
- [x] Make shipping estimates optional because fetching shipping for every product adds one API call per product.

### Task 4: Controller UI And API

**Files:**
- Create: `app/controller.py`

- [x] Add run creation for fresh 100k harvests.
- [x] Show ETA, active workers, products/min, pages/min, duplicate rate, and recent pages.
- [x] Show worker setup commands for remote servers.

### Task 5: Verification And Push

**Files:**
- Modify: all above

- [x] Run tests.
- [x] Run controller smoke checks.
- [x] Commit and push to `NeyerXj/ZenDropSupply`.
