/**
 * 网页版的目录浏览器。
 * 桌面版优先走 pywebview 的原生文件夹对话框（见 screens.js 的 browse 按钮），
 * 只有拿不到原生对话框时才用这个。
 */
import { $, esc, api, toast, openOverlay, closeOverlay } from "./core.js";
import { t } from "./i18n/index.js";

let onPickCallback = null;

export function openFsBrowser(startPath, onPick) {
  onPickCallback = onPick || null;
  load(startPath || "");
}

async function load(path) {
  const sheet = $("#fsSheet");
  let data;
  try {
    data = await api("/api/fs?path=" + encodeURIComponent(path));
  } catch (e) {
    toast(t("fs.cannot_open", { err: e.message }));
    if (!path) return;
    // 打不开就退回上一级，别把用户卡死在一个坏路径上
    const parent = path.replace(/[\\/][^\\/]*$/, "");
    if (parent && parent !== path) return load(parent);
    return;
  }
  render(data);
  openOverlay("fsbrowser");
}

function render(d) {
  const dirs = (d.dirs || []).map(x =>
    `<button class="btn btn-outline diritem" data-go="${esc(x.path)}" type="button">${esc(x.name)}</button>`
  ).join("");
  $("#fsSheet").innerHTML = `
    <div class="row" style="justify-content:space-between">
      <h2>${esc(t("fs.title"))}</h2>
      <button class="btn-ghost" data-close type="button">${esc(t("common.close"))}</button>
    </div>
    <p class="mono" style="margin:1rem 0">${esc(t("fs.info", { path: d.path, n: d.audio_count }))}</p>
    <div class="row">
      <button class="btn btn-secondary" data-go="${esc(d.parent)}" type="button">${esc(t("fs.up"))}</button>
      <button class="btn btn-primary" data-pick="${esc(d.path)}" type="button">${esc(t("fs.pick"))}</button>
    </div>
    <div class="hr"></div>
    <div class="dirlist">${dirs || `<div class="empty">${esc(t("fs.no_subdir"))}</div>`}</div>`;

  const sheet = $("#fsSheet");
  sheet.querySelector("[data-close]").addEventListener("click", () => closeOverlay("fsbrowser"));
  sheet.querySelector("[data-pick]").addEventListener("click", e => pick(e.currentTarget.dataset.pick));
  sheet.querySelectorAll("[data-go]").forEach(b =>
    b.addEventListener("click", e => load(e.currentTarget.dataset.go)));
}

function pick(path) {
  closeOverlay("fsbrowser");
  if (onPickCallback) onPickCallback(path);
}
