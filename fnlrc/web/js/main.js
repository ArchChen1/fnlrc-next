/**
 * 装配与启动。各模块只导出 init 函数，副作用集中在这里，方便看清启动顺序。
 *
 * 启动顺序有讲究：
 *   1. 先定语言（`initLang`）—— 后面所有渲染都靠它取文案；
 *   2. 再装各屏 —— 它们**在渲染时**才读 `t()`，所以顺序对了就不会烙死中文；
 *   3. 切语言时只重跑渲染（`rerender`），不刷新页面、不打断正在跑的任务。
 */
import { S } from "./state.js";
import { $ } from "./core.js";
import { t } from "./i18n/index.js";
import { initTabs, initPick, initPreview, initReport, requestTab,
         renderBackends, renderSummary, renderReport, renderFilters,
         bumpStats, renderLog, renderReportSummary, renderProgress } from "./screens.js";
import { initPanels, initLang, setLangChangeHook } from "./panels.js";
import { initWizard } from "./wizard.js";
import { initManual } from "./manual.js";

/**
 * 换语言后重画所有「JS 里拼出来」的部分。
 * 静态节点由 `applyI18n()` 处理（`data-i18n` 属性），这里只管动态渲染的。
 */
function rerender() {
  renderBackends();
  renderProgress();
  if (S.summary) renderSummary(S.summary, S.root);
  if (S.results.length) {
    bumpStats(S.results);
    renderLog();
    renderFilters();
    renderReport();
    renderReportSummary();
  }
  // 几个被 JS 改过文字、又恢复到默认值的按钮
  const scan = $("#doScan");
  if (scan && !scan.disabled) scan.textContent = t("pick.scan");
  const preview = $("#doPreview");
  if (preview && !preview.disabled) preview.textContent = t("summary.preview");
}

function boot() {
  initLang();          // ① 语言优先，后面所有渲染都依赖它

  initTabs();          // ② Tab 先装：恢复上次所在面板、接键盘与点击
  initPick();
  initPreview();
  initReport();
  initPanels();
  initManual();

  setLangChangeHook(rerender);

  // 首次使用走引导（欢迎 → 令牌 → 选库）；已有配置则回落到原来的令牌闸门
  initWizard();

  // 顶栏「报告 ⤓」：与点 Tab 同一条路径，没报告就提示先跑
  $("#jumpReport").addEventListener("click", () => requestTab(4));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}

export { S };
