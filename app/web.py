from __future__ import annotations

import hashlib
import hmac
import json
import shutil
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import Settings, load_settings
from app.database import open_database
from app.providers.competitor_shopify import CompetitorShopifyClient
from app.providers.zendrop import ZendropMcpClient
from app.services.approval_matching import list_approval_cards, queue_approval_match_jobs
from app.services.competitor_pipeline import CompetitorPipeline
from app.services.dashboard import (
    PRODUCT_STATUSES,
    build_pipeline_steps,
    get_summary,
    list_competitor_products,
    list_zendrop_products,
    update_competitor_product_status,
)
from app.services.filtering import ProductFilterConfig, get_active_filter_config, save_active_filter_config
from app.services.final_catalog import list_final_catalog_status, queue_final_image_jobs, queue_shopify_upload_jobs
from app.services.pipeline_state import (
    create_competitor_batch_run,
    enqueue_pipeline_job,
    get_pipeline_activity,
    list_pipeline_jobs,
    list_pipeline_runs,
)
from app.services.search_terms import zendrop_search_queries, zendrop_search_text
from app.services.zendrop_pipeline import ZendropPipeline


ProductStatus = Literal[
    "ready_for_zendrop",
    "rejected",
    "skipped_male",
    "skipped_not_women",
    "skipped_season",
    "skipped_language",
    "zendrop_matched",
    "draft_ready",
    "uploaded_draft",
]


class StatusUpdateRequest(BaseModel):
    status: ProductStatus


class CompetitorScrapeRequest(BaseModel):
    store_url: str = Field(default="https://lozendafashion.com", min_length=8)
    pages: int = Field(default=1, ge=1, le=20)
    limit: int | None = Field(default=10, ge=1, le=200)


class ZendropSearchRequest(BaseModel):
    keyword: str = Field(min_length=2)
    limit: int = Field(default=10, ge=1, le=60)
    country_code: str = Field(default="ca", min_length=2, max_length=2)


class FilterConfigRequest(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=80)
    women_keywords: list[str] = Field(default_factory=list)
    male_keywords: list[str] = Field(default_factory=list)
    summer_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)


class PipelineRunRequest(BaseModel):
    name: str = Field(default="Competitor batch", min_length=1, max_length=120)
    store_urls: list[str] = Field(min_length=1, max_length=50)
    pages_requested: int = Field(default=5, ge=1, le=20)
    product_limit: int | None = Field(default=120, ge=1, le=200)


class ApprovalStatusRequest(BaseModel):
    status: Literal["approved", "rejected", "skipped"]
    manual_supplier_url: str | None = None


class FinalImagesRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    images_per_product: int = Field(default=6, ge=5, le=8)


class ShopifyDraftUploadRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    min_images: int = Field(default=5, ge=1, le=8)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class AnalyticsFileUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=220)
    content: str = Field(min_length=1)
    source_store_url: str | None = Field(default=None, max_length=500)
    run_id: int | None = None


def approval_work_exists(database, product_match_id: int) -> bool:
    if database.execute("select 1 from generated_contents where product_match_id = ?", (product_match_id,)).fetchone():
        return True
    rows = database.execute(
        """
        select payload_json
        from pipeline_jobs
        where stage = 'openai_content'
          and status in ('queued', 'running', 'done')
        """
    ).fetchall()
    for (payload_json,) in rows:
        try:
            if int(json.loads(payload_json).get("product_match_id") or 0) == product_match_id:
                return True
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return False


def cancel_approval_work(database, product_match_id: int, competitor_product_id: int) -> int:
    canceled = 0
    rows = database.execute(
        """
        select id, stage, payload_json
        from pipeline_jobs
        where status in ('queued', 'running')
          and stage in ('openai_content', 'final_model_images', 'shopify_draft_upload')
        """
    ).fetchall()
    for job_id, stage, payload_json in rows:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            continue
        if stage == "openai_content" and int(payload.get("product_match_id") or 0) != product_match_id:
            continue
        if stage in {"final_model_images", "shopify_draft_upload"} and int(payload.get("competitor_product_id") or 0) != competitor_product_id:
            continue
        database.execute(
            """
            update pipeline_jobs
            set status = 'canceled',
                error_message = null,
                updated_at = current_timestamp
            where id = ?
            """,
            (job_id,),
        )
        canceled += 1
    database.execute("delete from generated_contents where product_match_id = ?", (product_match_id,))
    database.execute("delete from generated_images where product_match_id = ?", (product_match_id,))
    image_set_row = database.execute(
        "select id from final_image_sets where competitor_product_id = ?",
        (competitor_product_id,),
    ).fetchone()
    if image_set_row:
        database.execute("delete from final_generated_images where image_set_id = ?", (image_set_row[0],))
        database.execute("delete from final_image_sets where id = ?", (image_set_row[0],))
    database.execute("delete from shopify_draft_products where competitor_product_id = ?", (competitor_product_id,))
    return canceled


