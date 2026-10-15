const state = { view: "monitor", runtime: null, cameras: [], alerts: [], jobs: [], settings: null, alertFilter: "all", cameraRenderSignature: null, alertRenderSignature: null, settingsFormDirty: false, debugEventHistory: new Map(), debugEventCursor: new Map(), debugXViews: new Map(), debugXRefreshTimers: new Map(), debugXDrag: null, debugPreview: null };
const pageTitles = { monitor: "Live monitor", alerts: "Alert centre", cameras: "Camera sources", config: "Detection configuration" };
const weaponClasses = ["knife", "gun"];
const weaponLabels = { knife: "Knife", gun: "Gun" };
const weaponDebugLabels = { parang_machete: "Parang / machete", knife: "Knife", baseball_bat: "Baseball bat", long_gun: "Long gun", gun: "Gun" };
const debugWeaponColors = { parang_machete: "#79a9ff", knife: "#56d6ad", baseball_bat: "#f2bb58", long_gun: "#c38bff", gun: "#ff7f8a" };
const modes = ["sensitive", "moderate", "certain"];
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { const body = await response.json(); message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail); } catch (_) {}
    throw new Error(message.replaceAll("'", ""));
  }
  return response.status === 204 ? null : response.json();
}

function toast(message, type = "success") {
  const element = document.createElement("div");
  element.className = `toast ${type}`; element.textContent = message;
  $("#toast-stack").append(element); setTimeout(() => element.remove(), 5000);
}

function setView(view) {
  if (!pageTitles[view]) return;
  state.view = view;
  $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach(item => item.classList.toggle("active", item.id === `view-${view}`));
  $("#page-title").textContent = pageTitles[view];
  window.history.replaceState(null, "", `#${view}`);
}

