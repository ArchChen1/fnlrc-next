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
import { persistEnabled } from "./dashboard.js";

const TAB_KEY = "fnlrc_tab";
const TAB_COUNT = 5;

/** 自动监测模式只复用前 3 步（选库 / 概览 / 补完），后两步是手动流程专有 */
const AUTO_TABS = 3;

function tabCount() {
  return S.mode === "auto" ? AUTO_TABS : TAB_COUNT;
}

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
    if ($("#app")?.dataset.view === "dash") return;
    if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (e.key === "ArrowRight") {
      e.preventDefault(); go(Math.min(currentTab() + 1, tabCount() - 1));
    } else if (e.key === "ArrowLeft") {
      e.preventDefault(); go(Math.max(currentTab() - 1, 0));
    }
  });

  // 刷新后回到上次所在 Tab（锁定态则退回第一个并告知原因）
  let saved = 0;
  try { saved = parseInt(localStorage.getItem(TAB_KEY), 10); } catch (e) { /* 隐私模式 */ }
  const lost = Number.isInteger(saved) && saved > 0 && saved < TAB_COUNT && lockReason(saved);
  if (!Number.isInteger(saved) || saved < 0 || saved >= TAB_COUNT || lost) saved = 0;
  // 自动监测只到第 3 步，别恢复到一个根本不存在的 Tab
  if (saved >= tabCount()) saved = tabCount() - 1;

  syncTabLocks();
  go(saved);
  // 扫描 / 任务结果不落浏览器，刷新后这类 Tab 会变空 —— 直说，别让用户疑惑
  if (lost) toast(t(lost));
}

export function currentTab() {
  return S.tab || 0;
}

/**
 * 按当前模式刷新界面外壳：
 *   auto   —— 只露出前 3 个 Tab，主按钮变成「保存并开始监测」
 *   manual —— 完整 5 个 Tab，主按钮仍是「开始补完」
 * 进入流程时调一次；切语言时也靠 rerender 再调一次。
 */
export function applyMode() {
  const auto = S.mode === "auto";

  // 自动监测会把配置落盘，所以链要与服务端已存的保持一致；
  // 立即执行则每次从已存配置起步，用户改了只影响这一次。
  if (S.config && Array.isArray(S.config.translate_order) && S.config.translate_order.length) {
    S.translateOrder = S.config.translate_order.slice();
  } else if (!S.translateOrder) {
    S.translateOrder = ["source", "remote"];
  }

  $$(".tab").forEach(el => {
    const i = +el.dataset.tab;
    el.hidden = auto && i >= AUTO_TABS;
  });

  const run = $("#doRun");
  if (run) run.textContent = auto ? t("auto.save_start") : t("preview.run");

  const note = $("#preview.not_written");
  const modeHint = $("#modeHint");
  if (modeHint) {
    modeHint.textContent = auto ? t("mode.auto_hint") : t("mode.manual_hint");
    modeHint.hidden = !S.mode;
  }
  if (note) note.hidden = auto;   // 自动模式不写库，这句没意义

  // 监听目录只对自动监测有意义 —— 立即执行本来就不监听
  const autoBox = $("#autoWatch");
  if (autoBox) autoBox.hidden = !auto;
  if (auto) fillAutoWatch();

  // 自动模式没有「进度 / 报告」Tab，别停在那两个上面
  if (auto && S.tab >= AUTO_TABS) go(0);
  syncTabLocks();
}

/**
 * 把服务端已存的监听配置回填到自动监测那一段（进入流程时读一次）。
 *
 * 监听目录刻意**留空显示**：留空 = 跟着选库走，这是默认行为。
 * 只有当它确实和音乐库不同（用户特意指定了别的目录）时才回显。
 */
function fillAutoWatch() {
  const w = (S.config && S.config.watch) || {};
  const same = (a, b) => !!a && !!b && a.trim().toLowerCase() === b.trim().toLowerCase();
  const root = w.watch_root || "";
  const rootInput = $("#autoWatchRoot");
  if (rootInput) rootInput.value = same(root, S.root) ? "" : root;

  const set = (id, v) => { const el = $(id); if (el) el.value = v == null ? "" : v; };
  set("#autoWatchMoveTo", w.watch_move_to || "");
  const after = $("#autoWatchAfter");
  if (after) after.value = w.watch_after || "keep";
  const wr = $("#autoWatchWrite");
  if (wr) wr.checked = w.watch_write !== false;
  const quiet = $("#autoWatchQuiet");
  if (quiet) quiet.value = w.watch_quiet_s != null ? w.watch_quiet_s : 8;
  const poll = $("#autoWatchPoll");
  if (poll) poll.value = w.watch_poll_s != null ? w.watch_poll_s : 5;
  const auto = $("#autoWatchAutostart");
  if (auto) auto.checked = !!w.watch_autostart;

  syncAutoWatchUI();
}

