/**
 * 前端 i18n（M4）。零依赖、零构建 —— 每语言一个模块，显式登记。
 *
 * 与后端 `fnlrc/i18n/` **同一套键名**（`status.written`、`backend.source` 两边都用），
 * 便于对照维护，也能用脚本校验两边键是否齐。
 *
 * 约定：
 * - 缺 key 回退英文（FALLBACK），再缺就显示 key 本身 —— **绝不显示空白**。
 * - 数据（status 枚举、报告字段、CSV）不翻译，只有展示层翻译。
 * - 新增语言 = 新增一个 `xx.js` + 这里 `CATALOGS` 加一行。
 */
import zh from "./zh.js";
import en from "./en.js";

/** 显式登记（零构建环境不能像 Node 那样扫目录） */
export const CATALOGS = { zh, en };

/** 缺翻译时回退到这个语言 —— 英文覆盖最全 */
export const FALLBACK = "en";

/** 语言代码 → 该语言自己的名字（下拉框用，永远不翻译） */
export const LANGS = [["zh", "中文"], ["en", "English"]];

const STORE_KEY = "fnlrc_lang";
let current = "zh";

/** `zh-CN` / `en_US` / `EN` → 受支持的代码；不支持返回空串 */
export function normalize(lang) {
  if (!lang) return "";
  const low = String(lang).trim().toLowerCase().replace(/_/g, "-");
  if (CATALOGS[low]) return low;
  const head = low.split("-")[0];
  return CATALOGS[head] ? head : "";
}

export function getLang() {
  return current;
}

/** 切换语言。不支持的值保持原样（不抛异常）。 */
export function setLang(lang) {
  const got = normalize(lang);
  if (got) current = got;
  try { localStorage.setItem(STORE_KEY, current); } catch (e) { /* 隐私模式会抛，忽略 */ }
  if (typeof document !== "undefined") {
    document.documentElement.lang = current === "zh" ? "zh-CN" : current;
  }
  return current;
}

/** 优先级：用户上次选择 > 浏览器语言 > zh */
export function detectLang() {
  let saved = "";
  try { saved = localStorage.getItem(STORE_KEY) || ""; } catch (e) { /* 忽略 */ }
  if (normalize(saved)) return normalize(saved);
  const nav = String((typeof navigator !== "undefined" && navigator.language) || "zh").toLowerCase();
  if (nav.startsWith("zh")) return "zh";
  if (nav.startsWith("en")) return "en";
  return "zh";
}

function lookup(key) {
  const cur = CATALOGS[current] || {};
  if (cur[key] != null) return cur[key];
  const fb = CATALOGS[FALLBACK] || {};
  if (fb[key] != null) return fb[key];
  return null;
}

/** 这条文案在当前语言（或回退语言）里存在吗 */
export function has(key) {
  return lookup(key) != null;
}

/**
 * 翻译。变量用 `{name}` 占位：`t("summary.dirs", { n: 3, root: "/music" })`。
 * 占位符对不上时返回原文，不抛异常 —— 翻译文件写错不该把界面搞白。
 */
export function t(key, vars) {
  const s = lookup(key);
  if (s == null) return key;
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] != null ? String(vars[k]) : m));
}

/** 有位对应的语义值（枚举）用它：查不到就用 fallback，而不是吐出一个 key */
export function tOr(key, fallback) {
  const s = lookup(key);
  if (s == null) return fallback === undefined ? key : fallback;
  return s;
}

/**
 * 把静态 DOM 里的文案换掉。
 *
 * - `data-i18n="key"`        → textContent
 * - `data-i18n-html="key"`   → innerHTML（文案里带 `<b>`/`<span class="mono">`）
 * - `data-i18n-ph="key"`     → placeholder
 * - `data-i18n-title="key"`  → title
 * - `data-i18n-aria="key"`   → aria-label
 */
export function applyI18n(root) {
  const scope = root || document;
  scope.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  scope.querySelectorAll("[data-i18n-html]").forEach(el => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  scope.querySelectorAll("[data-i18n-ph]").forEach(el => {
    el.setAttribute("placeholder", t(el.dataset.i18nPh));
  });
  scope.querySelectorAll("[data-i18n-title]").forEach(el => {
    el.setAttribute("title", t(el.dataset.i18nTitle));
  });
  scope.querySelectorAll("[data-i18n-aria]").forEach(el => {
    el.setAttribute("aria-label", t(el.dataset.i18nAria));
  });
  if (scope === document) document.title = t("app.title");
}
