/* Shared by the desktop WebView and browser: interactive in-app slice inspection. */
const slicePreview = {
  enabled: false, busy: false, focus: null, axis: 'y', gap: 2,
  selected: null, selectionMode: 'single',
  indices: [], counts: [], bounds: null, sliceBounds: [], pickTargets: [],
  requestId: 0, pickRequestId: 0, originalCamera: null, overviewCamera: null,
  fitNext: false,
};

function captureSliceCamera() {
  return { theta: cam.theta, phi: cam.phi, dist: cam.dist, target: cam.target.clone(),
    radius: modelRadius, minDist: MIN_DIST, maxDist: MAX_DIST };
}

function restoreSliceCamera(saved) {
  if (!saved) return;
  cam.theta = saved.theta; cam.phi = saved.phi; cam.dist = saved.dist;
  cam.target.copy(saved.target);
  modelRadius = saved.radius; MIN_DIST = saved.minDist; MAX_DIST = saved.maxDist;
}

function slicePreviewBounds() {
  return S.step === 'slices' && slicePreview.enabled ? slicePreview.bounds : null;
}

function fitSliceCamera(bounds) {
  if (!bounds || !camera) return;
  const size = bounds.max.map((v, i) => v - bounds.min[i]);
  const radius = Math.max(0.5, Math.hypot(...size) / 2);
  modelRadius = radius;
  MIN_DIST = Math.max(0.1, radius / 40);
  MAX_DIST = Math.max(20, radius * 20);
  const height = $('cv3d').clientHeight || innerHeight;
  const usableHeight = Math.max(100, height - $('selbar').getBoundingClientRect().height - 112);
  const usableWidth = Math.max(100, $('cv3d').clientWidth - 40);
  cam.target.set(...bounds.min.map((v, i) => (v + bounds.max[i]) / 2));
  cam.dist = radius / Math.sin(35 * Math.PI / 180) * 1.12 * Math.max(height / usableHeight, height / usableWidth);
  invalidate3d();
}

function updateSliceSummary() {
  const b = getBoxBounds();
  const axis = $('sliceAxis').value;
  if (slicePreview.busy) {
    $('sliceCount').textContent = '正在生成真实截面…';
  } else if (slicePreview.enabled) {
    $('sliceCount').textContent = slicePreview.focus !== null
      ? `当前 ${axis.toUpperCase()} = ${slicePreview.focus}`
      : `${axis.toUpperCase()} 坐标：${slicePreview.indices[0]} ～ ${slicePreview.indices.at(-1)}`;
  } else if (b) {
    const count = b['b' + axis + '1'] - b['b' + axis + '0'] + 1;
    $('sliceCount').textContent = `${axis.toUpperCase()} 方向 · 每格一片 · 最多 ${count} 片（略过空片）`;
  } else {
    $('sliceCount').textContent = '先在第二步确认选区';
  }
  $('btnSliceBatch').disabled = slicePreview.busy || !lastBuiltMeta;
  $('btnSliceBatch').textContent = slicePreview.enabled ? '重新生成预览' : '生成切片预览';
  $('btnSlicePreviewReset').hidden = !slicePreview.enabled && !slicePreview.busy;
  updateSliceList();
  $('guideDoubleClickText').textContent = S.step === 'slices' ? '查看单片 · Esc 返回全部' : '清除选区';
}

function sendSlicePreview() {
  const scale = Number($('sliceScale').value);
  const thickness = Number($('sliceThickness').value);
  if (!(scale > 0) || !(thickness > 0) || !Number.isFinite(scale + thickness)) {
    setSliceStatus('厚度和比例必须是大于 0 的有限数值。');
    return false;
  }
  slicePreview.busy = true;
  slicePreview.pickRequestId++;
  const requestId = ++slicePreview.requestId;
  worker.postMessage({ t: 'slice', enabled: true, axis: $('sliceAxis').value,
    gap: slicePreview.gap / scale, thicknessRatio: thickness / scale,
    focus: slicePreview.selected !== null, index: slicePreview.focus,
    selectedIndices: slicePreview.selected,
    requestId, generation: current3dGeneration });
  updateSliceSummary();
  return true;
}

