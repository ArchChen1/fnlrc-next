/**
 * 五个 Tab 面板：选库 → 扫描 → 预览 → 执行 → 报告。
 * M5 起从「5 屏整屏吸附」改成「单页 + Tab」，业务渲染逻辑保持不变，
 * 只换了外壳（容器与跳转方式）。
 */
import { S, ACTION_LABEL, STATUS_LABEL, backends, filters, GROUP, tone } from "./state.js";
import { $, $$, esc, api, toast, fmtTime } from "./core.js";
import { t } from "./i18n/index.js";
import { openFsBrowser } from "./fsbrowser.js";
import { openManual, canEdit } from "./manual.js";

const TAB_KEY = "fnlrc_tab";
const TAB_COUNT = 5;

/* ─────────────────────────────────────────── Tab 切换 */

/**
 * Tab 何时可用（见 M5 设计 §3.4）。返回锁定原因文案键，空串表示可用。
 * 刻意**不禁用**按钮：`data-locked` + CSS 置灰，点了给一句提示，
 * 让用户能先看到后面长什么样，而不是面对一排点不动的灰块。
 */
function lockReason(i) {
  if (i >= 1 && i <= 2 && !S.summary) return "tab.locked_scan";
  if (i === 3 && !S.jobId && !S.results.length) return "tab.locked_run";
  if (i === 4 && !S.results.length && !S.jobId) return "tab.locked_report";
  return "";
}

/** 重新计算并刷新所有 Tab 的锁定态。扫描完 / 跑完任务后各调一次。 */
export function syncTabLocks() {
  $$(".tab").forEach(el => {
    const i = +el.dataset.tab;
    const locked = !!lockReason(i);
    if (locked) el.dataset.locked = "true";
    else delete el.dataset.locked;
  });
}

export function initTabs() {
  const tabs = $$(".tab");

  tabs.forEach(el => el.addEventListener("click", () => {
    const i = +el.dataset.tab;
    const why = lockReason(i);
    if (why) { toast(t(why)); return; }
    go(i);
  }));

  // 键盘：左右箭头切 Tab；面板/弹层打开时不抢键
  addEventListener("keydown", e => {
    if ($(".overlay.open")) return;
    if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (e.key === "ArrowRight") {
      e.preventDefault(); go(Math.min(currentTab() + 1, TAB_COUNT - 1));
    } else if (e.key === "ArrowLeft") {
      e.preventDefault(); go(Math.max(currentTab() - 1, 0));
    }
  });

  // 刷新后回到上次所在 Tab（锁定态则退回第一个并告知原因）
  let saved = 0;
  try { saved = parseInt(localStorage.getItem(TAB_KEY), 10); } catch (e) { /* 隐私模式 */ }
  const lost = Number.isInteger(saved) && saved > 0 && saved < TAB_COUNT && lockReason(saved);
  if (!Number.isInteger(saved) || saved < 0 || saved >= TAB_COUNT || lost) saved = 0;

  syncTabLocks();
  go(saved);
  // 扫描 / 任务结果不落浏览器，刷新后这类 Tab 会变空 —— 直说，别让用户疑惑
  if (lost) toast(t(lost));
}

export function currentTab() {
  return S.tab || 0;
}

/**
 * 用户主动要某个 Tab（顶栏按钮、其他模块调用）：锁定就提示原因，不硬跳。
 * 与 `go()` 的区别：`go()` 是程序内部跳转（扫描完成后自动进概览等），不走锁。
 */
export function requestTab(i) {
  const why = lockReason(i);
  if (why) { toast(t(why)); return; }
  go(i);
}

/**
 * 切到第 i 个面板。隐藏其余、同步 aria-selected、记住选择。
 * 与旧版 `go(i)` 的区别：不再滚动整屏，只是显隐面板 —— 页面滚动位置回到顶部
 * （否则从一个长面板切到短面板会停在半空）。
 */
