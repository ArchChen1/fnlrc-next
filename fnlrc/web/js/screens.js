/**
 * 五个场景屏：选库 → 扫描 → 预览 → 执行 → 报告。
 * 对应 skill 的「一屏一事」，也对应 UI-SPEC 的五步。
 */
import { S, ACTION_LABEL, STATUS_LABEL, BACKENDS, FILTERS, GROUP, tone } from "./state.js";
import { $, $$, esc, api, toast, fmtTime } from "./core.js";
import { openFsBrowser } from "./fsbrowser.js";

const track = () => $("#track");

/* ─────────────────────────────────────────── 场景切换 */

export function initSnap() {
  S.scenes = $$(".scene");
  const t = track();
  const obs = new IntersectionObserver(entries => {
    entries.forEach(e => {
      if (e.isIntersecting && e.intersectionRatio > 0.5) {
        const i = S.scenes.indexOf(e.target);
        $$(".dot").forEach((d, j) => d.setAttribute("aria-current", String(i === j)));
      }
    });
  }, { root: t, threshold: [0.5] });
  S.scenes.forEach(s => obs.observe(s));
  $$(".dot").forEach(d => d.addEventListener("click", () => go(+d.dataset.i)));

  // 键盘可达：面板打开时不抢键
  addEventListener("keydown", e => {
    if ($(".overlay.open")) return;
    const cur = currentScene();
    if (e.key === "ArrowDown" || e.key === "PageDown") {
      e.preventDefault(); go(Math.min(cur + 1, S.scenes.length - 1));
    } else if (e.key === "ArrowUp" || e.key === "PageUp") {
      e.preventDefault(); go(Math.max(cur - 1, 0));
    }
  });
}

/**
 * 用相对位移计算目标位置，而不是 `i × 视口高`。
 * 后者只在「每屏恰好一个视口高」时成立 —— 一旦某屏因为内容多而变高，
 * 后面所有屏的位移就全错位了（实测踩过）。
 */
function sceneOffset(t, scene) {
  return t.scrollTop + (scene.getBoundingClientRect().top - t.getBoundingClientRect().top);
}

export function go(i) {
  const t = track();
  const scene = S.scenes[i];
  if (!scene) return;
  t.scrollTo({ top: sceneOffset(t, scene), behavior: "smooth" });
}

function currentScene() {
  const t = track();
  let best = 0, bestDist = Infinity;
  S.scenes.forEach((s, i) => {
    const d = Math.abs(sceneOffset(t, s) - t.scrollTop);
    if (d < bestDist) { bestDist = d; best = i; }
  });
  return best;
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
  if (!root) { toast("先填一个音乐库路径"); return; }
  S.root = root;
  localStorage.setItem("fnlrc_root", root);

  const btn = $("#doScan");
  btn.disabled = true; btn.textContent = "扫描中…";
  try {
    const d = await api("/api/scan", { root });
    S.summary = d.summary;
    renderSummary(d.summary);
    go(1);
  } catch (e) {
    toast("扫描失败：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "扫描音乐库";
  }
}

function renderSummary(s) {
  const need = s.mono_or_single + s.missing + s.broken;
  const tiles = [["音频总数", s.total], ["已有双语", s.bilingual],
                 ["需要补完", need], ["纯音乐跳过", s.instrumental]];
  $("#stats").innerHTML = tiles.map(([k, n], i) =>
    `<div class="stat rv d${i * 75}"><div class="n">${n}</div><div class="k">${k}</div></div>`
  ).join("");

  const tops = Object.keys(s.by_top || {}).sort();
  $("#bytop").innerHTML = tops.map(t => {
    const d = s.by_top[t];
    return `<tr><td>${esc(t)}</td><td>${d.audio}</td><td>${d.bilingual}</td>
            <td>${d.mono}</td><td>${d.none}</td><td>${d.broken}</td></tr>`;
  }).join("") || `<tr><td colspan="6"><div class="empty">
      这个文件夹里没找到音频文件。支持 mp3/flac/m4a/wav/ogg/ape 等。</div></td></tr>`;

  $("#scanNote").textContent = `${tops.length} 个目录 · ${S.root}`;
}

/* ─────────────────────────────────────────── ③ 预览 */

export function initPreview() {
  // 两列排布：五个后端竖着排会把这屏撑出视口（skill 禁止内容超屏）
  $("#backendPick").className = "grid g2";
  $("#backendPick").innerHTML = BACKENDS.map(([v, label], i) =>
    `<label class="row checkline" style="margin-bottom:.35rem">
       <input type="radio" name="backend" value="${v}" ${i === 0 ? "checked" : ""}>
       <span>${label}</span>
     </label>`).join("");
  $$('input[name=backend]').forEach(r => r.addEventListener("change", showBackendHint));
  showBackendHint();

  $("#doPreview").addEventListener("click", doPreview);
  $("#doRun").addEventListener("click", doRun);
  $("#doStop").addEventListener("click", doStop);
}

export function currentBackend() {
  const picked = $$('input[name=backend]').find(r => r.checked);
  return picked ? picked.value : "source";
}

function showBackendHint() {
  const row = BACKENDS.find(b => b[0] === currentBackend());
  $("#backendHint").textContent = row ? row[2] : "";
}

async function doPreview() {
  if (!S.root) { toast("先扫描一个音乐库"); go(0); return; }
  const btn = $("#doPreview");
  btn.disabled = true; btn.textContent = "计算中…";
  try {
    const d = await api("/api/preview", { root: S.root });
    S.preview = d;
    renderPreview(d);
    go(2);
  } catch (e) {
    toast("预览失败：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "查看补完预览";
  }
}

function renderPreview(d) {
  const dist = d.distribution || {};
  $("#dist").innerHTML = Object.keys(dist).sort().map(k =>
    `<div class="row" style="justify-content:space-between">
       <span>${esc(ACTION_LABEL[k] || k)}</span><strong>${dist[k]}</strong></div>`
  ).join("") || '<div class="empty">没有需要处理的歌</div>';

  $("#previewRows").innerHTML = (d.rows || []).slice(0, 400).map(r =>
    `<tr><td>${esc(ACTION_LABEL[r.action] || r.action)}</td>
         <td class="path" title="${esc(r.file)}">${esc(r.file)}</td>
         <td style="color:rgba(255,255,255,.6)">${esc(r.reason)}${
           r.embedded_lyrics ? '<br><span class="pill warn" style="margin-top:.4rem">内嵌歌词会压住外挂文件</span>' : ""}</td></tr>`
  ).join("") || '<tr><td colspan="3"><div class="empty">没有需要处理的歌</div></td></tr>';

  $("#runNote").textContent = `共 ${d.total} 首`;
}

/* ─────────────────────────────────────────── ④ 执行 */

async function doRun() {
  if (!S.root) { toast("先扫描一个音乐库"); go(0); return; }
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
    setProgress(0, d.total, "已开始");
    $("#doStop").disabled = false;
    go(3);
    streamJob(d.job_id, d.total);
  } catch (e) {
    toast("启动失败：" + e.message);
    $("#doRun").disabled = false;
  }
}

function setProgress(done, total, label) {
  const pct = total ? Math.round(done / total * 100) : 0;
  $("#progBar").style.width = pct + "%";
  $("#progPct").textContent = pct + "%";
  $("#progText").textContent = `${label}　${done} / ${total}`;
}

function bumpStats(results) {
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
  const tiles = [["已写入", c.written], ["单语", c.mono], ["跳过", c.skipped],
                 ["无歌词", c.no_lyrics], ["超时", c.timeout], ["待人工", c.manual],
                 ["失败", c.failed]];
  $("#liveStats").innerHTML = tiles.map(([k, n]) =>
    `<div class="stat"><div class="n" style="font-size:1.4rem">${n}</div><div class="k">${k}</div></div>`
  ).join("");
}

function logLine(r) {
  const val = r.validation ? ` 并行组=${r.pairs} 比例=${r.validation.pair_ratio}` : "";
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
      setProgress(d.index, d.total, "进行中");
    } else if (d.type === "done") {
      S.stats = d.stats || {};
      es.close();
      finishReport();
    } else if (d.type === "stopped") {
      es.close();
      $("#doStop").disabled = true;
      $("#doRun").disabled = false;
      setProgress(d.index, total, "已停止");
      toast("已停止");
    } else if (d.type === "error") {
      es.close();
      $("#doRun").disabled = false;
      toast("出错了：" + d.message);
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
    toast("正在停止…");
  } catch (e) {
    toast("停止失败：" + e.message);
  }
}

