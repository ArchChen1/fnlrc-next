/**
 * 覆盖式面板：设置 / 历史 / 令牌闸门。
 *
 * 三者各占一个 overlay，互不覆盖彼此的 DOM —— 早先把它们塞进同一个 sheet，
 * 结果打开「历史」会把设置表单整块替换掉，关掉之后就再也填不了 key。
 */
import { S, STATUS_LABEL } from "./state.js";
import { $, $$, esc, api, toast, openOverlay, closeOverlay, fmtTime } from "./core.js";
import { t, applyI18n, LANGS, getLang, setLang, detectLang } from "./i18n/index.js";
import { showResults } from "./screens.js";

/** 语言在**会话中**改了之后要重画的东西。由 main.js 注册（避免和 screens 循环引用）。 */
let onLangChange = null;
export function setLangChangeHook(fn) { onLangChange = fn; }

/** dashboard 上的「开始/停止监测」——走与设置面板完全同一条路径。
 *  设置面板是弹层，可能还没打开过，所以不能依赖它的表单值：
 *  停止直接调接口；开始前用服务端已存配置（dashboard 本身不提供表单）。 */
export async function toggleWatchFromDashboard() {
  try {
    if (S.watchRunning) {
      await api("/api/watch/stop", {});
      S.watchRunning = false;
      toast(t("settings.watch_stopped"));
    } else {
      const d = await api("/api/watch/start", {});
      S.watchRunning = !!(d.status && d.status.running);
      toast(d.already ? t("settings.watch_already") : t("settings.watch_started"));
      openWatchStream();
    }
  } catch (e) {
    toast(e.message);
  }
}

/** 从 dashboard 进入流程 */
export function initPanels() {  $("#openSettings").addEventListener("click", openSettings);
  $("#openHistory").addEventListener("click", openHistory);

  // 点遮罩关闭；面板内的 ✕ 关闭
  $$(".overlay").forEach(o => o.addEventListener("click", e => {
    if (e.target === o) o.classList.remove("open");
  }));
  $$("[data-close]").forEach(b =>
    b.addEventListener("click", () => closeOverlay(b.dataset.close)));

  $("#saveSettings").addEventListener("click", saveSettings);
  $("#testRemote").addEventListener("click", testRemote);
  $("#testWrite").addEventListener("click", testWrite);
  $("#rotateToken").addEventListener("click", rotateToken);
  $("#fnosProbe").addEventListener("click", () => fnosRun("probe"));
  $("#fnosPreview").addEventListener("click", () => fnosRun("plan"));
  $("#fnosApply").addEventListener("click", fnosApply);
  $("#watchSave").addEventListener("click", saveWatch);
  $("#watchToggle").addEventListener("click", toggleWatch);
  $("#watchLogClear").addEventListener("click", async () => {
    const log = $("#watchLog");
    if (log) log.textContent = "";
    syncWatchLogEmpty();
    try { await api("/api/watch/log/clear", {}); }
    catch (e) { toast(e.message); }
  });
  const hoursEl = $("#watchLogHours");
  if (hoursEl) {
    hoursEl.addEventListener("change", saveWatchLogHours);
  }

  buildLangPicker();
  initSettingsNav();
}

/* ─────────────────────────────────────────── 界面语言 */

/** 下拉框从 LANGS 生成 —— 新增语言只要在 i18n/index.js 登记一行。 */
function buildLangPicker() {
  const sel = $("#cfgLang");
  if (!sel) return;
  sel.innerHTML = LANGS.map(([code, name]) =>
    `<option value="${esc(code)}">${esc(name)}</option>`).join("");
  sel.value = getLang();
  sel.addEventListener("change", () => switchLang(sel.value));
}

/**
 * 切换语言：换语言 → 重刷静态 DOM → 重画动态部分。
 *
 * 当前跑着的任务不受影响（SSE 里的 status 是枚举值，只在渲染时翻译），
 * 所以这里不需要重启任何东西。
 */