export function go(i) {
  if (i < 0 || i >= TAB_COUNT) return;
  S.tab = i;

  $$(".panel").forEach((p, j) => { p.hidden = j !== i; });
  $$(".tab").forEach(tab => {
    tab.setAttribute("aria-selected", String(+tab.dataset.tab === i));
    tab.tabIndex = +tab.dataset.tab === i ? 0 : -1;
  });

  const panel = $("#panel" + i);
  if (panel) panel.scrollIntoView({ block: "start" });
  try { localStorage.setItem(TAB_KEY, String(i)); } catch (e) { /* 忽略 */ }
}

/* ─────────────────────────────────────────── ① 选库 */

export function initPick() {
  const saved = localStorage.getItem("fnlrc_root");
  if (saved) $("#root").value = saved;
  $("#root").addEventListener("change", e =>
    localStorage.setItem("fnlrc_root", e.target.value.trim()));
  $("#browse").addEventListener("click", browse);
  $("#doScan").addEventListener("click", startScan);
}

function browse() {
  const native = window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder;
  if (native) {
    window.pywebview.api.pick_folder().then(p => { if (p) $("#root").value = p; });
    return;
  }
  openFsBrowser($("#root").value.trim(), p => { $("#root").value = p; });
}

async function startScan() {
  const root = $("#root").value.trim();
  if (!root) { toast(t("pick.need_root")); return; }

  const btn = $("#doScan");
  btn.disabled = true; btn.textContent = t("pick.scanning");
  try {
    const d = await api("/api/scan", { root });
    adoptScanned(root, d.summary);
    go(1);
  } catch (e) {
    toast(t("pick.scan_failed", { err: e.message }));
  } finally {
    btn.disabled = false; btn.textContent = t("pick.scan");
  }
}

/**
 * 接管一次成功的扫描：写回主界面、记住状态并渲染概览。
 * 首次使用引导（wizard.js）复用它 —— 引导里扫到的库直接进主界面，用户不用再扫一遍。
 */
export function adoptScanned(root, summary) {
  S.root = root;
  S.summary = summary;
  localStorage.setItem("fnlrc_root", root);
  const input = $("#root");
  if (input) input.value = root;
  renderSummary(summary, root);
  syncTabLocks();          // 扫描完成后「概览 / 补完」解锁
}

export function renderSummary(s, root) {
  const need = s.mono_or_single + s.missing + s.broken;
  const tiles = [[t("summary.stat_total"), s.total], [t("summary.stat_bilingual"), s.bilingual],
                 [t("summary.stat_need"), need], [t("summary.stat_instrumental"), s.instrumental]];
  $("#stats").innerHTML = tiles.map(([k, n], i) =>
    `<div class="stat rv d${i * 75}"><div class="n">${n}</div><div class="k">${k}</div></div>`
  ).join("");

  const tops = Object.keys(s.by_top || {}).sort();
  $("#bytop").innerHTML = tops.map(dir => {
    const d = s.by_top[dir];
    return `<tr><td>${esc(dir)}</td><td>${d.audio}</td><td>${d.bilingual}</td>
            <td>${d.mono}</td><td>${d.none}</td><td>${d.broken}</td></tr>`;
  }).join("") || `<tr><td colspan="6"><div class="empty">
      ${esc(t("summary.no_audio"))}</div></td></tr>`;

  $("#scanNote").textContent = t("summary.dirs", { n: tops.length, root: root || S.root });
}

/* ─────────────────────────────────────────── ③ 预览 */

export function initPreview() {
  renderBackends();
  $$('input[name=backend]').forEach(r => r.addEventListener("change", showBackendHint));
  showBackendHint();

  $("#doPreview").addEventListener("click", doPreview);
  $("#doRun").addEventListener("click", doRun);
  $("#doStop").addEventListener("click", doStop);
}

