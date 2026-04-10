const state = {
  settings: null,
  firstLoad: true,
  lastRefreshTs: 0,
  soundEnabled: true,
  played: new Set(),
  knownRows: new Map(),
};

const $ = (id) => document.getElementById(id);

async function init() {
  const settingsResp = await fetch("/api/settings");
  state.settings = await settingsResp.json();
  state.soundEnabled = !!state.settings.sound.enabled;
  $("screen-title").textContent = state.settings.screen.title;
  renderSoundBtn();
  bindEvents();
  startClock();
  await refreshAlerts();
  setInterval(refreshAlerts, state.settings.screen.refreshIntervalMs || 5000);
}

function bindEvents() {
  $("severity-filter").addEventListener("change", refreshAlerts);
  $("status-filter").addEventListener("change", refreshAlerts);
  $("refresh-btn").addEventListener("click", refreshAlerts);
  $("sound-toggle").addEventListener("click", () => {
    state.soundEnabled = !state.soundEnabled;
    renderSoundBtn();
    pulseButton($("sound-toggle"));
  });
  $("close-btn").addEventListener("click", closeModal);
  $("mask").addEventListener("click", closeModal);
}

function renderSoundBtn() {
  $("sound-toggle").textContent = `声音：${state.soundEnabled ? "开启" : "关闭"}`;
}

async function refreshAlerts() {
  const params = new URLSearchParams({
    severity: $("severity-filter").value,
    status: $("status-filter").value,
    limit: String(state.settings.screen.maxItems || 100),
  });

  const resp = await fetch(`/api/alerts?${params.toString()}`);
  const data = await resp.json();
  const items = data.items || [];
  const prevTs = state.lastRefreshTs;
  state.lastRefreshTs = Date.now();

  renderSummary(items);
  renderTable(items, prevTs);
  renderFeed(items.slice(0, 5));
  $("last-refresh").textContent = formatTime(new Date().toISOString());

  if (!state.firstLoad) {
    playNewAlertSound(items, prevTs);
    pulsePanelIfNew(items, prevTs);
  }
  state.firstLoad = false;
}

function renderSummary(items) {
  const counts = { critical: 0, warning: 0, info: 0, total: items.length };
  items.forEach((x) => {
    if (counts[x.severity] !== undefined) counts[x.severity] += 1;
  });

  animateNumber($("critical-count"), counts.critical);
  animateNumber($("warning-count"), counts.warning);
  animateNumber($("info-count"), counts.info);
  animateNumber($("total-count"), counts.total);
}

