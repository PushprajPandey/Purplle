/**
 * Store Intelligence — Web dashboard (light theme)
 * Polls FastAPI every 3s for live store metrics.
 */

const REFRESH_MS = 3000;
const API = "";

const STAGE_LABELS = {
  total_entries: "Store entry",
  zone_visitors: "Zone visit",
  billing_visitors: "Billing queue",
  purchasers: "Purchase",
};

let storeIds = ["STORE_BLR_002"];
let refreshTimer = null;

async function fetchJson(path) {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

async function loadStoreList() {
  try {
    const health = await fetchJson("/health");
    const stores = Object.keys(health.last_event_per_store || {});
    if (stores.length) storeIds = stores;
  } catch {
    /* keep default */
  }

  const select = document.getElementById("storeSelect");
  select.innerHTML = "";
  storeIds.forEach((id) => {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = id.replace(/_/g, " ");
    select.appendChild(opt);
  });
}

function formatPct(value) {
  return `${(value * 100).toFixed(1)}%`;
}

function renderKpis(metrics, anomalies) {
  const grid = document.getElementById("kpiGrid");
  const critical = anomalies.filter((a) => a.severity === "CRITICAL").length;
  const warn = anomalies.filter((a) => a.severity === "WARN").length;

  grid.innerHTML = `
    <article class="kpi-card accent-violet">
      <div class="kpi-label">Unique visitors</div>
      <div class="kpi-value">${metrics.unique_visitors}</div>
      <div class="kpi-meta">Customer entries today (staff excluded)</div>
    </article>
    <article class="kpi-card accent-teal">
      <div class="kpi-label">Conversion rate</div>
      <div class="kpi-value">${formatPct(metrics.conversion_rate)}</div>
      <div class="kpi-meta">Visitors who purchased · matched to POS within 5 min</div>
    </article>
    <article class="kpi-card accent-coral">
      <div class="kpi-label">Billing queue</div>
      <div class="kpi-value">${metrics.current_queue_depth}</div>
      <div class="kpi-meta">Visitors in billing zone now</div>
    </article>
    <article class="kpi-card accent-amber">
      <div class="kpi-label">Queue abandonment</div>
      <div class="kpi-value">${formatPct(metrics.abandonment_rate)}</div>
      <div class="kpi-meta">${anomalies.length} active alert${anomalies.length === 1 ? "" : "s"} (${critical} critical, ${warn} warn)</div>
    </article>
  `;
}

function renderFunnel(funnel) {
  const container = document.getElementById("funnelChart");
  const stages = Object.keys(STAGE_LABELS);
  const maxCount = Math.max(...stages.map((s) => funnel[s].count), 1);

  container.innerHTML = stages
    .map((key) => {
      const stage = funnel[key];
      const width = Math.round((stage.count / maxCount) * 100);
      return `
        <div class="funnel-step">
          <span class="funnel-label">${STAGE_LABELS[key]}</span>
          <span class="funnel-stats">
            <strong>${stage.count}</strong>
            ${stage.drop_off_percent > 0 ? `· −${stage.drop_off_percent}% drop` : ""}
          </span>
          <div class="funnel-bar-wrap">
            <div class="funnel-bar" style="width: ${width}%"></div>
          </div>
        </div>
      `;
    })
    .join("");
}

function heatColor(score) {
  const alpha = 0.15 + (score / 100) * 0.55;
  return `rgba(107, 78, 170, ${alpha})`;
}

function renderHeatmap(heatmap) {
  const grid = document.getElementById("heatmapGrid");
  const conf = document.getElementById("heatmapConfidence");
  conf.textContent = heatmap.data_confidence
    ? "Visit frequency normalised 0–100 (high confidence)"
    : "Low session count — interpret with caution (<20 sessions)";

  if (!heatmap.zones.length) {
    grid.innerHTML = '<p class="empty-state">No zone data yet. Run the detection pipeline and ingest events.</p>';
    return;
  }

  grid.innerHTML = heatmap.zones
    .map(
      (z) => `
      <div class="zone-tile" style="background: ${heatColor(z.visit_frequency_normalised)}">
        <div class="zone-name">${z.zone_id.replace("ZONE_", "")}</div>
        <div class="zone-score">${Math.round(z.visit_frequency_normalised)}</div>
        <div class="zone-dwell">avg dwell ${(z.avg_dwell_ms / 1000).toFixed(1)}s</div>
      </div>
    `
    )
    .join("");
}

function renderAnomalies(anomalies) {
  const list = document.getElementById("anomalyList");
  if (!anomalies.length) {
    list.innerHTML = '<li class="empty-state">No active anomalies — store operating normally.</li>';
    return;
  }

  list.innerHTML = anomalies
    .map(
      (a) => `
      <li class="anomaly-item">
        <span class="severity-badge severity-${a.severity}">${a.severity}</span>
        <div class="anomaly-body">
          <h3>${a.anomaly_type.replace(/_/g, " ")}</h3>
          <p>${a.description}</p>
          <p class="anomaly-action">${a.suggested_action}</p>
        </div>
      </li>
    `
    )
    .join("");
}

function renderHealth(health, storeId) {
  const banner = document.getElementById("healthBanner");
  const stale = health.stale_feed?.[storeId];
  const lastEvent = health.last_event_per_store?.[storeId];

  banner.classList.add("visible");
  banner.classList.toggle("healthy", health.status === "healthy" && !stale);
  banner.classList.toggle("degraded", health.status !== "healthy" || stale);

  let msg = `API ${health.status}`;
  if (lastEvent) msg += ` · Last event ${new Date(lastEvent).toLocaleString()}`;
  if (stale) msg += " · ⚠ Stale feed (>10 min)";
  banner.textContent = msg;
}

async function refresh() {
  const storeId = document.getElementById("storeSelect").value;
  const statusEl = document.getElementById("refreshStatus");

  const paths = [
    `/stores/${storeId}/metrics`,
    `/stores/${storeId}/funnel`,
    `/stores/${storeId}/heatmap`,
    `/stores/${storeId}/anomalies`,
    "/health",
  ];
  const results = await Promise.allSettled(paths.map((p) => fetchJson(p)));

  const pick = (i) =>
    results[i].status === "fulfilled" ? results[i].value : null;

  const metrics = pick(0);
  const funnel = pick(1);
  const heatmap = pick(2);
  const anomalies = pick(3) ?? [];
  const health = pick(4);

  if (metrics) renderKpis(metrics, anomalies);
  if (funnel) renderFunnel(funnel);
  if (heatmap) renderHeatmap(heatmap);
  if (anomalies) renderAnomalies(anomalies);
  if (health) renderHealth(health, storeId);

  const coreOk = metrics && funnel && heatmap;
  if (coreOk) {
    statusEl.textContent = "Live";
    statusEl.className = "refresh-pill live";
    document.getElementById("lastUpdated").textContent =
      `Updated ${new Date().toLocaleString()}`;
  } else {
    statusEl.textContent = "API offline";
    statusEl.className = "refresh-pill error";
    results.forEach((r, i) => {
      if (r.status === "rejected") console.error(paths[i], r.reason);
    });
  }
}

function startPolling() {
  if (refreshTimer) clearInterval(refreshTimer);
  refresh();
  refreshTimer = setInterval(refresh, REFRESH_MS);
}

document.getElementById("storeSelect").addEventListener("change", refresh);

loadStoreList().then(startPolling);