/** 切语言后要重画后端选择（名字和说明都是翻译过的） */
export function renderBackends() {
  const cur = currentBackend();
  // 两列排布：五个后端竖着排会把这屏撑出视口（skill 禁止内容超屏）
  $("#backendPick").className = "grid g2";
  $("#backendPick").innerHTML = backends().map(([v, label]) =>
    `<label class="row checkline" style="margin-bottom:.35rem">
       <input type="radio" name="backend" value="${v}" ${v === cur ? "checked" : ""}>
       <span>${esc(label)}</span>
     </label>`).join("");
  $$('input[name=backend]').forEach(r => r.addEventListener("change", showBackendHint));
  showBackendHint();
}

export function currentBackend() {
  const picked = $$('input[name=backend]').find(r => r.checked);
  return picked ? picked.value : "source";
}

function showBackendHint() {
  const row = backends().find(b => b[0] === currentBackend());
  $("#backendHint").textContent = row ? row[2] : "";
}

async function doPreview() {
  if (!S.root) { toast(t("preview.need_scan")); go(0); return; }
  const btn = $("#doPreview");
  btn.disabled = true; btn.textContent = t("preview.calculating");
  try {
    const d = await api("/api/preview", { root: S.root });
    S.preview = d;
    renderPreview(d);
    go(2);
  } catch (e) {
    toast(t("preview.failed", { err: e.message }));
  } finally {
    btn.disabled = false; btn.textContent = t("summary.preview");
  }
}

function renderPreview(d) {
  const dist = d.distribution || {};
  const empty = `<div class="empty">${esc(t("preview.nothing"))}</div>`;
  $("#dist").innerHTML = Object.keys(dist).sort().map(k =>
    `<div class="row" style="justify-content:space-between">
       <span>${esc(ACTION_LABEL[k] || k)}</span><strong>${dist[k]}</strong></div>`
  ).join("") || empty;

  $("#previewRows").innerHTML = (d.rows || []).slice(0, 400).map(r =>
    `<tr><td>${esc(ACTION_LABEL[r.action] || r.action)}</td>
         <td class="path" title="${esc(r.file)}">${esc(r.file)}</td>
         <td style="color:rgba(255,255,255,.6)">${esc(r.reason)}${
           r.embedded_lyrics ? `<br><span class="pill warn" style="margin-top:.4rem">${esc(t("preview.embedded"))}</span>` : ""}</td></tr>`
  ).join("") || `<tr><td colspan="3">${empty}</td></tr>`;

  $("#runNote").textContent = t("preview.total", { n: d.total });
}

/* ─────────────────────────────────────────── ④ 执行 */

async function doRun() {
  if (!S.root) { toast(t("preview.need_scan")); go(0); return; }
  const body = {
    root: S.root,
    write: !$("#previewMode").checked,
    force: $("#forceMode").checked,
    // 外文单语歌词：保留本地时间轴只补译文（默认开）。复选框在设置面板里。
    keep_local: !$("#cfgKeepLocal") || $("#cfgKeepLocal").checked,
    translate: currentBackend()
  };

  $("#doRun").disabled = true;
  $("#log").innerHTML = "";
  $("#liveStats").innerHTML = "";

  try {
    const d = await api("/api/run", body);
    S.jobId = d.job_id;
    S.results = [];
    setProgress(0, d.total, "run.started");
    $("#doStop").disabled = false;
    syncTabLocks();        // 任务开跑后「进度」解锁
    go(3);
    streamJob(d.job_id, d.total);
  } catch (e) {
    toast(t("run.start_failed", { err: e.message }));
    $("#doRun").disabled = false;
  }
}

function setProgress(done, total, labelKey) {
  const pct = total ? Math.round(done / total * 100) : 0;
  $("#progBar").style.width = pct + "%";
  $("#progPct").textContent = pct + "%";
  $("#progText").textContent = `${t(labelKey)}　${done} / ${total}`;
  // 记住状态：切语言时能按新语言重画，而不是停在最初那句
  S.progress = { done, total, labelKey };
}