export function switchLang(code) {
  setLang(code);
  applyI18n();
  if (onLangChange) onLangChange();
  // 这几处文字是 JS 写进去的，applyI18n 管不到 —— 切语言时按当前状态重写一次
  setWatchRunning(S.watchRunning);
  const sel = $("#cfgLang");
  if (sel) sel.value = getLang();
  toast(t("settings.saved"));
}

/** 启动时把语言定下来：localStorage > 浏览器 > zh。 */
export function initLang() {
  setLang(detectLang());
  applyI18n();
}

/* ─────────────────────────────────────────── 回填到飞牛 */

let fnosPlan = null;   // 记住上一次预览的结果，执行时要拿它对账

function fnosPaths() {
  return {
    app_data: $("#fnosAppData").value.trim(),
    appmeta: $("#fnosAppmeta").value.trim()
  };
}

/** 给「一行歌词都没有」的歌新建行。默认关 —— 它会往另一个应用的库里插记录。 */
function allowInsert() {
  const cb = $("#fnosAllowInsert");
  return !!(cb && cb.checked);
}

// 勾选框一变，之前那次预览就不作数了（差异数会变），要求重新预览。
// 不这么做的话，第③步会拿旧的 expect_changed 去对账，直接 409。
document.addEventListener("change", e => {
  if (e.target && e.target.id === "fnosAllowInsert") {
    fnosPlan = null;
    const btn = document.getElementById("fnosApply");
    if (btn) btn.disabled = true;
    const st = document.getElementById("fnosStatus");
    if (st) st.textContent = t("settings.fnos_toggle_changed");
  }
});

async function fnosRun(stage) {
  const out = $("#fnosOut");
  const status = $("#fnosStatus");
  const paths = fnosPaths();
  if (!paths.app_data && !paths.appmeta) {
    toast(t("settings.fnos_need_paths"));
    return;
  }
  const music = ($("#root").value || S.root || "").trim();
  if (stage === "plan" && !music) { toast(t("settings.fnos_need_music")); return; }

  $("#fnosApply").disabled = true;
  fnosPlan = null;
  status.textContent = stage === "probe" ? t("settings.fnos_probing") : t("settings.fnos_planning");
  out.textContent = "";
  try {
    // ① 检测只查结构，**不带音乐库路径** —— 带了就会走成"差异计划"分支
    const d = await api("/api/fnos/preview", {
      ...paths,
      music_root: stage === "probe" ? "" : music
    });
    out.textContent = d.text || d.error || "";
    if (!d.ok) {
      status.textContent = t("settings.fnos_bad", { msg: d.error || "structure mismatch, stop here" });
      return;
    }
    if (d.stage === "probe") {
      status.textContent = t("settings.fnos_probe_ok");
    } else {
      // 执行时按「原地更新 + 新建」的**总数**对账，所以这里也存总数
      const ins = allowInsert() ? (d.insertable || 0) : 0;
      fnosPlan = { ...paths, music, changed: d.changed + ins };
      const bits = [`update ${d.changed}`];
      if (d.insertable) bits.push(allowInsert() ? `insert ${d.insertable}` : `${d.insertable} insertable`);
      status.textContent = (d.changed || ins)
        ? bits.join(", ") + ` (skip ${d.same + d.no_lrc})`
          + (d.write_enabled ? "." : ". Write access is off on the server, step ③ will be rejected.")
        : t("settings.fnos_nothing");
      $("#fnosApply").disabled = !((d.changed || ins) && d.write_enabled);
    }
  } catch (e) {
    status.textContent = t("settings.fnos_bad", { msg: e.message });
  }
}