function formatBytes(bytes) { if (!bytes) return "0 B"; const units = ["B", "KB", "MB", "GB"]; const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 3); return `${(bytes / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`; }
function formatDuration(seconds) { const total = Math.max(0, Math.round(seconds || 0)); return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`; }
function formatEta(seconds) { if (seconds == null) return "estimating"; return seconds < 60 ? `${Math.ceil(seconds)}s remaining` : `${Math.ceil(seconds / 60)}m remaining`; }
function formatDate(value) { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—"; }
function activeJob(cameraId) { return state.jobs.find(job => job.cameraId === cameraId && ["queued", "running"].includes(job.status)); }
function latestJob(cameraId) { return state.jobs.find(job => job.cameraId === cameraId); }

function renderRuntime() {
  if (!state.runtime) return;
  const isPersistent = state.runtime.persistenceMode === "sqlite";
  $("#sidebar-mode").textContent = `${isPersistent ? "Local SQLite" : "Memory Demo"}${state.runtime.authMode === "firebase" ? " · Firebase Auth" : ""}`;
  $("#upload-privacy-note").textContent = "Local media mode: files remain under this GuardAId server and are never uploaded to Firebase.";
  const warnings = state.runtime.warnings || [];
  $("#runtime-banner").classList.toggle("hidden", !warnings.length); $("#runtime-banner").textContent = warnings.join(" · ");
  $("#pipeline-readiness").innerHTML = Object.entries(state.runtime.readiness || {}).map(([key, item]) => {
    const ready = Boolean(item.workerConnected);
    return `<div class="readiness-row ${ready ? "ready" : ""}"><i></i><div><strong>${escapeHtml(key)} · ${ready ? (item.loaded ? "loaded" : "ready") : "unavailable"}</strong><small>${escapeHtml(item.message)}</small></div></div>`;
  }).join("");
}

function renderPolicy() {
  if (!state.settings) return;
  const s = state.settings;
  const escalation = s.alertNotificationEscalation;
  const recordingFloor = Math.min(s.violence.sensitiveThreshold, s.violence.moderateThreshold, s.violence.certainThreshold);
  $("#policy-summary").innerHTML = `<div><dt>Violence recording floor</dt><dd>${Number(recordingFloor).toFixed(6)}</dd></div><div><dt>Violence thresholds</dt><dd>${[s.violence.sensitiveThreshold, s.violence.moderateThreshold, s.violence.certainThreshold].map(value => Number(value).toFixed(6)).join(" / ")}</dd></div><div><dt>Violence window</dt><dd>${s.violence.segmentSeconds}s / ${s.violence.strideSeconds}s</dd></div><div><dt>Incident merge gap</dt><dd>${s.violence.incidentMergeGapSeconds}s</dd></div><div><dt>Unanswered alert follow-up</dt><dd>${escalation.sameAudienceRepeatAfterSeconds / 60}m → all users ${escalation.allUsersEscalationAfterSeconds / 60}m</dd></div><div><dt>Detection debug</dt><dd>${s.detectionDebugModeEnabled ? "On · new analyses" : "Off"}</dd></div><div><dt>Weapon rule</dt><dd>${s.weapon.nRequired} of ${s.weapon.mWindow} @ ${s.weapon.sampleFps} FPS per class and profile</dd></div><div><dt>VLM scene input</dt><dd>${s.vlm.sceneDescriptionFrames} uniform frames</dd></div><div><dt>VLM budget</dt><dd>${s.vlm.videoTokenBudget} tokens</dd></div><div><dt>Maximum feeds</dt><dd>${state.runtime?.maxActiveFeeds ?? 4}</dd></div>`;
  $("#config-version").textContent = `Config v${s.configVersion}`;
}

function collectDebugEvents() {
  state.jobs.forEach(job => {
    if (!job.debugModeEnabled) return;
    const cursor = state.debugEventCursor.get(job.id) || 0;
    const fresh = (job.debugEvents || []).filter(item => item.sequence > cursor).sort((a, b) => a.sequence - b.sequence);
    if (!fresh.length) return;
    const history = state.debugEventHistory.get(job.id) || [];
    history.push(...fresh);
    if (history.length > 3000) history.splice(0, history.length - 3000);
    state.debugEventHistory.set(job.id, history);
    state.debugEventCursor.set(job.id, fresh[fresh.length - 1].sequence);
  });
}

function debugEventTime(event) {
  const start = Number(event.sourceSeconds) || 0;
  return event.detector === "violence" && event.windowEndSeconds != null
    ? (start + Number(event.windowEndSeconds)) / 2
    : start;
}

function debugXView(jobId, duration) {
  const saved = state.debugXViews.get(jobId);
  if (!saved) return { start: 0, end: duration };
  const span = Math.min(duration, Math.max(.01, saved.end - saved.start));
  const start = Math.max(0, Math.min(duration - span, saved.start));
  return { start, end: start + span };
}

function debugTimeLabel(seconds, visibleDuration) {
  const value = Math.max(0, seconds);
  if (visibleDuration < 10 && value >= 60) {
    const minutes = Math.floor(value / 60);
    const remainder = (value % 60).toFixed(1).padStart(4, "0");
    return `${minutes}:${remainder}`;
  }
  if (value >= 60) return formatDuration(value);
  return `${value.toFixed(visibleDuration < 10 ? 1 : 0)}s`;
}

function debugChartHtml(job, detector) {
  const events = (state.debugEventHistory.get(job.id) || []).filter(item => item.detector === detector).sort((a, b) => debugEventTime(a) - debugEventTime(b));
  const isViolence = detector === "violence";
  const weaponScoreNames = [...new Set(events.flatMap(event => Object.keys(event.scores || {})))];
  const series = isViolence
    ? [{ key: "Fight", label: "Fight probability", color: "#79a9ff" }]
    : (weaponScoreNames.length ? weaponScoreNames : ["knife", "gun"])
      .map(key => ({ key, label: weaponDebugLabels[key] || key, color: debugWeaponColors[key] || "#79a9ff" }));
  const width = 720, height = 300, left = 49, right = 704, top = 18, bottom = 250;
  const plotWidth = right - left, plotHeight = bottom - top;
  const duration = Math.max(.1, Number(job.durationSeconds) || 0, ...events.map(debugEventTime));
  const view = debugXView(job.id, duration), visibleDuration = Math.max(.01, view.end - view.start);
  const xFor = time => left + ((time - view.start) / visibleDuration) * plotWidth;
  const yFor = score => top + (1 - Math.max(0, Math.min(1, Number(score) || 0))) * plotHeight;
  const yTicks = [1, .75, .5, .25, 0];
  const xTicks = [0, .25, .5, .75, 1];
  const grid = yTicks.map(value => {
    const y = yFor(value);
    return `<line class="chart-grid-line" x1="${left}" y1="${y}" x2="${right}" y2="${y}"/><text class="chart-axis-label y-label" x="${left - 9}" y="${y + 4}" text-anchor="end">${Math.round(value * 100)}%</text>`;
  }).join("") + xTicks.map(fraction => {
    const x = left + fraction * plotWidth, seconds = view.start + fraction * visibleDuration;
    const label = debugTimeLabel(seconds, visibleDuration);
    return `<line class="chart-grid-line x-grid-line" x1="${x}" y1="${top}" x2="${x}" y2="${bottom}"/><text class="chart-axis-label x-label" x="${x}" y="${bottom + 22}" text-anchor="middle">${label}</text>`;
  }).join("");
  const lines = series.map(item => {
    const points = events.map(event => ({ x: xFor(debugEventTime(event)), y: yFor(event.scores?.[item.key] || 0) }));
    if (!points.length) return "";
    const path = points.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
    const markerStride = Math.max(1, Math.ceil(points.length / 260));
    const markers = points.map((point, index) => {
      const record = events[index];
      const positive = isViolence ? (record.positiveLabels || []).includes("Fight") : (record.positiveLabels || []).includes(item.key);
      const alert = ["alert", "review_queued"].includes(record.status) && positive;
      if (index % markerStride !== 0 && index !== points.length - 1 && !positive) return "";
      const outline = alert ? "#ff5964" : positive ? "#ffc34d" : "#64748b";
      return `<circle class="chart-sample-dot" cx="${point.x.toFixed(2)}" cy="${point.y.toFixed(2)}" r="2.35" fill="${item.color}" stroke="${outline}"/>`;
    }).join("");
    return `<path class="chart-series-line" d="${path}" stroke="${item.color}"/>${markers}`;
  }).join("");
  const chartTitle = isViolence ? "MC3-18 violence probability" : "YOLO weapon confidence";
  const legend = series.map(item => `<span class="chart-legend-item"><i style="--series-color:${item.color}"></i>${escapeHtml(item.label)}</span>`).join("");
  const empty = events.length ? "" : `<div class="debug-chart-empty">Waiting for ${isViolence ? "a complete MC3 window" : "the first YOLO sample"}.</div>`;
  const clipId = `debug-clip-${String(job.id).replace(/[^a-z0-9_-]/gi, "")}-${detector}`;
  const shownRange = `${view.start.toFixed(2)}–${view.end.toFixed(2)}s`;
  return `<article class="debug-chart-card"><header class="debug-chart-head"><div><strong>${chartTitle}</strong><small>${events.length.toLocaleString()} samples · ${shownRange} · Y 0–100%</small></div><div class="debug-chart-actions"><button type="button" class="debug-zoom-out" data-job-id="${escapeHtml(job.id)}" aria-label="Zoom out time axis" title="Zoom out">−</button><button type="button" class="debug-zoom-in" data-job-id="${escapeHtml(job.id)}" aria-label="Zoom in time axis" title="Zoom in">+</button><button type="button" class="debug-zoom-reset" data-job-id="${escapeHtml(job.id)}" aria-label="Show full video timeline" title="Show full video">Reset</button><span class="chart-source-tag">${isViolence ? "MC3" : "YOLO"}</span></div></header><div class="debug-chart-wrap"><svg class="debug-chart-svg" viewBox="0 0 ${width} ${height}" role="button" tabindex="0" aria-label="${chartTitle}. Click a time to inspect its nearest sample. Scroll or pinch to zoom horizontally; right-drag to pan." data-job-id="${escapeHtml(job.id)}" data-detector="${detector}" data-duration="${duration}" data-view-start="${view.start}" data-view-end="${view.end}" data-plot-left="${left}" data-plot-width="${plotWidth}"><title>${chartTitle}; click to inspect, scroll or pinch to zoom X, right-drag to pan</title>${grid}<path class="chart-axis-line" d="M${left},${top} V${bottom} H${right}"/><defs><clipPath id="${clipId}"><rect x="${left}" y="${top}" width="${plotWidth}" height="${plotHeight}"/></clipPath></defs><g class="chart-data-layer" clip-path="url(#${clipId})">${lines}</g><rect class="chart-hit-area" x="${left}" y="${top}" width="${plotWidth}" height="${plotHeight}"/></svg>${empty}</div><div class="debug-chart-legend">${legend}</div><small class="debug-chart-hint">Click a time to inspect the nearest ${isViolence ? "video window" : "sampled frame"}; scroll or pinch to zoom X, right-drag to pan. Y stays fixed.</small></article>`;
}

function debugTimelineHtml(job) {
  return `<div class="debug-chart-stack">${debugChartHtml(job, "violence")}${debugChartHtml(job, "weapon")}</div>`;
}

function debugPanelHtml(job) {
  const events = state.debugEventHistory.get(job.id) || [];
  const lastSequence = events[events.length - 1]?.sequence || 0;
  const truncated = job.debugEventCount > events.length;
  return `<section class="debug-panel" data-debug-job-id="${escapeHtml(job.id)}" data-chart-event-count="${events.length}" data-chart-last-sequence="${lastSequence}" data-chart-duration="${Number(job.durationSeconds) || 0}"><header class="debug-panel-head"><div><strong>Detection Debug</strong><small>${job.debugEventCount.toLocaleString()} detector samples collected</small></div><span class="debug-live">${["queued", "running"].includes(job.status) ? "LIVE" : "RUN COMPLETE"}</span></header><div class="debug-legend"><span><i class="legend-alert"></i>Alert sample</span><span><i class="legend-positive"></i>Threshold hit</span><span><i class="legend-muted"></i>Below threshold</span></div><small class="debug-hint">Click a time to preview the nearest sample. Use +/− or scroll/pinch to zoom the X axis, Reset for the full video, or right-drag to pan; Y stays at 0–100%.</small>${truncated ? `<small class="debug-truncated">Showing the latest ${events.length.toLocaleString()} samples in these charts.</small>` : ""}${debugTimelineHtml(job)}</section>`;
}

function drawDebugWeaponFrame() {
  const preview = state.debugPreview, image = $("#debug-preview-image"), canvas = $("#debug-preview-canvas");
  if (!preview || preview.event.detector !== "weapon" || !image.naturalWidth || !image.naturalHeight) return;
  const width = image.naturalWidth, height = image.naturalHeight;
  canvas.width = width; canvas.height = height;
  const context = canvas.getContext("2d");
  context.drawImage(image, 0, 0, width, height);
  const positives = new Set(preview.event.positiveLabels || []);
  const boxes = (preview.event.boxes || []).filter(box => positives.has(box.className));
  boxes.forEach(box => {
    const x = Math.max(0, Math.min(width, Number(box.x1) * width));
    const y = Math.max(0, Math.min(height, Number(box.y1) * height));
    const boxWidth = Math.max(0, Math.min(width - x, (Number(box.x2) - Number(box.x1)) * width));
    const boxHeight = Math.max(0, Math.min(height - y, (Number(box.y2) - Number(box.y1)) * height));
    const color = ["alert", "review_queued"].includes(preview.event.status) ? "#ff5964" : "#ffc34d";
    const lineWidth = Math.max(2, Math.round(width / 480));
    context.lineWidth = lineWidth;
    context.strokeStyle = color;
    context.strokeRect(x, y, boxWidth, boxHeight);
    const label = `${weaponDebugLabels[box.className] || box.className} ${(Number(box.confidence) * 100).toFixed(1)}% · ${(Number(box.threshold) * 100).toFixed(1)}%`;
    context.font = `600 ${Math.max(12, Math.round(width / 75))}px system-ui, sans-serif`;
    const labelHeight = Math.max(21, Math.round(width / 44));
    const labelWidth = Math.min(width, context.measureText(label).width + 12);
    const labelY = Math.max(labelHeight, y);
    context.fillStyle = color;
    context.fillRect(x, labelY - labelHeight, labelWidth, labelHeight);
    context.fillStyle = "#09111a";
    context.fillText(label, x + 6, labelY - Math.round(labelHeight * .29), labelWidth - 8);
  });
  $("#debug-preview-empty").hidden = true;
  $("#debug-preview-canvas").hidden = false;
  $("#debug-preview-caption").textContent = boxes.length
    ? `${boxes.length} candidate box${boxes.length === 1 ? "" : "es"} · ${positives.size} class${positives.size === 1 ? "" : "es"} passed threshold`
    : "No weapon candidate passed the technical prefilter for this frame.";
}

function updateDebugPreviewProgress() {
  const preview = state.debugPreview, video = $("#debug-preview-video");
  if (!preview || preview.event.detector !== "violence") return;
  const duration = Math.max(.001, preview.end - preview.start);
  const elapsed = Math.max(0, Math.min(duration, video.currentTime - preview.start));
  $("#debug-preview-progress i").style.width = `${(elapsed / duration) * 100}%`;
  if (video.currentTime >= preview.end - .015) {
    video.pause();
    $("#debug-preview-pause").textContent = "Replay";
  }
}

function monitorDebugPreviewPlayback() {
  const preview = state.debugPreview, video = $("#debug-preview-video");
  if (!preview || preview.event.detector !== "violence") return;
  preview.rafId = null;
  if (video.paused) return;
  updateDebugPreviewProgress();
  if (!video.paused) preview.rafId = requestAnimationFrame(monitorDebugPreviewPlayback);
}

function closeDebugPreview() {
  const dialog = $("#debug-preview-dialog"), video = $("#debug-preview-video"), canvas = $("#debug-preview-canvas");
  if (state.debugPreview?.rafId != null) cancelAnimationFrame(state.debugPreview.rafId);
  video.pause();
  video.onloadedmetadata = null; video.onseeked = null; video.onerror = null;
  video.removeAttribute("src"); video.load();
  const image = $("#debug-preview-image"); image.onload = null; image.onerror = null; image.removeAttribute("src"); image.hidden = true;
  const context = canvas.getContext("2d"); context?.clearRect(0, 0, canvas.width, canvas.height);
  canvas.hidden = true; $("#debug-preview-video").hidden = true;
  $("#debug-preview-empty").hidden = true;
  $("#debug-preview-progress i").style.width = "0%";
  state.debugPreview = null;
  if (dialog.open) dialog.close();
}

function openDebugPreview(jobId, sequence) {
  const job = state.jobs.find(item => item.id === jobId);
  const camera = state.cameras.find(item => item.id === job?.cameraId);
  const event = (state.debugEventHistory.get(jobId) || []).find(item => Number(item.sequence) === Number(sequence));
  if (!job || !camera || !event) { toast("This detector record is no longer available.", "error"); return; }
  const dialog = $("#debug-preview-dialog"), video = $("#debug-preview-video"), image = $("#debug-preview-image"), canvas = $("#debug-preview-canvas");
  if (dialog.open) closeDebugPreview();
  const isViolence = event.detector === "violence";
  const start = Math.max(0, Number(event.sourceSeconds) || 0);
  const end = isViolence ? Math.max(start + .001, Number(event.windowEndSeconds) || start + 1) : start;
  state.debugPreview = { jobId, cameraId: camera.id, event, start, end, rafId: null };
  $("#debug-preview-chip").textContent = isViolence ? "MC3 WINDOW" : "YOLO FRAME";
  $("#debug-preview-chip").classList.toggle("weapon", !isViolence);
  $("#debug-preview-title").textContent = isViolence ? "Violence classifier window" : "Weapon detector frame";
  const timeLabel = isViolence ? `${start.toFixed(2)}–${end.toFixed(2)}s` : `${start.toFixed(2)}s`;
  $("#debug-preview-subtitle").textContent = `${camera.name} · ${timeLabel} · detector threshold result`;
  $("#debug-preview-caption").textContent = isViolence ? `Fight probability ${(Number(event.scores?.Fight || 0) * 100).toFixed(2)}%` : "Loading sampled frame…";
  const threshold = Number(event.thresholds?.selected || 0);
  $("#debug-preview-detail").textContent = isViolence
    ? `Selected threshold ${(threshold * 100).toFixed(2)}% · ${event.status.replaceAll("_", " ")}`
    : "Red: alert · yellow: weapon confidence met its class threshold";
  $("#debug-preview-progress").hidden = !isViolence;
  $("#debug-preview-replay").hidden = !isViolence;
  $("#debug-preview-pause").hidden = !isViolence;
  $("#debug-preview-replay").textContent = `Replay ${Math.max(.1, end - start).toFixed(1)}-second window`;
  $("#debug-preview-pause").textContent = "Pause";
  $("#debug-preview-video").hidden = !isViolence;
  image.hidden = true;
  canvas.hidden = true;
  $("#debug-preview-empty").textContent = isViolence ? "Loading selected video window…" : "Decoding the sampled source frame…";
  $("#debug-preview-empty").hidden = false;
  dialog.showModal();
  if (isViolence) {
    video.onloadedmetadata = () => {
      const preview = state.debugPreview;
      if (!preview || preview.jobId !== jobId) return;
      if (Number.isFinite(video.duration)) preview.end = Math.min(preview.end, video.duration);
      preview.start = Math.min(preview.start, Math.max(0, video.duration - .001));
      video.currentTime = preview.start;
      if (Math.abs(video.currentTime - preview.start) < .01) {
        $("#debug-preview-empty").hidden = true;
        video.play().catch(() => { $("#debug-preview-pause").textContent = "Resume"; });
      }
    };
    video.onseeked = () => {
      if (!state.debugPreview || state.debugPreview.jobId !== jobId) return;
      $("#debug-preview-empty").hidden = true;
      video.play().catch(() => { $("#debug-preview-pause").textContent = "Resume"; });
    };
    video.onerror = () => { $("#debug-preview-empty").textContent = "This browser could not play the source video format."; $("#debug-preview-empty").hidden = false; toast("The source video could not be opened at this point.", "error"); };
    video.src = camera.mediaUrl;
    video.load();
  } else {
    video.pause(); video.removeAttribute("src"); video.load();
    image.onload = drawDebugWeaponFrame;
    image.onerror = () => { $("#debug-preview-empty").textContent = "The sampled source frame could not be decoded."; $("#debug-preview-empty").hidden = false; toast("The sampled source frame could not be decoded.", "error"); };
    image.src = `/api/cameras/${encodeURIComponent(camera.id)}/frame?seconds=${encodeURIComponent(start)}`;
  }
}

function openNearestDebugSample(chart, clientX) {
  const jobId = chart.dataset.jobId, detector = chart.dataset.detector;
  const events = (state.debugEventHistory.get(jobId) || []).filter(item => item.detector === detector);
  if (!events.length) return;
  const bounds = chart.getBoundingClientRect();
  const viewX = ((clientX - bounds.left) / Math.max(1, bounds.width)) * 720;
  const left = Number(chart.dataset.plotLeft), plotWidth = Number(chart.dataset.plotWidth);
  const viewStart = Number(chart.dataset.viewStart), viewEnd = Number(chart.dataset.viewEnd);
  const fraction = Math.max(0, Math.min(1, (viewX - left) / plotWidth));
  const targetSeconds = viewStart + fraction * (viewEnd - viewStart);
  const nearest = events.reduce((best, item) => Math.abs(debugEventTime(item) - targetSeconds) < Math.abs(debugEventTime(best) - targetSeconds) ? item : best);
  openDebugPreview(jobId, nearest.sequence);
}

function refreshDebugCharts(jobId) {
  const job = state.jobs.find(item => item.id === jobId);
  const panel = $(`.debug-panel[data-debug-job-id="${CSS.escape(jobId)}"]`);
  const stack = panel && $(".debug-chart-stack", panel);
  if (!job || !stack) return;
  const history = state.debugEventHistory.get(jobId) || [];
  stack.insertAdjacentHTML("afterend", debugTimelineHtml(job));
  stack.remove();
  panel.dataset.chartEventCount = String(history.length);
  panel.dataset.chartLastSequence = String(history[history.length - 1]?.sequence || 0);
  panel.dataset.chartDuration = String(Number(job.durationSeconds) || 0);
}

function setDebugXRange(jobId, duration, start, end, refresh = true) {
  const fullDuration = Math.max(.1, duration);
  const span = Math.max(Math.min(.25, fullDuration), Math.min(fullDuration, end - start));
  const clampedStart = Math.max(0, Math.min(fullDuration - span, start));
  if (span >= fullDuration - .001) state.debugXViews.delete(jobId);
  else state.debugXViews.set(jobId, { start: clampedStart, end: clampedStart + span });
  if (refresh) refreshDebugCharts(jobId);
}

function scheduleDebugChartRefresh(jobId) {
  if (state.debugXRefreshTimers.has(jobId)) return;
  const timer = window.setTimeout(() => {
    state.debugXRefreshTimers.delete(jobId);
    refreshDebugCharts(jobId);
  }, 40);
  state.debugXRefreshTimers.set(jobId, timer);
}

function zoomDebugXAtPointer(chart, deltaY, clientX) {
  const jobId = chart.dataset.jobId, duration = Number(chart.dataset.duration);
  const { start, end } = debugXView(jobId, duration), currentSpan = end - start;
  const bounds = chart.getBoundingClientRect();
  const viewX = ((clientX - bounds.left) / Math.max(1, bounds.width)) * 720;
  const plotLeft = Number(chart.dataset.plotLeft), plotWidth = Number(chart.dataset.plotWidth);
  const anchorRatio = Math.max(0, Math.min(1, (viewX - plotLeft) / plotWidth));
  const anchorSeconds = start + anchorRatio * currentSpan;
  const minSpan = Math.min(.25, duration);
  const scale = Math.exp(Math.max(-1.4, Math.min(1.4, deltaY * .0015)));
  const nextSpan = Math.max(minSpan, Math.min(duration, currentSpan * scale));
  const nextStart = anchorSeconds - anchorRatio * nextSpan;
  setDebugXRange(jobId, duration, nextStart, nextStart + nextSpan, false);
  scheduleDebugChartRefresh(jobId);
}

function changeDebugZoom(button, direction) {
  const jobId = button.dataset.jobId;
  const chart = $(".debug-chart-svg", button.closest(".debug-chart-card"));
  if (!chart) return;
  const duration = Number(chart.dataset.duration), start = Number(chart.dataset.viewStart), end = Number(chart.dataset.viewEnd);
  if (direction === "reset") { state.debugXViews.delete(jobId); refreshDebugCharts(jobId); return; }
  const nextSpan = (end - start) * (direction === "in" ? .7 : 1.4);
  const center = (start + end) / 2;
  setDebugXRange(jobId, duration, center - nextSpan / 2, center + nextSpan / 2);
}

function jobPanel(job) {
  if (!job) return "";
  const running = ["queued", "running"].includes(job.status);
  return `<div class="job-panel ${escapeHtml(job.status)}"><div class="job-line"><strong class="job-status">${escapeHtml(job.status.toUpperCase())}</strong><span class="job-progress-label">${job.progressPercent.toFixed(1)}% · ${formatDuration(job.sourceSeconds)} / ${formatDuration(job.durationSeconds)}</span></div><div class="progress-track"><i style="width:${job.progressPercent}%"></i></div><div class="job-counters"><span>Weapon frames ${job.weaponFramesProcessed}</span><span>MC3 windows ${job.violenceWindowsProcessed}</span><span>VLM reviews ${job.borderlineReviewsProcessed}</span><span>VLM pending ${job.pendingVlmJobs}</span><span>Alerts ${job.alertsCreated}</span></div><small class="job-message">${escapeHtml(job.error || job.latestResult || (running ? formatEta(job.etaSeconds) : "Analysis complete"))}</small>${job.debugModeEnabled ? debugPanelHtml(job) : ""}</div>`;
}

function updateJobPanel(card, job) {
  let panel = $(".job-panel", card);
  if (!job) { panel?.remove(); return; }
  if (!panel) {
    $(".camera-card-foot", card).insertAdjacentHTML("afterend", jobPanel(job));
    return;
  }
  const running = ["queued", "running"].includes(job.status);
  panel.className = `job-panel ${escapeHtml(job.status)}`;
  $(".job-status", panel).textContent = job.status.toUpperCase();
  $(".job-progress-label", panel).textContent = `${job.progressPercent.toFixed(1)}% · ${formatDuration(job.sourceSeconds)} / ${formatDuration(job.durationSeconds)}`;
  $(".progress-track i", panel).style.width = `${job.progressPercent}%`;
  $(".job-counters", panel).innerHTML = `<span>Weapon frames ${job.weaponFramesProcessed}</span><span>MC3 windows ${job.violenceWindowsProcessed}</span><span>VLM reviews ${job.borderlineReviewsProcessed}</span><span>VLM pending ${job.pendingVlmJobs}</span><span>Alerts ${job.alertsCreated}</span>`;
  $(".job-message", panel).textContent = job.error || job.latestResult || (running ? formatEta(job.etaSeconds) : "Analysis complete");
  if (!job.debugModeEnabled) return;
  let debugPanel = $(".debug-panel", panel);
  if (!debugPanel) {
    panel.insertAdjacentHTML("beforeend", debugPanelHtml(job));
    return;
  }
  $(".debug-panel-head small", debugPanel).textContent = `${job.debugEventCount.toLocaleString()} detector samples collected`;
  $(".debug-live", debugPanel).textContent = running ? "LIVE" : "RUN COMPLETE";
  const history = state.debugEventHistory.get(job.id) || [];
  const newestSequence = history[history.length - 1]?.sequence || 0;
  const lastSequence = Number(debugPanel.dataset.chartLastSequence || 0);
  const previousDuration = Number(debugPanel.dataset.chartDuration || 0);
  const duration = Number(job.durationSeconds) || 0;
  if ((newestSequence !== lastSequence || Math.abs(duration - previousDuration) > .001) && state.debugXDrag?.jobId !== job.id) {
    const stack = $(".debug-chart-stack", debugPanel);
    stack.insertAdjacentHTML("afterend", debugTimelineHtml(job));
    stack.remove();
    debugPanel.dataset.chartEventCount = String(history.length);
    debugPanel.dataset.chartLastSequence = String(newestSequence);
    debugPanel.dataset.chartDuration = String(duration);
  }
  const truncated = job.debugEventCount > history.length;
  let note = $(".debug-truncated", debugPanel);
  if (truncated && !note) {
    note = document.createElement("small");
    note.className = "debug-truncated";
    debugPanel.insertBefore(note, $(".debug-chart-stack", debugPanel));
  }
  if (truncated && note) note.textContent = `Showing the latest ${history.length.toLocaleString()} samples in these charts.`;
  else note?.remove();
}

function renderCameras() {
  collectDebugEvents();
  const enabled = state.cameras.filter(camera => camera.enabled);
  const signature = JSON.stringify(enabled.map(camera => { const job = activeJob(camera.id) || latestJob(camera.id); return [camera.id, camera.name, camera.location, camera.mediaUrl, job?.id, job && ["queued", "running"].includes(job.status) ? "active" : job?.status]; }));
  if (signature === state.cameraRenderSignature) {
    enabled.forEach(camera => { const card = $(`.camera-card[data-camera-id="${CSS.escape(camera.id)}"]`), job = activeJob(camera.id) || latestJob(camera.id); if (!card) return; updateJobPanel(card, job); });
  } else {
    state.cameraRenderSignature = signature;
    $("#camera-wall").innerHTML = enabled.map(camera => {
      const job = activeJob(camera.id) || latestJob(camera.id), running = job && ["queued", "running"].includes(job.status);
      return `<article class="camera-card" data-camera-id="${escapeHtml(camera.id)}"><div class="video-wrap"><video src="${escapeHtml(camera.mediaUrl)}" controls muted preload="metadata"></video><div class="camera-overlay"><span class="camera-label">${escapeHtml(camera.name)}</span><span class="camera-status ${running ? "analysing" : ""}">${running ? "ANALYSING" : escapeHtml(camera.status.toUpperCase())}</span></div></div><div class="camera-card-foot"><div><strong>${escapeHtml(camera.location)}</strong><small>${formatDuration(camera.durationSeconds)} source · ${formatBytes(camera.sizeBytes)}</small></div><button class="button ${running ? "danger" : "primary"} ${running ? "cancel-job" : "start-camera"}" data-id="${escapeHtml(running ? job.id : camera.id)}">${running ? "Stop analysis" : "Start analysis"}</button></div>${jobPanel(job)}</article>`;
    }).join("");
  }
  $("#camera-empty").classList.toggle("hidden", enabled.length > 0); $("#camera-wall").classList.toggle("hidden", !enabled.length); $("#feed-count").textContent = `${enabled.length} feed${enabled.length === 1 ? "" : "s"}`;
  $("#camera-total").textContent = `${state.cameras.length} total`;
  $("#camera-admin-list").innerHTML = state.cameras.length ? state.cameras.map(camera => `<div class="camera-admin-row"><div class="camera-thumb">▣</div><div><strong>${escapeHtml(camera.name)} ${camera.enabled ? "" : "· disabled"}</strong><small>${escapeHtml(camera.location)} · ${formatBytes(camera.sizeBytes)} · ${formatDuration(camera.durationSeconds)}</small></div><div class="row-actions"><button class="replace-camera" data-id="${escapeHtml(camera.id)}" title="Replace video">↥</button><button class="edit-camera" data-id="${escapeHtml(camera.id)}" title="Edit">✎</button><button class="delete-camera" data-id="${escapeHtml(camera.id)}" title="Delete">×</button></div></div>`).join("") : `<div class="empty-state"><div class="empty-glyph">▣</div><h3>No cameras configured</h3></div>`;
}

function renderAlerts() {
  const filtered = state.alerts.filter(alert => state.alertFilter === "all" || alert.type.includes(state.alertFilter));
  $("#alert-count").textContent = state.alerts.length; $("#alerts-empty").classList.toggle("hidden", filtered.length > 0); $("#alerts-list").classList.toggle("hidden", !filtered.length);
  const signature = JSON.stringify([state.alertFilter, filtered.map(alert => [alert.id, alert.status, alert.isProvisional, alert.isBorderlinePromoted, alert.borderlineReviewStatus, alert.borderlineReviewSummary, alert.detectionQualifications, alert.weaponClassScores, alert.evidenceStatus, alert.descriptionStatus, alert.sceneDescription, (alert.evidence || []).map(item => item.id)])]);
  if (signature === state.alertRenderSignature) return;
  state.alertRenderSignature = signature;
  $("#alerts-list").innerHTML = filtered.map(alert => {
    const evidenceItems = alert.evidence || [];
    const media = renderAlertEvidence(evidenceItems, alert.evidenceStatus);
    const reviewLabel = ({ queued: "VLM review queued", running: "VLM reviewing", positive: "VLM confirmed", negative: "VLM did not confirm", failed: "VLM review failed", disabled: "VLM review disabled", cancelled: "VLM review cancelled", legacy_unknown: "Historical review state unavailable" })[alert.borderlineReviewStatus];
    const qualification = alert.detectionQualifications || {};
    const violenceModes = Array.isArray(qualification.violence) ? qualification.violence : [];
    const weaponModes = qualification.weapon && typeof qualification.weapon === "object" ? qualification.weapon : {};
    const weaponScores = alert.weaponClassScores || {};
    const basis = [];
    if (violenceModes.length) basis.push(`Violence notification qualification: ${violenceModes.map(escapeHtml).join(", ")}`);
    for (const [mode, classes] of Object.entries(weaponModes)) {
      if (Array.isArray(classes) && classes.length) basis.push(`${escapeHtml(mode)} weapon qualification: ${classes.map(escapeHtml).join(", ")}`);
    }
    const scoreEntries = Object.entries(weaponScores).filter(([, score]) => Number.isFinite(Number(score)));
    if (scoreEntries.length) basis.push(`Weapon scores: ${scoreEntries.map(([name, score]) => `${escapeHtml(name)} ${(Number(score) * 100).toFixed(1)}%`).join(" · ")}`);
    const review = alert.borderlineReviewSummary || {};
    const reviewCounts = Number(review.total || 0) > 0
      ? `${review.total} window${Number(review.total) === 1 ? "" : "s"}: ${review.positive || 0} positive · ${review.negative || 0} negative · ${review.failed || 0} failed · ${review.cancelled || 0} cancelled${Number(review.pending || 0) ? ` · ${review.pending} pending` : ""}`
      : "No review windows";
    const reviewDetail = review.latestReason
      ? `Latest VLM note: ${escapeHtml(review.latestReason)}${review.latestUncertainty ? ` · uncertainty ${escapeHtml(review.latestUncertainty)}` : ""}`
      : review.latestErrorCode ? `Latest review error: ${escapeHtml(review.latestErrorCode)}` : "";
    return `<article class="panel alert-card"><div class="alert-evidence">${media}</div><div class="alert-main"><header><span class="alert-type">${escapeHtml(alert.type.replaceAll("_", " "))}</span><strong>${escapeHtml(alert.cameraName)}</strong>${reviewLabel ? `<span class="provisional-chip">${escapeHtml(reviewLabel)}</span>` : ""}</header><p>${escapeHtml(alert.sceneDescription || `Scene description ${alert.descriptionStatus}.`)}</p><small class="muted">Model ${escapeHtml(alert.modelVersion)} · Config v${alert.configVersion}${alert.isBorderlinePromoted ? " · VLM confirmed" : ""}</small>${basis.length ? `<div class="alert-audit-lines">${basis.map(line => `<small>${line}</small>`).join("")}</div>` : ""}<div class="alert-review-summary"><small><strong>Borderline review:</strong> ${escapeHtml(reviewCounts)}</small>${reviewDetail ? `<small>${reviewDetail}</small>` : ""}</div></div><div class="alert-meta"><span>Confidence <strong>${(alert.maxConfidence * 100).toFixed(1)}%</strong></span><span>Detections <strong>${alert.detectionCount}</strong></span><span>Source <strong>${Number(alert.sourceStartSeconds || 0).toFixed(1)}–${Number(alert.sourceEndSeconds || 0).toFixed(1)}s</strong></span><span>Status <strong>${escapeHtml(alert.status)}</strong></span><span>${formatDate(alert.lastDetectedAt)}</span></div></article>`;
  }).join("");
  wireAlertEvidenceGalleries($("#alerts-list"));
}

function renderAlertEvidence(evidenceItems, status) {
  if (!evidenceItems.length) {
    return `<span>${({ preparing: "Evidence is being prepared", failed: "Evidence capture/storage failed", legacy_unknown: "Historical evidence status unavailable" })[status] || "No evidence media is attached"}</span>`;
  }
  const slides = evidenceItems.map((evidence, index) => {
    if (!evidence.url) return "";
    const timestamp = evidence.startSeconds == null ? "" : `<figcaption>Source ${Number(evidence.startSeconds).toFixed(1)}s</figcaption>`;
    const media = evidence.mediaType === "weapon_screenshot"
      ? `<a href="${escapeHtml(evidence.url)}" target="_blank" rel="noopener"><img src="${escapeHtml(evidence.url)}" alt="Weapon evidence frame ${index + 1}" loading="lazy"></a>`
      : `<video src="${escapeHtml(evidence.url)}" controls preload="metadata"></video>`;
    return `<figure class="alert-evidence-slide">${media}${timestamp}</figure>`;
  }).join("");
  return `<div class="alert-evidence-gallery"><div class="alert-evidence-track" tabindex="0" aria-label="Swipe to review alert evidence">${slides}</div>${evidenceItems.length > 1 ? `<button class="evidence-gallery-nav previous" type="button" aria-label="Previous evidence">‹</button><button class="evidence-gallery-nav next" type="button" aria-label="Next evidence">›</button><span class="evidence-gallery-count">1 / ${evidenceItems.length}</span>` : ""}</div>`;
}

function wireAlertEvidenceGalleries(root) {
  $$(".alert-evidence-gallery", root).forEach(gallery => {
    const track = $(".alert-evidence-track", gallery);
    const slides = $$(".alert-evidence-slide", track);
    if (slides.length < 2) return;
    const previous = $(".evidence-gallery-nav.previous", gallery);
    const next = $(".evidence-gallery-nav.next", gallery);
    const counter = $(".evidence-gallery-count", gallery);
    const update = () => {
      const index = Math.min(slides.length - 1, Math.round(track.scrollLeft / Math.max(1, track.clientWidth)));
      counter.textContent = `${index + 1} / ${slides.length}`;
      previous.disabled = index === 0;
      next.disabled = index === slides.length - 1;
    };
    previous.addEventListener("click", () => track.scrollBy({ left: -track.clientWidth, behavior: "smooth" }));
    next.addEventListener("click", () => track.scrollBy({ left: track.clientWidth, behavior: "smooth" }));
    track.addEventListener("scroll", update, { passive: true });
    update();
  });
}

function buildThresholdInputs() {
  $("#weapon-threshold-inputs").innerHTML = weaponClasses.map(className => `<div class="threshold-row"><strong>${weaponLabels[className]}</strong>${modes.map(mode => `<input aria-label="${mode} ${weaponLabels[className]}" name="weapon.classThresholds.${mode}.${className}" type="text" inputmode="decimal" autocomplete="off" spellcheck="false" required>`).join("")}</div>`).join("");
}
function parseWeaponThreshold(value) {
  let text = String(value).trim().replace(",", ".");
  if (text.startsWith(".")) text = `0${text}`;
  return text ? Number(text) : NaN;
}
function roundWeaponThreshold(value) {
  const number = parseWeaponThreshold(value);
  if (!Number.isFinite(number)) return NaN;
  return Math.round((number + Number.EPSILON) * 10000) / 10000;
}
function setInput(name, value) {
  const input = $(`[name="${name}"]`, $("#settings-form"));
  if (!input) return;
  if (input.type === "checkbox") input.checked = Boolean(value);
  else if (name.startsWith("weapon.classThresholds.")) input.value = roundWeaponThreshold(value).toFixed(4);
  else input.value = value;
}
function fillSettingsForm(force = false) {
  if (state.settingsFormDirty && !force) return;
  if (!state.settings) return;
  const s = state.settings;
  setInput("detectionDebugModeEnabled", s.detectionDebugModeEnabled);
  Object.entries(s.violence).forEach(([key, value]) => setInput(`violence.${key}`, value));
  Object.entries(s.weapon).forEach(([key, value]) => typeof value !== "object" && setInput(`weapon.${key}`, value));
  Object.entries(s.vlm).forEach(([key, value]) => setInput(`vlm.${key}`, value));
  setInput("alertNotificationEscalation.sameAudienceRepeatMinutes", s.alertNotificationEscalation.sameAudienceRepeatAfterSeconds / 60);
  setInput("alertNotificationEscalation.allUsersEscalationMinutes", s.alertNotificationEscalation.allUsersEscalationAfterSeconds / 60);
  modes.forEach(mode => weaponClasses.forEach(className => setInput(`weapon.classThresholds.${mode}.${className}`, s.weapon.classThresholds[mode][className])));
}
function numeric(form, name) { return Number($(`[name="${name}"]`, form).value); }
function settingsFromForm() {
  const form = $("#settings-form"), current = structuredClone(state.settings);
  current.detectionDebugModeEnabled = $("[name='detectionDebugModeEnabled']", form).checked;
  ["segmentSeconds", "strideSeconds", "detectorInputFrames", "sensitiveThreshold", "moderateThreshold", "certainThreshold", "incidentMergeGapSeconds"].forEach(key => current.violence[key] = numeric(form, `violence.${key}`));
  ["sampleFps", "nmsCandidateFloor", "nmsIouThreshold", "nRequired", "mWindow", "incidentMergeGapSeconds"].forEach(key => current.weapon[key] = numeric(form, `weapon.${key}`));
  modes.forEach(mode => weaponClasses.forEach(className => current.weapon.classThresholds[mode][className] = roundWeaponThreshold($(`[name="weapon.classThresholds.${mode}.${className}"]`, form).value)));
  ["inputFps", "sceneDescriptionFrames", "videoTokenBudget", "maxOutputTokens"].forEach(key => current.vlm[key] = numeric(form, `vlm.${key}`));
  current.alertNotificationEscalation.sameAudienceRepeatAfterSeconds = Math.round(numeric(form, "alertNotificationEscalation.sameAudienceRepeatMinutes") * 60);
  current.alertNotificationEscalation.allUsersEscalationAfterSeconds = Math.round(numeric(form, "alertNotificationEscalation.allUsersEscalationMinutes") * 60);
  current.vlm.borderlineReviewEnabled = $("[name='vlm.borderlineReviewEnabled']", form).checked; current.vlm.sceneDescriptionEnabled = $("[name='vlm.sceneDescriptionEnabled']", form).checked;
  return current;
}
async function saveSettings(settings, message = "Configuration saved", resetForm = true) { state.settings = await api("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) }); if (resetForm) state.settingsFormDirty = false; renderPolicy(); fillSettingsForm(resetForm); toast(`${message} · version ${state.settings.configVersion}`); }
async function videoDuration(file) { return new Promise(resolve => { const video = document.createElement("video"), url = URL.createObjectURL(file); video.preload = "metadata"; video.onloadedmetadata = () => { const value = Number.isFinite(video.duration) ? video.duration : 0; URL.revokeObjectURL(url); resolve(value); }; video.onerror = () => { URL.revokeObjectURL(url); resolve(0); }; video.src = url; }); }

async function refreshAll(quiet = false) {
  try { [state.runtime, state.cameras, state.alerts, state.jobs, state.settings] = await Promise.all([api("/api/runtime"), api("/api/cameras"), api("/api/alerts"), api("/api/analysis"), api("/api/settings")]); renderRuntime(); renderPolicy(); renderCameras(); renderAlerts(); fillSettingsForm(); }
  catch (error) { if (!quiet) toast(`Unable to load GuardAId: ${error.message}`, "error"); }
}

function bindEvents() {
  $$(".nav-item").forEach(item => item.addEventListener("click", () => setView(item.dataset.view))); $$('[data-go]').forEach(button => button.addEventListener("click", () => setView(button.dataset.go))); $("#refresh-button").addEventListener("click", () => refreshAll());
  const fileInput = $("#camera-file"), dropzone = $("#dropzone"); fileInput.addEventListener("change", () => $("#file-label").textContent = fileInput.files[0]?.name || "Choose a CCTV video"); ["dragenter", "dragover"].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.add("dragging"); })); ["dragleave", "drop"].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.remove("dragging"); })); dropzone.addEventListener("drop", event => { if (event.dataTransfer.files.length) { fileInput.files = event.dataTransfer.files; fileInput.dispatchEvent(new Event("change")); } });
  $("#camera-upload-form").addEventListener("submit", async event => { event.preventDefault(); const form = event.currentTarget, button = $("button[type='submit']", form), file = fileInput.files[0]; if (!file) return; button.disabled = true; button.textContent = "Adding camera..."; try { const data = new FormData(); data.append("video", file); data.append("name", $("#camera-name").value); data.append("location", $("#camera-location").value || "Unspecified"); data.append("durationSeconds", await videoDuration(file)); await api("/api/cameras", { method: "POST", body: data }); form.reset(); $("#file-label").textContent = "Choose a CCTV video"; await refreshAll(true); toast("Camera source added"); } catch (error) { toast(error.message, "error"); } finally { button.disabled = false; button.textContent = "Add camera feed"; } });

  document.addEventListener("click", async event => {
    const zoomButton = event.target.closest(".debug-zoom-in, .debug-zoom-out, .debug-zoom-reset");
    if (zoomButton) { changeDebugZoom(zoomButton, zoomButton.classList.contains("debug-zoom-in") ? "in" : zoomButton.classList.contains("debug-zoom-out") ? "out" : "reset"); return; }
    const debugChart = event.target.closest(".debug-chart-svg"); if (debugChart) { if (event.button === 0) openNearestDebugSample(debugChart, event.clientX); return; }
    const start = event.target.closest(".start-camera"); if (start) { start.disabled = true; start.textContent = "Loading detectors..."; try { const job = await api(`/api/analysis/${encodeURIComponent(start.dataset.id)}`, { method: "POST" }); state.jobs = [job, ...state.jobs.filter(item => item.id !== job.id)]; state.cameraRenderSignature = null; renderCameras(); const video = $(`.camera-card[data-camera-id="${CSS.escape(job.cameraId)}"] video`); if (video) { video.currentTime = 0; await video.play().catch(() => {}); } toast(`Real-time analysis started for ${job.cameraName}`); } catch (error) { toast(error.message, "error"); } finally { start.disabled = false; } }
    const cancel = event.target.closest(".cancel-job"); if (cancel) { try { await api(`/api/analysis/${encodeURIComponent(cancel.dataset.id)}`, { method: "DELETE" }); toast("Stop requested"); await refreshAll(true); } catch (error) { toast(error.message, "error"); } }
    const edit = event.target.closest(".edit-camera"); if (edit) { const camera = state.cameras.find(item => item.id === edit.dataset.id); $("#edit-camera-id").value = camera.id; $("#edit-camera-name").value = camera.name; $("#edit-camera-location").value = camera.location; $("#edit-camera-enabled").checked = camera.enabled; $("#edit-camera-dialog").showModal(); }
    const replace = event.target.closest(".replace-camera"); if (replace) { const input = document.createElement("input"); input.type = "file"; input.accept = "video/mp4,video/webm,video/quicktime,.mkv,.avi"; input.onchange = async () => { const file = input.files[0]; if (!file) return; const data = new FormData(); data.append("video", file); data.append("durationSeconds", await videoDuration(file)); try { await api(`/api/cameras/${encodeURIComponent(replace.dataset.id)}/video`, { method: "PUT", body: data }); await refreshAll(true); toast("Camera video replaced"); } catch (error) { toast(error.message, "error"); } }; input.click(); }
    const remove = event.target.closest(".delete-camera"); if (remove) { const camera = state.cameras.find(item => item.id === remove.dataset.id); if (!camera || !confirm(`Delete ${camera.name} and its stored source video?`)) return; try { await api(`/api/cameras/${encodeURIComponent(camera.id)}`, { method: "DELETE" }); await refreshAll(true); toast("Camera deleted"); } catch (error) { toast(error.message, "error"); } }
  });
  document.addEventListener("keydown", event => {
    const chart = event.target.closest(".debug-chart-svg");
    if (chart && ["Enter", " "].includes(event.key)) { event.preventDefault(); const bounds = chart.getBoundingClientRect(); openNearestDebugSample(chart, bounds.left + bounds.width / 2); }
  });
  const cameraWall = $("#camera-wall");
  cameraWall.addEventListener("contextmenu", event => { if (event.target.closest(".debug-chart-svg")) event.preventDefault(); });
  cameraWall.addEventListener("wheel", event => {
    const chart = event.target.closest(".debug-chart-svg");
    if (!chart || !event.deltaY) return;
    // Chromium reports laptop trackpad pinch as a Ctrl+wheel event; ordinary
    // mouse-wheel scrolling over a chart uses the same X-only zoom interaction.
    event.preventDefault();
    const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1);
    zoomDebugXAtPointer(chart, delta, event.clientX);
  }, { passive: false });
  cameraWall.addEventListener("pointerdown", event => {
    const chart = event.target.closest(".debug-chart-svg");
    if (!chart || event.button !== 2) return;
    event.preventDefault();
    const bounds = chart.getBoundingClientRect(), plotPixels = bounds.width * Number(chart.dataset.plotWidth) / 720;
    if (!plotPixels) return;
    chart.focus(); chart.setPointerCapture(event.pointerId); chart.classList.add("is-panning");
    state.debugXDrag = {
      chart, pointerId: event.pointerId, jobId: chart.dataset.jobId, duration: Number(chart.dataset.duration),
      startX: event.clientX, start: Number(chart.dataset.viewStart), end: Number(chart.dataset.viewEnd), plotPixels, deltaX: 0,
    };
  });
  document.addEventListener("pointermove", event => {
    const drag = state.debugXDrag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag.deltaX = event.clientX - drag.startX;
    const svgDelta = drag.deltaX * 720 / Math.max(1, drag.chart.getBoundingClientRect().width);
    $(".chart-data-layer", drag.chart)?.setAttribute("transform", `translate(${svgDelta.toFixed(1)} 0)`);
    event.preventDefault();
  });
  const finishDebugPan = (event, cancelled = false) => {
    const drag = state.debugXDrag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    state.debugXDrag = null;
    drag.chart.classList.remove("is-panning");
    $(".chart-data-layer", drag.chart)?.removeAttribute("transform");
    if (drag.chart.hasPointerCapture(event.pointerId)) drag.chart.releasePointerCapture(event.pointerId);
    if (!cancelled && Math.abs(drag.deltaX) > 2) {
      const shiftSeconds = -drag.deltaX / drag.plotPixels * (drag.end - drag.start);
      setDebugXRange(drag.jobId, drag.duration, drag.start + shiftSeconds, drag.end + shiftSeconds);
    }
  };
  document.addEventListener("pointerup", event => finishDebugPan(event));
  document.addEventListener("pointercancel", event => finishDebugPan(event, true));
  const previewDialog = $("#debug-preview-dialog"), previewVideo = $("#debug-preview-video");
  previewVideo.addEventListener("timeupdate", updateDebugPreviewProgress);
  previewVideo.addEventListener("play", () => {
    const preview = state.debugPreview;
    if (preview?.event.detector === "violence" && preview.rafId == null) preview.rafId = requestAnimationFrame(monitorDebugPreviewPlayback);
  });
  $("#debug-preview-close").addEventListener("click", closeDebugPreview);
  $("#debug-preview-done").addEventListener("click", closeDebugPreview);
  $("#debug-preview-replay").addEventListener("click", () => {
    const preview = state.debugPreview; if (!preview || preview.event.detector !== "violence") return;
    if (Math.abs(previewVideo.currentTime - preview.start) < .01) previewVideo.play().catch(() => {});
    else previewVideo.currentTime = preview.start;
    $("#debug-preview-pause").textContent = "Pause";
  });
  $("#debug-preview-pause").addEventListener("click", () => {
    const preview = state.debugPreview; if (!preview || preview.event.detector !== "violence") return;
    if (previewVideo.paused && previewVideo.currentTime >= preview.end - .015) {
      previewVideo.currentTime = preview.start;
      $("#debug-preview-pause").textContent = "Pause";
      return;
    }
    if (previewVideo.paused) { previewVideo.play().catch(() => {}); $("#debug-preview-pause").textContent = "Pause"; }
    else { previewVideo.pause(); $("#debug-preview-pause").textContent = "Resume"; }
  });
  previewDialog.addEventListener("click", event => { if (event.target === previewDialog) closeDebugPreview(); });
  previewDialog.addEventListener("cancel", event => { event.preventDefault(); closeDebugPreview(); });
  $("#edit-camera-form").addEventListener("submit", async event => { event.preventDefault(); const id = $("#edit-camera-id").value; try { await api(`/api/cameras/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("#edit-camera-name").value, location: $("#edit-camera-location").value, enabled: $("#edit-camera-enabled").checked }) }); $("#edit-camera-dialog").close(); await refreshAll(true); toast("Camera updated"); } catch (error) { toast(error.message, "error"); } });
  $("#settings-form").addEventListener("input", event => {
    state.settingsFormDirty = true;
    const input = event.target.closest("input[name^='weapon.classThresholds.']");
    if (!input) return;
    const value = parseWeaponThreshold(input.value);
    input.setCustomValidity(Number.isFinite(value) && value >= 0 && value <= 1 ? "" : "Enter a threshold from 0 to 1.");
  });
  $("#settings-form").addEventListener("change", () => { state.settingsFormDirty = true; });
  $("#settings-form").addEventListener("focusout", event => {
    const input = event.target.closest("input[name^='weapon.classThresholds.']");
    if (!input || input.value === "") return;
    const value = parseWeaponThreshold(input.value);
    if (Number.isFinite(value) && value >= 0 && value <= 1) {
      input.setCustomValidity("");
      input.value = roundWeaponThreshold(value).toFixed(4);
    } else {
      input.setCustomValidity("Enter a threshold from 0 to 1.");
    }
  });
  $("#settings-form").addEventListener("submit", async event => { event.preventDefault(); const button = $("button[type='submit']", event.currentTarget); button.disabled = true; try { await saveSettings(settingsFromForm()); } catch (error) { toast(error.message, "error"); } finally { button.disabled = false; } });
  $$("#alert-filters button").forEach(button => button.addEventListener("click", () => { state.alertFilter = button.dataset.filter; $$("#alert-filters button").forEach(item => item.classList.toggle("active", item === button)); renderAlerts(); }));
}

function startClock() { const dateFormat = new Intl.DateTimeFormat(undefined, { weekday: "long", year: "numeric", month: "short", day: "2-digit" }), timeFormat = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }); const update = () => { const now = new Date(); $("#clock").innerHTML = `<small>${escapeHtml(dateFormat.format(now))}</small><strong>${escapeHtml(timeFormat.format(now))}</strong>`; }; update(); setInterval(update, 1000); }
document.addEventListener("DOMContentLoaded", () => { buildThresholdInputs(); bindEvents(); startClock(); setView(location.hash.slice(1) || "monitor"); refreshAll(); setInterval(() => refreshAll(true), 1000); });