function renderTable(items, prevTs) {
  const body = $("alerts-body");
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="7" class="empty">当前无告警</td></tr>`;
    return;
  }

  body.innerHTML = items.map((item, index) => {
    const isFresh = !state.firstLoad && Number(item.updated_at_ts) > prevTs && item.status !== "recovered";
    const animClass = isFresh ? "row-fresh" : "";
    return `
      <tr class="${animClass}" style="animation-delay:${index * 0.03}s">
        <td><span class="badge severity-${item.severity}">${item.severity}</span></td>
        <td><span class="badge status-${item.status}">${statusText(item.status)}</span></td>
        <td>${escapeHtml(item.alert_name)}</td>
        <td>${escapeHtml(item.target)}</td>
        <td>${escapeHtml(formatTime(item.trigger_time))}</td>
        <td>${escapeHtml(formatTime(item.updated_at))}</td>
        <td>
          <button onclick="showDetail(${item.id})">详情</button>
          ${item.status === "recovered" ? "" : `<button onclick="ackAlert(${item.id})">确认</button>`}
        </td>
      </tr>
    `;
  }).join("");

  requestAnimationFrame(() => {
    body.querySelectorAll(".row-fresh").forEach((row) => {
      row.classList.add("row-fresh-active");
      setTimeout(() => row.classList.remove("row-fresh-active"), 1800);
    });
  });
}

function renderFeed(items) {
  const box = $("feed-list");
  box.innerHTML = items.map((item, index) => `
    <div class="feed-item" style="animation-delay:${index * 0.06}s">
      <div><span class="badge severity-${item.severity}">${item.severity}</span></div>
      <h4>${escapeHtml(item.alert_name)}</h4>
      <div>${escapeHtml(item.content)}</div>
      <small>${escapeHtml(item.target)} | ${escapeHtml(formatTime(item.updated_at))}</small>
    </div>
  `).join("");
}

async function showDetail(id) {
  const resp = await fetch(`/api/alerts/${id}`);
  const item = await resp.json();

  $("detail-title").textContent = item.alert_name;
  $("detail-status").textContent = statusText(item.status);
  $("detail-severity").textContent = item.severity;
  $("detail-target").textContent = item.target;
  $("detail-content").textContent = item.content;
  $("detail-tags").textContent = JSON.stringify(item.tags, null, 2);
  $("detail-metrics").textContent = JSON.stringify(item.metrics, null, 2);
  $("detail-raw").textContent = JSON.stringify(item.raw_payload, null, 2);

  $("modal").classList.remove("hidden");
  document.querySelector(".modal-content").animate(
    [
      { opacity: 0, transform: "translateY(14px) scale(.98)" },
      { opacity: 1, transform: "translateY(0) scale(1)" }
    ],
    { duration: 220, easing: "ease-out" }
  );
}

function closeModal() {
  $("modal").classList.add("hidden");
}

async function ackAlert(id) {
  await fetch(`/api/alerts/${id}/ack`, { method: "POST" });
  pulseButton(event?.target);
  await refreshAlerts();
}

function playNewAlertSound(items, prevTs) {
  const news = items.filter(
    (x) =>
      x.status !== "recovered" &&
      Number(x.updated_at_ts) > prevTs &&
      !state.played.has(`${x.id}:${x.updated_at_ts}`)
  );

  if (!news.length || !state.soundEnabled) return;

  news.sort(
    (a, b) =>
      ["critical", "warning", "info"].indexOf(a.severity) -
      ["critical", "warning", "info"].indexOf(b.severity)
  );

  const top = news[0];
  state.played.add(`${top.id}:${top.updated_at_ts}`);
  playSound(top.severity);
}

async function playSound(severity) {
  const file = state.settings.sound[severity];
  if (file) {
    try {
      const audio = new Audio(file);
      audio.volume = severity === "critical" ? 1 : 0.75;
      await audio.play();
      return;
    } catch (_) {}
  }

  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return;

  const ctx = new AudioContext();
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();

  osc.type = severity === "critical" ? "square" : "sine";
  osc.frequency.value = severity === "critical" ? 920 : severity === "warning" ? 700 : 560;
  gain.gain.value = severity === "critical" ? 0.09 : 0.06;

  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + (severity === "critical" ? 0.36 : 0.22));
}

function startClock() {
  const run = () => {
    $("clock").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  };
  run();
  setInterval(run, 1000);
}

function statusText(status) {
  return { firing: "触发中", processing: "处理中", recovered: "已恢复" }[status] || status;
}

function formatTime(v) {
  const d = new Date(v);
  return isNaN(d.getTime()) ? String(v) : d.toLocaleString("zh-CN", { hour12: false });
}

function escapeHtml(v) {
  return String(v ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function animateNumber(el, nextValue) {
  const current = Number(el.dataset.value || "0");
  if (current === nextValue) return;

  const start = performance.now();
  const duration = 450;
  el.dataset.value = String(nextValue);

  function frame(now) {
    const progress = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const value = Math.round(current + (nextValue - current) * eased);
    el.textContent = value;
    if (progress < 1) requestAnimationFrame(frame);
  }

  requestAnimationFrame(frame);
}

function pulseButton(button) {
  if (!button || !button.animate) return;
  button.animate(
    [
      { transform: "scale(1)" },
      { transform: "scale(1.06)" },
      { transform: "scale(1)" }
    ],
    { duration: 240, easing: "ease-out" }
  );
}

function pulsePanelIfNew(items, prevTs) {
  const hasFresh = items.some((item) => Number(item.updated_at_ts) > prevTs && item.status !== "recovered");
  if (!hasFresh) return;

  const board = document.querySelector(".board");
  if (!board || !board.animate) return;

  board.animate(
    [
      { boxShadow: "0 20px 60px rgba(52, 78, 124, 0.12)" },
      { boxShadow: "0 24px 80px rgba(47, 124, 255, 0.22)" },
      { boxShadow: "0 20px 60px rgba(52, 78, 124, 0.12)" }
    ],
    { duration: 900, easing: "ease-out" }
  );
}

init();

