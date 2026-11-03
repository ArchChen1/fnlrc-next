/**
 * 全局状态与文案表。
 *
 * 依赖的只有 i18n（它自身零依赖），保持这层单向可以避免循环引用：
 *   i18n ← state ← 其余所有模块
 *
 * 文案全部走 `t()`，切换语言后**重新调用一次取值**即可 —— 所以这里的
 * 表用 Proxy 包了一层，不是静态对象。用 Proxy 而不是函数，是为了让
 * `ACTION_LABEL[k]` 这种现有写法一行都不用改。
 */
import { t, tOr } from "./i18n/index.js";

/** 界面文案表。查不到时回退到原始枚举值，绝不显示空白。 */
export const ACTION_LABEL = new Proxy({}, {
  get: (_, k) => (typeof k === "string" ? tOr("action." + k, k) : undefined)
});

export const STATUS_LABEL = new Proxy({}, {
  get: (_, k) => (typeof k === "string" ? tOr("status." + k, k) : undefined)
});

/** 状态分组：决定配色与筛选归类（纯数据，不翻译） */
export const GROUP = {
  ok: ["written", "written_keep_local", "dumped"],
  skip: ["skipped_existing", "skip", "skip_instrumental"],
  soft: ["no_lyrics", "timeout", "written_mono"],
  bad: ["lang_mismatch", "validate_failed", "write_failed", "error"]
};

export function tone(status) {
  if (GROUP.ok.includes(status)) return "ok";
  if (GROUP.bad.includes(status)) return "bad";
  return "warn";
}

/**
 * 翻译后端。**必须用函数**而不是常量数组 —— 模块加载时还没确定语言
 * （`initLang()` 在 boot() 里才跑），常量会把中文烤死在里面。
 */
export function backends() {
  return ["source", "remote", "none"].map(v => ([
    v, t("backend." + v), t("backend." + v + ".hint")
  ]));
}

export const FILTER_KEYS = ["all", "written", "written_mono",
                            "no_lyrics", "timeout", "lang_mismatch", "failed"];

/** 同上：惰性求值，切语言后重新调一次就行 */
export function filters() {
  return FILTER_KEYS.map(k => [k, t("filter." + k)]);
}


const TOKEN = (window.__FNLRC_TOKEN__ || "").trim();

export const S = {
  /** 服务端在回环访问时注入；否则由令牌闸门填入并持久化 */
  token: TOKEN || localStorage.getItem("fnlrc_token") || "",
  root: "",
  summary: null,
  preview: null,
  jobId: "",
  results: [],
  stats: {},
  config: {},
  watchRunning: false,
  filter: "all",
  /** 当前 Tab 序号（0..4），M5 起代替原来的 scenes */
  tab: 0
};
