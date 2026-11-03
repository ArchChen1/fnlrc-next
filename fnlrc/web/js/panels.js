/**
 * 覆盖式面板：设置 / 历史 / 令牌闸门。
 *
 * 三者各占一个 overlay，互不覆盖彼此的 DOM —— 早先把它们塞进同一个 sheet，
 * 结果打开「历史」会把设置表单整块替换掉，关掉之后就再也填不了 key。
 */
import { S } from "./state.js";
import { $, $$, esc, api, toast, openOverlay, closeOverlay, fmtTime } from "./core.js";
import { showResults } from "./screens.js";

export function initPanels() {
  $("#openSettings").addEventListener("click", openSettings);
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
    if (st) st.textContent = "开关变了，请重新点一次「② 预览差异」。";
  }
});

async function fnosRun(stage) {
  const out = $("#fnosOut");
  const status = $("#fnosStatus");
  const paths = fnosPaths();
  if (!paths.app_data && !paths.appmeta) {
    toast("先填飞牛的数据目录 —— 主库和分片是两处，都要填");
    return;
  }
  const music = ($("#root").value || S.root || "").trim();
  if (stage === "plan" && !music) { toast("先在第一屏扫描一次音乐库，我才能知道从哪儿比对"); return; }

  $("#fnosApply").disabled = true;
  fnosPlan = null;
  status.textContent = stage === "probe" ? "正在探测结构…" : "正在比对…";
  out.textContent = "";
  try {
    // ① 检测只查结构，**不带音乐库路径** —— 带了就会走成"差异计划"分支
    const d = await api("/api/fnos/preview", {
      ...paths,
      music_root: stage === "probe" ? "" : music
    });
    out.textContent = d.text || d.error || "";
    if (!d.ok) {
      status.textContent = "❌ " + (d.error || "结构对不上，先别往下走");
      return;
    }
    if (d.stage === "probe") {
      status.textContent = "✅ 结构正常。接着点「② 预览差异」。";
    } else {
      // 执行时按「原地更新 + 新建」的**总数**对账，所以这里也存总数
      const ins = allowInsert() ? (d.insertable || 0) : 0;
      fnosPlan = { ...paths, music, changed: d.changed + ins };
      const bits = [`原地改 ${d.changed} 首`];
      if (d.insertable) {
        bits.push(allowInsert() ? `新建 ${d.insertable} 首` : `另有 ${d.insertable} 首可新建`);
      }
      status.textContent = (d.changed || ins)
        ? bits.join("、") + `（跳过 ${d.same + d.no_lrc} 首）`
          + (d.write_enabled ? "。" : "。服务端没开启写入，第③步会被拒。")
        : "没有需要改的，磁盘和库里已经一致。";
      $("#fnosApply").disabled = !((d.changed || ins) && d.write_enabled);
    }
  } catch (e) {
    status.textContent = "❌ " + e.message;
  }
}

async function fnosApply() {
  if (!fnosPlan || !fnosPlan.changed) return;
  const ok = confirm(
    `要动飞牛数据库里 ${fnosPlan.changed} 首歌的歌词。\n\n` +
    `已有歌词的只换正文，保留 lyric_id —— 歌单、收藏、播放历史都不受影响。\n` +
    (allowInsert() ? `没有歌词的会**新建**一条歌词记录。\n` : "") +
    `写之前会自动备份，出问题可以从备份恢复。\n\n` +
    `继续吗？`);
  if (!ok) return;

  const status = $("#fnosStatus");
  $("#fnosApply").disabled = true;
  status.textContent = "正在写入…";
  try {
    const d = await api("/api/fnos/apply", {
      app_data: fnosPlan.app_data, appmeta: fnosPlan.appmeta,
      music_root: fnosPlan.music, expect_changed: fnosPlan.changed,
      allow_insert: allowInsert()
    });
    status.textContent = (d.changed || d.inserted)
      ? `✅ 回填完成：原地改 ${d.changed} 首、新建 ${d.inserted || 0} 首。` +
        `备份在 ${d.backup_dir}。` +
        (d.failed.length ? ` 有 ${d.failed.length} 首失败。` : "") +
        " 如果飞牛里没立刻变化，重启一下飞牛音乐应用。"
      : (d.message || "没有需要改的。");
    const bad = (d.insert_report || []).filter(x => x.why)
      .map(x => `新建失败 ${x.file}\n  ${x.why}`);
    if (d.failed.length || bad.length) {
      $("#fnosOut").textContent =
        bad.concat(d.failed.map(x => `${x.file}\n  ${x.why}`)).join("\n");
    }
    fnosPlan = null;
  } catch (e) {
    status.textContent = "❌ " + e.message;
    $("#fnosApply").disabled = false;
  }
}

