/**
 * 全局状态与文案表。**不依赖任何其它模块** —— 其余模块都 import 它，
 * 保持它零依赖可以避免循环引用。
 */

/** 界面文案集中在这里，为将来 i18n 留口（UI-SPEC §5） */
export const ACTION_LABEL = {
  create: "会新建",
  bilingualize: "会补成双语",
  rebuild: "会重做",
  skip: "会跳过"
};

export const STATUS_LABEL = {
  written: "✓ 双语",
  // 与 written 的区别：译文是按**用户本地已有的时间轴/断句**插进去的，
  // 本地文件的结构没被换掉。用户在意这个区别，所以要能一眼看出来。
  written_keep_local: "✓ 双语·留本地轴",
  dumped: "✓ 预览",
  written_mono: "⚠ 单语",
  skipped_existing: "⊘ 跳过",
  skip: "⊘ 跳过",
  skip_instrumental: "⊘ 纯音乐",
  no_lyrics: "⚠ 无歌词",
  timeout: "⚠ 超时",
  lang_mismatch: "🔴 待人工",
  validate_failed: "🔴 未通过",
  write_failed: "🔴 写入失败",
  error: "🔴 出错"
};

/** 状态分组：决定配色与筛选归类 */
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

/** 翻译后端。第二项是说明，直接显示在预览屏的提示里 */
export const BACKENDS = [
  ["source", "源站译词", "默认。只用网易云自带的中文译词，不调任何后端、完全不出网；源站没有译词就写单语。"],
  ["remote", "我的 API", "用自己的 DeepSeek key 补翻源站没给译文的那些行。会把歌词文本发出去。"],
  ["none", "不翻译", "只修格式与乱码，不生成译文。"]
];

export const FILTERS = [
  ["all", "全部"], ["written", "已写入"], ["written_mono", "单语"],
  ["no_lyrics", "无歌词"], ["timeout", "超时"], ["lang_mismatch", "待人工"], ["failed", "失败"]
];

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
  filter: "all",
  scenes: []
};
