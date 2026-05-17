# V2 Prod-Like Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mixed V1 dashboard with a gated V2 flow: source products, Zendrop plus AI matching, manual approval, fake image generation, and manual Shopify draft upload.

**Architecture:** Keep FastAPI, PostgreSQL, Docker, and the existing provider clients, but enforce stage gates in service queries and worker transitions. The UI becomes an operator console with visible source, match, approval, approved production, and draft lanes. No final/draft item is visible or queued unless a match was explicitly approved.

**Tech Stack:** FastAPI, PostgreSQL, Docker Compose, vanilla JS/CSS, OpenAI Responses API, Zendrop MCP, Shopify Admin GraphQL, fake local image generation.

---

### Task 1: Lock V2 Stage Gates

**Files:**
- Modify: `app/services/final_catalog.py`
- Modify: `app/services/worker.py`
- Test: `tests/test_final_catalog.py`
- Test: `tests/test_worker.py`

- [x] **Step 1: Write failing tests**

Add tests proving unapproved source products do not appear in final catalog and approved content queues `final_model_images`.

- [x] **Step 2: Run tests to verify RED**

Run: `docker compose run --rm --build -e PYTHONPATH=/app pipeline python -m pytest -q tests/test_final_catalog.py::test_final_catalog_status_hides_unapproved_source_products tests/test_worker.py::test_openai_content_job_queues_final_model_images_for_approved_product`
Expected: FAIL before implementation.

- [x] **Step 3: Implement backend gates**

Filter final catalog through `product_matches.status = 'approved'` and enqueue `final_model_images` after `openai_content`.

- [x] **Step 4: Verify GREEN**

Run the same focused tests and then the full suite.

---

### Task 2: Rebuild V2 Operator UI

**Files:**
- Modify: `app/web.py`
- Modify: `app/web_static/dashboard.js`
- Modify: `app/web_static/dashboard.css`
- Test: `tests/test_web_dashboard.py`

- [ ] **Step 1: Render V2 lanes**

Show source queue, active matching jobs, approval cards, approved production, and upload drafts as separate lanes.

- [ ] **Step 2: Hide final products before approval**

Final lane reads only approved products and shows empty state otherwise.

- [ ] **Step 3: Make next action deterministic**

Build preview queues matching; approve queues content/images; upload drafts is the only Shopify action.

---

### Task 3: Runtime Verification

**Files:**
- Runtime only

- [ ] **Step 1: Reset runtime state except keys**

Clear pipeline rows, products, matches, generated content/images, final image sets, and Shopify draft records.

- [ ] **Step 2: Rebuild and scale**

Run: `docker compose up -d --build --scale worker=4 web worker`

- [ ] **Step 3: Browser QA**

Open `http://localhost:8080`, verify the V2 page loads, no final products appear before approval, and active jobs are visible.

- [ ] **Step 4: Commit and push**

Commit V2 and push `main`.