async function fnosApply() {
  if (!fnosPlan || !fnosPlan.changed) return;
  const ok = confirm(t("settings.fnos_confirm", {
    n: fnosPlan.changed,
    insert: allowInsert() ? t("settings.fnos_confirm_insert") : ""
  }));
  if (!ok) return;

  const status = $("#fnosStatus");
  $("#fnosApply").disabled = true;
  status.textContent = t("settings.fnos_writing");
  try {
    const d = await api("/api/fnos/apply", {
      app_data: fnosPlan.app_data, appmeta: fnosPlan.appmeta,
      music_root: fnosPlan.music, expect_changed: fnosPlan.changed,
      allow_insert: allowInsert()
    });
    status.textContent = (d.changed || d.inserted)
      ? `✅ updated ${d.changed}, inserted ${d.inserted || 0}. Backup: ${d.backup_dir}.`
        + (d.failed.length ? ` ${d.failed.length} failed.` : "")
        + " If Feiniu doesn't change right away, restart the Feiniu Music app."
      : (d.message || t("settings.fnos_nothing"));
    const bad = (d.insert_report || []).filter(x => x.why)
      .map(x => `${t("settings.fnos_insert_failed")} ${x.file}\n  ${x.why}`);
    if (d.failed.length || bad.length) {
      $("#fnosOut").textContent =
        bad.concat(d.failed.map(x => `${x.file}\n  ${x.why}`)).join("\n");
    }
    fnosPlan = null;
  } catch (e) {
    status.textContent = t("settings.fnos_bad", { msg: e.message });
    $("#fnosApply").disabled = false;
  }
}

/* ─────────────────────────────────────────── 设置 */

/**
 * 设置面板改成「左导航 + 右内容」之后，切换只改 aria-current 与 hidden。
 * 状态不落盘：每次打开都从左栏第一项开始，跟大多数软件一致。
 */
function initSettingsNav() {
  const nav = $("#setNav");
  const panes = $("#setPanes");
  if (!nav || !panes) return;

  const show = pane => {
    $$("#setNav .setNavItem").forEach(b =>
      b.setAttribute("aria-current", String(b.dataset.pane === pane)));
    $$("#setPanes .setPane").forEach(s => { s.hidden = s.dataset.pane !== pane; });
    panes.scrollTop = 0;
    if (pane === "watchlog") { syncWatchLogEmpty(); loadWatchLog(); }
  };

  nav.addEventListener("click", e => {
    const btn = e.target.closest("[data-pane]");
    if (btn) show(btn.dataset.pane);
  });
  // 键盘上下箭头在导航里走动，符合 aria 的 tablist 习惯
  nav.addEventListener("keydown", e => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const items = $$("#setNav .setNavItem");
    const i = items.findIndex(b => b.getAttribute("aria-current") === "true");
    const next = items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length];
    if (next) { e.preventDefault(); next.focus(); show(next.dataset.pane); }
  });

  show("lang");
}

async function openSettings() {
  openOverlay("settings");
  syncWatchLogEmpty();
  try {
    S.config = await api("/api/config");
    applyConfig(S.config);
    loadWatchLog();     // 顺便把落盘的历史日志拉回来
  } catch (e) {
    toast(t("settings.read_failed", { err: e.message }));
  }
}

function applyConfig(c) {
  // DeepSeek 官方文档的 base_url 是**不带 /v1** 的（带 /v1 也能用，但以文档为准）；
  // 模型名只有 deepseek-flash 与 deepseek-v4-pro 两个。
  $("#cfgBase").value = c.remote_base_url || "https://api.deepseek.com";
  $("#cfgModel").value = c.remote_model || "deepseek-flash";
  $("#cfgBackup").checked = !!c.backup;
  $("#cfgKeepLocal").checked = c.keep_local !== false;
  $("#cfgKey").value = "";
  $("#writeResult").textContent = "";
  $("#testResult").textContent = "";
  const langSel = $("#cfgLang");
  if (langSel) langSel.value = getLang();

  const keyInput = $("#cfgKey");
  if (c.remote_locked_by_env) {
    keyInput.disabled = true;
    keyInput.placeholder = t("settings.key_env_ph");
    $("#keyState").textContent = t("settings.key_env");
  } else {
    keyInput.disabled = false;
    keyInput.placeholder = "sk-…";
    $("#keyState").textContent = c.remote_configured
      ? t("settings.key_set", { masked: c.remote_key_masked })
      : t("settings.key_unset");
  }

  const picked = $$('input[name=backend]').find(r => r.value === (c.translate || "source"));
  if (picked) {
    picked.checked = true;
    picked.dispatchEvent(new Event("change"));
  }

  applyWatchConfig(c.watch || {}, !!c.watch_running);
}

