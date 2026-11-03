/**
 * 人工翻译编辑器（M3）。
 *
 * 报告页对「还缺译文的歌」点「补译文」→ 这里弹出逐行编辑框。
 * 决策对齐 `plan/DECISIONS-CONFIRMED.md`：
 *   C1 保存要走访问令牌（core.api 自动带上 S.token）；
 *   C2 显式点「保存」，未保存内容先存浏览器本地草稿，防误关丢失；
 *   C3 记忆库默认关（勾选后才把这次填的译文记进翻译记忆）；
 *   C4 不在网页里做上下文列（整首歌词本来就在眼前，直接看上一行即可）。
 *
 * 复用后端 `/api/manual/lines`（读）与 `/api/manual/save`（写），
 * 与 CLI `translate-export` / `translate-import` 是同一套 `manual.py` 逻辑。
 */
import { S } from "./state.js";
import { $, esc, api, toast, openOverlay, closeOverlay } from "./core.js";
import { t } from "./i18n/index.js";

const DRAFT_PREFIX = "fnlrc_draft:";
const EDITABLE = ["written_mono", "lang_mismatch", "timeout",
                  "written", "written_keep_local", "dumped"];

/** 打开编辑器时记下的当前歌曲上下文 */
let cur = null;   // { root, rel, song, draft }

export function initManual() {
  const sheet = $("#manualSheet");
  // 统一走事件委托：面板内容是动态渲染的，直接绑按钮会随重绘失效
  sheet.addEventListener("click", e => {
    if (e.target.closest("[data-mclose]")) { closeOverlay("manual"); return; }
    if (e.target.closest("#mSave")) { doSave(); return; }
    if (e.target.closest("#mClear")) { clearDraft(); renderSheet(); return; }
  });
  sheet.addEventListener("input", e => {
    if (e.target.matches("input[data-i]")) stashDraft();
  });
}

/** 报告页那一列「操作」里的按钮，是否该给这首歌 */
export function canEdit(status) {
  return EDITABLE.includes(status);
}

/** 从报告行打开编辑器 */
export async function openManual(rel) {
  if (!S.root) { toast(t("manual.need_root")); return; }
  cur = { root: S.root, rel, song: null, draft: null };
  renderSheet(t("manual.loading"));
  openOverlay("manual");
  try {
    const d = await api("/api/manual/lines", { root: S.root, files: [rel] });
    const song = (d.songs || [])[0];
    if (!song || song.error) {
      renderSheet(song && song.error ? song.error : t("manual.no_lyrics"));
      return;
    }
    cur.song = song;
    cur.draft = loadDraft(rel);
    renderSheet();
  } catch (e) {
    renderSheet(t("manual.load_failed", { err: e.message }));
  }
}

/* ── 渲染 ─────────────────────────────────────────────── */

function renderSheet(message) {
  const sheet = $("#manualSheet");
  if (message !== undefined) {
    sheet.innerHTML = `
      <div class="row" style="justify-content:space-between">
        <h2>${esc(t("manual.title"))}</h2>
        <button class="btn-ghost" data-mclose type="button">${esc(t("common.close"))}</button>
      </div>
      <div class="hr"></div>
      <div class="empty">${esc(message)}</div>`;
    return;
  }

  const song = cur.song;
  const draft = cur.draft || {};
  const nDraft = Object.keys(draft).length;
  const rows = song.lines.map(ln => {
    const val = draft[ln.index] != null ? draft[ln.index] : (ln.translation || "");
    return `<div class="mline">
      <span class="mtime mono">${esc(ln.time)}</span>
      <span class="msrc">${esc(ln.original)}</span>
      <input class="mtran" data-i="${ln.index}" type="text"
             value="${esc(val)}" placeholder="${esc(t("manual.placeholder"))}" autocomplete="off">
    </div>`;
  }).join("");

  sheet.innerHTML = `
    <div class="row" style="justify-content:space-between">
      <h2>${esc(t("manual.title"))}</h2>
      <button class="btn-ghost" data-mclose type="button">${esc(t("common.close"))}</button>
    </div>
    <p class="small" style="margin-top:.4rem">
      ${esc(song.title || "")} ${song.artist ? "· " + esc(song.artist) : ""}
      <span class="mono" style="margin-left:.5rem">${esc(song.file)}</span>
    </p>
    <p class="note" style="margin:.9rem 0 1rem">
      ${t("manual.note")}
      ${nDraft ? `<span style="color:#fff">${esc(t("manual.draft_restored", { n: nDraft }))}</span>` : ""}
    </p>

    <div class="scroller mlist" id="mList">
      <div class="mline mhead"><span>${esc(t("manual.time"))}</span><span>${esc(t("manual.original"))}</span><span>${esc(t("manual.translation"))}</span></div>
      ${rows || `<div class="empty">${esc(t("manual.no_lines"))}</div>`}
    </div>

    <div id="mProblems" class="prebox" style="color:var(--bad)"></div>

    <div class="row" style="margin-top:1.4rem">
      <label class="row checkline" style="margin:0">
        <input type="checkbox" id="mMemory"> <span>${esc(t("manual.memory"))}</span>
      </label>
    </div>
    <div class="row" style="margin-top:1rem">
      <button class="btn btn-primary" id="mSave" type="button">${esc(t("manual.save"))}</button>
      <button class="btn btn-outline" id="mClear" type="button">${esc(t("manual.discard"))}</button>
      <span class="small" style="margin-left:auto">${esc(t("manual.draft_hint"))}</span>
    </div>`;

  $("#mList").scrollTop = 0;
}

