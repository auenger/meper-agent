/*!
 * AgentFlowChat embed loader —— 把 frontend-client 以 iframe 形态嵌入第三方站点。
 * 纯原生 JS（不进 React 构建、不引框架），放 public/ 由静态服务原样托管。
 *
 * 形态：右下角浮动启动器（client.png）→ 点击从右侧滑出 drawer。
 * 用户身份通过 cookie（mep-access-token）自动注入，无需登录面板。
 * 用户名和退出按钮由 client 内部侧边栏管理（不在 widget 层显示）。
 *
 * 鉴权：握手时 client 发 agentflow:request_config，本脚本回
 * agentflow:config{apiKey, userToken?} 把凭据注入 iframe。
 * token 由接入方页面写入 cookie，widget 只读取。
 *
 * 用法（自动初始化）：
 *   <script src="chat-widget.js"
 *           data-chat-url="http://localhost:3001"
 *           data-api-key="af_live_xxx"
 *           data-title="AI 助手"
 *           data-width="680px"></script>
 *
 * API：window.AgentFlowChat = { init, open, close, toggle, destroy, isOpen, setToken, logout }
 */
(function (window, document) {
  'use strict';

  var API_KEY = 'AgentFlowChat';
  var HOST_ID = 'agent-flow-chat-host';
  var currentScript = document.currentScript;

  if (window[API_KEY] && window[API_KEY].__afc) return;

  var state = {
    host: null, shadow: null, shell: null, panel: null,
    launcher: null, iframe: null, loading: null, closeButton: null,
    initialized: false, open: false, iframeLoaded: false,
    previousFocus: null, config: null, userName: '',
    loadedToken: '' // iframe 初始化时使用的 token，用于检测身份变化
  };

  function bool(v, f) { return v === undefined || v === null || v === '' ? f : !/^(false|0|no|off)$/i.test(String(v)); }
  function cssLength(v, f) { if (typeof v === 'number' && isFinite(v)) return v + 'px'; var t = String(v || '').trim(); return /^\d+(\.\d+)?(px|rem|em|vw|vh|%)$/.test(t) ? t : f; }
  function intBetween(v, f, min, max) { var n = parseInt(v, 10); return isFinite(n) ? Math.min(max, Math.max(min, n)) : f; }
  function scriptDataset() { return currentScript && currentScript.dataset ? currentScript.dataset : {}; }
  function resolveOrigin() { try { if (currentScript && currentScript.src) return new URL(currentScript.src).origin; } catch (e) {} return window.location.origin; }
  function resolveLogoUrl() { try { return new URL('/client.png', resolveOrigin()).href; } catch (e) { return '/client.png'; } }

  function normalizeConfig(options) {
    var d = scriptDataset(), i = options || {}, z = i.zIndex !== undefined ? i.zIndex : d.zIndex;
    return {
      chatUrl: String(i.chatUrl || d.chatUrl || resolveOrigin()),
      title: String(i.title || d.title || 'AI 助手'),
      width: cssLength(i.width || d.width, '680px'),
      right: cssLength(i.right || d.right, '24px'),
      bottom: cssLength(i.bottom || d.bottom, '24px'),
      zIndex: intBetween(z, 2147483000, 1, 2147483646),
      openOnLoad: bool(i.openOnLoad !== undefined ? i.openOnLoad : d.openOnLoad, false),
      apiKey: String(i.apiKey || d.apiKey || ''),
      tokenCookie: String(i.tokenCookie || d.tokenCookie || 'mep-access-token')
    };
  }

  function emit(name) {
    var ev; try { ev = new CustomEvent('agent-flow-chat:' + name, { detail: { chatUrl: state.config.chatUrl } }); }
    catch (e) { ev = document.createEvent('CustomEvent'); ev.initCustomEvent('agent-flow-chat:' + name, false, false, { chatUrl: state.config.chatUrl }); }
    window.dispatchEvent(ev);
  }

  function buildHost() {
    var host = document.createElement('div');
    host.id = HOST_ID; host.setAttribute('data-agent-flow-chat', '');
    host.style.cssText = 'position:fixed;inset:0;width:0;height:0;z-index:' + state.config.zIndex + ';pointer-events:none';

    var shadow = host.attachShadow ? host.attachShadow({ mode: 'open' }) : host;
    var style = document.createElement('style');
    style.textContent = [
      ':host{all:initial}','*,*::before,*::after{box-sizing:border-box}',
      '.afc-panel{position:fixed;z-index:2;top:0;right:0;width:min(var(--afc-width),100vw);height:100vh;height:100dvh;background:#fff;box-shadow:-18px 0 48px rgba(15,23,42,.18);transform:translate3d(102%,0,0);visibility:hidden;pointer-events:none;transition:transform .34s cubic-bezier(.22,1,.36,1),visibility .34s;overflow:hidden;border-left:1px solid rgba(148,163,184,.22)}',
      '.afc-open .afc-panel{transform:translate3d(0,0,0);visibility:visible;pointer-events:auto}',
      '.afc-close{position:absolute;z-index:5;top:max(15px,env(safe-area-inset-top));right:max(15px,env(safe-area-inset-right));width:30px;height:30px;padding:0;border:1px solid rgba(148,163,184,.18);border-radius:8px;background:rgba(255,255,255,.52);color:rgba(63,73,91,.68);display:grid;place-items:center;cursor:pointer;box-shadow:0 4px 12px rgba(15,23,42,.08);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);transition:background .16s ease,color .16s ease,transform .16s ease}',
      '.afc-close:hover{background:rgba(255,255,255,.82);color:#1f2937;transform:scale(1.06)}',
      '.afc-close:active{transform:scale(.94)}',
      '.afc-close:focus-visible,.afc-launcher:focus-visible{outline:3px solid rgba(94,129,255,.35);outline-offset:3px}',
      '.afc-close svg{width:15px;height:15px;stroke:currentColor}',
      '.afc-body{position:absolute;inset:0;background:#fff}',
      '.afc-frame{display:block;width:100%;height:100%;border:0;background:#fff;opacity:0;transition:opacity .2s ease}',
      '.afc-loaded .afc-frame{opacity:1}',
      '.afc-loading{position:absolute;inset:0;display:grid;place-items:center;background:#fff;color:#667085;font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;transition:opacity .2s ease,visibility .2s ease}',
      '.afc-loaded .afc-loading{opacity:0;visibility:hidden}',
      '.afc-spinner{width:28px;height:28px;border:3px solid #e8ebf2;border-top-color:#7168ff;border-radius:50%;animation:afc-spin .75s linear infinite}',
      '.afc-launcher{position:fixed;z-index:3;right:var(--afc-right);bottom:var(--afc-bottom);width:60px;height:60px;padding:0;border:0;border-radius:20px;background:linear-gradient(145deg,#f7f5ff 5%,#e8f5ff 48%,#fff0fb 100%);box-shadow:0 14px 32px rgba(75,85,150,.22),0 3px 10px rgba(91,106,178,.16),inset 0 0 0 1px rgba(255,255,255,.82);cursor:pointer;display:grid;place-items:center;pointer-events:auto;transition:transform .2s ease,box-shadow .2s ease,opacity .2s ease,visibility .2s ease;animation:afc-float 4.4s ease-in-out infinite}',
      '.afc-launcher::before{content:"";position:absolute;inset:-5px;border-radius:24px;border:1px solid rgba(114,111,255,.18);opacity:.65;animation:afc-pulse 2.8s ease-out infinite}',
      '.afc-launcher:hover{transform:translateY(-3px) scale(1.04);box-shadow:0 18px 38px rgba(75,85,150,.28),0 5px 14px rgba(91,106,178,.2),inset 0 0 0 1px rgba(255,255,255,.9)}',
      '.afc-open .afc-launcher{opacity:0;visibility:hidden;pointer-events:none;transform:translateY(12px) scale(.86)}',
      '.afc-logo{width:42px;height:42px;object-fit:contain;display:block;filter:drop-shadow(0 4px 6px rgba(93,76,220,.22));animation:afc-breathe 3.2s ease-in-out infinite;pointer-events:none;user-select:none;-webkit-user-drag:none}',
      '@keyframes afc-spin{to{transform:rotate(360deg)}}','@keyframes afc-float{0%,100%{margin-bottom:0}50%{margin-bottom:5px}}',
      '@keyframes afc-pulse{0%{transform:scale(.9);opacity:.7}75%,100%{transform:scale(1.18);opacity:0}}',
      '@keyframes afc-breathe{0%,100%{transform:scale(1)}50%{transform:scale(1.08)}}',
      '@media(max-width:640px){.afc-panel{width:100vw;border-left:0}.afc-launcher{right:max(16px,env(safe-area-inset-right));bottom:max(16px,env(safe-area-inset-bottom))}}',
      '@media(prefers-reduced-motion:reduce){.afc-panel,.afc-launcher,.afc-loading,.afc-frame{transition:none}.afc-launcher,.afc-launcher::before,.afc-logo,.afc-spinner{animation:none}}'
    ].join('');

    var shell = document.createElement('div');
    shell.className = 'afc-shell';
    shell.style.setProperty('--afc-width', state.config.width);
    shell.style.setProperty('--afc-right', state.config.right);
    shell.style.setProperty('--afc-bottom', state.config.bottom);
    shell.innerHTML = [
      '<aside class="afc-panel" role="dialog" aria-label="对话窗口">',
      '  <button class="afc-close" type="button" aria-label="关闭"><svg viewBox="0 0 24 24" fill="none" stroke-width="1.9" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg></button>',
      '  <div class="afc-body">',
      '    <iframe class="afc-frame" title="AI 对话" allow="clipboard-read; clipboard-write; microphone" referrerpolicy="strict-origin-when-cross-origin"></iframe>',
      '    <div class="afc-loading"><div><div class="afc-spinner"></div></div></div>',
      '  </div>',
      '</aside>',
      '<button class="afc-launcher" type="button" aria-label="打开 AI 助手" aria-expanded="false"><img class="afc-logo" alt="" /></button>'
    ].join('');

    shadow.appendChild(style); shadow.appendChild(shell);
    (document.body || document.documentElement).appendChild(host);

    state.host = host; state.shadow = shadow; state.shell = shell;
    state.panel = shell.querySelector('.afc-panel');
    state.launcher = shell.querySelector('.afc-launcher');
    state.iframe = shell.querySelector('.afc-frame');
    state.loading = shell.querySelector('.afc-body');
    state.closeButton = shell.querySelector('.afc-close');
    state.iframe.title = state.config.title;
    shell.querySelector('.afc-logo').src = resolveLogoUrl();

    state.launcher.addEventListener('click', open);
    state.closeButton.addEventListener('click', close);
    state.iframe.addEventListener('load', function () {
      state.iframeLoaded = true;
      // 延迟隐藏 widget loading——等 iframe 内 React 完全渲染，
      // 避免 widget spinner 和 client Spin 两种加载动画交替出现。
      setTimeout(function () { state.loading.classList.add('afc-loaded'); }, 300);
    });
    document.addEventListener('keydown', onKeyDown, true);

    // 点击面板外部（宿主页其他区域）关闭面板。
    var overlay = document.createElement('div');
    overlay.className = 'afc-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:1;pointer-events:none;';
    overlay.addEventListener('click', function () { close(); });
    shadow.insertBefore(overlay, shell);
    state.overlay = overlay;
  }

  function loadIframe() {
    var src = state.iframe.getAttribute('src');
    if (!src || src === 'about:blank') state.iframe.setAttribute('src', state.config.chatUrl);
  }

  /* ═══ Cookie ═══ */
  function readCookie(name) {
    if (!name || !document.cookie) return '';
    var p = name + '=', parts = document.cookie.split(';');
    for (var i = 0; i < parts.length; i++) { var t = parts[i].trim(); if (t.indexOf(p) === 0) { var v = t.substring(p.length); try { v = decodeURIComponent(v); } catch (e) {} return v; } }
    return '';
  }
  function writeCookie(name, value) { document.cookie = name + '=' + encodeURIComponent(value) + ';path=/;max-age=' + (30 * 24 * 60 * 60) + ';SameSite=Lax'; }
  function deleteCookie(name) { document.cookie = name + '=;path=/;max-age=0;SameSite=Lax'; }

  function resolveUserToken() {
    var primary = readCookie(state.config.tokenCookie);
    if (primary) return primary;
    var underscored = state.config.tokenCookie.replace(/-/g, '_');
    return underscored !== state.config.tokenCookie ? readCookie(underscored) : '';
  }

  /* ═══ client 握手 ═══ */
  function onMessage(e) {
    if (!state.iframe || e.source !== state.iframe.contentWindow) return;
    var data = e.data || {};
    if (data.type === 'agentflow:request_config') sendConfig();
    // client 退出登录时通知 widget 清 cookie + 关闭
    if (data.type === 'agentflow:logout') onLogout();
    // client 验证 token 失败（无效/过期/未绑定）→ client 内部已显示错误页
    // widget 不再删 cookie 和关闭——让用户看到 client 里的具体错误提示
    // （cookie 由接入方页面管理，widget 不主动清理）
    if (data.type === 'agentflow:token_invalid') {
      // no-op: 错误提示由 client iframe 内部处理
    }
  }

  function sendConfig() {
    if (!state.iframe || !state.config.apiKey) return;
    var userToken = resolveUserToken();
    var targetOrigin; try { targetOrigin = new URL(state.config.chatUrl).origin; } catch (err) { targetOrigin = '*'; }
    state.iframe.contentWindow.postMessage(
      { type: 'agentflow:config', apiKey: state.config.apiKey, userToken: userToken || undefined },
      targetOrigin
    );
  }

  function onLogout() {
    deleteCookie(state.config.tokenCookie);
    var underscored = state.config.tokenCookie.replace(/-/g, '_');
    if (underscored !== state.config.tokenCookie) deleteCookie(underscored);
    state.userName = '';
    state.loadedToken = '';
    state.open = false;
    // 强制重载 iframe
    if (state.iframeLoaded) {
      state.iframeLoaded = false;
      state.loading.classList.remove('afc-loaded');
      state.iframe.src = 'about:blank';
    }
    close();
  }

  /* ═══ 开关 ═══ */
  function init(options) {
    if (state.initialized) return api;
    state.config = normalizeConfig(options);
    buildHost();
    state.initialized = true;
    window.addEventListener('message', onMessage);
    if (state.config.openOnLoad) open();
    return api;
  }

  function open() {
    if (!state.initialized) init();
    if (state.open) return api;
    // 检测身份变化：cookie 里的 token 变了 → 强制重载 iframe，
    // 让 client 用新 token 重新初始化（session 列表等状态全部刷新）。
    var currentToken = resolveUserToken();
    if (state.loadedToken && currentToken !== state.loadedToken) {
      state.loadedToken = currentToken;
      if (state.iframeLoaded) {
        state.iframeLoaded = false;
        state.loading.classList.remove('afc-loaded');
        state.iframe.src = 'about:blank';
      }
    } else if (currentToken) {
      state.loadedToken = currentToken;
    }
    state.previousFocus = document.activeElement;
    state.open = true;
    loadIframe();
    if (state.iframeLoaded) sendConfig();
    state.shell.classList.add('afc-open');
    if (state.overlay) state.overlay.style.pointerEvents = 'auto';
    state.launcher.setAttribute('aria-expanded', 'true');
    setTimeout(function () { if (state.open && state.closeButton) state.closeButton.focus(); }, 30);
    emit('open');
    return api;
  }

  function close() {
    if (!state.initialized) return api;
    if (state.overlay) state.overlay.style.pointerEvents = 'none';
    if (!state.open) return api;
    state.open = false;
    state.shell.classList.remove('afc-open');
    state.launcher.setAttribute('aria-expanded', 'false');
    if (state.previousFocus && typeof state.previousFocus.focus === 'function') state.previousFocus.focus();
    emit('close');
    return api;
  }

  function toggle() { return state.open ? close() : open(); }

  function destroy() {
    if (!state.initialized) return;
    document.removeEventListener('keydown', onKeyDown, true);
    window.removeEventListener('message', onMessage);
    if (state.host && state.host.parentNode) state.host.parentNode.removeChild(state.host);
    state.host = state.shadow = state.shell = state.panel = state.launcher = state.iframe = null;
    state.initialized = state.open = state.iframeLoaded = false;
    state.userName = '';
    state.loadedToken = '';
  }

  function onKeyDown(event) { if (state.open && event.key === 'Escape') close(); }

  var api = {
    __afc: true, init: init, open: open, close: close, toggle: toggle, destroy: destroy,
    isOpen: function () { return state.open; },
    setToken: function (token) { if (token) writeCookie(state.config.tokenCookie, token); else deleteCookie(state.config.tokenCookie); state.userName = ''; },
    logout: function () { onLogout(); }
  };

  window[API_KEY] = api;

  function autoInit() { var d = scriptDataset(); if (bool(d.autoInit, true)) init(); }
  if (document.readyState === 'loading' && !document.body) document.addEventListener('DOMContentLoaded', autoInit, { once: true });
  else autoInit();
})(window, document);