function startSlicePreview() {
  if (!workerReady || !lastBuiltMeta || isBuilding3d) {
    setSliceStatus('请等待第二步的模型构建完成。');
    return;
  }
  if (!slicePreview.enabled && !slicePreview.busy) slicePreview.originalCamera = captureSliceCamera();
  slicePreview.focus = null;
  slicePreview.selected = null;
  slicePreview.fitNext = true;
  slicePreview.overviewCamera = null;
  cam.theta = $('sliceAxis').value === 'z' ? 1.15 : 0.38;
  cam.phi = 1.08;
  if (sendSlicePreview()) setSliceStatus('正在读取完整选区并生成切片预览…');
}

function acceptSlicePreview(message) {
  if (message.requestId !== slicePreview.requestId || S.step !== 'slices') return;
  slicePreview.busy = false;
  if (!message.enabled) return;
  slicePreview.enabled = true;
  slicePreview.axis = message.axis;
  slicePreview.indices = message.indices;
  slicePreview.counts = message.counts;
  slicePreview.bounds = message.bounds;
  slicePreview.sliceBounds = message.sliceBounds;
  slicePreview.selected = message.selectedIndices ?? (message.focus ? [message.index] : null);
  slicePreview.focus = slicePreview.selected?.length === 1 ? slicePreview.selected[0] : null;
  if (slicePreview.fitNext && (slicePreview.selected === null || slicePreview.selected.length)) {
    fitSliceCamera(message.bounds);
    slicePreview.fitNext = false;
  }
  updateSliceSummary();
  setSliceStatus('', false);
  invalidate3d();
}

function failSlicePreview(message) {
  if (message.requestId !== slicePreview.requestId) return;
  slicePreview.busy = false;
  updateSliceSummary();
  setSliceStatus('切片预览失败：' + message.msg);
}

function focusSlice(index) {
  if (!slicePreview.enabled || slicePreview.busy || !slicePreview.indices.includes(index)) return;
  selectSlices([index]);
}

function selectSlices(indices) {
  if (!slicePreview.enabled) return;
  if (slicePreview.selected === null && indices !== null) slicePreview.overviewCamera = captureSliceCamera();
  slicePreview.selected = indices === null ? null : slicePreview.indices.filter(index => indices.includes(index));
  slicePreview.focus = slicePreview.selected?.length === 1 ? slicePreview.selected[0] : null;
  slicePreview.fitNext = true;
  if (indices === null) restoreSliceCamera(slicePreview.overviewCamera);
  else if (slicePreview.focus !== null) {
    if (slicePreview.axis === 'y') { cam.phi = 0.08; cam.theta = Math.PI / 2; }
    else { cam.phi = 1.46; cam.theta = slicePreview.axis === 'x' ? 0 : Math.PI / 2; }
  } else if (indices.length > 1) {
    cam.theta = slicePreview.axis === 'z' ? 1.15 : 0.38; cam.phi = 1.08;
  }
  sendSlicePreview();
}

function exitSliceFocus() {
  if (slicePreview.selected === null) return false;
  selectSlices(null);
  return true;
}

function resetSlicePreview(restore = true) {
  const wasActive = slicePreview.enabled || slicePreview.busy;
  slicePreview.requestId++;
  slicePreview.pickRequestId++;
  slicePreview.enabled = false; slicePreview.busy = false; slicePreview.focus = null;
  slicePreview.selected = null;
  slicePreview.indices = []; slicePreview.counts = [];
  slicePreview.bounds = null; slicePreview.sliceBounds = []; slicePreview.pickTargets = [];
  if (restore) restoreSliceCamera(slicePreview.originalCamera);
  else if (slicePreview.originalCamera) {
    modelRadius = slicePreview.originalCamera.radius;
    MIN_DIST = slicePreview.originalCamera.minDist;
    MAX_DIST = slicePreview.originalCamera.maxDist;
  }
  slicePreview.originalCamera = null; slicePreview.overviewCamera = null;
  if (worker && wasActive) worker.postMessage({ t: 'slice', enabled: false,
    requestId: slicePreview.requestId, generation: current3dGeneration });
  updateSliceSummary();
  if (wasActive) setSliceStatus('已还原整体，原选区保持不变。');
  invalidate3d();
}