def reset_pipeline_workspace(database, storage_dir: Path) -> dict[str, int]:
    tables = [
        "shopify_draft_products",
        "final_generated_images",
        "final_image_sets",
        "generated_images",
        "generated_contents",
        "product_matches",
        "pipeline_jobs",
        "uploaded_analytics_files",
        "competitor_stores",
        "pipeline_runs",
        "competitor_products",
        "zendrop_products",
    ]
    deleted_counts: dict[str, int] = {}
    for table in tables:
        deleted_counts[table] = database.execute(f"select count(*) from {table}").fetchone()[0]
        database.execute(f"delete from {table}")
    database.commit()
    for dirname in ("analytics_uploads", "competitor_images", "generated_images", "final_model_images"):
        directory = storage_dir / dirname
        if not directory.exists():
            continue
        for child in directory.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)
    return deleted_counts


LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TTD Admin Login</title>
  <link rel="stylesheet" href="/assets/dashboard.css?v=match-status-v1">
</head>
<body class="login-body">
  <main class="login-card">
    <div class="brand">
      <span class="brand-mark">T</span>
      <div>
        <h1>TTD Pipeline Control</h1>
        <p>Admin panel</p>
      </div>
    </div>
    <form id="loginForm" class="login-form">
      <label>Login<input name="username" autocomplete="username" autofocus></label>
      <label>Password<input name="password" type="password" autocomplete="current-password"></label>
      <button type="submit">Enter admin panel</button>
      <p id="loginError" class="form-error"></p>
    </form>
  </main>
  <script>
    document.getElementById("loginForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: form.elements.username.value,
          password: form.elements.password.value
        })
      });
      if (response.ok) {
        window.location.href = "/";
        return;
      }
      document.getElementById("loginError").textContent = "Wrong login or password.";
    });
  </script>