/* ─────────────────────────────────────────── ⑤ 报告 */

export function initReport() {
  $("#exportCsv").addEventListener("click", () => {
    if (!S.jobId) { toast("还没有跑过任务"); return; }
    const q = S.filter === "all" ? "" : "status=" + encodeURIComponent(S.filter) + "&";
    location.href = `/api/report/${S.jobId}?${q}format=csv`;
  });
  $("#restart").addEventListener("click", () => go(0));
}

export function finishReport() {
  $("#doStop").disabled = true;
  $("#doRun").disabled = false;

  const st = S.stats || {};
  $("#reportTitle").textContent = "完成！";
  $("#reportSummary").textContent = [
    `写入 ${st.written || 0}`, `单语 ${st.written_mono || 0}`, `跳过 ${st.skipped || 0}`,
    `无歌词 ${st.no_lyrics || 0}`, `超时 ${st.timeout || 0}`,
    `待人工 ${st.lang_mismatch || 0}`, `失败 ${st.failed || 0}`
  ].join(" · ") + (st.elapsed_s ? `　总用时 ${Math.round(st.elapsed_s)} 秒` : "");

  renderFilters();
  renderReport();
  setProgress(S.results.length, S.results.length, "已完成");
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

function renderFilters() {
  $("#filterTabs").innerHTML = FILTERS.map(([k, label]) =>
    `<button class="btn ${S.filter === k ? "btn-primary" : "btn-outline"}" data-f="${k}" type="button">${label}</button>`
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

function renderReport() {
  let rows = S.results.filter(matchFilter);
  // 「待人工确认」永远置顶 —— 这是唯一需要用户真正决策的一类
  rows = rows.slice().sort((a, b) =>
    (b.status === "lang_mismatch") - (a.status === "lang_mismatch"));

  $("#reportRows").innerHTML = rows.map(r => {
    const v = r.validation || {};
    const title = r.note ? ` title="${esc(r.note)}"` : "";
    return `<tr${title}>
      <td><span class="pill ${tone(r.status)}">${esc(STATUS_LABEL[r.status] || r.status)}</span></td>
      <td class="path" title="${esc(r.file)}">${esc(r.file)}</td>
      <td style="color:rgba(255,255,255,.7)">${esc(r.matched || "—")}</td>
      <td>${esc(r.source || "—")}</td>
      <td>${r.pairs || "—"}</td>
      <td>${v.pair_ratio != null ? v.pair_ratio : "—"}</td></tr>`;
  }).join("") || '<tr><td colspan="6"><div class="empty">这一栏没有内容</div></td></tr>';
}

export { fmtTime };