/** 目的地只在复制/移动时才有意义；监听目录留空时给一句说明。 */
export function syncAutoWatchUI() {
  const after = $("#autoWatchAfter");
  const field = $("#autoMoveToField");
  // 用内联样式而不是 hidden 属性：label.field{display:block} 会压过 [hidden]
  if (field) {
    const show = !!(after && (after.value === "copy" || after.value === "move"));
    field.hidden = !show;
    field.style.display = show ? "" : "none";
  }

  const hint = $("#autoWatchHint");
  if (hint) {
    const typed = ($("#autoWatchRoot")?.value || "").trim();
    hint.textContent = typed
      ? t("auto.root_custom")
      : (S.root ? t("auto.root_default", { root: S.root }) : t("auto.root_no_lib"));
  }
}

/** 读回自动监测那一段的表单值。监听目录留空 → 用选库选的音乐库。 */
export function autoWatchPatch() {
  const typed = ($("#autoWatchRoot")?.value || "").trim();
  return {
    watch_root: typed || S.root || "",
    watch_after: $("#autoWatchAfter")?.value || "keep",
    watch_move_to: ($("#autoWatchMoveTo")?.value || "").trim(),
    watch_write: !!$("#autoWatchWrite")?.checked,
    watch_quiet_s: parseInt($("#autoWatchQuiet")?.value, 10) || 8,
    watch_poll_s: parseInt($("#autoWatchPoll")?.value, 10) || 5,
    watch_autostart: !!$("#autoWatchAutostart")?.checked
  };
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
  if (i < 0 || i >= tabCount()) return;
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
    // 立即执行：不把这次选的库写成「上次的库」—— 改了只对这一次生效
    const d = await api("/api/scan", { root, persist: persistEnabled() });
    adoptScanned(root, d.summary, { remember: persistEnabled() });
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
export function adoptScanned(root, summary, opts) {
  S.root = root;
  S.summary = summary;
  // 只有持久模式（自动监测 / 引导）才记住路径，手动执行不留痕
  if (!opts || opts.remember !== false) localStorage.setItem("fnlrc_root", root);
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
  $("#doPreview").addEventListener("click", doPreview);
  $("#doRun").addEventListener("click", doRun);
  $("#doStop").addEventListener("click", doStop);

  // 自动监测那段：目的地字段随「补完后」显隐；监听目录留空时提示会用它监听哪个库
  $("#autoWatchAfter")?.addEventListener("change", syncAutoWatchUI);
  $("#autoWatchRoot")?.addEventListener("input", syncAutoWatchUI);
}

/** 当前降级链（有序）。空 = 还没配过，用默认值 */
export function currentOrder() {
  return S.translateOrder && S.translateOrder.length
    ? S.translateOrder.slice()
    : ["source", "remote"];
}

/** 兼容旧接口：链里的第一项当作「主后端」 */
export function currentBackend() {
  return currentOrder()[0] || "source";
}

/**
 * 渲染翻译后端 = **可拖动排序的降级链**（M6）。
 *
 * 为什么改成排序而不是单选：以后会有更多 provider，用户只要调整顺序就能表达
 * 「先问源站、不够再问 AI」或反过来。多选勾决定这一环**参不参与**。
 *
 * 交互刻意用原生 HTML5 drag & drop + 上下按钮双通道：
 * 手机上拖拽不稳，按钮是保底手段（触控目标 ≥44px）。
 */
export function renderBackends() {
  const box = $("#backendPick");
  if (!box) return;
  const order = currentOrder();
  const all = backends();          // [[v, label, hint], ...]
  const labelOf = Object.fromEntries(all.map(([v, l]) => [v, l]));
  const hintOf = Object.fromEntries(all.map(([v, , h]) => [v, h]));

  box.className = "chain";
  box.innerHTML = `
    <p class="small chainIntro" data-i18n="backend.chain_intro">
      从上往下依次尝试。排在前面的先用，补不上的行再交给下一个。
    </p>
    <ol class="chainList" id="chainList">
      ${order.map((v, i) => `
        <li class="chainRow" draggable="true" data-backend="${esc(v)}" data-idx="${i}">
          <span class="chainGrip" aria-hidden="true">⋮⋮</span>
          <label class="chainCheck">
            <input type="checkbox" class="chainEnable" checked>
            <span class="chainLabel">${esc(labelOf[v] || v)}</span>
          </label>
          <span class="chainHint">${esc(hintOf[v] || "")}</span>
          <span class="chainMove">
            <button class="btn-ghost chainUp" type="button" title="上移" ${i === 0 ? "disabled" : ""}>↑</button>
            <button class="btn-ghost chainDown" type="button" title="下移" ${i === order.length - 1 ? "disabled" : ""}>↓</button>
          </span>
        </li>`).join("")}
    </ol>
    <p class="note" style="margin-top:.7rem" id="backendHint"></p>`;

  // 勾选：只有勾上的项参与降级（至少留一个）
  $$(".chainEnable", box).forEach(cb => cb.addEventListener("change", () => {
    const enabled = enabledOrder();
    if (!enabled.length) { cb.checked = true; toast(t("backend.need_one")); return; }
    S.translateOrder = enabled;
    updateChainHint();
  }));

  // 上下按钮：手机/键盘的保底排序手段
  $$(".chainUp", box).forEach(b => b.addEventListener("click", () => moveBy(b, -1)));
  $$(".chainDown", box).forEach(b => b.addEventListener("click", () => moveBy(b, 1)));

  // 原生拖拽
  bindDrag(box);
  updateChainHint();
}

/** 当前链里被勾选（启用）的部分 */
function enabledOrder() {
  return $$("#chainList .chainRow")
    .filter(li => li.querySelector(".chainEnable").checked)
    .map(li => li.dataset.backend);
}

function moveBy(btn, delta) {
  const order = enabledOrder();
  const li = btn.closest(".chainRow");
  const v = li.dataset.backend;
  const from = order.indexOf(v);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= order.length) return;
  order.splice(to, 0, order.splice(from, 1)[0]);
  S.translateOrder = order;
  renderBackends();
}
/** dragstart 记下来源，dragover 时实时挪位（比只换 class 更直观） */
function bindDrag(box) {
  let dragged = null;
  $$(".chainRow", box).forEach(li => {
    li.addEventListener("dragstart", e => {
      dragged = li;
      li.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", li.dataset.backend); } catch (err) { /* 忽略 */ }
    });
    li.addEventListener("dragend", () => {
      li.classList.remove("dragging");
      $$(".chainRow", box).forEach(x => x.classList.remove("over"));
      dragged = null;
      S.translateOrder = enabledOrder();
      updateChainHint();
    });
    li.addEventListener("dragover", e => {
      e.preventDefault();
      if (!dragged || dragged === li) return;
      li.classList.add("over");
    });
    li.addEventListener("dragleave", () => li.classList.remove("over"));
    li.addEventListener("drop", e => {
      e.preventDefault();
      li.classList.remove("over");
      if (!dragged || dragged === li) return;
      const rows = $$(".chainRow", box);
      const from = rows.indexOf(dragged), to = rows.indexOf(li);
      moveRow(from, to);
      dragged = null;
    });
  });
  bindTouchDrag(box);
}