/** 监听设置区：回填 autostart，再按运行状态刷按钮。 */
function applyWatchConfig(w, running) {
  const auto = $("#watchAutostart");
  if (auto) auto.checked = !!w.watch_autostart;
  setWatchRunning(running);
  if (running) openWatchStream();   // 刷新页面后，若服务端还在监听就重连事件流
}

function setWatchRunning(running) {
  S.watchRunning = running;
  const btn = $("#watchToggle");
  if (btn) btn.textContent = running ? t("settings.watch_stop") : t("settings.watch_start");
  const st = $("#watchState");
  if (st) st.textContent = running ? t("settings.watch_running") : t("settings.watch_idle");
  if (st) st.style.color = running ? "#5eead4" : "";
}

/**
 * 设置面板只保留「服务启动时自动开始监听」这一项。
 * 监听目录 / 补完后 / 静默秒数等全在首页的自动监测里填 —— 那里能顺便看到状态，
 * 两处都能改的话会出现「改了一个、另一个又发回去盖掉」的问题。
 */
function watchPatch() {
  return { watch_autostart: !!$("#watchAutostart")?.checked };
}

async function saveWatch() {
  try {
    S.config = await api("/api/config", watchPatch());
    toast(t("settings.watch_saved"));
  } catch (e) {
    toast(t("settings.save_failed", { err: e.message }));
  }
}

/** 开始/停止。开始前先把面板上的值存一次，省得用户忘了点保存。 */
async function toggleWatch() {
  const btn = $("#watchToggle");
  const st = $("#watchState");
  btn.disabled = true;
  try {
    if (S.watchRunning) {
      await api("/api/watch/stop", {});
      setWatchRunning(false);
      toast(t("settings.watch_stopped"));
    } else {
      await api("/api/config", watchPatch());     // 先落盘，start 读的是已存配置
      const d = await api("/api/watch/start", {});
      setWatchRunning(!!(d.status && d.status.running));
      toast(d.already ? t("settings.watch_already") : t("settings.watch_started"));
      openWatchStream();
    }
  } catch (e) {
    // 监听目录等设置已经挪到首页的「自动监测」里了。这里失败多半是那边还没填，
    // 所以直接把话说清楚、并把人送过去，而不是只丢一句服务端报错。
    if (st) { st.textContent = t("settings.watch_need_auto"); st.style.color = ""; }
    toast(t("settings.watch_need_auto"));
  } finally {
    btn.disabled = false;
  }
}

/* SSE：把监听事件追加到日志框。断线后浏览器会自动重连。
   日志同时由服务端落盘（默认保留 48 小时，见 watcher.WatchLog）—— 关掉面板、
   重启服务后再打开，会先拉回历史记录（loadWatchLog），所以这里不再"刷新就没了"。 */
let watchSrc = null;
function openWatchStream() {
  if (!S.token || watchSrc) return;
  const url = "/api/watch/events?token=" + encodeURIComponent(S.token);
  watchSrc = new EventSource(url);
  watchSrc.onmessage = ev => {
    let d;
    try { d = JSON.parse(ev.data); } catch { return; }
    appendWatchLine(sseToWatchRec(d));
  };
  watchSrc.onerror = () => { /* 静默重连；服务停了会一直重试 */ };
}

/** SSE 事件 → 与服务端落盘记录同形的对象（便于共用同一套渲染）。 */
function sseToWatchRec(ev) {
  const r = ev.result || {};
  const name = ev.rel || r.file || (ev.file || "").split(/[\\/]/).pop();
  return { ts: Date.now() / 1000, event: ev.event || "", status: r.status || "",
           name, note: r.note || "" };
}

