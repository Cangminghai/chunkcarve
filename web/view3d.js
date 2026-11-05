/* view3d.js — 2.5D：Worker 客户端、相机、红色切割框 */

/* ============================================================
   2.5D（deepslate 渲染——游戏原生模型 + 贴图，正确面剔除）
   关键修复：getBlockFlags 必须返回 opaque 标志，deepslate 靠它做面剔除。
   ============================================================ */
/* ---------- deepslate 渲染 Worker（后台线程）客户端 ---------- */
let worker = null, workerReady = false;
let lastBuiltMeta = null;
let fitKey = '';
const cam = {
  theta: Math.PI * 0.28,
  phi: Math.PI * 0.30,
  dist: 420,
  target: new THREE.Vector3(0, 0, 0),
};
let camera = null;   // 仅用于 raycast 数学（fov/aspect 与 deepslate 投影一致）

function setLoadText(txt) {
  const el = $('loading');
  if (txt && el && !el.classList.contains('hide')) $('loadTxt').textContent = txt;
}

let current3dGeneration = 0;
let isBuilding3d = false;

function set3dLoading(on, txt = '构建 2.5D') {
  if (on) {
    if (!isBuilding3d) {
      isBuilding3d = true;
      setLoading(true, txt);
    } else if (txt) {
      setLoadText(txt);
    }
  } else {
    if (isBuilding3d) {
      isBuilding3d = false;
      setLoading(false);
    }
  }
}

function next3dGeneration() {
  resetSlicePreview(false);
  current3dGeneration++;
  lastBuiltMeta = null;
  fitKey = '';
  if ($('hTri')) $('hTri').textContent = '—';
  set3dLoading(false);
  if (worker) {
    worker.postMessage({ t: 'reset', generation: current3dGeneration });
  }
  return current3dGeneration;
}

function init3d() {
  if (worker) return;
  camera = new THREE.PerspectiveCamera(70, 1, 0.1, 1000000);
  const wrap = $('wrap3d');
  const cv = $('cv3d');
  cv.width = Math.max(1, Math.round((wrap.clientWidth || window.innerWidth) * dpr));
  cv.height = Math.max(1, Math.round((wrap.clientHeight || window.innerHeight) * dpr));
  const off = cv.transferControlToOffscreen();
  worker = new Worker('/worker3d.js');
  worker.onmessage = e => {
    const m = e.data;
    if (m.generation !== undefined && m.generation !== current3dGeneration) {
      return;
    }
    if (m.t === 'ready') { workerReady = true; invalidate3d(); }
    else if (m.t === 'progress') { setLoadText(m.text); }
    else if (m.t === 'built') { onBuilt(m.meta); }
    else if (m.t === 'slices') { acceptSlicePreview(m); }
    else if (m.t === 'slice_error') { failSlicePreview(m); }
    else if (m.t === 'slice_picked') { acceptSlicePick(m); }
    else if (m.t === 'error') { onBuildError(m.msg); }
  };
  // 渲染线程崩溃（OOM / 脚本错误）兜底：给出明确提示而不是黑屏
  worker.onerror = ev => {
    console.error('渲染线程崩溃:', ev.message || ev);
    workerReady = false;
    onWorkerCrash(ev.message || '未知错误');
    ev.preventDefault && ev.preventDefault();
  };
  worker.onmessageerror = () => onWorkerCrash('消息解码失败');
  worker.postMessage({ t: 'init', canvas: off, w: cv.width, h: cv.height, generation: current3dGeneration }, [off]);
  resize3d();
  animate3d();
}

function onWorkerCrash(msg) {
  workerReady = false;
  $('empty3d').style.display = 'flex';
  $('empty3d').querySelector('span').textContent =
    `渲染线程崩溃：${msg}。请刷新页面（Ctrl+Shift+R），然后缩小选区`;
  set3dLoading(false);
}