/* ─────────────────────────────────────────── 设置 */

async function openSettings() {
  openOverlay("settings");
  try {
    S.config = await api("/api/config");
    applyConfig(S.config);
  } catch (e) {
    toast("读取配置失败：" + e.message);
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

  const keyInput = $("#cfgKey");
  if (c.remote_locked_by_env) {
    keyInput.disabled = true;
    keyInput.placeholder = "由环境变量提供，不能在网页里改";
    $("#keyState").textContent =
      "API Key 由环境变量 FNLRC_REMOTE_API_KEY 提供（优先于配置文件）。";
  } else {
    keyInput.disabled = false;
    keyInput.placeholder = "sk-…";
    $("#keyState").textContent = c.remote_configured
      ? `已配置：${c.remote_key_masked}。留空表示不修改。`
      : "还没配置。Key 只存在服务端，页面拿不到回显。";
  }

  const picked = $$('input[name=backend]').find(r => r.value === (c.translate || "source"));
  if (picked) {
    picked.checked = true;
    picked.dispatchEvent(new Event("change"));
  }

  applyWatchConfig(c.watch || {}, !!c.watch_running);
}

/** 监听设置区：先用服务端存的值回填，再按运行状态刷按钮。 */
function applyWatchConfig(w, running) {
  $("#watchRoot").value = w.watch_root || "";
  $("#watchAfter").value = w.watch_after || "keep";
  $("#watchMoveTo").value = w.watch_move_to || "";
  $("#watchWrite").checked = w.watch_write !== false;
  $("#watchQuiet").value = w.watch_quiet_s != null ? w.watch_quiet_s : 8;
  $("#watchPoll").value = w.watch_poll_s != null ? w.watch_poll_s : 5;
  $("#watchAutostart").checked = !!w.watch_autostart;
  setWatchRunning(running);
  if (running) openWatchStream();   // 刷新页面后，若服务端还在监听就重连事件流
}

function setWatchRunning(running) {
  S.watchRunning = running;
  const btn = $("#watchToggle");
  if (btn) btn.textContent = running ? "停止监听" : "开始监听";
  const st = $("#watchState");
  if (st) st.textContent = running ? "● 正在监听" : "已停止";
  if (st) st.style.color = running ? "#5eead4" : "";
}

function watchPatch() {
  return {
    watch_root: $("#watchRoot").value.trim(),
    watch_after: $("#watchAfter").value,
    watch_move_to: $("#watchMoveTo").value.trim(),
    watch_write: $("#watchWrite").checked,
    watch_quiet_s: parseInt($("#watchQuiet").value, 10) || 8,
    watch_poll_s: parseInt($("#watchPoll").value, 10) || 5,
    watch_autostart: $("#watchAutostart").checked
  };
}

async function saveWatch() {
  try {
    S.config = await api("/api/config", watchPatch());
    toast("监听设置已保存");
  } catch (e) {
    toast("保存失败：" + e.message);
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
      toast("已停止监听");
    } else {
      await api("/api/config", watchPatch());     // 先落盘，start 读的是已存配置
      const d = await api("/api/watch/start", {});
      setWatchRunning(!!(d.status && d.status.running));
      toast(d.already ? "本来就在监听" : "开始监听");
      openWatchStream();
    }
  } catch (e) {
    if (st) { st.textContent = "❌ " + e.message; st.style.color = ""; }
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
}

/* SSE：把监听事件追加到日志框。断线后浏览器会自动重连。 */
let watchSrc = null;
function openWatchStream() {
  if (!S.token || watchSrc) return;
  const url = "/api/watch/events?token=" + encodeURIComponent(S.token);
  watchSrc = new EventSource(url);
  watchSrc.onmessage = ev => {
    let d;
    try { d = JSON.parse(ev.data); } catch { return; }
    const log = $("#watchLog");
    if (!log) return;
    const r = d.result || {};
    const name = d.rel || r.file || (d.file || "").split(/[\\/]/).pop();
    const mark = d.event === "processing" ? "… 处理中"
               : (r.status || d.event || "完成");
    const note = r.note ? `  (${r.note})` : "";
    const line = `[${new Date().toLocaleTimeString()}] ${name} → ${mark}${note}`;
    log.textContent = (log.textContent + "\n" + line).trim().split("\n").slice(-200).join("\n");
    log.scrollTop = log.scrollHeight;
  };
  watchSrc.onerror = () => { /* 静默重连；服务停了会一直重试 */ };
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
      ? `已配置：${S.config.remote_key_masked}。留空表示不修改。`
      : "还没配置。";
    toast("已保存");
  } catch (e) {
    toast("保存失败：" + e.message);
  }
}