/** 切语言后重画进度那行（其它部分不依赖语言不了） */
export function renderProgress() {
  if (!S.progress) return;
  const { done, total, labelKey } = S.progress;
  $("#progText").textContent = `${t(labelKey)}　${done} / ${total}`;
}

export function bumpStats(results) {
  const c = { written: 0, mono: 0, skipped: 0, no_lyrics: 0, timeout: 0, manual: 0, failed: 0 };
  results.forEach(r => {
    const s = r.status;
    if (GROUP.ok.includes(s)) c.written++;
    else if (s === "written_mono") c.mono++;
    else if (GROUP.skip.includes(s)) c.skipped++;
    else if (s === "no_lyrics") c.no_lyrics++;
    else if (s === "timeout") c.timeout++;
    else if (s === "lang_mismatch") c.manual++;
    else c.failed++;
  });
  const tiles = [[t("run.ok"), c.written], [t("run.mono"), c.mono], [t("run.skipped"), c.skipped],
                 [t("run.no_lyrics"), c.no_lyrics], [t("run.timeout"), c.timeout],
                 [t("run.manual"), c.manual], [t("run.failed"), c.failed]];
  $("#liveStats").innerHTML = tiles.map(([k, n]) =>
    `<div class="stat"><div class="n" style="font-size:1.4rem">${n}</div><div class="k">${k}</div></div>`
  ).join("");
}
/** 日志整块重画。只在切语言时用 —— 平时是逐行 append（不重排、不闪）。 */
export function renderLog() {
  const box = $("#log");
  if (!box) return;
  box.innerHTML = "";
  S.results.forEach(logLine);
  box.scrollTop = box.scrollHeight;
}

/** 报告头那句汇总。单独拎出来是为了切语言时能重画。 */
export function renderReportSummary() {
  const st = S.stats || {};
  $("#reportSummary").textContent = [
    t("report.summary_written", { n: st.written || 0 }),
    t("report.summary_mono", { n: st.written_mono || 0 }),
    t("report.summary_skipped", { n: st.skipped || 0 }),
    t("report.summary_no_lyrics", { n: st.no_lyrics || 0 }),
    t("report.summary_timeout", { n: st.timeout || 0 }),
    t("report.summary_manual", { n: st.lang_mismatch || 0 }),
    t("report.summary_failed", { n: st.failed || 0 })
  ].join(" · ") + (st.elapsed_s ? `　${t("report.elapsed", { n: Math.round(st.elapsed_s) })}` : "");
  const title = $("#reportTitle");
  if (title) title.textContent = t("report.finished");
}
function logLine(r) {
  const val = r.validation
    ? ` ${t("run.log_pairs")}=${r.pairs} ${t("run.log_ratio")}=${r.validation.pair_ratio}`
    : "";
  const matched = r.matched ? ` ← ${r.matched}${r.score != null ? ` (${r.score})` : ""}` : "";
  const note = r.note ? `  ${r.note}` : "";
  const el = document.createElement("div");
  el.className = tone(r.status);
  el.textContent = `${(STATUS_LABEL[r.status] || r.status).padEnd(10)} ${r.file}${val}${matched}${note}  ${r.elapsed_s}s`;
  const box = $("#log");
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}

function streamJob(jobId, total) {
  const es = new EventSource(`/api/jobs/${jobId}/events?token=${encodeURIComponent(S.token)}`);
  es.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if (d.type === "song") {
      S.results.push(d.result);
      logLine(d.result);
      bumpStats(S.results);
      setProgress(d.index, d.total, "run.progress");
    } else if (d.type === "done") {
      S.stats = d.stats || {};
      es.close();
      finishReport();
    } else if (d.type === "stopped") {
      es.close();
      $("#doStop").disabled = true;
      $("#doRun").disabled = false;
      setProgress(d.index, total, "run.stopped");
      toast(t("run.stopped"));
    } else if (d.type === "error") {
      es.close();
      $("#doRun").disabled = false;
      toast(t("run.error", { err: d.message }));
    }
  };
  // 断线：拉一次快照补上，别让界面停在半路
  es.onerror = () => {
    es.close();
    api(`/api/jobs/${jobId}`).then(snap => {
      S.results = snap.results || S.results;
      bumpStats(S.results);
      if (snap.done) finishReport();
    }).catch(() => {});
  };
}

