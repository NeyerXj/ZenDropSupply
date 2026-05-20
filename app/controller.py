from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.config import Settings, load_settings
from app.database import open_database
from app.services.harvester import (
    HarvestRunRequest,
    create_run,
    dashboard_snapshot,
    update_run_status,
)


class CreateRunRequest(BaseModel):
    target_unique: int = Field(default=100000, ge=1, le=1200000)
    requested_origin_country_code: str = Field(default="cn", min_length=2, max_length=2)
    destination_country_code: str = Field(default="us", min_length=2, max_length=2)
    keywords: list[str] | None = Field(default=None, max_length=300)
    per_page_limit: int = Field(default=60, ge=1, le=60)
    max_pages_per_keyword: int = Field(default=2000, ge=1, le=25000)
    fetch_shipping: bool = False


def get_settings() -> Settings:
    return load_settings()


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or load_settings()
    app = FastAPI(title="ZenDrop Supply Controller")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.get("/api/dashboard")
    def dashboard(settings: Settings = Depends(get_settings)) -> dict:
        effective_settings = settings or app_settings
        with open_database(effective_settings.database_url) as connection:
            return dashboard_snapshot(connection, effective_settings)

    @app.post("/api/runs")
    def start_run(request: CreateRunRequest, settings: Settings = Depends(get_settings)) -> dict:
        effective_settings = settings or app_settings
        with open_database(effective_settings.database_url) as connection:
            run = create_run(
                connection,
                HarvestRunRequest(
                    target_unique=request.target_unique,
                    requested_origin_country_code=request.requested_origin_country_code,
                    destination_country_code=request.destination_country_code,
                    keywords=request.keywords,
                    per_page_limit=request.per_page_limit,
                    max_pages_per_keyword=request.max_pages_per_keyword,
                    fetch_shipping=request.fetch_shipping,
                ),
            )
            return {"run": run, "dashboard": dashboard_snapshot(connection, effective_settings)}

    @app.post("/api/runs/{run_id}/{action}")
    def run_action(run_id: int, action: str, settings: Settings = Depends(get_settings)) -> dict:
        status = {"pause": "paused", "resume": "queued", "cancel": "canceled"}.get(action, action)
        effective_settings = settings or app_settings
        with open_database(effective_settings.database_url) as connection:
            run = update_run_status(connection, run_id, status)
            return {"run": run, "dashboard": dashboard_snapshot(connection, effective_settings)}

    return app


INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ZenDrop Supply</title>
  <style>
    :root { --bg:#f5f7fa; --panel:#fff; --text:#17202e; --muted:#607089; --line:#d7dee8; --accent:#0f766e; --danger:#b42318; --warn:#b76e00; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); }
    header { display:flex; justify-content:space-between; align-items:center; gap:24px; padding:22px 30px; background:var(--panel); border-bottom:1px solid var(--line); }
    h1 { margin:0; font-size:24px; line-height:1.15; }
    h2 { margin:0 0 16px; font-size:16px; }
    main { display:grid; grid-template-columns:360px 1fr; gap:22px; padding:22px 30px 40px; }
    section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:20px; }
    label { display:grid; gap:6px; color:var(--muted); font-size:13px; margin-bottom:13px; }
    input, textarea { width:100%; min-height:38px; padding:8px 10px; border:1px solid var(--line); border-radius:6px; font:inherit; color:var(--text); background:#fff; }
    textarea { min-height:86px; resize:vertical; }
    button { min-height:38px; padding:8px 12px; border:1px solid transparent; border-radius:6px; background:var(--accent); color:#fff; font:700 13px/1 Inter, ui-sans-serif, system-ui; cursor:pointer; }
    button.secondary { background:#fff; color:var(--text); border-color:var(--line); }
    button.danger { background:var(--danger); }
    .muted { color:var(--muted); }
    .status { display:inline-flex; align-items:center; min-height:24px; padding:0 9px; border-radius:999px; background:#e6f4f1; color:#0f766e; font-weight:800; font-size:12px; }
    .grid { display:grid; grid-template-columns:repeat(4, minmax(130px, 1fr)); gap:12px; margin-bottom:18px; }
    .stat { border:1px solid var(--line); border-radius:8px; padding:14px; background:#fbfcfe; min-height:76px; }
    .stat strong { display:block; font-size:23px; }
    .stat span { color:var(--muted); font-size:12px; }
    .actions { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:16px; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th, td { padding:9px 8px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
    th { color:var(--muted); font-weight:700; }
    code { display:block; white-space:pre-wrap; word-break:break-word; background:#101828; color:#eef6ff; border-radius:8px; padding:12px; font-size:12px; line-height:1.45; }
    .check-row { display:flex; align-items:center; gap:10px; }
    .check-row input { width:auto; min-height:auto; }
    @media (max-width: 980px) { main { grid-template-columns:1fr; padding:16px; } header { padding:18px 16px; } .grid { grid-template-columns:repeat(2, minmax(130px, 1fr)); } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>ZenDrop Supply</h1>
      <div class="muted">Fresh distributed Zendrop harvest database</div>
    </div>
    <div id="status" class="status">Loading</div>
  </header>
  <main>
    <section>
      <h2>New run</h2>
      <label>Target products <input id="targetUnique" type="number" min="1" max="1200000" value="100000"></label>
      <label>Ship from metadata <input id="origin" maxlength="2" value="cn"></label>
      <label>Deliver to <input id="destination" maxlength="2" value="us"></label>
      <label>Page size <input id="pageSize" type="number" min="1" max="60" value="60"></label>
      <label>Max pages per keyword <input id="maxPages" type="number" min="1" max="25000" value="2000"></label>
      <label>Keywords, one per line <textarea id="keywords" placeholder="Leave empty for full catalog browse"></textarea></label>
      <label class="check-row"><input id="fetchShipping" type="checkbox"><span>Fetch US shipping price for every product</span></label>
      <div class="actions">
        <button id="startButton">Start fresh run</button>
        <button id="refreshButton" class="secondary">Refresh</button>
      </div>
      <h2>Remote worker</h2>
      <div class="muted" style="margin-bottom:10px;">Use this after WireGuard or another private link exposes Postgres.</div>
      <code id="workerCommand">Loading...</code>
    </section>
    <section>
      <h2>Progress</h2>
      <div class="grid">
        <div class="stat"><strong id="uniqueProducts">0</strong><span>processed in run</span></div>
        <div class="stat"><strong id="targetProducts">0</strong><span>target</span></div>
        <div class="stat"><strong id="eta">warming up</strong><span>ETA</span></div>
        <div class="stat"><strong id="speed">0/min</strong><span>products/min</span></div>
        <div class="stat"><strong id="pages">0</strong><span>pages done</span></div>
        <div class="stat"><strong id="duplicates">0%</strong><span>duplicate rate</span></div>
        <div class="stat"><strong id="workers">0</strong><span>workers</span></div>
        <div class="stat"><strong id="errors">0</strong><span>rate limits</span></div>
      </div>
      <div class="actions">
        <button id="pauseButton" class="secondary">Pause</button>
        <button id="resumeButton" class="secondary">Resume</button>
        <button id="cancelButton" class="danger">Cancel</button>
      </div>
      <h2>Workers</h2>
      <table style="margin-bottom:18px;">
        <thead><tr><th>Worker</th><th>Status</th><th>Pages</th><th>Products</th><th>Heartbeat</th></tr></thead>
        <tbody id="workersBody"><tr><td colspan="5" class="muted">No workers yet</td></tr></tbody>
      </table>
      <h2>Recent pages</h2>
      <table>
        <thead><tr><th>Keyword</th><th>Page</th><th>Status</th><th>Worker</th><th>Products</th><th>New</th><th>Duration</th></tr></thead>
        <tbody id="pagesBody"><tr><td colspan="7" class="muted">No pages yet</td></tr></tbody>
      </table>
    </section>
  </main>
  <script>
    let currentRunId = null;
    const value = (id) => document.getElementById(id).value;
    const numberValue = (id) => Number(value(id) || 0);
    async function fetchJson(url, options = {}) {
      const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    }
    function render(snapshot) {
      const run = snapshot.run;
      const metrics = snapshot.metrics || {};
      currentRunId = run ? run.id : null;
      document.getElementById("status").textContent = run ? run.status : "idle";
      document.getElementById("uniqueProducts").textContent = run ? run.unique_products : 0;
      document.getElementById("targetProducts").textContent = run ? run.target_unique : 0;
      document.getElementById("eta").textContent = metrics.eta_label || "warming up";
      document.getElementById("speed").textContent = `${metrics.products_per_minute || 0}/min`;
      document.getElementById("pages").textContent = run ? run.pages_done : 0;
      document.getElementById("duplicates").textContent = `${Math.round((metrics.duplicate_rate || 0) * 100)}%`;
      document.getElementById("workers").textContent = (snapshot.workers || []).length;
      document.getElementById("errors").textContent = run ? run.rate_limit_hits : 0;
      const setup = snapshot.worker_setup || {};
      document.getElementById("workerCommand").textContent = `${setup.clone || ""}\\ncp .env.example .env\\n# set ZENDROP_API_TOKEN and DATABASE_URL\\n${setup.env || ""}\\n${setup.run || ""}`;
      const workers = snapshot.workers || [];
      document.getElementById("workersBody").innerHTML = workers.length ? workers.map((worker) => `
        <tr><td>${worker.worker_id}</td><td>${worker.status}</td><td>${worker.processed_pages}</td><td>${worker.processed_products}</td><td>${worker.seconds_since_heartbeat}s ago</td></tr>
      `).join("") : '<tr><td colspan="5" class="muted">No workers yet</td></tr>';
      const pages = snapshot.recent_pages || [];
      document.getElementById("pagesBody").innerHTML = pages.length ? pages.map((page) => `
        <tr><td>${page.keyword || "catalog"}</td><td>${page.page}</td><td>${page.status}</td><td>${page.claimed_by || ""}</td><td>${page.product_count}</td><td>${page.new_product_count}</td><td>${page.duration_ms ? `${page.duration_ms}ms` : ""}</td></tr>
      `).join("") : '<tr><td colspan="7" class="muted">No pages yet</td></tr>';
    }
    async function refresh() { render(await fetchJson("/api/dashboard")); }
    document.getElementById("refreshButton").onclick = refresh;
    document.getElementById("startButton").onclick = async () => {
      const keywords = value("keywords").split("\\n").map((line) => line.trim()).filter(Boolean);
      const response = await fetchJson("/api/runs", {
        method: "POST",
        body: JSON.stringify({
          target_unique: numberValue("targetUnique"),
          requested_origin_country_code: value("origin"),
          destination_country_code: value("destination"),
          keywords: keywords.length ? keywords : null,
          per_page_limit: numberValue("pageSize"),
          max_pages_per_keyword: numberValue("maxPages"),
          fetch_shipping: document.getElementById("fetchShipping").checked
        })
      });
      render(response.dashboard);
    };
    for (const action of ["pause", "resume", "cancel"]) {
      document.getElementById(`${action}Button`).onclick = async () => {
        if (!currentRunId) return;
        const response = await fetchJson(`/api/runs/${currentRunId}/${action}`, { method: "POST" });
        render(response.dashboard);
      };
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


app = create_app()
