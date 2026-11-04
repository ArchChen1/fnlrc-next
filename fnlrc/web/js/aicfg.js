/**
 * 「AI 翻译」配置弹窗（M6.x）。
 *
 * 场景：补完页勾选降级链里的「AI 翻译」时，如果服务端还没有一份**可用的**
 * 配置（`/api/config` 的 `remote_configured` 为假），就地弹一个设置框，
 * 让用户填 Key / Base URL / 模型名 —— 版式对齐设置面板里的「AI 翻译」一节。
 *
 * 保存成功后 resolve(true)，由调用方把那一项勾上。**取消则 resolve(false)**，
 * 不让用户以为没配置也能用。
 *
 * 与设置面板共用同一套 `/api/config` 契约：`remote_api_key` 传 `"••••"`
 * 表示不改动已存的 key（服务端认这个占位符），这里只在用户真的填了才发。
 */
import { S } from "./state.js";
import { $, api, toast, openOverlay, closeOverlay } from "./core.js";
import { t } from "./i18n/index.js";

let bound = false;
let resolveCb = null;

function bind() {
  if (bound) return;
  bound = true;
  $("#aiCfgTest").addEventListener("click", doTest);
  $("#aiCfgSave").addEventListener("click", doSave);
  $("#aiCfgCancel").addEventListener("click", () => settle(false));
  // 点遮罩关闭 = 取消
  $("#aiCfg").addEventListener("click", e => { if (e.target === $("#aiCfg")) settle(false); });
  // 填了 Key 就清掉上一次的测试结论，免得误导
  $("#aiCfgKey").addEventListener("input", () => { $("#aiCfgTestResult").textContent = ""; });
}

/** 收尾：关弹窗并回一个「有没有保存成功」。重复调用只生效一次。 */
function settle(ok) {
  closeOverlay("aiCfg");
  const cb = resolveCb; resolveCb = null;
  if (cb) cb(ok);
}

/**
 * 打开弹窗，返回 `Promise<boolean>`：保存成功（或本来就有可用配置）= true，
 * 取消 = false。调用方据此决定是否把「AI 翻译」那一项勾上。
 */
export function openAiConfig() {
  bind();
  return new Promise(resolve => {
    (async () => {
      let cfg;
      try {
        cfg = await api("/api/config");
        S.config = cfg;
      } catch (e) {
        toast(t("settings.read_failed", { err: e.message }));
        resolve(false);
        return;
      }

      // 已有一份可用配置（含环境变量提供的 key）→ 不打扰，直接放行
      if (cfg.remote_configured) {
        resolve(true);
        return;
      }

      resolveCb = resolve;
      // 走到这里说明服务端没有任何可用的 key（环境变量提供的 key 也算「可用」，
      // 所以 env-locked 的情况早在上面的 remote_configured 分支放行了）。
      $("#aiCfgBase").value = cfg.remote_base_url || "https://api.deepseek.com";
      $("#aiCfgModel").value = cfg.remote_model || "deepseek-flash";
      $("#aiCfgKey").value = "";
      $("#aiCfgKey").disabled = false;
      $("#aiCfgKey").placeholder = "sk-…";
      $("#aiCfgTestResult").textContent = "";
      $("#aiCfgState").textContent = t("settings.key_unset");
      openOverlay("aiCfg");
    })();
  });
}

async function doTest() {
  const out = $("#aiCfgTestResult");
  out.textContent = t("settings.testing");
  const body = {
    remote_base_url: $("#aiCfgBase").value.trim(),
    remote_model: $("#aiCfgModel").value.trim()
  };
  const typed = $("#aiCfgKey").value.trim();
  if (typed) body.remote_api_key = typed;
  try {
    const d = await api("/api/test-remote", body);
    out.textContent = d.ok
      ? t("settings.test_ok", { msg: d.message })
      : t("settings.test_bad", { msg: d.message });
  } catch (e) {
    out.textContent = t("settings.test_bad", { msg: e.message });
  }
}

async function doSave() {
  const key = $("#aiCfgKey").value.trim();
  if (!key) { toast(t("aitrans.need_key")); return; }

  const btn = $("#aiCfgSave");
  btn.disabled = true;
  try {
    const patch = {
      remote_base_url: $("#aiCfgBase").value.trim(),
      remote_model: $("#aiCfgModel").value.trim(),
      remote_api_key: key
    };
    S.config = await api("/api/config", patch);
    toast(t("aitrans.saved"));
    settle(true);
  } catch (e) {
    toast(t("settings.save_failed", { err: e.message }));
  } finally {
    btn.disabled = false;
  }
}