/** 把一条记录渲染成日志文本行。 */
function watchLineText(rec) {
  const mark = rec.event === "processing" ? t("settings.watch_processing")
             : (rec.status ? (STATUS_LABEL[rec.status] || rec.status)
                           : rec.event || t("settings.watch_done"));
  const note = rec.note ? `  (${rec.note})` : "";
  return `[${fmtLogTime(rec.ts)}] ${rec.name || ""} → ${mark}${note}`;
}

function fmtLogTime(ts) {
  const d = new Date((ts || 0) * 1000);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? d.toLocaleTimeString() : d.toLocaleString();
}

function appendWatchLine(rec) {
  const log = $("#watchLog");
  if (!log) return;
  log.textContent = (log.textContent + "\n" + watchLineText(rec)).trim()
    .split("\n").slice(-500).join("\n");
  log.scrollTop = log.scrollHeight;
  syncWatchLogEmpty();
}

/** 拉回服务端落盘的历史日志（打开面板 / 切到「监听日志」时调用）。 */
async function loadWatchLog() {
  if (!S.token) return;
  try {
    const d = await api("/api/watch/log?limit=500");
    const log = $("#watchLog");
    if (!log) return;
    const lines = (d.events || []).map(watchLineText);
    if (lines.length) {
      log.textContent = lines.join("\n");
      log.scrollTop = log.scrollHeight;
    }
    syncWatchLogEmpty();
    // 回填保留时长设置（只在表单还空着时填，避免盖掉用户正在输入的值）
    const hours = $("#watchLogHours");
    if (hours && document.activeElement !== hours) hours.value = d.hours;
  } catch (e) { /* 读不到历史不影响实时流 */ }
}

/** 保留时长改动即保存（这一个字段独立于「监听」页的保存按钮）。 */
async function saveWatchLogHours() {
  const el = $("#watchLogHours");
  if (!el) return;
  const raw = el.value.trim();
  const hours = raw === "" ? 48 : Number(raw);
  if (!Number.isFinite(hours) || hours < 0) {
    toast(t("settings.watch_log_keep_bad"));
    return;
  }
  try {
    S.config = await api("/api/config", { watch_log_hours: hours });
    toast(t("settings.saved"));
  } catch (e) {
    toast(t("settings.save_failed", { err: e.message }));
  }
}

/** 日志为空时显示提示行，有内容就藏起来。 */
function syncWatchLogEmpty() {
  const log = $("#watchLog");
  const hint = $("#watchLogEmpty");
  if (hint) hint.hidden = !!(log && log.textContent.trim());
}

async function saveSettings() {
  const patch = {
    remote_base_url: $("#cfgBase").value.trim(),
    remote_model: $("#cfgModel").value.trim(),
    backup: $("#cfgBackup").checked,
    keep_local: $("#cfgKeepLocal").checked,
    translate: ($$('input[name=backend]').find(r => r.checked) || {}).value || "source"
  };
  // 空 = 不修改（服务端认这个占位符），而不是把已存的 key 清空
  patch.remote_api_key = $("#cfgKey").value.trim() || "••••";

  try {
    S.config = await api("/api/config", patch);
    $("#cfgKey").value = "";
    $("#keyState").textContent = S.config.remote_configured
      ? t("settings.key_set", { masked: S.config.remote_key_masked })
      : t("settings.key_unset");
    toast(t("settings.saved"));
  } catch (e) {
    toast(t("settings.save_failed", { err: e.message }));
  }
}

async function testRemote() {
  const out = $("#testResult");
  out.textContent = t("settings.testing");
  const body = {
    remote_base_url: $("#cfgBase").value.trim(),
    remote_model: $("#cfgModel").value.trim()
  };
  const typed = $("#cfgKey").value.trim();
  if (typed) body.remote_api_key = typed;   // 留空则用服务端已存的
  try {
    const d = await api("/api/test-remote", body);
    out.textContent = d.ok
      ? t("settings.test_ok", { msg: d.message })
      : t("settings.test_bad", { msg: d.message });
  } catch (e) {
    out.textContent = t("settings.test_bad", { msg: e.message });
  }
}

