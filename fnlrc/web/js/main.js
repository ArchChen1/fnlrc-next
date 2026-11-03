/**
 * 装配与启动。各模块只导出 init 函数，副作用集中在这里，方便看清启动顺序。
 */
import { S } from "./state.js";
import { $ } from "./core.js";
import { initSnap, initPick, initPreview, initReport, go } from "./screens.js";
import { initPanels } from "./panels.js";
import { initWizard } from "./wizard.js";

function boot() {
  initSnap();
  initPick();
  initPreview();
  initReport();
  initPanels();
  // 首次使用走引导（欢迎 → 令牌 → 选库）；已有配置则回落到原来的令牌闸门
  initWizard();

  $("#jumpReport").addEventListener("click", () => go(4));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}

export { S };
