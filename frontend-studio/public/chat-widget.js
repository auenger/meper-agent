/*!
 * AgentFlowChat embed loader —— 把 frontend-client 以 iframe 形态嵌入第三方站点。
 * 纯原生 JS（不进 React 构建、不引框架），放 public/ 由静态服务原样托管。
 *
 * 形态：右下角浮动启动器（client.png）→ 点击从右侧滑出 drawer。
 * 未登录时先显示登录面板（输入 token），登录后显示聊天。
 * 用户名和退出按钮由 client 内部侧边栏管理（不在 widget 层显示）。
 *
 * 鉴权：握手时 client 发 agentflow:request_config，本脚本回
 * agentflow:config{apiKey, userToken?} 把凭据注入 iframe。
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
    loginPanel: null, loginInput: null, loginButton: null, loginError: null,
    initialized: false, open: false, iframeLoaded: false,
    previousFocus: null, config: null, userName: '', loginMode: false
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
      userToken: String(i.userToken || d.userToken || ''),
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
      '.afc-close:focus-visible,.afc-launcher:focus-visible,.afc-login-btn:focus-visible{outline:3px solid rgba(94,129,255,.35);outline-offset:3px}',
      '.afc-close svg{width:15px;height:15px;stroke:currentColor}',
      '.afc-body{position:absolute;inset:0;background:#fff}',
      '.afc-frame{display:block;width:100%;height:100%;border:0;background:#fff;opacity:0;transition:opacity .2s ease}',
      '.afc-loaded .afc-frame{opacity:1}',
      '.afc-loading{position:absolute;inset:0;display:grid;place-items:center;background:#fff;color:#667085;font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;transition:opacity .2s ease,visibility .2s ease}',
      '.afc-loaded .afc-loading{opacity:0;visibility:hidden}',
      '.afc-spinner{width:28px;height:28px;border:3px solid #e8ebf2;border-top-color:#7168ff;border-radius:50%;animation:afc-spin .75s linear infinite}',
      '.afc-login{position:absolute;z-index:6;inset:0;display:flex;align-items:center;justify-content:center;background:#fff;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}',
      '.afc-login-inner{width:100%;max-width:320px;padding:32px 28px}',
      '.afc-login h3{margin:0 0 8px;font-size:20px;font-weight:700;color:#1e293b}',
      '.afc-login p{margin:0 0 24px;font-size:13px;color:#64748b}',
      '.afc-login-input{width:100%;height:42px;padding:0 14px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:14px;color:#1e293b;outline:none;transition:border-color .15s ease}',
      '.afc-login-input:focus{border-color:#7168ff}',
      '.afc-login-input::placeholder{color:#94a3b8}',
      '.afc-login-btn{width:100%;height:42px;margin-top:16px;border:0;border-radius:8px;background:linear-gradient(135deg,#6366f1,#7168ff);color:#fff;font-size:14px;font-weight:600;cursor:pointer;transition:opacity .15s ease,transform .1s ease}',
      '.afc-login-btn:hover{opacity:.9}','.afc-login-btn:active{transform:scale(.98)}','.afc-login-btn:disabled{opacity:.5;cursor:wait}',
      '.afc-login-error{margin-top:12px;padding:8px 12px;border-radius:6px;background:#fef2f2;color:#ef4444;font-size:12px;display:none}',
      '.afc-login-error.afc-show{display:block}',
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
      '  <div class="afc-login" style="display:none"><div class="afc-login-inner">',
      '    <h3>欢迎</h3><p>请输入您的 Token 开始对话</p>',
      '    <input class="afc-login-input" type="password" placeholder="meper_xxxxxxxx" autocomplete="off" />',
      '    <button class="afc-login-btn" type="button">进入</button>',
      '    <div class="afc-login-error"></div>',
      '  </div></div>',
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
    state.loginPanel = shell.querySelector('.afc-login');
    state.loginInput = shell.querySelector('.afc-login-input');
    state.loginButton = shell.querySelector('.afc-login-btn');
    state.loginError = shell.querySelector('.afc-login-error');
    state.iframe.title = state.config.title;
    shell.querySelector('.afc-logo').src = resolveLogoUrl();

    state.launcher.addEventListener('click', onLauncherClick);
    state.closeButton.addEventListener('click', close);
    state.loginButton.addEventListener('click', onLoginSubmit);
    state.loginInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') onLoginSubmit(); });
    state.iframe.addEventListener('load', function () {
      state.iframeLoaded = true;
      // 延迟隐藏 widget loading——等 iframe 内 React 完全渲染，
      // 避免 widget spinner 和 client Spin 两种加载动画交替出现。
      setTimeout(function () { state.loading.classList.add('afc-loaded'); }, 300);
    });
    document.addEventListener('keydown', onKeyDown, true);

    // 点击面板外部（宿主页其他区域）关闭面板。
    // host 默认 pointer-events:none（不挡宿主页），面板打开时通过 shell 的
    // overlay 拦截点击。overlay 透明、不挡视觉，只在面板打开时启用。
    var overlay = document.createElement('div');
    overlay.className = 'afc-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:1;pointer-events:none;';
    overlay.addEventListener('click', function () { close(); });
    shadow.insertBefore(overlay, shell);
    state.overlay = overlay;
  }

  function loadIframe() { if (!state.iframe.getAttribute('src')) state.iframe.setAttribute('src', state.config.chatUrl); }

  /* ═══ Cookie ═══ */
  function readCookie(name) {
    if (!name || !document.cookie) return '';
    var p = name + '=', parts = document.cookie.split(';');
    for (var i = 0; i < parts.length; i++) { var t = parts[i].trim(); if (t.indexOf(p) === 0) { var v = t.substring(p.length); try { v = decodeURIComponent(v); } catch (e) {} return v; } }
    return '';
  }
  function writeCookie(name, value) { document.cookie = name + '=' + encodeURIComponent(value) + ';path=/;max-age=' + (30 * 24 * 60 * 60) + ';SameSite=Lax'; }
  function deleteCookie(name) { document.cookie = name + '=;path=/;max-age=0'; }

  function resolveUserToken() {
    if (state.config.userToken) return state.config.userToken;
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

  /* ═══ 登录 ═══ */
  function onLauncherClick() {
    var token = resolveUserToken();
    if (token) { open(); } else { showLoginPanel(); }
  }

  function showLoginPanel() {
    state.loginMode = true;
    state.loginError.classList.remove('afc-show');
    state.loginInput.value = '';
    state.shell.classList.add('afc-open');
    if (state.overlay) state.overlay.style.pointerEvents = 'auto';
    state.launcher.setAttribute('aria-expanded', 'true');
    state.loginPanel.style.display = 'flex';
    state.loading.style.display = 'none';
    setTimeout(function () { state.loginInput.focus(); }, 100);
  }

  function hideLoginPanel() {
    state.loginMode = false;
    state.loginPanel.style.display = 'none';
    state.loading.style.display = '';
  }

  function onLoginSubmit() {
    var token = (state.loginInput.value || '').trim();
    if (!token) { showLoginError('请输入 Token'); return; }
    state.loginButton.disabled = true;
    state.loginButton.textContent = '验证中...';
    state.loginError.classList.remove('afc-show');

    fetchUserInfo(token).then(function (info) {
      writeCookie(state.config.tokenCookie, token);
      state.userName = info.name;
      state.loginButton.disabled = false;
      state.loginButton.textContent = '进入';
      hideLoginPanel();
      open();
    }).catch(function (err) {
      state.loginButton.disabled = false;
      state.loginButton.textContent = '进入';
      showLoginError((err && err.message) ? err.message : 'Token 无效或已过期');
    });
  }

  function showLoginError(msg) { state.loginError.textContent = msg; state.loginError.classList.add('afc-show'); }

  function fetchUserInfo(token) {
    var baseUrl = state.config.chatUrl.replace(/\/+$/, '');
    return fetch(baseUrl + '/api/v1/ext/userinfo', {
      method: 'GET',
      headers: { 'Authorization': 'Bearer ' + state.config.apiKey, 'X-User-Token': token }
    }).then(function (resp) {
      if (!resp.ok) {
        return resp.json().then(function (data) {
          throw new Error(
            (data && data.error && data.error.message) ||
            (data && data.detail && data.detail.message) ||
            (data && data.message) || 'Token 验证失败（' + resp.status + '）'
          );
        });
      }
      return resp.json();
    }).then(function (data) { return { name: data.name || '用户' }; });
  }

  function onLogout() {
    deleteCookie(state.config.tokenCookie);
    state.userName = '';
    state.open = false;
    // 重载 iframe 让 client 回到未登录状态
    if (state.iframeLoaded) {
      state.iframeLoaded = false;
      state.loading.classList.remove('afc-loaded');
      state.iframe.removeAttribute('src');
    }
    // 不关闭面板，直接显示登录面板（让用户可以输入新 token）
    showLoginPanel();
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
    var token = resolveUserToken();
    if (!token) { showLoginPanel(); return api; }
    state.previousFocus = document.activeElement;
    state.open = true;
    state.loginMode = false;
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
    if (state.loginMode) {
      state.loginMode = false;
      state.shell.classList.remove('afc-open');
      state.launcher.setAttribute('aria-expanded', 'false');
      hideLoginPanel();
      return api;
    }
    if (!state.open) return api;
    state.open = false;
    state.shell.classList.remove('afc-open');
    state.launcher.setAttribute('aria-expanded', 'false');
    if (state.previousFocus && typeof state.previousFocus.focus === 'function') state.previousFocus.focus();
    emit('close');
    return api;
  }

  function toggle() { return state.loginMode ? close() : (state.open ? close() : open()); }

  function destroy() {
    if (!state.initialized) return;
    document.removeEventListener('keydown', onKeyDown, true);
    window.removeEventListener('message', onMessage);
    if (state.host && state.host.parentNode) state.host.parentNode.removeChild(state.host);
    state.host = state.shadow = state.shell = state.panel = state.launcher = state.iframe = null;
    state.initialized = state.open = state.iframeLoaded = state.loginMode = false;
    state.userName = '';
  }

  function onKeyDown(event) { if ((state.open || state.loginMode) && event.key === 'Escape') close(); }

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
