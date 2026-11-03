/**
 * DOM 小工具 + 带令牌的 fetch 封装。
 * 所有网络请求都从这里走，方便统一处理鉴权失败与错误信息。
 */
import { S } from "./state.js";

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** 拼 HTML 前一律过这里，避免路径/歌名里的引号把结构撑破 */
export function esc(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

export function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => el.classList.remove("show"), 2800);
}

export function escapeAttr(v) {
  return esc(v);
}

/**
 * 请求服务端。POST 一律带访问令牌；失败时把服务端的 detail 抛成 Error。
 */
export async function api(path, body, method) {
  const opt = { method: method || (body === undefined ? "GET" : "POST"), headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  if (S.token) opt.headers["X-FNLRC-Token"] = S.token;

  const res = await fetch(path, opt);
  let data = null;
  try { data = await res.json(); } catch (e) { /* 非 JSON 响应，走下面的通用错误 */ }
  if (!res.ok) {
    const msg = (data && (data.detail || data.message)) || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return data;
}

export function openOverlay(id) { $("#" + id).classList.add("open"); }
export function closeOverlay(id) { $("#" + id).classList.remove("open"); }

export function fmtTime(sec) {
  return sec ? new Date(sec * 1000).toLocaleString("zh-CN") : "";
}
