/**
 * 首页 dashboard（M6）。
 *
 * 定位：这个工具主要用来**无人值守**，所以默认落地页不该是「选库」那个空输入框，
 * 而应该是「它还在跑吗 / 有没有要我处理的 / 最近成不成」。
 *
 * 两个入口：
 *   - 自动监测：进入流程但**配置会持久化**，并且会启动监听
 *   - 立即执行：进入流程但**配置只对这一次生效**，不落盘、不动自动监测的配置
 *
 * 两者的区别统一由 `S.mode` 表示，各处保存逻辑据此决定是否持久化。
 */
import { S, STATUS_LABEL, tone } from "./state.js";
import { $, $$, esc, api, toast, fmtTime } from "./core.js";
import { t } from "./i18n/index.js";

/** 当前模式："dash" | "auto" | "manual" */
export const MODE_AUTO = "auto";
export const MODE_MANUAL = "manual";

/** 这次进入流程时，改配置要不要落盘 */
export function persistEnabled() {
  return S.mode === MODE_AUTO;
}

/* ─────────────────────────────────────────── 视图切换 */

export function setView(view) {
  const app = $("#app");
  if (!app) return;
  app.dataset.view = view;
  document.body.classList.toggle("inFlow", view === "flow");
  try { localStorage.setItem("fnlrc_view", view); } catch (e) { /* 隐私模式 */ }
  scrollTo({ top: 0 });
}

export function openFlow(mode) {
  S.mode = mode;
  // 立即执行：先记下自动监测那份配置，退出时不用恢复 —— 因为根本不写盘
  setView("flow");
}

export function backToDash() {
  S.mode = "";
  setView("dash");
  refreshDash();
}

/* ─────────────────────────────────────────── 渲染 */

/** 读一次服务端状态，把三块都重画。 */
export async function refreshDash() {
  if (!$("#dash")) return;
  await Promise.all([renderStatus(), renderRecent()]);
  renderTodoPending();
}

async function renderStatus() {
  const line = $("#dashWatchState");
  const meta = $("#dashWatchMeta");
  const dot = $("#dashDot");
  const btn = $("#dashToggleWatch");
  if (!line) return;

  try {
    const [cfg, st] = await Promise.all([api("/api/config"), api("/api/watch/status")]);
    S.config = cfg;
    const running = !!st.running;
    S.watchRunning = running;

    line.textContent = running ? t("dash.watch_on") : t("dash.watch_off");
    dot.classList.toggle("on", running);
    if (btn) btn.textContent = running ? t("dash.watch_stop") : t("dash.watch_start");

    const root = (cfg.watch || {}).watch_root || "";
    const bits = [];
    if (root) bits.push(root);
    const counts = st.counts || {};
    const done = (counts.processed || 0) + (counts.moved || 0);
    if (running && done) bits.push(t("dash.watch_count", { n: done }));
    else if (!root) bits.push(t("dash.watch_no_root"));
    if (meta) meta.textContent = bits.join("　·　");
  } catch (e) {
    line.textContent = t("dash.watch_error", { err: e.message });
    dot.classList.remove("on");
  }
}

/** 待办数字要等最近一次报告算过才知道，先占位再填。 */
async function renderTodoPending() {
  const box = $("#dashTodo");
  if (!box) return;
  try {
    const d = await api("/api/jobs");
    const jobs = d.jobs || [];
    const latest = jobs[0];
    renderTodo(latest ? (latest.stats || {}) : null, latest);
  } catch (e) {
    renderTodo(null, null);
  }
}