function setSlicePreviewGap(value) {
  const gap = Number(value);
  if (!Number.isFinite(gap)) return;
  slicePreview.gap = clamp(gap, 0, 20);
  $('sliceGap').value = slicePreview.gap;
  $('sliceGapNumber').value = slicePreview.gap;
  if (slicePreview.enabled || slicePreview.busy) {
    slicePreview.fitNext = true;
    sendSlicePreview();
  }
}

function changeSliceDirection() {
  if (slicePreview.enabled || slicePreview.busy) startSlicePreview();
  else { updateSliceSummary(); invalidate3d(); }
}

function refreshSlicePreviewSettings() {
  if (slicePreview.enabled || slicePreview.busy) {
    slicePreview.fitNext = true;
    sendSlicePreview();
  }
}

function pickSliceAt(clientX, clientY) {
  if (!slicePreview.enabled || slicePreview.busy) return;
  const r = $('cv3d').getBoundingClientRect();
  raycaster.setFromCamera(new THREE.Vector2((clientX - r.left) / r.width * 2 - 1,
    1 - (clientY - r.top) / r.height * 2), camera);
  worker.postMessage({ t: 'slice_pick', origin: raycaster.ray.origin.toArray(),
    direction: raycaster.ray.direction.toArray(), requestId: ++slicePreview.pickRequestId,
    generation: current3dGeneration });
}

function acceptSlicePick(message) {
  if (message.requestId !== slicePreview.pickRequestId || S.step !== 'slices' || !slicePreview.enabled) return;
  if (message.index !== null) focusSlice(message.index);
}

const _slicePoint = new THREE.Vector3();
function drawSliceOverlay(ctx, canvas) {
  slicePreview.pickTargets = [];
  const project = point => {
    _slicePoint.set(...point).project(camera);
    return { x: (_slicePoint.x + 1) * canvas.width / 2,
      y: (1 - _slicePoint.y) * canvas.height / 2, depth: _slicePoint.z };
  };
  const lineLoop = points => {
    const p = points.map(project);
    if (p.some(v => v.depth < -1 || v.depth > 1)) return;
    ctx.beginPath(); p.forEach((v, i) => i ? ctx.lineTo(v.x, v.y) : ctx.moveTo(v.x, v.y));
    ctx.closePath(); ctx.stroke();
  };
  const plane = (lo, hi, dim, coordinate) => {
    const [u, v] = [0, 1, 2].filter(d => d !== dim);
    return [[0, 0], [1, 0], [1, 1], [0, 1]].map(([a, b]) => {
      const p = [...lo]; p[dim] = coordinate; p[u] = a ? hi[u] : lo[u]; p[v] = b ? hi[v] : lo[v]; return p;
    });
  };
  ctx.lineWidth = 1 * dpr;
  if (slicePreview.enabled) {
    const dim = { x: 0, y: 1, z: 2 }[slicePreview.axis];
    ctx.strokeStyle = 'rgba(37,99,235,0.32)';
    for (const item of slicePreview.sliceBounds) {
      if (slicePreview.selected !== null && !slicePreview.selected.includes(item.index)) continue;
      lineLoop(plane(item.min, item.max, dim, item.max[dim]));
      const p = project(item.anchor || item.min.map((v, i) => (v + item.max[i]) / 2));
      if (p.depth >= -1 && p.depth <= 1) slicePreview.pickTargets.push({
        index: item.index, x: p.x / dpr, y: p.y / dpr, depth: p.depth,
      });
    }
  } else {
    const b = getBoxBounds();
    if (!b) return;
    const lo = [b.bx0 - structBox.x0, b.by0 - structBox.y0, b.bz0 - structBox.z0];
    const hi = [b.bx1 + 1 - structBox.x0, b.by1 + 1 - structBox.y0, b.bz1 + 1 - structBox.z0];
    const axis = $('sliceAxis').value;
    const dim = { x: 0, y: 1, z: 2 }[axis];
    const bounds = axis === 'y' ? hi[dim] - lo[dim]
      : axis === 'x' ? hi[dim] - lo[dim] : hi[dim] - lo[dim];
    const shown = Math.min(12, bounds);
    const stride = Math.max(1, Math.ceil(bounds / shown));
    ctx.strokeStyle = 'rgba(37,99,235,0.5)';
    for (let p = 0; p < bounds; p += stride) {
      const position = lo[dim] + p;
      const points = plane(lo, hi, dim, position);
      lineLoop(points);
      const projected = points.map(project);
      if (projected.some(v => v.depth < -1 || v.depth > 1)) continue;
      const center = projected.reduce((acc, v) => ({ x: acc.x + v.x / 4, y: acc.y + v.y / 4,
        depth: Math.min(acc.depth, v.depth) }), { x: 0, y: 0, depth: Infinity });
      slicePreview.pickTargets.push({ index: axis === 'x' ? structBox.x0 + position
        : axis === 'y' ? structBox.y0 + position : structBox.z0 + position,
        x: center.x / dpr, y: center.y / dpr, depth: center.depth });
    }
  }
}