/** 把第 from 行挪到第 to 行，再重画。桌面与触屏共用这一条路径。 */
function moveRow(from, to) {
  const rows = $$("#chainList .chainRow");
  if (from < 0 || to < 0 || from >= rows.length || to >= rows.length || from === to) return;
  const nodes = rows.slice();
  nodes.splice(to, 0, nodes.splice(from, 1)[0]);
  const list = $("#chainList");
  if (list) nodes.forEach(n => list.appendChild(n));
  S.translateOrder = enabledOrder();
  renderBackends();
}

/**
 * 触屏拖拽（M6.1）。
 *
 * 手机浏览器基本不支持 HTML5 drag & drop —— 手指按住不会触发 dragstart。
 * 所以这里用 Pointer Events 自己实现：按住握把移动，经过哪一行就把它挪到那儿。
 * 只绑在握把上，这样点复选框、点上下按钮都不会误触发排序。
 */
function bindTouchDrag(box) {
  $$(".chainGrip", box).forEach(grip => {
    grip.style.touchAction = "none";     // 阻止浏览器把滑动当成滚页面

    grip.addEventListener("pointerdown", e => {
      if (e.pointerType === "mouse") return;   // 桌面走原生拖拽，别双份
      const row = grip.closest(".chainRow");
      if (!row) return;
      e.preventDefault();
      grip.setPointerCapture?.(e.pointerId);

      row.classList.add("dragging");
      let lastTo = $$("#chainList .chainRow").indexOf(row);

      const cleanup = () => {
        row.classList.remove("dragging");
        $$("#chainList .chainRow").forEach(x => x.classList.remove("over"));
        S.translateOrder = enabledOrder();
        updateChainHint();
        grip.removeEventListener("pointermove", onMove);
        grip.removeEventListener("pointerup", cleanup);
        grip.removeEventListener("pointercancel", cleanup);
      };

      const onMove = ev => {
        const rows = $$("#chainList .chainRow");
        // 找出手指当前落在哪一行
        let hit = -1;
        rows.forEach((r, i) => {
          const b = r.getBoundingClientRect();
          if (ev.clientY >= b.top && ev.clientY <= b.bottom) hit = i;
        });
        if (hit < 0 || hit === lastTo) return;
        const cur = rows.indexOf(row);
        if (cur < 0) return;
        moveRowLive(cur, hit);
        lastTo = hit;
      };

      grip.addEventListener("pointermove", onMove);
      grip.addEventListener("pointerup", cleanup);
      grip.addEventListener("pointercancel", cleanup);
    });
  });
}