function renderTodo(stats, latestJob) {
  const box = $("#dashTodo");
  const hint = $("#dashTodoHint");
  if (!box) return;

  // 只有「真正需要人来决策/修」的才进待办。单语、跳过不算 —— 它们本身就是结果。
  const cells = [
    ["lang_mismatch", t("dash.todo_manual")],
    ["failed", t("dash.todo_failed")],
    ["no_lyrics", t("dash.todo_no_lyrics")]
  ];

  box.innerHTML = cells.map(([key, label]) => {
    const n = stats ? (stats[key] || 0) : 0;
    const alert = n > 0 ? " hasAlert" : "";
    return `<button class="todoCell${alert}" type="button" data-todo="${key}">
        <div class="n">${n}</div><div class="k">${esc(label)}</div>
      </button>`;
  }).join("");

  const total = stats ? cells.reduce((a, [k]) => a + (stats[k] || 0), 0) : 0;
  if (hint) {
    hint.textContent = !stats ? t("dash.todo_none_yet")
      : total ? t("dash.todo_hint", { n: total })
      : t("dash.todo_clear");
  }

  $$("#dashTodo [data-todo]").forEach(b => b.addEventListener("click", async () => {
    if (!latestJob) { toast(t("dash.todo_none_yet")); return; }
    try {
      const rep = await api("/api/report/" + latestJob.job_id);
      const { showResults } = await import("./screens.js");
      openFlow(MODE_MANUAL);
      showResults(rep.results, rep.stats, latestJob.job_id);
      // 跳到对应筛选项，省得用户再自己点
      const { S: st } = await import("./state.js");
      st.filter = b.dataset.todo;
      const { renderFilters, renderReport } = await import("./screens.js");
      renderFilters(); renderReport();
    } catch (e) {
      toast(t("history.report_failed", { err: e.message }));
    }
  }));
}

async function renderRecent() {
  const box = $("#dashRecent");
  if (!box) return;
  try {
    const d = await api("/api/jobs");
    const jobs = (d.jobs || []).slice(0, 5);
    if (!jobs.length) {
      box.innerHTML = `<div class="empty">${esc(t("dash.recent_empty"))}</div>`;
      return;
    }
    box.innerHTML = jobs.map(j => {
      const st = j.stats || {};
      const written = st.written || 0;
      const failed = st.failed || 0;
      const bad = failed ? ` · <span style="color:var(--bad)">${esc(t("dash.recent_failed", { n: failed }))}</span>` : "";
      return `<div class="recentRow">
          <span class="grow"><span class="path" title="${esc(j.root)}">${esc(j.root)}</span></span>
          <span class="small">${esc(fmtTime(j.started_at))}</span>
          <span class="small">${esc(t("dash.recent_written", { n: written }))}${bad}</span>
          <button class="btn btn-outline" data-job="${esc(j.job_id)}" type="button">${esc(t("history.view"))}</button>
        </div>`;
    }).join("");

    $$("#dashRecent [data-job]").forEach(b => b.addEventListener("click", async () => {
      try {
        const rep = await api("/api/report/" + b.dataset.job);
        const { showResults } = await import("./screens.js");
        openFlow(MODE_MANUAL);
        showResults(rep.results, rep.stats, b.dataset.job);
      } catch (e) {
        toast(t("history.report_failed", { err: e.message }));
      }
    }));
  } catch (e) {
    box.innerHTML = `<div class="empty">${esc(t("history.failed", { err: e.message }))}</div>`;
  }
}

/* ─────────────────────────────────────────── 装配 */

export function initDash(onAuto, onManual, onToggleWatch) {
  $("#btnAuto")?.addEventListener("click", onAuto);
  $("#btnManual")?.addEventListener("click", onManual);
  $("#dashToggleWatch")?.addEventListener("click", onToggleWatch);
  $("#dashRefresh")?.addEventListener("click", () => refreshDash());
  $("#backDash")?.addEventListener("click", backToDash);

  // 「全部历史」复用原来的历史弹层
  $("#dashAllHistory")?.addEventListener("click", () => {
    document.getElementById("openHistory")?.click();
  });

  refreshDash();
  // 每 30 秒刷一次状态条：无人值守时页可能开着不动
  setInterval(() => {
    if ($("#app")?.dataset.view === "dash") refreshStatusOnly();
  }, 30000);
}

async function refreshStatusOnly() {
  await renderStatus();
}
