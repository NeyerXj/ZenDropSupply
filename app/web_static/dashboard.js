const state = {
  summary: {},
  runs: [],
  jobStatus: { summary: [], active_jobs: [] },
  approvalCards: [],
  finalProducts: [],
  competitorProducts: [],
};

const labels = {
  competitor_total: "Collected",
  ready_for_zendrop: "Ready",
  preview_cards_total: "Preview",
  manual_approved_total: "Approved",
  shopify_draft_total: "Drafts",
};

function money(value, currency = "USD") {
  if (value === null || value === undefined) return "-";
  return `${Number(value).toFixed(2)} ${currency}`;
}

function showToast(message) {
  document.getElementById("toast").textContent = message;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 401) {
    window.location.href = "/";
    return {};
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

function fieldToKeywords(value) {
  return value
    .split(",")
    .map((keyword) => keyword.trim())
    .filter(Boolean);
}

function keywordsToField(keywords) {
  return (keywords || []).join(", ");
}

function renderSummary() {
  const keys = ["competitor_total", "ready_for_zendrop", "preview_cards_total", "manual_approved_total", "shopify_draft_total"];
  document.getElementById("summaryCards").innerHTML = keys.map((key) => `
    <article class="metric-card">
      <div class="metric-value">${state.summary[key] ?? 0}</div>
      <div class="metric-label">${labels[key]}</div>
    </article>
  `).join("");
}

function renderSourceBreakdown() {
  const target = document.getElementById("sourceBreakdown");
  const counts = state.summary.status_counts || {};
  const entries = Object.entries(counts).sort((left, right) => String(left[0]).localeCompare(String(right[0])));
  if (!entries.length) {
    target.innerHTML = "";
    return;
  }
  target.innerHTML = entries.map(([status, count]) => `
    <span class="status-pill ${status}">${status}: ${count}</span>
  `).join("");
}

function renderPipeline(steps) {
  document.getElementById("pipelineSteps").innerHTML = steps.map((step) => `
    <div class="pipeline-step ${step.state}">
      <span class="pipeline-dot"></span>
      <span class="pipeline-title">${step.title}</span>
      <span class="pipeline-metric">${step.metric}</span>
    </div>
  `).join("");
}

function renderPipelineRuns() {
  const target = document.getElementById("pipelineRuns");
  if (!state.runs.length) {
    target.innerHTML = `<div class="run-empty">No active runs.</div>`;
    return;
  }
  target.innerHTML = state.runs.slice(0, 3).map((run) => `
    <div class="run-chip">
      <strong>${run.name}</strong>
      <span>${run.status} · ${run.raw_input.store_urls.length} stores · ${run.raw_input.pages_requested} pages</span>
    </div>
  `).join("");
}

function renderJobActivity() {
  const target = document.getElementById("jobActivity");
  const jobs = state.jobStatus.active_jobs || [];
  const failedJobs = state.jobStatus.failed_jobs || [];
  const summary = state.jobStatus.summary || [];
  if (!jobs.length) {
    const doneTotal = summary
      .filter((entry) => entry.status === "done")
      .reduce((total, entry) => total + Number(entry.count || 0), 0);
    const failedTotal = summary
      .filter((entry) => entry.status === "failed")
      .reduce((total, entry) => total + Number(entry.count || 0), 0);
    target.innerHTML = `
      <div class="job-summary">
        <span>No active jobs</span>
        <strong>${doneTotal} done</strong>
        ${failedTotal ? `<strong>${failedTotal} failed</strong>` : ""}
      </div>
      ${failedJobs.length ? renderJobList(failedJobs, "Recent failed jobs") : ""}
      ${renderRecentMatchDiagnostics()}
    `;
    return;
  }
  target.innerHTML = `
    <div class="job-summary">
      ${["running", "queued", "failed"].map((status) => {
        const count = summary
          .filter((entry) => entry.status === status)
          .reduce((total, entry) => total + Number(entry.count || 0), 0);
        return `<span>${status}: <strong>${count}</strong></span>`;
      }).join("")}
    </div>
    <div class="job-list">
      ${jobs.map(renderJobRow).join("")}
    </div>
  `;
}

function renderRecentMatchDiagnostics() {
  const jobs = (state.jobStatus.recent_done_jobs || [])
    .filter((job) => job.stage === "approval_match_product")
    .slice(0, 8);
  if (!jobs.length) return "";
  return `
    <div class="job-list" aria-label="Recent match diagnostics">
      ${jobs.map((job) => {
        const diagnostics = job.result?.diagnostics || {};
        const candidates = (diagnostics.candidates || [])
          .map((candidate) => `${candidate.name} (${Math.round(candidate.score)})`)
          .join(", ");
        return `
          <article class="job-row done">
            <div>
              <strong>${diagnostics.product_title || jobPayloadLabel(job) || "Match job"}</strong>
              <span>${diagnostics.reason || "Match job completed"}</span>
            </div>
            ${diagnostics.selected_candidate ? `<small>${diagnostics.selected_candidate} · score ${Math.round(diagnostics.score || 0)}</small>` : ""}
            ${candidates ? `<small>${candidates}</small>` : ""}
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function renderJobList(jobs, label) {
  return `
    <div class="job-list" aria-label="${label}">
      ${jobs.map(renderJobRow).join("")}
    </div>
  `;
}

function renderJobRow(job) {
  return `
    <article class="job-row ${job.status}">
      <div>
        <strong>${job.stageLabel || job.stage}</strong>
        <span>${job.status} · ${jobAge(job)}${job.run_id ? ` · run ${job.run_id}` : ""}</span>
      </div>
      <small>${jobPayloadLabel(job)}</small>
      ${job.error_message ? `<p>${job.error_message}</p>` : ""}
    </article>
  `;
}

function jobAge(job) {
  const source = job.locked_at || job.updated_at || job.created_at;
  if (!source) return "no timestamp";
  const normalized = String(source).includes("T") ? String(source) : String(source).replace(" ", "T");
  const parsed = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(normalized) ? normalized : `${normalized}Z`);
  if (Number.isNaN(parsed.getTime())) return source;
  const seconds = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function jobPayloadLabel(job) {
  const payload = job.payload || {};
  if (payload.store_url) return payload.store_url;
  if (payload.keyword) return payload.keyword;
  if (payload.product_ids) return `products ${payload.product_ids.join(", ")}`;
  if (payload.competitor_product_id) return `product ${payload.competitor_product_id}`;
  return "";
}

function renderApprovalCards() {
  const target = document.getElementById("approvalCards");
  if (!state.approvalCards.length) {
    target.innerHTML = `<div class="empty-state">No match preview cards yet. Ready source products must pass Zendrop search and AI image match before approval.</div>`;
    return;
  }
  target.innerHTML = state.approvalCards.map((card) => {
    const processed = card.status !== "approval_pending";
    return `
    <article class="approval-card ${card.status}">
      <div class="approval-images">
        ${approvalImage(card.competitor.image_url, "Competitor")}
        ${approvalImage(card.zendrop.image_url, "Zendrop")}
      </div>
      <div class="approval-body">
        <div>
          <h4>${card.competitor.title}</h4>
          <p>Zendrop: ${card.zendrop.name}</p>
        </div>
        <div class="approval-metrics">
          <span class="status-pill ${card.status}">${card.status}</span>
          <span>Competitor ${money(card.competitor.price)}</span>
          <span>Zendrop ${money(card.zendrop.price_usd)} + ship ${money(card.zendrop.shipping_price_usd)} = ${money(card.zendrop.total_cost_usd)}</span>
          <span>Text match ${Math.round(card.zendrop_match_score)} · ${card.visual_status}</span>
        </div>
        <div class="approval-actions">
          <button type="button" data-approval-action="approved" data-match-id="${card.id}" ${card.status === "approved" ? "disabled" : ""}>Approve</button>
          <button class="secondary" type="button" data-approval-action="skipped" data-match-id="${card.id}" ${card.status === "skipped" ? "disabled" : ""}>Skip</button>
          <button class="danger" type="button" data-approval-action="rejected" data-match-id="${card.id}" ${card.status === "rejected" ? "disabled" : ""}>Reject</button>
          <input data-manual-url="${card.id}" placeholder="Manual URL" ${processed ? "disabled" : ""} value="${card.manual_supplier_url || ""}">
        </div>
      </div>
    </article>
  `;
  }).join("");
  target.querySelectorAll("[data-approval-action]").forEach((button) => {
    button.addEventListener("click", () => updateApprovalStatus(button.dataset.matchId, button.dataset.approvalAction));
  });
}

function approvalImage(src, label) {
  return `
    <figure>
      <img src="${src || ""}" alt="" onerror="this.style.visibility='hidden'">
      <figcaption>${label}</figcaption>
    </figure>
  `;
}

function renderFinalCatalog() {
  const target = document.getElementById("finalCatalog");
  const uploadButton = document.getElementById("uploadDraftsButton");
  const readyForUpload = state.finalProducts.filter((product) =>
    product.image_status === "ready" && Number(product.generated_count || 0) >= 5 && Number(product.media_count || 0) < 5
  );
  uploadButton.disabled = readyForUpload.length === 0;
  if (!state.finalProducts.length) {
    target.innerHTML = `<div class="empty-state">No approved products yet. Approve a match card first; then enhancer prepares fake model images.</div>`;
    return;
  }
  target.innerHTML = state.finalProducts.map((product) => `
    <article class="final-row">
      <img class="product-thumb" src="${product.source_image_url || ""}" alt="" onerror="this.style.visibility='hidden'">
      <div class="product-info">
        <h4 class="product-title">${product.title}</h4>
        <div class="product-meta">${money(product.price)} · images ${product.generated_count}/${product.target_count}</div>
        <div class="product-meta">${product.shopify_product_id || "No Shopify draft yet"}</div>
      </div>
      <span class="status-pill ${product.image_status}">${product.image_status}</span>
      <span class="status-pill ${product.shopify_status || "not_uploaded"}">${product.shopify_status || "not_uploaded"} · media ${product.media_count}</span>
    </article>
  `).join("");
}

function renderCompetitorProducts() {
  const target = document.getElementById("competitorProducts");
  renderSourceBreakdown();
  if (!state.competitorProducts.length) {
    target.innerHTML = `<div class="empty-state">No source products loaded. Start sourcing from a Shopify store or uploaded file.</div>`;
    return;
  }
  target.innerHTML = state.competitorProducts.map((product) => `
    <article class="product-row">
      <img class="product-thumb" src="${product.image_url || ""}" alt="" onerror="this.style.visibility='hidden'">
      <div class="product-info">
        <h4 class="product-title">${product.title}</h4>
        <div class="product-meta">${product.store_url} · ${product.handle}</div>
        <div class="product-meta">${product.product_type || "No type"} · ${money(product.price)}</div>
      </div>
      <span class="status-pill ${product.status}">${product.status}</span>
    </article>
  `).join("");
}

function renderNextAction() {
  const button = document.getElementById("nextActionButton");
  const title = document.getElementById("nextActionTitle");
  const description = document.getElementById("nextActionDescription");
  const readyProducts = state.finalProducts.filter((product) =>
    product.image_status === "ready" && Number(product.generated_count || 0) >= 5
  );
  const finalReady = readyProducts.length;
  const uploaded = readyProducts.filter((product) => Number(product.media_count || 0) >= 5).length;
  const approvedCards = state.approvalCards.filter((card) => card.status === "approved").length;
  const pendingCards = state.approvalCards.filter((card) => card.status === "approval_pending").length;
  const activeJobs = state.jobStatus.active_jobs || [];
  const runningJobs = activeJobs.filter((job) => job.status === "running");
  const queuedJobs = activeJobs.filter((job) => job.status === "queued");

  if (runningJobs.length) {
    title.textContent = `${stageLabel(runningJobs[0].stage)} is running`;
    description.textContent = `Current job: ${jobPayloadLabel(runningJobs[0]) || `job ${runningJobs[0].id}`}. Refresh happens automatically.`;
    button.textContent = "Refresh state";
    button.dataset.action = "refresh";
    return;
  }
  if (queuedJobs.length) {
    title.textContent = "Jobs are queued";
    description.textContent = "Waiting for the worker to pick them up. If this stays queued, start the worker container.";
    button.textContent = "Refresh state";
    button.dataset.action = "refresh";
    return;
  }
  if ((state.summary.competitor_total || 0) === 0) {
    title.textContent = "Add sources";
    description.textContent = "Paste Shopify stores or upload files, then start sourcing.";
    button.textContent = "Start sourcing";
    button.dataset.action = "start";
    return;
  }
  if (!state.approvalCards.length && (state.summary.ready_for_zendrop || 0) > 0) {
    title.textContent = "Build match preview";
    description.textContent = "Queue Zendrop plus AI image matching for ready source products.";
    button.textContent = "Build preview";
    button.dataset.action = "preview";
    return;
  }
  if (!state.approvalCards.length) {
    title.textContent = "No visible matches";
    description.textContent = "Sourcing finished, but filters or AI matching did not produce approval cards. Check source products below.";
    button.textContent = "Refresh state";
    button.dataset.action = "refresh";
    return;
  }
  if (pendingCards > 0) {
    title.textContent = "Approve products";
    description.textContent = "Approve, skip, or reject visible match cards. Only approved cards move forward.";
    button.textContent = "Refresh state";
    button.dataset.action = "refresh";
    return;
  }
  if (approvedCards > 0 && finalReady < approvedCards) {
    title.textContent = "Prepare approved products";
    description.textContent = "Product enhancer and fake image enhancer run only after approval. Retry if no jobs are active.";
    button.textContent = "Retry image enhancer";
    button.dataset.action = "images";
    return;
  }
  if (uploaded < finalReady) {
    title.textContent = "Upload Shopify drafts";
    description.textContent = "Create Shopify products as DRAFT only. No automatic publishing.";
    button.textContent = "Use Upload drafts";
    button.dataset.action = "refresh";
    return;
  }
  title.textContent = "Pipeline complete";
  description.textContent = "All ready products have Shopify drafts.";
  button.textContent = "Refresh state";
  button.dataset.action = "refresh";
}

function stageLabel(stage) {
  return {
    competitor_scrape: "Competitor scraping",
    zendrop_search: "Zendrop search",
    approval_matching: "Match preview",
    approval_match_product: "Match preview",
    openai_content: "Product enhancer",
    final_model_images: "Image enhancer",
    shopify_draft_upload: "Shopify draft upload",
  }[stage] || stage;
}

function renderFilterConfig(config) {
  const form = document.getElementById("sourceSetupForm");
  form.elements.women_keywords.value = keywordsToField(config.women_keywords);
  form.elements.male_keywords.value = keywordsToField(config.male_keywords);
  form.elements.summer_keywords.value = keywordsToField(config.summer_keywords);
  form.elements.exclude_keywords.value = keywordsToField(config.exclude_keywords);
}

async function loadState() {
  const [summary, pipeline, runs, jobStatus, approvalCards, finalCatalog, competitorProducts] = await Promise.all([
    fetchJson("/api/summary"),
    fetchJson("/api/pipeline"),
    fetchJson("/api/runs"),
    fetchJson("/api/job-status"),
    fetchJson("/api/approval-cards"),
    fetchJson("/api/final-catalog?limit=20"),
    fetchJson("/api/competitor-products?limit=30"),
  ]);
  state.summary = summary;
  state.runs = runs.runs || [];
  state.jobStatus = jobStatus;
  state.approvalCards = approvalCards.cards || [];
  state.finalProducts = finalCatalog.products || [];
  state.competitorProducts = competitorProducts.products || [];
  renderSummary();
  renderPipeline(pipeline.steps || []);
  renderPipelineRuns();
  renderJobActivity();
  renderApprovalCards();
  renderFinalCatalog();
  renderCompetitorProducts();
  renderNextAction();
}

async function loadFilterConfig() {
  const config = await fetchJson("/api/filter-config");
  renderFilterConfig(config);
}

async function saveFilters(form) {
  const payload = {
    name: "default",
    women_keywords: fieldToKeywords(form.elements.women_keywords.value),
    male_keywords: fieldToKeywords(form.elements.male_keywords.value),
    summer_keywords: fieldToKeywords(form.elements.summer_keywords.value),
    exclude_keywords: fieldToKeywords(form.elements.exclude_keywords.value),
  };
  await fetchJson("/api/filter-config", { method: "PUT", body: JSON.stringify(payload) });
}

async function uploadAnalyticsFiles(form) {
  const files = Array.from(document.getElementById("analyticsFiles").files || []);
  for (const file of files) {
    await fetchJson("/api/uploads/analytics-files", {
      method: "POST",
      body: JSON.stringify({
        filename: file.name,
        content: await file.text(),
        source_store_url: form.elements.store_urls.value.split(/\n|,/)[0]?.trim() || null,
      }),
    });
  }
  return files.length;
}

async function startSourcing(form) {
  const storeUrls = form.elements.store_urls.value
    .split(/\n|,/)
    .map((storeUrl) => storeUrl.trim())
    .filter(Boolean);
  if (!storeUrls.length && !document.getElementById("analyticsFiles").files.length) {
    throw new Error("Add at least one Shopify store or HTML/TXT file.");
  }
  await saveFilters(form);
  const uploadedCount = await uploadAnalyticsFiles(form);
  let jobsCount = 0;
  if (storeUrls.length) {
    const result = await fetchJson("/api/runs", {
      method: "POST",
      body: JSON.stringify({
        name: form.elements.name.value || "Competitor sourcing",
        store_urls: storeUrls,
        pages_requested: Number(form.elements.pages_requested.value),
        product_limit: Number(form.elements.limit.value),
      }),
    });
    jobsCount = result.jobs_count || 0;
  }
  showToast(`Sourcing queued. ${jobsCount} store jobs, ${uploadedCount} files.`);
}

async function submitSourceSetup(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type='submit']");
  const previousText = button.textContent;
  try {
    button.disabled = true;
    button.textContent = "Starting...";
    await startSourcing(form);
    await loadState();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = previousText;
  }
}

async function runNextAction() {
  const button = document.getElementById("nextActionButton");
  const previousText = button.textContent;
  try {
    button.disabled = true;
    button.textContent = "Working...";
    if (button.dataset.action === "start") {
      await startSourcing(document.getElementById("sourceSetupForm"));
    } else if (button.dataset.action === "preview") {
      const result = await fetchJson("/api/run/approval-matching", { method: "POST" });
      showToast(`Match preview jobs queued: ${result.count}.`);
    } else if (button.dataset.action === "images") {
      const result = await fetchJson("/api/run/final-images", {
        method: "POST",
        body: JSON.stringify({ limit: 10, images_per_product: 6 }),
      });
      showToast(`Model photo jobs queued: ${result.jobs_queued}.`);
    }
    await loadState();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = previousText;
  }
}

async function uploadDrafts() {
  const button = document.getElementById("uploadDraftsButton");
  const previousText = button.textContent;
  try {
    button.disabled = true;
    button.textContent = "Queueing...";
    const result = await fetchJson("/api/run/shopify-drafts", {
      method: "POST",
      body: JSON.stringify({ limit: 10, min_images: 5 }),
    });
    showToast(`Shopify draft jobs queued: ${result.jobs_queued}.`);
    await loadState();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.textContent = previousText;
  }
}

async function resetPipeline() {
  const button = document.getElementById("resetPipelineButton");
  if (!window.confirm("Reset all pipeline data, jobs, matches, generated images and Shopify draft records? Keys and settings stay untouched.")) {
    return;
  }
  const previousText = button.textContent;
  try {
    button.disabled = true;
    button.textContent = "Resetting...";
    const result = await fetchJson("/api/admin/reset", { method: "POST" });
    const deletedTotal = Object.values(result.deleted || {}).reduce((total, count) => total + Number(count || 0), 0);
    showToast(`Pipeline reset. ${deletedTotal} records removed.`);
    await loadState();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = previousText;
  }
}

async function updateApprovalStatus(productMatchId, status) {
  const input = document.querySelector(`[data-manual-url="${productMatchId}"]`);
  const card = document.querySelector(`[data-match-id="${productMatchId}"]`)?.closest(".approval-card");
  const buttons = card ? Array.from(card.querySelectorAll("[data-approval-action]")) : [];
  try {
    buttons.forEach((button) => { button.disabled = true; });
    const result = await fetchJson(`/api/approval-cards/${productMatchId}/status`, {
      method: "POST",
      body: JSON.stringify({
        status,
        manual_supplier_url: input?.value || null,
      }),
    });
    if (status === "approved") {
      showToast(result.content_job_queued ? "Approved. Product enhancer queued." : "Approved. Product enhancer already exists.");
    } else {
      const retryText = result.retry_job_queued ? " New match search queued." : "";
      showToast(result.canceled_jobs ? `Card marked as ${status}. ${result.canceled_jobs} jobs canceled.${retryText}` : `Card marked as ${status}.${retryText}`);
    }
    await loadState();
  } catch (error) {
    showToast(error.message);
    buttons.forEach((button) => { button.disabled = false; });
  }
}

document.getElementById("sourceSetupForm").addEventListener("submit", submitSourceSetup);
document.getElementById("nextActionButton").addEventListener("click", runNextAction);
document.getElementById("uploadDraftsButton").addEventListener("click", uploadDrafts);
document.getElementById("resetPipelineButton").addEventListener("click", resetPipeline);

Promise.all([loadFilterConfig(), loadState()]).catch((error) => showToast(error.message));

setInterval(() => {
  const activeJobs = state.jobStatus.active_jobs || [];
  if (activeJobs.some((job) => ["queued", "running"].includes(job.status))) {
    loadState().catch((error) => showToast(error.message));
  }
}, 10000);