async function doStop() {
  if (!S.jobId) return;
  $("#doStop").disabled = true;
  try {
    await api(`/api/jobs/${S.jobId}/stop`, {});
    toast(t("run.stopping"));
  } catch (e) {
    toast(t("run.stop_failed", { err: e.message }));
  }
}

/* ─────────────────────────────────────────── ⑤ 报告 */

export function initReport() {
  $("#exportCsv").addEventListener("click", () => {
    if (!S.jobId) { toast(t("report.no_job")); return; }
    const q = S.filter === "all" ? "" : "status=" + encodeURIComponent(S.filter) + "&";
    location.href = `/api/report/${S.jobId}?${q}format=csv`;
  });
  $("#restart").addEventListener("click", () => go(0));
}

export function finishReport() {
  $("#doStop").disabled = true;
  $("#doRun").disabled = false;

  renderReportSummary();
  renderFilters();
  renderReport();
  syncTabLocks();          // 有报告了，「报告」解锁
  setProgress(S.results.length, S.results.length, "common.done");
  go(4);
}

/** 从历史任务打开报告时复用 */
export function showResults(results, stats, jobId) {
  S.results = results || [];
  S.stats = stats || {};
  S.jobId = jobId || "";
  S.filter = "all";
  finishReport();
}

export function renderFilters() {
  $("#filterTabs").innerHTML = filters().map(([k, label]) =>
    `<button class="btn ${S.filter === k ? "btn-primary" : "btn-outline"}" data-f="${k}" type="button">${esc(label)}</button>`
  ).join("");
  $$("#filterTabs [data-f]").forEach(b => b.addEventListener("click", () => {
    S.filter = b.dataset.f;
    renderFilters();
    renderReport();
  }));
}

function matchFilter(r) {
  const f = S.filter;
  if (f === "all") return true;
  if (f === "written") return GROUP.ok.includes(r.status);
  if (f === "failed") return GROUP.bad.includes(r.status) && r.status !== "lang_mismatch";
  return r.status === f;
}

export function renderReport() {
  let rows = S.results.filter(matchFilter);
  // 「待人工确认」永远置顶 —— 这是唯一需要用户真正决策的一类
  rows = rows.slice().sort((a, b) =>
    (b.status === "lang_mismatch") - (a.status === "lang_mismatch"));

  $("#reportRows").innerHTML = rows.map(r => {
    const v = r.validation || {};
    const title = r.note ? ` title="${esc(r.note)}"` : "";
    const edit = canEdit(r.status)
      ? `<button class="btn-ghost mopen" data-file="${esc(r.file)}" type="button">${esc(t("report.edit"))}</button>`
      : "—";
    return `<tr${title}>
      <td><span class="pill ${tone(r.status)}">${esc(STATUS_LABEL[r.status] || r.status)}</span></td>
      <td class="path" title="${esc(r.file)}">${esc(r.file)}</td>
      <td style="color:rgba(255,255,255,.7)">${esc(r.matched || "—")}</td>
      <td>${esc(r.source || "—")}</td>
      <td>${r.pairs || "—"}</td>
      <td>${v.pair_ratio != null ? v.pair_ratio : "—"}</td>
      <td>${edit}</td></tr>`;
  }).join("") || `<tr><td colspan="7"><div class="empty">${esc(t("report.empty_filter"))}</div></td></tr>`;

  // 逐行「补译文」按钮：打开人工翻译编辑器
  $$("#reportRows .mopen").forEach(b =>
    b.addEventListener("click", () => openManual(b.dataset.file)));
}

export { fmtTime };