/** 触屏过程中只挪 DOM（不整块重画），否则手指还按着就丢了捕获。 */
function moveRowLive(from, to) {
  const rows = $$("#chainList .chainRow");
  if (from < 0 || to < 0 || from >= rows.length || to >= rows.length || from === to) return;
  const node = rows[from];
  const target = rows[to];
  const list = $("#chainList");
  if (!node || !target || !list) return;
  list.removeChild(node);
  if (from < to) target.after(node); else target.before(node);
}

function updateChainHint() {
  const el = $("#backendHint");
  if (!el) return;
  const labels = enabledOrder().map(v => backends().find(b => b[0] === v)?.[1] || v);
  el.textContent = labels.length
    ? t("backend.chain_now", { chain: labels.join(" → ") })
    : t("backend.need_one");
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
  if (S.mode === "auto") { await runAuto(); return; }

  const body = {
    root: S.root,
    write: !$("#previewMode").checked,
    force: $("#forceMode").checked,
    // 外文单语歌词：保留本地时间轴只补译文（默认开）。复选框在设置面板里。
    keep_local: !$("#cfgKeepLocal") || $("#cfgKeepLocal").checked,
    translate: currentBackend(),
    translate_order: enabledOrder()
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

/**
 * 自动监测模式下的「保存并开始监测」。
 *
 * 自动监测复用前 3 步收集配置，然后：
 *   1. **持久化**（这是它与「立即执行」的关键区别）；
 *   2. 监听目录留空时，用选库选的音乐库 —— 少填一次、也少一个概念；
 *   3. 启动监听，然后回 dashboard 看状态条。
 *
 * 它不写任何音乐文件：写入由监听在真正发现新歌时决定。
 */
async function runAuto() {
  const btn = $("#doRun");
  btn.disabled = true;
  const old = btn.textContent;
  try {
    // 监听目录留空是允许的（= 监听选库选的音乐库），但两者都空就没法开始
    const watch = autoWatchPatch();
    if (!watch.watch_root) {
      toast(t("auto.need_watch_root"));
      go(0);
      $("#root")?.focus();
      return;
    }
    // 复制/移动却没填目的地 —— 服务端会拒，不如在这儿先说清楚
    if ((watch.watch_after === "copy" || watch.watch_after === "move")
        && !watch.watch_move_to) {
      toast(t("auto.need_move_to"));
      $("#autoWatchMoveTo")?.focus();
      return;
    }

    btn.textContent = t("auto.saving");
    // 只落盘「自动监测」关心的这几项；其余（API key 等）走设置面板
    await api("/api/config", {
      translate: currentBackend(),
      translate_order: enabledOrder(),
      keep_local: !$("#cfgKeepLocal") || $("#cfgKeepLocal").checked,
      backup: $("#cfgBackup") ? $("#cfgBackup").checked : true,
      ...watch
    });

    const d = await api("/api/watch/start", {});
    if (d.status && d.status.running) S.watchRunning = true;
    toast(d.already ? t("settings.watch_already") : t("auto.started"));

    const { backToDash } = await import("./dashboard.js");
    backToDash();
  } catch (e) {
    toast(t("auto.start_failed", { err: e.message }));
  } finally {
    btn.disabled = false;
    btn.textContent = old || t("preview.run");
  }
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
