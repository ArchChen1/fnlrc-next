/**
 * 首次使用引导（M1 前置）：欢迎 → 填令牌 → 选库。
 *
 * 只在「全新环境 / 配置损坏」时出现 —— 判据由服务端给（`/api/config` 的
 * `setup_needed`）。已有配置直接进主界面，并沿用原来的令牌闸门（panels.initGate）。
 *
 * 不破坏既有令牌机制：令牌校验仍然是「拿它去调一个受保护的接口」，只是换成
 * 一个**只读、无副作用**的校验端点 `/api/auth`，并且包上了引导语。
 */
import { S } from "./state.js";
import { $, $$, api, openOverlay, closeOverlay } from "./core.js";
import { t } from "./i18n/index.js";
import { go, adoptScanned } from "./screens.js";
import { openFsBrowser } from "./fsbrowser.js";
import { initGate } from "./panels.js";

const LABEL_KEY = { 0: "wizard.step_welcome", 1: "wizard.step_token", 2: "wizard.step_library" };

let flow = [];        // 本次实际要走的步骤；回环访问时省掉「令牌」这一步
let at = 0;           // 在 flow 里的下标
let needToken = false;
let scanned = null;   // 引导里扫描成功的 { root, summary }
let setupVersion = 1; // 服务端给的引导口径版本，完成时回写，便于将来重引导

export async function initWizard() {
  let cfg = null;
  try {
    cfg = await api("/api/config");
  } catch (e) {
    /* 服务没起或响应异常：交给下面的令牌闸门兜底 */
  }

  // 不是首次 → 回到原来的令牌闸门（绝大多数人是这条路）
  if (cfg && !cfg.setup_needed) { initGate(); return; }
  if (cfg && cfg.setup_version) setupVersion = cfg.setup_version;

  // 回环访问时服务端已把令牌注进页面（window.__FNLRC_TOKEN__）；否则要用户手填
  needToken = !S.token;
  flow = needToken ? [0, 1, 2] : [0, 2];
  at = 0;

  bind();
  show();
  openOverlay("wizard");
}

function show() {
  const step = flow[at];
  $$(".wizstep").forEach(s => { s.hidden = Number(s.dataset.step) !== step; });
  $("#wizEyebrow").textContent = t("step.of", { i: at + 1, n: flow.length });
  $("#wizDots").innerHTML = flow.map((s, i) =>
    `<span class="wizdot" aria-current="${i === at}" data-done="${i < at}">${t(LABEL_KEY[s])}</span>`
  ).join("");
  if (step === 2 && !$("#wizRoot").value) {
    $("#wizRoot").value = localStorage.getItem("fnlrc_root") || "";
  }
}

function bind() {
  $("#wizSkip").addEventListener("click", () => finish(false));
  $("#wizStart").addEventListener("click", advance);
  $("#wizVerify").addEventListener("click", verifyToken);
  $("#wizToken").addEventListener("keydown", e => { if (e.key === "Enter") verifyToken(); });
  $("#wizBack1").addEventListener("click", retreat);
  $("#wizBack2").addEventListener("click", retreat);
  $("#wizBrowse").addEventListener("click", browse);
  $("#wizScan").addEventListener("click", scan);
  $("#wizRoot").addEventListener("keydown", e => { if (e.key === "Enter") scan(); });
  $("#wizDone").addEventListener("click", () => finish(true));
}

function advance() { if (at < flow.length - 1) { at++; show(); } }
function retreat() { if (at > 0) { at--; show(); } }

async function verifyToken() {
  const tok = $("#wizToken").value.trim();
  if (!tok) { $("#wizTokenMsg").textContent = t("gate.need"); return; }
  const prev = S.token;
  S.token = tok;
  const btn = $("#wizVerify");
  btn.disabled = true; btn.textContent = t("wizard.verifying");
  try {
    await api("/api/auth");        // 与写操作同一套校验，但只读、无副作用
    localStorage.setItem("fnlrc_token", tok);
    $("#wizTokenMsg").textContent = t("wizard.token_ok");
    advance();
  } catch (e) {
    S.token = prev;                // 错了就还原，别把坏令牌留在状态里
    $("#wizTokenMsg").textContent = t("gate.bad", { msg: e.message });
  } finally {
    btn.disabled = false; btn.textContent = t("wizard.verify");
  }
}

function browse() {
  const native = window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder;
  if (native) {
    window.pywebview.api.pick_folder().then(p => { if (p) $("#wizRoot").value = p; });
    return;
  }
  openFsBrowser($("#wizRoot").value.trim(), p => { $("#wizRoot").value = p; });
}

async function scan() {
  const root = $("#wizRoot").value.trim();
  if (!root) { $("#wizMsg").textContent = t("pick.need_root"); return; }
  const btn = $("#wizScan");
  btn.disabled = true; btn.textContent = t("wizard.scanning");
  $("#wizDone").disabled = true;
  try {
    const d = await api("/api/scan", { root });   // 只读：不联网、不写库
    scanned = { root, summary: d.summary };
    $("#wizMsg").textContent = t("wizard.scan_ok", { n: d.summary.total });
    $("#wizDone").disabled = false;
  } catch (e) {
    scanned = null;
    $("#wizMsg").textContent = e.message;   // 服务端已带「打不开这个路径：」前缀
  } finally {
    btn.disabled = false; btn.textContent = t("wizard.scan");
  }
}

/**
 * 结束引导。
 * @param {boolean} markDone  true=用户点了「完成」；false=点了「跳过」。
 *   两者都记为「不再引导」——但跳过时若还没拿到令牌，下面会退回原始闸门。
 */
async function finish(markDone) {
  if (markDone) await saveSetupDone();
  closeOverlay("wizard");
  if (scanned) {
    adoptScanned(scanned.root, scanned.summary);   // 结果直接接管主界面
    go(1);
  } else {
    syncRootToMain();
  }
  initGate();   // 没令牌就弹原来的闸门（有令牌时它自己会跳过）
}

/** 填了路径但没扫描：至少把它带进主界面，省得用户再抄一遍。 */
function syncRootToMain() {
  const v = ($("#wizRoot").value || "").trim();
  if (!v) return;
  localStorage.setItem("fnlrc_root", v);
  const main = document.getElementById("root");
  if (main && !main.value) main.value = v;
}

async function saveSetupDone() {
  try {
    await api("/api/config", { setup_done: true, setup_version: setupVersion });
  } catch (e) {
    /* 没令牌 / 被拒：先不记。宁可下次再引导一次，也不假装已配置好 */
  }
}