async function testRemote() {
  const out = $("#testResult");
  out.textContent = "测试中…";
  const body = {
    remote_base_url: $("#cfgBase").value.trim(),
    remote_model: $("#cfgModel").value.trim()
  };
  const typed = $("#cfgKey").value.trim();
  if (typed) body.remote_api_key = typed;   // 留空则用服务端已存的
  try {
    const d = await api("/api/test-remote", body);
    out.textContent = (d.ok ? "✅ " : "❌ ") + d.message;
  } catch (e) {
    out.textContent = "❌ " + e.message;
  }
}

/** SMB 只读挂载 / 容器权限会让你「能读不能写」——先测一次，别等跑了半小时才失败。 */
async function testWrite() {
  const out = $("#writeResult");
  const root = ($("#root").value || S.root || "").trim();
  if (!root) { out.textContent = "先在第一屏填音乐库路径"; return; }
  out.textContent = "测试中…";
  try {
    const d = await api("/api/write-test?path=" + encodeURIComponent(root));
    out.textContent = (d.ok ? "✅ " : "❌ ") + d.message;
  } catch (e) {
    out.textContent = "❌ " + e.message;
  }
}

async function rotateToken() {
  toast("令牌只能在服务端重置：fnlrc token --rotate");
}

/* ─────────────────────────────────────────── 历史 */

async function openHistory() {
  openOverlay("history");
  const sheet = $("#historySheet");
  sheet.innerHTML = `<div class="row" style="justify-content:space-between">
      <h2>历史任务</h2>
      <button class="btn-ghost" data-close type="button">关闭 ✕</button>
    </div><div class="hr"></div><div class="empty">读取中…</div>`;
  sheet.querySelector("[data-close]").addEventListener("click", () => closeOverlay("history"));

  try {
    const d = await api("/api/jobs");
    const jobs = d.jobs || [];
    sheet.innerHTML = `<div class="row" style="justify-content:space-between">
        <h2>历史任务</h2>
        <button class="btn-ghost" data-close type="button">关闭 ✕</button>
      </div><div class="hr"></div>` + (jobs.map(j => `
        <div class="row" style="justify-content:space-between;padding:.7rem 0;border-bottom:1px solid var(--line-soft)">
          <span class="mono" style="max-width:50%;overflow:hidden;text-overflow:ellipsis"
                title="${esc(j.root)}">${esc(j.root)}</span>
          <span class="small">${esc(fmtTime(j.started_at))}</span>
          <span class="small">写 ${(j.stats || {}).written || 0}</span>
          <button class="btn btn-outline" data-job="${esc(j.job_id)}" type="button">查看</button>
        </div>`).join("") || '<div class="empty">还没有历史任务</div>');

    sheet.querySelector("[data-close]").addEventListener("click", () => closeOverlay("history"));
    sheet.querySelectorAll("[data-job]").forEach(b => b.addEventListener("click", async () => {
      try {
        const rep = await api("/api/report/" + b.dataset.job);
        closeOverlay("history");
        showResults(rep.results, rep.stats, b.dataset.job);
      } catch (e) {
        toast("读取报告失败：" + e.message);
      }
    }));
  } catch (e) {
    sheet.innerHTML = `<div class="empty">读取历史失败：${esc(e.message)}</div>`;
  }
}

/* ─────────────────────────────────────────── 令牌闸门 */

export function initGate() {
  if (S.token) return;
  openOverlay("gate");
  const submit = async () => {
    const t = $("#gateToken").value.trim();
    if (!t) { $("#gateMsg").textContent = "先填令牌"; return; }
    S.token = t;
    try {
      // 拿配置当校验：能读到就说明令牌对
      await api("/api/config");
      localStorage.setItem("fnlrc_token", t);
      closeOverlay("gate");
    } catch (e) {
      S.token = "";
      $("#gateMsg").textContent = "令牌不对：" + e.message;
    }
  };
  $("#gateSubmit").addEventListener("click", submit);
  $("#gateToken").addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
}