/* ── 草稿 ─────────────────────────────────────────────── */

function loadDraft(rel) {
  try {
    const raw = localStorage.getItem(DRAFT_PREFIX + rel);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

/** 把当前输入框里的非空改动收成 {index: value} 存本地 */
function stashDraft() {
  if (!cur || !cur.song) return;
  const map = {};
  $("#manualSheet").querySelectorAll("input[data-i]").forEach(inp => {
    const v = inp.value.trim();
    if (v) map[inp.dataset.i] = v;
  });
  cur.draft = Object.keys(map).length ? map : null;
  try {
    if (cur.draft) localStorage.setItem(DRAFT_PREFIX + cur.rel, JSON.stringify(cur.draft));
    else localStorage.removeItem(DRAFT_PREFIX + cur.rel);
  } catch (e) { /* 隐私模式下 localStorage 会抛，忽略即可 */ }
}

function clearDraft() {
  if (!cur) return;
  try { localStorage.removeItem(DRAFT_PREFIX + cur.rel); } catch (e) { /* 忽略 */ }
  cur.draft = null;
  // 清空所有输入框
  const sheet = $("#manualSheet");
  if (sheet) sheet.querySelectorAll("input[data-i]").forEach(inp => { inp.value = ""; });
  toast(t("manual.discarded"));
}

/* ── 保存 ─────────────────────────────────────────────── */

function collectLines() {
  const byIndex = {};
  document.querySelectorAll("#manualSheet input[data-i]").forEach(inp => {
    byIndex[inp.dataset.i] = inp.value.trim();
  });
  return cur.song.lines.map(ln => ({
    index: ln.index,
    original: ln.original,
    translation: byIndex[ln.index] || "",
  }));
}

async function doSave() {
  if (!cur || !cur.song) return;
  const btn = $("#mSave");
  const box = $("#mProblems");
  box.textContent = "";
  btn.disabled = true; btn.textContent = t("common.saving");
  try {
    const d = await api("/api/manual/save", {
      root: cur.root,
      file: cur.rel,
      lines: collectLines(),
      memory: $("#mMemory") && $("#mMemory").checked,
      backup: true,
    });
    if (!d.ok) {
      box.textContent = (d.problems || [t("manual.rejected_generic")]).join("\n");
      toast(t("manual.rejected"));
      return;
    }
    clearDraftSilent();
    toast(t("manual.saved", { n: d.lines }));
    closeOverlay("manual");
  } catch (e) {
    toast(t("manual.save_failed", { err: e.message }));
  } finally {
    btn.disabled = false; btn.textContent = t("manual.save");
  }
}

/** 保存成功后静默清掉草稿（不再弹 toast） */
function clearDraftSilent() {
  try { localStorage.removeItem(DRAFT_PREFIX + cur.rel); } catch (e) { /* 忽略 */ }
  cur.draft = null;
}