/** SMB 只读挂载 / 容器权限会让你「能读不能写」——先测一次，别等跑了半小时才失败。 */
async function testWrite() {
  const out = $("#writeResult");
  const root = ($("#root").value || S.root || "").trim();
  if (!root) { out.textContent = t("settings.need_root_first"); return; }
  out.textContent = t("settings.testing");
  try {
    const d = await api("/api/write-test?path=" + encodeURIComponent(root));
    out.textContent = d.ok
      ? t("settings.test_ok", { msg: d.message })
      : t("settings.test_bad", { msg: d.message });
  } catch (e) {
    out.textContent = t("settings.test_bad", { msg: e.message });
  }
}

async function rotateToken() {
  toast(t("settings.token_cli_only"));
}

/* ─────────────────────────────────────────── 历史 */

async function openHistory() {
  openOverlay("history");
  const sheet = $("#historySheet");
  sheet.innerHTML = `<div class="row" style="justify-content:space-between">
      <h2>${esc(t("history.title"))}</h2>
      <button class="btn-ghost" data-close type="button">${esc(t("common.close"))}</button>
    </div><div class="hr"></div><div class="empty">${esc(t("common.loading"))}</div>`;
  sheet.querySelector("[data-close]").addEventListener("click", () => closeOverlay("history"));

  try {
    const d = await api("/api/jobs");
    const jobs = d.jobs || [];
    sheet.innerHTML = `<div class="row" style="justify-content:space-between">
        <h2>${esc(t("history.title"))}</h2>
        <button class="btn-ghost" data-close type="button">${esc(t("common.close"))}</button>
      </div><div class="hr"></div>` + (jobs.map(j => `
        <div class="row" style="justify-content:space-between;padding:.7rem 0;border-bottom:1px solid var(--line-soft)">
          <span class="mono" style="max-width:50%;overflow:hidden;text-overflow:ellipsis"
                title="${esc(j.root)}">${esc(j.root)}</span>
          ${j.trigger === "watch"
            ? `<span class="pill" title="${esc(t("history.watch_hint"))}">${esc(t("history.watch_badge"))}</span>`
            : `<span class="small">${esc(t("history.manual_badge"))}</span>`}
          <span class="small">${esc(fmtTime(j.started_at))}</span>
          <span class="small">${esc(t("history.written", { n: (j.stats || {}).written || 0 }))}</span>
          <button class="btn btn-outline" data-job="${esc(j.job_id)}" type="button">${esc(t("history.view"))}</button>
        </div>`).join("") || `<div class="empty">${esc(t("history.empty"))}</div>`);

    sheet.querySelector("[data-close]").addEventListener("click", () => closeOverlay("history"));
    sheet.querySelectorAll("[data-job]").forEach(b => b.addEventListener("click", async () => {
      try {
        const rep = await api("/api/report/" + b.dataset.job);
        closeOverlay("history");
        showResults(rep.results, rep.stats, b.dataset.job);
      } catch (e) {
        toast(t("history.report_failed", { err: e.message }));
      }
    }));
  } catch (e) {
    sheet.innerHTML = `<div class="empty">${esc(t("history.failed", { err: e.message }))}</div>`;
  }
}

/* ─────────────────────────────────────────── 令牌闸门 */

export function initGate() {
  if (S.token) return;
  openOverlay("gate");
  const submit = async () => {
    const tok = $("#gateToken").value.trim();
    if (!tok) { $("#gateMsg").textContent = t("gate.need"); return; }
    S.token = tok;
    try {
      // 拿配置当校验：能读到就说明令牌对
      await api("/api/config");
      localStorage.setItem("fnlrc_token", tok);
      closeOverlay("gate");
    } catch (e) {
      S.token = "";
      $("#gateMsg").textContent = t("gate.bad", { msg: e.message });
    }
  };
  $("#gateSubmit").addEventListener("click", submit);
  $("#gateToken").addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
}