$('btnSliceBatch').onclick = startSlicePreview;
$('sliceGap').addEventListener('input', e => setSlicePreviewGap(e.target.value));
$('sliceGapNumber').addEventListener('input', e => setSlicePreviewGap(e.target.value));
$('btnSlicePreviewReset').onclick = () => resetSlicePreview();

function updateSliceList() {
  const list = $('sliceList'), active = slicePreview.enabled;
  const multi = slicePreview.selectionMode === 'multi';
  const indices = active ? slicePreview.indices : [];
  const key = JSON.stringify([slicePreview.axis, indices]);
  if (list.dataset.key !== key) {
    list.dataset.key = key;
    const rows = indices.map(index => {
      const label = document.createElement('label');
      label.className = 'slice-choice'; label.dataset.index = index;
      const input = document.createElement('input');
      input.name = 'slice-choice'; input.value = index;
      input.setAttribute('aria-label', `${slicePreview.axis.toUpperCase()}=${index}`);
      input.addEventListener('change', () => {
        if (slicePreview.selectionMode === 'single') selectSlices([index]);
        else {
          const selected = new Set(slicePreview.selected || []);
          input.checked ? selected.add(index) : selected.delete(index);
          selectSlices([...selected]);
        }
      });
      const text = document.createElement('span');
      text.textContent = `${slicePreview.axis.toUpperCase()} = ${index}`;
      label.append(input, text);
      return label;
    });
    list.replaceChildren(...rows);
  }
  list.querySelectorAll('input').forEach(input => {
    input.type = multi ? 'checkbox' : 'radio';
    input.checked = slicePreview.selected !== null && slicePreview.selected.includes(Number(input.value));
    input.closest('label').classList.toggle('on', input.checked);
    input.disabled = !active || slicePreview.axis !== $('sliceAxis').value;
  });
  $('sliceSelectionCount').textContent = !active ? '尚未生成'
    : slicePreview.selected === null ? `全部 ${indices.length} 片` : `已选 ${slicePreview.selected.length} / ${indices.length}`;
  $('sliceListHint').textContent = !active ? '生成切片后，在这里选择要查看的切片。'
    : multi ? '勾选坐标查看多片；Esc 显示全部。' : '点击坐标查看截面；Esc 显示全部。';
  $('btnSliceShowAll').disabled = !active || slicePreview.selected === null;
  for (const id of ['btnSliceSelectAll', 'btnSliceSelectNone']) {
    $(id).hidden = !multi; $(id).disabled = !active;
  }
  document.querySelectorAll('#sliceSelectionMode button').forEach(button => {
    const on = button.dataset.mode === slicePreview.selectionMode;
    button.classList.toggle('on', on); button.setAttribute('aria-pressed', String(on));
  });
}

document.querySelectorAll('#sliceSelectionMode button').forEach(button => {
  button.onclick = () => {
    slicePreview.selectionMode = button.dataset.mode;
    if (slicePreview.enabled && slicePreview.selectionMode === 'single' && slicePreview.selected?.length > 1)
      selectSlices([slicePreview.selected[0]]);
    else if (slicePreview.enabled && slicePreview.selectionMode === 'multi' && slicePreview.selected === null)
      selectSlices([]);
    updateSliceList();
  };
});
$('btnSliceShowAll').onclick = () => selectSlices(null);
$('btnSliceSelectAll').onclick = () => selectSlices([...slicePreview.indices]);
$('btnSliceSelectNone').onclick = () => selectSlices([]);