</body>
</html>"""


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TTD Pipeline Control</title>
  <link rel="stylesheet" href="/assets/dashboard.css">
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">T</span>
        <div>
          <h1>TTD Pipeline Control</h1>
          <p>Local dropshipping ops</p>
        </div>
      </div>
      <nav id="pipelineSteps" class="pipeline-steps" aria-label="Pipeline steps"></nav>
      <div class="sidebar-actions">
        <button id="resetPipelineButton" type="button" class="danger sidebar-button">Reset pipeline</button>
        <a class="logout-link" href="/logout">Logout</a>
      </div>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <div>
          <h2>Admin pipeline</h2>
          <p>Sources → filters → Zendrop search → approval → enhancer → Shopify draft.</p>
        </div>
        <div id="toast" class="toast" role="status" aria-live="polite"></div>
      </header>

      <section class="metric-grid" id="summaryCards" aria-label="Pipeline summary"></section>

      <section class="operator-grid" aria-label="Pipeline setup">
        <form id="sourceSetupForm" class="operator-panel">
          <div class="section-heading">
            <h3>1. Sources and filters</h3>
            <p>Paste Shopify stores or add HTML/TXT exports, then run sourcing from one place.</p>
          </div>
          <label>Run name<input name="name" value="Competitor sourcing"></label>
          <label>Shopify stores<textarea name="store_urls" rows="5" placeholder="velanora-fashion.com&#10;lozendafashion.com"></textarea></label>
          <div class="field-row">
            <label>Pages per store<input name="pages_requested" type="number" value="5" min="1" max="20"></label>
            <label>Product limit<input name="limit" type="number" value="120" min="1" max="200"></label>
          </div>
          <label>HTML/TXT files<input id="analyticsFiles" name="analytics_files" type="file" accept=".html,.htm,.txt" multiple></label>
          <div class="filter-grid">
            <label>Women keywords<textarea name="women_keywords" rows="2"></textarea></label>
            <label>Male skip keywords<textarea name="male_keywords" rows="2"></textarea></label>
            <label>Summer keywords<textarea name="summer_keywords" rows="2"></textarea></label>
            <label>Exclude categories<textarea name="exclude_keywords" rows="2" placeholder="lingerie, underwear"></textarea></label>
          </div>
          <button type="submit">Save and start sourcing</button>
        </form>

        <section class="next-panel">
          <div class="section-heading">
            <h3 id="nextActionTitle">Next action</h3>
            <p id="nextActionDescription">Load the current pipeline state.</p>
          </div>
          <button id="nextActionButton" type="button">Refresh state</button>
          <div id="pipelineRuns" class="compact-run-list"></div>
          <div id="jobActivity" class="job-activity"></div>
        </section>
      </section>

      <section class="stage-section">
        <div class="lane-heading">
          <div>
            <h3>2. Source products</h3>
            <p>Collected competitor products. Only ready products move into Zendrop search and AI match.</p>
          </div>
          <div id="sourceBreakdown" class="lane-stats"></div>
        </div>
        <section id="competitorProducts" class="product-list source-list" aria-label="Collected source products"></section>
      </section>

      <section class="stage-section">
        <div>
          <h3>3. Match preview and approval</h3>
          <p>Each card compares competitor and Zendrop before generation starts.</p>
        </div>
        <section id="approvalCards" class="approval-grid" aria-label="Approval cards"></section>
      </section>

      <section class="stage-section">
        <div class="lane-heading">
          <div>
            <h3>4. Approved production</h3>
            <p>Only approved products appear here. Image enhancer uses fake images in test mode.</p>
          </div>
          <button id="uploadDraftsButton" type="button" class="secondary">Upload drafts</button>
        </div>
        <section id="finalCatalog" class="final-catalog" aria-label="Final Shopify products"></section>
      </section>
    </main>
  </div>
  <script src="/assets/dashboard.js?v=match-status-v1"></script>
</body>
</html>"""


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or load_settings()
    static_dir = Path(__file__).parent / "web_static"
    app_settings.storage_dir.mkdir(parents=True, exist_ok=True)

    web_app = FastAPI(title="TTD Pipeline Control")
    web_app.mount("/assets", StaticFiles(directory=static_dir), name="assets")
    web_app.mount("/media", StaticFiles(directory=app_settings.storage_dir), name="media")

    def get_database():
        with open_database(app_settings.database_url) as database:
            yield database

    def session_token() -> str:
        payload = app_settings.admin.username.encode()
        secret = app_settings.admin.session_secret.encode()
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    def is_authenticated(request: Request) -> bool:
        return hmac.compare_digest(request.cookies.get("ttd_admin_session", ""), session_token())

    def require_admin(request: Request) -> None:
        if not is_authenticated(request):
            raise HTTPException(status_code=401, detail="Login required")

    @web_app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> str:
        if not is_authenticated(request):
            return LOGIN_HTML
        return DASHBOARD_HTML

    @web_app.get("/logout")
    def logout() -> Response:
        response = Response(status_code=303, headers={"Location": "/"})
        response.delete_cookie("ttd_admin_session")
        return response

    @web_app.post("/api/login")
    def login(request: LoginRequest) -> Response:
        if not (
            hmac.compare_digest(request.username, app_settings.admin.username)
            and hmac.compare_digest(request.password, app_settings.admin.password)
        ):
            raise HTTPException(status_code=401, detail="Wrong login or password")
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "ttd_admin_session",
            session_token(),
            httponly=True,
            samesite="lax",
        )
        return response

    @web_app.get("/api/summary")
    def summary(_: None = Depends(require_admin), database=Depends(get_database)) -> dict:
        return get_summary(database)

    @web_app.get("/api/pipeline")
    def pipeline(_: None = Depends(require_admin), database=Depends(get_database)) -> dict:
        return {"steps": build_pipeline_steps(get_summary(database))}

    @web_app.get("/api/runs")
    def pipeline_runs(
        _: None = Depends(require_admin),
        database=Depends(get_database),
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict:
        return {"runs": list_pipeline_runs(database=database, limit=limit)}

    @web_app.get("/api/job-status")
    def job_status(
        _: None = Depends(require_admin),
        database=Depends(get_database),
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> dict:
        return get_pipeline_activity(database=database, limit=limit)

    @web_app.post("/api/runs")
    def create_pipeline_run(request: PipelineRunRequest, _: None = Depends(require_admin), database=Depends(get_database)) -> dict:
        run = create_competitor_batch_run(
            database=database,
            name=request.name,
            store_urls=request.store_urls,
            pages_requested=request.pages_requested,
            product_limit=request.product_limit,
        )
        jobs = list_pipeline_jobs(database=database, run_id=run["id"])
        return {"run": run, "jobs_count": len(jobs), "jobs": jobs}

    @web_app.get("/api/competitor-products")
    def competitor_products(
        _: None = Depends(require_admin),
        database=Depends(get_database),
        status: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict:
        if status and status not in PRODUCT_STATUSES:
            raise HTTPException(status_code=422, detail="Unsupported product status")
        return {
            "products": list_competitor_products(
                database=database,
                storage_dir=app_settings.storage_dir,
                status=status,
                limit=limit,
            )
        }

    @web_app.get("/api/zendrop-products")
    def zendrop_products(
        _: None = Depends(require_admin),
        database=Depends(get_database),
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict:
        return {"products": list_zendrop_products(database=database, limit=limit)}

    @web_app.get("/api/approval-cards")
    def approval_cards(
        _: None = Depends(require_admin),
        database=Depends(get_database),
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> dict:
        return {"cards": list_approval_cards(database=database, storage_dir=app_settings.storage_dir, limit=limit)}

    @web_app.get("/api/final-catalog")
    def final_catalog(
        _: None = Depends(require_admin),
        database=Depends(get_database),
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict:
        return {"products": list_final_catalog_status(database=database, storage_dir=app_settings.storage_dir, limit=limit)}

    @web_app.post("/api/run/final-images")
    def run_final_images(request: FinalImagesRequest, _: None = Depends(require_admin), database=Depends(get_database)) -> dict:
        queued = queue_final_image_jobs(
            database=database,
            limit=request.limit,
            images_per_product=request.images_per_product,
        )
        return {"count": queued, "jobs_queued": queued}

    @web_app.post("/api/run/shopify-drafts")
    def run_shopify_drafts(request: ShopifyDraftUploadRequest, _: None = Depends(require_admin), database=Depends(get_database)) -> dict:
        queued = queue_shopify_upload_jobs(database=database, limit=request.limit, min_images=request.min_images)
        return {"count": queued, "jobs_queued": queued}

    @web_app.post("/api/uploads/analytics-files")
    def upload_analytics_file(
        request: AnalyticsFileUploadRequest,
        _: None = Depends(require_admin),
        database=Depends(get_database),
    ) -> dict:
        upload_dir = app_settings.storage_dir / "analytics_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(request.filename).name
        digest = hashlib.sha256(request.content.encode("utf-8")).hexdigest()[:12]
        storage_path = upload_dir / f"{digest}-{filename}"
        storage_path.write_text(request.content, encoding="utf-8")
        database.execute(
            """
            insert into uploaded_analytics_files (
                run_id, filename, storage_path, source_store_url, parsed_products_count
            )
            values (?, ?, ?, ?, 0)
            """,
            (request.run_id, filename, str(storage_path), request.source_store_url),
        )
        database.commit()
        return {"count": 1, "filename": filename}

    @web_app.post("/api/approval-cards/{product_match_id}/status")
    def update_approval_card_status(
        product_match_id: int,
        request: ApprovalStatusRequest,
        _: None = Depends(require_admin),
        database=Depends(get_database),
    ) -> dict:
        row = database.execute(
            """
            select pm.id, pm.competitor_product_id, pm.status, pj.run_id
            from product_matches pm
            left join pipeline_jobs pj on pj.stage in ('approval_matching', 'approval_match_product') and pj.status = 'done'
            where pm.id = ?
            order by pj.updated_at desc
            limit 1
            """,
            (product_match_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Approval card not found")
        content_job_queued = False
        canceled_jobs = 0
        retry_job_queued = False
        database.execute(
            """
            update product_matches
            set status = ?, manual_supplier_url = ?, updated_at = current_timestamp
            where id = ?
            """,
            (request.status, request.manual_supplier_url, product_match_id),
        )
        if request.status == "approved":
            if not approval_work_exists(database, product_match_id):
                enqueue_pipeline_job(
                    database=database,
                    run_id=row[3],
                    stage="openai_content",
                    payload={"product_match_id": product_match_id},
                    priority=200,
                )
                content_job_queued = True
        else:
            canceled_jobs = cancel_approval_work(
                database=database,
                product_match_id=product_match_id,
                competitor_product_id=row[1],
            )
            enqueue_pipeline_job(
                database=database,
                run_id=row[3],
                stage="approval_match_product",
                payload={"competitor_product_id": row[1]},
                priority=130,
            )
            retry_job_queued = True
        database.commit()
        return {
            "id": product_match_id,
            "status": request.status,
            "content_job_queued": content_job_queued,
            "canceled_jobs": canceled_jobs,
            "retry_job_queued": retry_job_queued,
        }

    @web_app.get("/api/filter-config")
    def filter_config(_: None = Depends(require_admin), database=Depends(get_database)) -> dict:
        return serialize_filter_config(get_active_filter_config(database))

    @web_app.put("/api/filter-config")
    def update_filter_config(request: FilterConfigRequest, _: None = Depends(require_admin), database=Depends(get_database)) -> dict:
        config = ProductFilterConfig(
            name=request.name,
            women_keywords=request.women_keywords,
            male_keywords=request.male_keywords,
            summer_keywords=request.summer_keywords,
            exclude_keywords=request.exclude_keywords,
        )
        return serialize_filter_config(save_active_filter_config(database, config))

    @web_app.post("/api/competitor-products/{product_id}/status")
    def update_status(
        product_id: int,
        request: StatusUpdateRequest,
        _: None = Depends(require_admin),
        database=Depends(get_database),
    ) -> dict:
        product = update_competitor_product_status(database=database, product_id=product_id, status=request.status)
        if product is None:
            raise HTTPException(status_code=404, detail="Competitor product not found")
        if request.status == "ready_for_zendrop":
            enqueue_pipeline_job(
                database=database,
                run_id=None,
                stage="zendrop_search",
                payload={
                    "keyword": zendrop_search_text(product["title"]),
                    "keywords": zendrop_search_queries(product["title"]),
                    "competitor_product_id": product_id,
                    "limit": 8,
                    "country_code": app_settings.zendrop.default_country_code,
                },
                priority=120,
            )
        return product

    @web_app.post("/api/run/competitor-scrape")
    async def run_competitor_scrape(request: CompetitorScrapeRequest, _: None = Depends(require_admin)) -> dict:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http_client:
            client = CompetitorShopifyClient(http_client=http_client)
            with open_database(app_settings.database_url) as database:
                filter_config = get_active_filter_config(database)
                pipeline_runner = CompetitorPipeline(
                    database=database,
                    client=client,
                    image_storage_dir=app_settings.storage_dir / "competitor_images",
                    filter_config=filter_config,
                )
                products = await pipeline_runner.scrape_store(
                    store_url=request.store_url,
                    pages=request.pages,
                    limit=request.limit,
                )
        return {"count": len(products), "store_url": request.store_url.rstrip("/")}

    @web_app.post("/api/run/zendrop-search")
    async def run_zendrop_search(request: ZendropSearchRequest, _: None = Depends(require_admin)) -> dict:
        if not app_settings.zendrop.api_token:
            raise HTTPException(status_code=400, detail="Zendrop API token is not configured")
        async with httpx.AsyncClient(timeout=30) as http_client:
            client = ZendropMcpClient(settings=app_settings.zendrop, http_client=http_client)
            with open_database(app_settings.database_url) as database:
                pipeline_runner = ZendropPipeline(database=database, zendrop_client=client)
                products = await pipeline_runner.search_and_store(
                    keyword=request.keyword,
                    limit=request.limit,
                    country_code=request.country_code,
                )
        return {"count": len(products), "keyword": request.keyword, "country_code": request.country_code}

    @web_app.post("/api/run/approval-matching")
    def run_approval_matching(_: None = Depends(require_admin), database=Depends(get_database)) -> dict:
        result = queue_approval_match_jobs(database=database)
        return {"count": result["jobs_queued"], **result}

    @web_app.post("/api/admin/reset")
    def reset_pipeline(_: None = Depends(require_admin), database=Depends(get_database)) -> dict:
        deleted = reset_pipeline_workspace(database=database, storage_dir=app_settings.storage_dir)
        return {"ok": True, "deleted": deleted}

    return web_app


def serialize_filter_config(config: ProductFilterConfig) -> dict:
    return {
        "name": config.name,
        "women_keywords": config.women_keywords,
        "male_keywords": config.male_keywords,
        "summer_keywords": config.summer_keywords,
        "exclude_keywords": config.exclude_keywords,
    }


app = create_app()