function resize3d() {
  const wrap = $('wrap3d');
  const w = wrap.clientWidth || window.innerWidth;
  const h = wrap.clientHeight || window.innerHeight;
  const pw = Math.max(1, Math.round(w * dpr));
  const ph = Math.max(1, Math.round(h * dpr));
  const ov = $('ov3d');
  ov.width = pw;
  ov.height = ph;
  if (camera) {
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  if (worker && workerReady) worker.postMessage({ t: 'resize', w: pw, h: ph });
  invalidate3d();
}

function updateCamera() {
  if (!camera) return;
  const exploded = slicePreviewBounds();
  // 目标点约束在模型中心 2.5 倍包围半径内：可绕模型自由探索，
  // 但不会越拖越远彻底丢失（配合 R 复位/网格锚点）
  if (lastBuiltMeta) {
    const R = modelRadius * 2.5;
    const cx = exploded ? (exploded.min[0] + exploded.max[0]) / 2 : lastBuiltMeta.w / 2;
    const cz = exploded ? (exploded.min[2] + exploded.max[2]) / 2 : lastBuiltMeta.d / 2;
    const cy = exploded ? (exploded.min[1] + exploded.max[1]) / 2 : (lastBuiltMeta.yMax - lastBuiltMeta.yMin + 1) / 2;
    cam.target.x = clamp(cam.target.x, cx - R, cx + R);
    cam.target.y = clamp(cam.target.y, cy - R, cy + R);
    cam.target.z = clamp(cam.target.z, cz - R, cz + R);
  }
  const r = cam.dist;
  const sp = Math.sin(cam.phi), cp = Math.cos(cam.phi);
  camera.position.set(
    cam.target.x + r * sp * Math.cos(cam.theta),
    cam.target.y + r * cp,
    cam.target.z + r * sp * Math.sin(cam.theta),
  );
  camera.lookAt(cam.target);
  if (S.step === 'slices') {
    const w = $('cv3d').clientWidth, h = $('cv3d').clientHeight;
    const bottom = $('selbar').getBoundingClientRect().height + 24;
    camera.setViewOffset(w, h, 0, (bottom - 72) / 2, w, h);
  } else camera.clearViewOffset();
  camera.updateMatrixWorld();
}

const _viewMat = new Float32Array(16);
// 结构本地原点（世界坐标），红框预览换算用
const structBox = { x0: 0, y0: 0, z0: 0, y1: 0, has: false };

/* ---------- 红色切割预览框与可拖拽手柄 ---------- */
const BOX_HANDLE_DEFS = [
  { id: '-x', axis: 'x', sign: -1, name: 'X小', cursor: 'ew-resize', prop: 'x0' },
  { id: '+x', axis: 'x', sign:  1, name: 'X大', cursor: 'ew-resize', prop: 'x1' },
  { id: '-y', axis: 'y', sign: -1, name: 'Y底', cursor: 'ns-resize', prop: 'y0' },
  { id: '+y', axis: 'y', sign:  1, name: 'Y顶', cursor: 'ns-resize', prop: 'y1' },
  { id: '-z', axis: 'z', sign: -1, name: 'Z小', cursor: 'nesw-resize', prop: 'z0' },
  { id: '+z', axis: 'z', sign:  1, name: 'Z大', cursor: 'nesw-resize', prop: 'z1' },
];

let activeBoxHandles = [];
let hoveredHandle = null;
let activeDrag = null;
let boxRebuildTimer = null;

function triggerBoxRebuild() {
  if (boxRebuildTimer) clearTimeout(boxRebuildTimer);
  boxRebuildTimer = setTimeout(() => {
    if (S.view === '3d') {
      schedule3d(true);
    }
  }, 300);
}

function getBoxBounds() {
  if (!structBox.has || !lastBuiltMeta) return null;
  const get = id => parseInt($(id).value, 10);
  const defX0 = structBox.x0, defX1 = structBox.x0 + lastBuiltMeta.w - 1;
  const defZ0 = structBox.z0, defZ1 = structBox.z0 + lastBuiltMeta.d - 1;
  const defY0 = structBox.y0, defY1 = structBox.y1;

  const x0v = get('x0In'), x1v = get('x1In');
  const z0v = get('z0In'), z1v = get('z1In');
  const y0v = parseInt($('yBotIn').value, 10);
  const y1v = parseInt($('yTopIn').value, 10);

  const x0 = Number.isFinite(x0v) ? x0v : defX0;
  const x1 = Number.isFinite(x1v) ? x1v : defX1;
  const z0 = Number.isFinite(z0v) ? z0v : defZ0;
  const z1 = Number.isFinite(z1v) ? z1v : defZ1;
  const y0 = Number.isFinite(y0v) ? y0v : (S.y0 !== null ? S.y0 : defY0);
  const y1 = Number.isFinite(y1v) ? y1v : (S.y1 !== null ? S.y1 : defY1);

  const bx0 = Math.min(x0, x1);
  const bx1 = Math.max(x0, x1);
  const by0 = Math.min(y0, y1);
  const by1 = Math.max(y0, y1);
  const bz0 = Math.min(z0, z1);
  const bz1 = Math.max(z0, z1);

  // 允许自由向外扩（最大跨度 256 格）以及向内缩
  const MAX_SPAN = 256;
  return {
    bx0, bx1, by0, by1, bz0, bz1,
    limitX0: bx1 - MAX_SPAN,
    limitX1: bx0 + MAX_SPAN,
    limitZ0: bz1 - MAX_SPAN,
    limitZ1: bz0 + MAX_SPAN,
    limitY0: S.info?.minY ?? -64, limitY1: S.info?.maxY ?? 319,
  };
}

const _ovV = new THREE.Vector3();
const _ovV2 = new THREE.Vector3();
const _ovClipA = new THREE.Vector4();
const _ovClipB = new THREE.Vector4();
const _ovViewProjection = new THREE.Matrix4();

function clipOverlayEdge(a, b, matrix) {
  _ovClipA.set(a[0], a[1], a[2], 1).applyMatrix4(matrix);
  _ovClipB.set(b[0], b[1], b[2], 1).applyMatrix4(matrix);

  let t0 = 0, t1 = 1;
  const planes = [
    v => v.x + v.w, v => v.w - v.x,
    v => v.y + v.w, v => v.w - v.y,
    v => v.z + v.w, v => v.w - v.z,
  ];
  for (const distance of planes) {
    const da = distance(_ovClipA), db = distance(_ovClipB);
    if (da < 0 && db < 0) return null;
    if ((da < 0) !== (db < 0)) {
      const t = da / (da - db);
      if (da < 0) t0 = Math.max(t0, t);
      else t1 = Math.min(t1, t);
      if (t0 > t1) return null;
    }
  }

  const dx = _ovClipB.x - _ovClipA.x;
  const dy = _ovClipB.y - _ovClipA.y;
  const dz = _ovClipB.z - _ovClipA.z;
  const dw = _ovClipB.w - _ovClipA.w;
  const project = t => {
    const w = _ovClipA.w + dw * t;
    if (w <= 0) return null;
    const iw = 1 / w;
    return [(_ovClipA.x + dx * t) * iw, (_ovClipA.y + dy * t) * iw, (_ovClipA.z + dz * t) * iw];
  };
  const p0 = project(t0), p1 = project(t1);
  return p0 && p1 ? [p0, p1] : null;
}

function computeBoxHandles() {
  activeBoxHandles = [];
  if (S.step === 'slices') return activeBoxHandles;
  if (!structBox.has || !camera || !lastBuiltMeta) return activeBoxHandles;

  const b = getBoxBounds();
  if (!b) return activeBoxHandles;

  const { bx0, bx1, by0, by1, bz0, bz1 } = b;
  const el = $('cv3d');
  if (!el) return activeBoxHandles;
  const wCss = el.clientWidth || window.innerWidth;
  const hCss = el.clientHeight || window.innerHeight;

  const cx = (bx0 + bx1) / 2;
  const cy = (by0 + by1) / 2;
  const cz = (bz0 + bz1) / 2;

  const handleCenters = {
    '-x': { p: [bx0, cy, cz], u: [1, 0, 0] },
    '+x': { p: [bx1, cy, cz], u: [1, 0, 0] },
    '-y': { p: [cx, by0, cz], u: [0, 1, 0] },
    '+y': { p: [cx, by1, cz], u: [0, 1, 0] },
    '-z': { p: [cx, cy, bz0], u: [0, 0, 1] },
    '+z': { p: [cx, cy, bz1], u: [0, 0, 1] },
  };

  for (const def of BOX_HANDLE_DEFS) {
    const hc = handleCenters[def.id];
    if (!hc) continue;

    _ovV.set(hc.p[0] - structBox.x0, hc.p[1] - structBox.y0, hc.p[2] - structBox.z0).project(camera);
    if (_ovV.z < -1 || _ovV.z > 1) continue;

    const sx = (_ovV.x + 1) / 2 * wCss;
    const sy = (1 - _ovV.y) / 2 * hCss;
    const px = sx * dpr;
    const py = sy * dpr;

    _ovV2.set(hc.p[0] + hc.u[0] - structBox.x0, hc.p[1] + hc.u[1] - structBox.y0, hc.p[2] + hc.u[2] - structBox.z0).project(camera);
    const s2x = (_ovV2.x + 1) / 2 * wCss;
    const s2y = (1 - _ovV2.y) / 2 * hCss;

    const axisDx = s2x - sx;
    const axisDy = s2y - sy;
    const axisLenSq = axisDx * axisDx + axisDy * axisDy;

    activeBoxHandles.push({
      ...def,
      pos3d: hc.p,
      sx, sy,
      px, py,
      axisDx, axisDy,
      axisLenSq,
      z: _ovV.z,
    });
  }

  activeBoxHandles.sort((a, b) => b.z - a.z);
  return activeBoxHandles;
}

function drawOverlay() {
  const ov = $('ov3d');
  const ctx = ov.getContext('2d');
  ctx.clearRect(0, 0, ov.width, ov.height);
  if (!structBox.has || !camera || !lastBuiltMeta) return;

  if (S.step === 'slices') {
    drawSliceOverlay(ctx, ov);
    return;
  }

  const b = getBoxBounds();
  if (!b) return;

  const { bx0, bx1, by0, by1, bz0, bz1 } = b;

  // 1. 绘制红框线框
  const pts = [
    [bx0, by0, bz0], [bx1, by0, bz0], [bx1, by0, bz1], [bx0, by0, bz1],
    [bx0, by1, bz0], [bx1, by1, bz0], [bx1, by1, bz1], [bx0, by1, bz1],
  ].map(([x, y, z]) => [x - structBox.x0, y - structBox.y0, z - structBox.z0]);
  const edges = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4],
    [0, 4], [1, 5], [2, 6], [3, 7]];
  _ovViewProjection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  ctx.strokeStyle = '#dc2626';
  ctx.lineWidth = 2 * dpr;
  ctx.beginPath();
  for (const [e1, e2] of edges) {
    const clipped = clipOverlayEdge(pts[e1], pts[e2], _ovViewProjection);
    if (!clipped) continue;
    const [[x0, y0], [x1, y1]] = clipped;
    ctx.moveTo((x0 + 1) / 2 * ov.width, (1 - y0) / 2 * ov.height);
    ctx.lineTo((x1 + 1) / 2 * ov.width, (1 - y1) / 2 * ov.height);
  }
  ctx.stroke();

  // 2. 绘制手柄（v4 白底红描边圆点 10px，hover 放大）
  const handles = computeBoxHandles();
  for (const h of handles) {
    const isHover = (hoveredHandle && hoveredHandle.id === h.id);
    const isDrag = (activeDrag && activeDrag.handle.id === h.id);
    const highlight = isHover || isDrag;
    const R = (highlight ? 7 : 5) * dpr;

    ctx.save();
    ctx.shadowColor = 'rgba(15, 23, 42, 0.28)';
    ctx.shadowBlur = 5 * dpr;
    ctx.shadowOffsetY = 1 * dpr;

    ctx.beginPath();
    ctx.arc(h.px, h.py, R, 0, Math.PI * 2);
    ctx.fillStyle = '#ffffff';
    ctx.fill();
    ctx.strokeStyle = '#dc2626';
    ctx.lineWidth = (highlight ? 2.5 : 2) * dpr;
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(h.px, h.py, R * (highlight ? 0.45 : 0.38), 0, Math.PI * 2);
    ctx.fillStyle = '#dc2626';
    ctx.fill();

    if (highlight) {
      ctx.shadowColor = 'transparent';
      ctx.font = `bold ${11 * dpr}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      const label = h.name;
      const tw = ctx.measureText(label).width;
      const padX = 6 * dpr, padY = 2 * dpr;
      const tx = h.px, ty = h.py - R - 5 * dpr;

      ctx.fillStyle = 'rgba(15, 23, 42, 0.9)';
      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(tx - tw / 2 - padX, ty - 12 * dpr - padY, tw + padX * 2, 14 * dpr + padY * 2, 4 * dpr);
      } else {
        ctx.rect(tx - tw / 2 - padX, ty - 12 * dpr - padY, tw + padX * 2, 14 * dpr + padY * 2);
      }
      ctx.fill();

      ctx.fillStyle = '#ffffff';
      ctx.fillText(label, tx, ty);
    }

    ctx.restore();
  }
}

function drawDeepslate() {
  if (!workerReady || !lastBuiltMeta) return;
  const e = camera.matrixWorldInverse.elements;
  for (let i = 0; i < 16; i++) _viewMat[i] = e[i];
  worker.postMessage({ t: 'draw', view: _viewMat, grid: !!S.showGrid,
    projection: Array.from(camera.projectionMatrix.elements) });
}

let needFrame = true;
function animate3d() {
  requestAnimationFrame(animate3d);
  if (S.view !== '3d' || !worker) return;
  if (needFrame) {
    updateCamera();
    drawDeepslate();
    drawOverlay();
    needFrame = false;
  }
}
function invalidate3d() { needFrame = true; }

/* ---------- 构建请求（重活全在 Worker） ---------- */
let heightBuildRequest = 0;
async function buildScene3d() {
  const request = ++heightBuildRequest, generation = current3dGeneration;
  if (slicePreview.enabled || slicePreview.busy) resetSlicePreview(false);
  const r = activeRange();
  if (r.n > 64) {
    $('empty3d').style.display = 'flex';
    $('empty3d').querySelector('span').textContent =
      `范围 ${r.n} 区块超过当前安全限制（64），请在左下角缩略图框选更小区域`;
    return;
  }
  if (!worker) return;
  set3dLoading(true, '探测地表高度');
  try {
    const ready = await ensureAutoHeight(r);
    if (request !== heightBuildRequest || generation !== current3dGeneration) return;
    if (!ready || S.view !== '3d') { set3dLoading(false); return; }
  } catch (error) {
    if (request === heightBuildRequest) onBuildError('地表高度探测失败：' + error.message);
    return;
  }
  set3dLoading(true, '构建 2.5D');
  const q = new URLSearchParams({
    cx0: r.cx0, cz0: r.cz0, cx1: r.cx1, cz1: r.cz1,
    decorations: S.includeDecorations ? '1' : '0',
  });
  if (r.box) {
    q.set('x0', r.box.x0); q.set('x1', r.box.x1);
    q.set('z0', r.box.z0); q.set('z1', r.box.z1);
  }
  if (S.y0 !== null) q.set('y0', S.y0);
  if (S.y1 !== null) q.set('y1', S.y1);
  worker.postMessage({ t: 'build', q: q.toString(), generation: current3dGeneration });
}

function onBuilt(meta) {
  lastBuiltMeta = meta;
  structBox.x0 = meta.x0; structBox.z0 = meta.z0;
  structBox.y0 = meta.yMin; structBox.y1 = meta.yMax;
  structBox.has = true;
  // 输入框全空（如"当前视野"模式）→ 自动填入模型边界，方便微调
  const empty = ['x0In', 'x1In', 'z0In', 'z1In'].every(id => $(id).value === '');
  if (empty) {
    $('x0In').value = meta.x0; $('x1In').value = meta.x0 + meta.w - 1;
    $('z0In').value = meta.z0; $('z1In').value = meta.z0 + meta.d - 1;
    invalidate3d();
  }
  $('empty3d').style.display = 'none';
  $('hTri').textContent = (meta.count / 1000).toFixed(1) + 'k';
  // 选区 + Y 范围都要参与适配键：Y 变化时结构本地原点平移，不重新适配会黑屏
  const key = `${meta.x0},${meta.z0},${meta.w},${meta.d},${meta.yMin},${meta.yMax}`;
  if (key !== fitKey) {
    const isInitial = !fitKey;
    fitKey = key;
    const H = meta.yMax - meta.yMin + 1;
    const radius = Math.sqrt((meta.w / 2) ** 2 + (H / 2) ** 2 + (meta.d / 2) ** 2);
    const fov = 70 * Math.PI / 180;
    cam.target.set(meta.w / 2, H / 2, meta.d / 2);
    if (isInitial) {
      cam.dist = radius / Math.sin(fov / 2) * 1.35;
    }
    // 缩放距离上下限随模型伸缩：小模型也能拉近看细节，大模型拉远不"卡住"
    modelRadius = radius;
    MIN_DIST = Math.max(1.5, radius / 30);
    MAX_DIST = radius * 14;
    cam.dist = Math.max(MIN_DIST, Math.min(MAX_DIST, cam.dist));
  }
  invalidate3d();
  drawMini();
  set3dLoading(false);
  updateSliceSummary();
}

function onBuildError(msg) {
  $('empty3d').style.display = 'flex';
  const net = /Failed to fetch|NetworkError|net::|timed? out/i.test(msg || '');
  $('empty3d').querySelector('span').textContent = net
    ? '无法连接本地服务（127.0.0.1:8770）——服务可能正在重启，稍等几秒再试，或刷新页面'
    : '构建失败：' + msg;
  set3dLoading(false);
}

let t3d = null;
function schedule3d(now) {
  if (S.view !== '3d') return;
  if (t3d) clearTimeout(t3d);
  if (now) return buildScene3d();
  t3d = setTimeout(buildScene3d, 200);
}

/* ---------- 3D 交互 ---------- */
const raycaster = new THREE.Raycaster();
const _planeN = new THREE.Vector3(0, 1, 0);

// 把屏幕坐标投影到世界 XZ 平面（穿过给定高度 yLevel 的水平面）
function screenToWorldXZ(clientX, clientY, yLevel) {
  const el = $('cv3d');
  const r = el.getBoundingClientRect();
  const nx = ((clientX - r.left) / r.width) * 2 - 1;
  const ny = -((clientY - r.top) / r.height) * 2 + 1;
  raycaster.setFromCamera(new THREE.Vector2(nx, ny), camera);
  const o = raycaster.ray.origin, dir = raycaster.ray.direction;
  if (Math.abs(dir.y) < 1e-4) return null;          // 掠射角射线与水平面平行
  const t = (yLevel - o.y) / dir.y;
  if (!isFinite(t) || t < 0) return null;
  if (Math.abs(t) > cam.dist * 4) return null;        // 指针指向无穷远 → 否则目标点会飞走
  return new THREE.Vector3(o.x + dir.x * t, yLevel, o.z + dir.z * t);
}

// 模型包围球半径：缩放距离上下限随模型大小伸缩（小模型也能拉近看细节）
let modelRadius = 80;
let MIN_DIST = 3, MAX_DIST = 4000;

// 屏幕上 1 像素在「过 target 的水平面」上对应多少世界单位（透视投影精确值）
function worldPerPixel() {
  const h = $('cv3d').clientHeight || window.innerHeight;
  const halfFov = (camera ? camera.fov : 42) * Math.PI / 360;
  return 2 * cam.dist * Math.tan(halfFov) / Math.max(h, 1);
}

// 屏幕位移 → target 位移。使模型跟随光标（SketchUp 手感）
// 水平：1 像素 = worldPerPixel；竖直：地面被压扁了，需要再除以 cos φ
function panVec(dx, dy) {
  const k = worldPerPixel();
  const cp = Math.cos(cam.phi);
  const ky = k / Math.max(cp, 0.25);     // 贴近地平线时退化，做个下限
  const cosT = Math.cos(cam.theta), sinT = Math.sin(cam.theta);
  return {
    x: -(dx * k * sinT + dy * ky * cosT),
    z: -(dx * k * -cosT + dy * ky * sinT),
  };
}

// SketchUp / Fusion 风格：以鼠标指针为中心缩放。
// 精确解：target 与指针点 p0 之间按 f 缩放（相机位置 = target + f·偏移），
// 因此指针射线上的世界点严格不动，反复缩放也不会漂移。
function zoomAtPointer(clientX, clientY, factor) {
  const oldDist = cam.dist;
  const newDist = clamp(oldDist * factor, MIN_DIST, MAX_DIST);
  if (Math.abs(newDist - oldDist) < 1e-6) return;
  const f = newDist / oldDist;
  cam.dist = newDist;
  const p0 = screenToWorldXZ(clientX, clientY, cam.target.y);
  if (p0) {
    cam.target.x = p0.x + (cam.target.x - p0.x) * f;
    cam.target.z = p0.z + (cam.target.z - p0.z) * f;
  }
  invalidate3d();
}

function hitTestHandle(clientX, clientY) {
  const el = $('cv3d');
  if (!el) return null;
  const handles = computeBoxHandles();
  const r = el.getBoundingClientRect();
  const mx = clientX - r.left, my = clientY - r.top;
  const HIT_R = 16;
  let best = null, bestDist = HIT_R;
  for (let i = handles.length - 1; i >= 0; i--) {
    const h = handles[i];
    const dist = Math.hypot(mx - h.sx, my - h.sy);
    if (dist <= bestDist) {
      bestDist = dist;
      best = h;
    }
  }
  return best;
}

(function bind3d() {
  const el = $('cv3d');
  let d = null;           // 旋转/平移状态

  el.addEventListener('contextmenu', e => e.preventDefault());

  el.addEventListener('mousedown', e => {
    // 左键点击优先判定是否命中红框手柄
    if (e.button === 0) {
      const hit = hitTestHandle(e.clientX, e.clientY);
      if (hit) {
        e.preventDefault();
        e.stopPropagation();

        const b = getBoxBounds();
        if (!b) return;

        $('x0In').value = b.bx0; $('x1In').value = b.bx1;
        $('z0In').value = b.bz0; $('z1In').value = b.bz1;
        $('yBotIn').value = b.by0; $('yTopIn').value = b.by1;
        S.box = { x0: b.bx0, x1: b.bx1, z0: b.bz0, z1: b.bz1 };
        S.y0 = b.by0; S.y1 = b.by1;
        updateBar();

        let startVal = 0;
        if (hit.prop === 'x0') startVal = b.bx0;
        else if (hit.prop === 'x1') startVal = b.bx1;
        else if (hit.prop === 'y0') startVal = b.by0;
        else if (hit.prop === 'y1') startVal = b.by1;
        else if (hit.prop === 'z0') startVal = b.bz0;
        else if (hit.prop === 'z1') startVal = b.bz1;

        activeDrag = {
          handle: hit,
          startMx: e.clientX,
          startMy: e.clientY,
          startVal,
          axisDx: hit.axisDx,
          axisDy: hit.axisDy,
          axisLenSq: hit.axisLenSq,
          bounds: b,
        };
        el.style.cursor = 'grabbing';
        invalidate3d();
        return;
      }
    }

    // 中键 = 平移（拖动画面）；右键 = 平移；左键 = 旋转
    if (e.button === 1) {
      e.preventDefault();
      d = {
        mode: 'pan',
        x: e.clientX, y: e.clientY,
        theta: cam.theta, phi: cam.phi, target: cam.target.clone(),
      };
      return;
    }
    // 左键 = 旋转；右键 = 平移
    d = {
      mode: e.button === 2 ? 'pan' : 'orbit',
      x: e.clientX, y: e.clientY,
      theta: cam.theta, phi: cam.phi, target: cam.target.clone(),
    };
  });

  window.addEventListener('mousemove', e => {
    if (activeDrag) {
      const dx = e.clientX - activeDrag.startMx;
      const dy = e.clientY - activeDrag.startMy;
      const lenSq = activeDrag.axisLenSq;
      if (lenSq > 1e-4) {
        const deltaBlocks = (dx * activeDrag.axisDx + dy * activeDrag.axisDy) / lenSq;
        const deltaInt = Math.round(deltaBlocks);
        const b = activeDrag.bounds;
        const h = activeDrag.handle;

        if (h.prop === 'x0') {
          const minX = b.limitX0;
          const maxX = parseInt($('x1In').value, 10);
          const val = clamp(activeDrag.startVal + deltaInt, minX, maxX);
          $('x0In').value = val;
          if (S.box) S.box.x0 = val;
        } else if (h.prop === 'x1') {
          const minX = parseInt($('x0In').value, 10);
          const maxX = b.limitX1;
          const val = clamp(activeDrag.startVal + deltaInt, minX, maxX);
          $('x1In').value = val;
          if (S.box) S.box.x1 = val;
        } else if (h.prop === 'z0') {
          const minZ = b.limitZ0;
          const maxZ = parseInt($('z1In').value, 10);
          const val = clamp(activeDrag.startVal + deltaInt, minZ, maxZ);
          $('z0In').value = val;
          if (S.box) S.box.z0 = val;
        } else if (h.prop === 'z1') {
          const minZ = parseInt($('z0In').value, 10);
          const maxZ = b.limitZ1;
          const val = clamp(activeDrag.startVal + deltaInt, minZ, maxZ);
          $('z1In').value = val;
          if (S.box) S.box.z1 = val;
        } else if (h.prop === 'y0') {
          useManualHeight();
          const minY = b.limitY0;
          const maxY = parseInt($('yTopIn').value, 10);
          const val = clamp(activeDrag.startVal + deltaInt, minY, maxY);
          $('yBotIn').value = val;
          S.y0 = val;
        } else if (h.prop === 'y1') {
          useManualHeight();
          const minY = parseInt($('yBotIn').value, 10);
          const maxY = b.limitY1;
          const val = clamp(activeDrag.startVal + deltaInt, minY, maxY);
          $('yTopIn').value = val;
          S.y1 = val;
        }

        updateBar();
        invalidate3d();
      }
      return;
    }

    if (d) {
      const dx = e.clientX - d.x, dy = e.clientY - d.y;
      if (d.mode === 'pan') {
        const pv = panVec(dx, dy);
        cam.target.x = d.target.x + pv.x;
        cam.target.z = d.target.z + pv.z;
      } else {   // orbit：模型跟随光标（SketchUp 手感）
        cam.theta = d.theta + dx * 0.006;
        cam.phi = clamp(d.phi - dy * 0.005, 0.06, 1.46);
      }
      invalidate3d();
      return;
    }

    // 悬停检测（未拖拽时只对 cv3d 区域检测）
    if (e.target !== el && !el.contains?.(e.target)) {
      if (hoveredHandle) {
        hoveredHandle = null;
        el.style.cursor = '';
        invalidate3d();
      }
      return;
    }
    const hit = hitTestHandle(e.clientX, e.clientY);
    if (hit !== hoveredHandle) {
      hoveredHandle = hit;
      el.style.cursor = hit ? hit.cursor : '';
      invalidate3d();
    }
  });

  el.addEventListener('mouseleave', () => {
    if (!activeDrag && hoveredHandle) {
      hoveredHandle = null;
      el.style.cursor = '';
      invalidate3d();
    }
  });

  window.addEventListener('mouseup', e => {
    if (activeDrag) {
      activeDrag = null;
      const hit = hitTestHandle(e.clientX, e.clientY);
      hoveredHandle = hit;
      el.style.cursor = hit ? hit.cursor : '';
      invalidate3d();
      triggerBoxRebuild();
    }
    d = null;
  });

  el.addEventListener('dblclick', e => {
    if (S.step === 'slices') {
      e.preventDefault();
      pickSliceAt(e.clientX, e.clientY);
      return;
    }
    clearSelection();
    drawMini();
    if (S.view === '3d') schedule3d(true);
  });

  el.addEventListener('wheel', e => {
    e.preventDefault();
    // SketchUp 风格：以鼠标指针为中心缩放。按 deltaY 连续取值，
    // 鼠标滚轮（一格 100）约 1.17 倍，触控板（一格 1~8）平滑累积。
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? (el.clientHeight || 900) : 1;
    const dy = clamp(e.deltaY * unit, -480, 480);
    // Shift = 细调缩放（步距 1/5）
    zoomAtPointer(e.clientX, e.clientY, Math.exp(dy * (e.shiftKey ? 0.00032 : 0.0016)));
  }, { passive: false });

  // 阻止中键默认的"自动滚动"圆盘
  el.addEventListener('auxclick', e => { if (e.button === 1) e.preventDefault(); });

  /* ---------- 触屏：单指旋转 / 双指缩放+平移 ---------- */
  let touch = null;
  el.addEventListener('touchstart', e => {
    if (e.touches.length === 1) {
      const t = e.touches[0];
      touch = { mode: 'rotate', x: t.clientX, y: t.clientY,
                theta: cam.theta, phi: cam.phi };
    } else if (e.touches.length === 2) {
      const a = e.touches[0], b = e.touches[1];
      touch = { mode: 'pinch',
                dist: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
                mx: (a.clientX + b.clientX) / 2, my: (a.clientY + b.clientY) / 2,
                target: cam.target.clone(), startDist: cam.dist };
    }
    e.preventDefault();
  }, { passive: false });
  el.addEventListener('touchmove', e => {
    if (!touch) return;
    if (touch.mode === 'rotate' && e.touches.length === 1) {
      const t = e.touches[0];
      const dx = t.clientX - touch.x, dy = t.clientY - touch.y;
      cam.theta = touch.theta + dx * 0.006;
      cam.phi = clamp(touch.phi - dy * 0.005, 0.06, 1.46);
    } else if (touch.mode === 'pinch' && e.touches.length === 2) {
      const a = e.touches[0], b = e.touches[1];
      const nd = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      cam.dist = clamp(touch.startDist * (touch.dist / Math.max(nd, 1)), 6, 4000);
      const mx = (a.clientX + b.clientX) / 2, my = (a.clientY + b.clientY) / 2;
      const pv = panVec(mx - touch.mx, my - touch.my);
      cam.target.x = touch.target.x + pv.x;
      cam.target.z = touch.target.z + pv.z;
    }
    invalidate3d();
    e.preventDefault();
  }, { passive: false });
  el.addEventListener('touchend', () => { touch = null; });
})();

window.__getBoxBounds = getBoxBounds;
window.__getBoxHandles = () => computeBoxHandles().map(h => ({
  id: h.id, name: h.name, prop: h.prop, axis: h.axis,
  sx: h.sx, sy: h.sy, px: h.px, py: h.py,
  cursor: h.cursor
}));
window.__getActiveDrag = () => activeDrag;
window.__getHoveredHandle = () => hoveredHandle;

/* ============================================================
   2.5D 俯视缩略选区：在 2.5D 里直接框选与缩放，无需退回俯视图
   ============================================================ */
const cvMini = document.getElementById('cvMini');
const mctx = cvMini.getContext('2d');
let miniImg = null, miniRange = null;
let miniHalf = 8;          // 视野半宽（区块数），默认 ±8 区块 = 16×16。支持滚轮缩放 2~32
let tMiniDebounce = null;
let miniCollapsed = false;
try { miniCollapsed = localStorage.getItem('mc3d-minimap-collapsed') === '1'; } catch {}

function setMiniCollapsed(collapsed) {
  miniCollapsed = collapsed;
  $('minimap').classList.toggle('collapsed', collapsed);
  $('btnMiniRestore').setAttribute('aria-expanded', String(!collapsed));
  try { localStorage.setItem('mc3d-minimap-collapsed', collapsed ? '1' : '0'); } catch {}
  if (!collapsed && S.view === '3d') resizeMini();
}
$('minimap').classList.toggle('collapsed', miniCollapsed);
$('btnMiniRestore').setAttribute('aria-expanded', String(!miniCollapsed));

// 缩略图中心与显示范围：默认对准当前选区中心，支持缩放
function miniRangeOf() {
  const r = activeRange();
  const cxc = Math.round((r.cx0 + r.cx1) / 2);
  const czc = Math.round((r.cz0 + r.cz1) / 2);
  const h = miniHalf;
  return { cx0: cxc - h, cz0: czc - h, cx1: cxc + h, cz1: czc + h };
}

async function loadMini() {
  if (miniCollapsed) return;
  const r = miniRangeOf();
  const px = Math.round(clamp(Math.max(cvMini.clientWidth, cvMini.clientHeight) * 2, 256, 768));
  const url = `/api/tile?cx0=${r.cx0}&cz0=${r.cz0}&cx1=${r.cx1}&cz1=${r.cz1}` +
              `&px=${px}&layer=terrain`;
  try {
    const res = await fetch(url);
    if (!res.ok) return;
    const blob = await res.blob();
    const img = new Image();
    img.src = URL.createObjectURL(blob);
    await img.decode();
    miniImg = img;
    miniRange = r;
    drawMini();
  } catch (e) { console.error(e); }
}

// 严谨的双轴独立归一化投影，杜绝 Y 轴比例错位与偏移
function miniWorldToPx(gx, gz) {
  const r = miniRange || miniRangeOf();
  const totalChunksX = r.cx1 - r.cx0;
  const totalChunksZ = r.cz1 - r.cz0;
  const w = cvMini.width;
  const h = cvMini.height;

  const nx = (gx - r.cx0 * CH) / (totalChunksX * CH);
  const nz = (gz - r.cz0 * CH) / (totalChunksZ * CH);

  return {
    x: nx * w,
    y: nz * h,
  };
}

function miniPxToWorld(px, py) {
  const r = miniRange || miniRangeOf();
  const totalChunksX = r.cx1 - r.cx0;
  const totalChunksZ = r.cz1 - r.cz0;
  const w = cvMini.width;
  const h = cvMini.height;

  const nx = px / w;
  const nz = py / h;

  return {
    gx: r.cx0 * CH + nx * (totalChunksX * CH),
    gz: r.cz0 * CH + nz * (totalChunksZ * CH),
  };
}

function drawMini() {
  const w = cvMini.width, h = cvMini.height;
  if (!w || !h) return;
  mctx.fillStyle = '#0b0c0f';
  mctx.fillRect(0, 0, w, h);
  if (miniImg && miniRange) {
    mctx.imageSmoothingEnabled = false;
    mctx.drawImage(miniImg, 0, 0, w, h);
  }
  // 当前选区 (S.sel) 闭区间渲染
  if (S.sel) {
    const a = miniWorldToPx(S.sel.cx0 * CH, S.sel.cz0 * CH);
    const b = miniWorldToPx((S.sel.cx1 + 1) * CH, (S.sel.cz1 + 1) * CH);
    const rx = Math.min(a.x, b.x);
    const ry = Math.min(a.y, b.y);
    const rw = Math.abs(b.x - a.x);
    const rh = Math.abs(b.y - a.y);

    mctx.fillStyle = 'rgba(77, 141, 255, 0.28)';
    mctx.fillRect(rx, ry, rw, rh);
    mctx.strokeStyle = '#4d8dff';
    mctx.lineWidth = 2.0;
    mctx.strokeRect(rx, ry, rw, rh);
  }
  // 当前红框微调切片盒 (S.box) 闭区间渲染
  if (S.box && miniRange) {
    const ba = miniWorldToPx(S.box.x0, S.box.z0);
    const bb = miniWorldToPx(S.box.x1 + 1, S.box.z1 + 1);
    const rx = Math.min(ba.x, bb.x);
    const ry = Math.min(ba.y, bb.y);
    const rw = Math.abs(bb.x - ba.x);
    const rh = Math.abs(bb.y - ba.y);

    mctx.fillStyle = 'rgba(220, 38, 38, 0.25)';
    mctx.fillRect(rx, ry, rw, rh);
    mctx.strokeStyle = '#dc2626';
    mctx.lineWidth = 2.0;
    mctx.strokeRect(rx, ry, rw, rh);
  }
  // 框选预览
  if (miniDrag) {
    const rx = Math.min(miniDrag.sx, miniDrag.ex);
    const ry = Math.min(miniDrag.sy, miniDrag.ey);
    const rw = Math.abs(miniDrag.ex - miniDrag.sx);
    const rh = Math.abs(miniDrag.ey - miniDrag.sy);

    mctx.fillStyle = 'rgba(77, 141, 255, 0.20)';
    mctx.fillRect(rx, ry, rw, rh);
    mctx.strokeStyle = 'rgba(120, 175, 255, 0.98)';
    mctx.lineWidth = 1.5;
    mctx.setLineDash([5, 3]);
    mctx.strokeRect(rx, ry, rw, rh);
    mctx.setLineDash([]);
  }
}

let miniDrag = null;
function resizeMini() {
  if (miniCollapsed || S.view !== '3d') return;
  requestAnimationFrame(() => {
    if (miniCollapsed || S.view !== '3d') return;
    const r = cvMini.getBoundingClientRect();
    if (r.width < 10) return;
    cvMini.width = Math.round(r.width * dpr);
    cvMini.height = Math.round(r.height * dpr);
    loadMini();
  });
}

function changeMiniZoom(dir) {
  // dir > 0 放大地图（缩小视野半宽），dir < 0 缩小地图（扩大视野半宽）
  const step = miniHalf <= 4 ? 1 : 2;
  const newHalf = clamp(miniHalf - dir * step, 2, 32);
  if (newHalf === miniHalf) return;
  miniHalf = newHalf;
  drawMini();
  clearTimeout(tMiniDebounce);
  tMiniDebounce = setTimeout(() => {
    loadMini();
  }, 120);
}

// 滚轮缩放小地图：地图外框不变，内部地图平滑缩放
cvMini.addEventListener('wheel', e => {
  e.preventDefault();
  const dir = e.deltaY < 0 ? 1 : -1; // 滚轮向上放大，滚轮向下缩小
  changeMiniZoom(dir);
}, { passive: false });

cvMini.addEventListener('mousedown', e => {
  e.preventDefault();
  const r = cvMini.getBoundingClientRect();
  const sx = (e.clientX - r.left) * dpr;
  const sy = (e.clientY - r.top) * dpr;
  miniDrag = { sx, sy, ex: sx, ey: sy };
  drawMini();
});
window.addEventListener('mousemove', e => {
  if (!miniDrag) return;
  const r = cvMini.getBoundingClientRect();
  miniDrag.ex = (e.clientX - r.left) * dpr;
  miniDrag.ey = (e.clientY - r.top) * dpr;
  drawMini();
});
window.addEventListener('mouseup', () => {
  if (!miniDrag) return;
  const sx0 = Math.min(miniDrag.sx, miniDrag.ex);
  const sx1 = Math.max(miniDrag.sx, miniDrag.ex);
  const sy0 = Math.min(miniDrag.sy, miniDrag.ey);
  const sy1 = Math.max(miniDrag.sy, miniDrag.ey);
  miniDrag = null;

  const g0 = miniPxToWorld(sx0, sy0);
  const g1 = miniPxToWorld(sx1, sy1);

  const dragPx = Math.hypot(sx1 - sx0, sy1 - sy0);
  if (dragPx < 5) {
    // 单击：选中单个区块
    const cx = floorDiv(Math.floor(g0.gx), CH);
    const cz = floorDiv(Math.floor(g0.gz), CH);
    setSelection(cx, cz, cx, cz);
  } else {
    // 框选多个区块：边界精准锚定，防止多溢出区块
    const cx0 = floorDiv(Math.floor(g0.gx), CH);
    const cz0 = floorDiv(Math.floor(g0.gz), CH);
    const cx1 = floorDiv(Math.floor(g1.gx - 0.001), CH);
    const cz1 = floorDiv(Math.floor(g1.gz - 0.001), CH);
    setSelection(
      Math.min(cx0, cx1), Math.min(cz0, cz1),
      Math.max(cx0, cx1), Math.max(cz0, cz1),
    );
  }
  drawMini();
  if (S.view === '3d') schedule3d(true);
});

// 收起地图只改变面板显示，保留选区、地图缩放和模型视角。
$('btnMiniCollapse').onclick = () => setMiniCollapsed(true);
$('btnMiniRestore').onclick = () => setMiniCollapsed(false);
const btnZoomIn = document.getElementById('btnMiniZoomIn');
if (btnZoomIn) {
  btnZoomIn.onclick = () => changeMiniZoom(1);
}
const btnZoomOut = document.getElementById('btnMiniZoomOut');
if (btnZoomOut) {
  btnZoomOut.onclick = () => changeMiniZoom(-1);
}
