/**
 * 装配与启动。各模块只导出 init 函数，副作用集中在这里，方便看清启动顺序。
 */
import { S } from "./state.js";
import { $ } from "./core.js";
import { initSnap, initPick, initPreview, initReport, go } from "./screens.js";
import { initPanels, initGate } from "./panels.js";

function boot() {
  initSnap();
  initPick();
  initPreview();
  initReport();
  initPanels();
  initGate();

  $("#jumpReport").addEventListener("click", () => go(4));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}

export { S };
