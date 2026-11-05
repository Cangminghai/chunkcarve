/* core.js — 全局状态 / 常量 / 工具 / 错误提示（最先加载） */

'use strict';

/* ============================================================
   错误提示：任何异常都直接显示在界面上，不要只躺在控制台里
   ============================================================ */
function showErr(msg, state = 'error') {
  const el = document.getElementById('errbar');
  if (!el) return;
  el.textContent = String(msg);
  el.dataset.state = state;
  el.setAttribute('role', state === 'error' ? 'alert' : 'status');
  el.style.display = 'block';
}
window.addEventListener('error', e => {
  showErr(`${e.message}  @${String(e.filename || '').split('/').pop()}:${e.lineno}`);
});
window.addEventListener('unhandledrejection', e => {
  const r = e.reason;
  showErr('异步失败: ' + ((r && r.message) || r));
});

/* ============================================================
   状态
   ============================================================ */
const S = {
  gx: -16, gz: 16,          // 视口中心（格坐标）
  span: 256,                // 视口宽度（格）
  layer: 'terrain',
  view: '2d',
  step: 'selection',
  showGrid: true,
  sel: null,                // {cx0, cz0, cx1, cz1} 含端
  box: null,                // 方块级切片 {x0, z0, x1, z1} 含端（优先于 sel）
  info: null,
  y0: null,                 // 由选区地表高度自动确定
  y1: null,
  showDetail: true,         // （已废弃：非完整方块始终渲染）
  includeDecorations: true,
  tileImg: null,
  tileRange: null,
  loading: 0,
  hover: { x: 0, z: 0 },
};

const CH = 16;
const MIN_SPAN = 24;
const MAX_SPAN = 4096;
const AUTO_HALF = 3;        // 2.5D 无选区时默认取视野中心 ±3 区块（6×6=36 区块）

/* 统一画布区主题配色 (配合 v4 Workbench Light 规范) */
const CANVAS_THEME = {
  bg2d: '#eceff3',
  grid2d: 'rgba(15, 23, 42, 0.12)',
  gridText: 'rgba(71, 85, 105, 0.75)',
  spawnStroke: '#16a34a',
  selFill: 'rgba(37, 99, 235, 0.18)',
  selStroke: '#2563eb',
  boxFill: 'rgba(37, 99, 235, 0.14)',
  boxStroke: '#2563eb',
};

const cv2d = document.getElementById('cv2d');
const ctx = cv2d.getContext('2d');
let dpr = Math.min(window.devicePixelRatio || 1, 2);

const $ = id => document.getElementById(id);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

// 带重试的 fetch：本地服务重启期间的瞬断自动恢复（3 次，250ms 起步退避）
async function fetchRetry(url, tries = 3) {
  let err;
  for (let i = 0; i < tries; i++) {
    try {
      return await fetch(url);
    } catch (e) {
      err = e;
      if (i < tries - 1) await new Promise(r => setTimeout(r, 250 * (i + 1)));
    }
  }
  throw err;
}
const floorDiv = (a, b) => Math.floor(a / b);

let firstLoad = true;
let loadingTimeoutTimer = null;
const LOADING_TIMEOUT_MS = 15000; // 15秒超时提醒（10~20秒区间）

function dismissLoading() {
  S.loading = 0;
  const el = $('loading');
  if (el) el.classList.add('hide');
  const hint = $('loadTimeoutHint');
  if (hint) hint.style.display = 'none';
  if (loadingTimeoutTimer) {
    clearTimeout(loadingTimeoutTimer);
    loadingTimeoutTimer = null;
  }
}

function setLoading(on, txt) {
  S.loading += on ? 1 : -1;
  if (S.loading < 0) S.loading = 0;
  const el = $('loading');
  const hint = $('loadTimeoutHint');
  if (S.loading > 0) {
    el.classList.remove('hide');
    el.classList.toggle('mini', !firstLoad);
    if (txt) $('loadTxt').textContent = txt;
    if (!loadingTimeoutTimer) {
      if (hint) hint.style.display = 'none';
      loadingTimeoutTimer = setTimeout(() => {
        if (S.loading > 0) {
          if (hint) hint.style.display = 'flex';
        }
      }, LOADING_TIMEOUT_MS);
    }
  } else {
    S.loading = 0;
    el.classList.add('hide');
    firstLoad = false;
    if (hint) hint.style.display = 'none';
    if (loadingTimeoutTimer) {
      clearTimeout(loadingTimeoutTimer);
      loadingTimeoutTimer = null;
    }
  }
}

/* ============================================================
   坐标换算
   ============================================================ */
function viewSpanH() { return S.span * cv2d.height / cv2d.width; }

function worldToScreen(gx, gz) {
  const zoom = cv2d.width / S.span;
  return {
    x: (gx - (S.gx - S.span / 2)) * zoom,
    y: (gz - (S.gz - viewSpanH() / 2)) * zoom,
  };
}
function screenToWorld(sx, sy) {
  const zoom = cv2d.width / S.span;
  return {
    x: (S.gx - S.span / 2) + sx / zoom,
    z: (S.gz - viewSpanH() / 2) + sy / zoom,
  };
}
function evWorld(e) { return screenToWorld(e.offsetX * dpr, e.offsetY * dpr); }
