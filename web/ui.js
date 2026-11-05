/* ui.js — 视图切换、底栏输入、导出与设置 */
const workflowExportFormats = { selection: 'glb', preview: 'glb' };

/* ============================================================
   视图与控件
   ============================================================ */
function setView(v) {
  setStep(v === '2d' ? 'selection' : 'preview');
}

function setStep(step) {
  if (step === 'slices') {
    $('experimentalFeatureDialog').showModal();
    return;
  }
  if (!['selection', 'preview'].includes(step)) return;
  if (S.step !== step) {
    workflowExportFormats[S.step] = $('sliceFormat').value;
    $('sliceFormat').value = workflowExportFormats[step];
  }
  const v = step === 'selection' ? '2d' : '3d';
  const viewChanged = S.view !== v;
  S.step = step;
  S.view = v;
  document.body.dataset.step = step;
  try { sessionStorage.setItem('mc3d-workflow-step', step); } catch {}
  document.body.classList.toggle('mode-3d', v === '3d');
  $('wrap3d').classList.toggle('on', v === '3d');
  document.querySelectorAll('#viewSeg button').forEach(b => {
    b.classList.toggle('on', b.dataset.step === step);
    if (b.dataset.step === step) b.setAttribute('aria-current', 'step');
    else b.removeAttribute('aria-current');
  });
  update3dModeButtons();
  updateBar();
  if (v === '3d') {
    init3d();
    resize3d();
    resizeMini();
    $('empty3d').style.display = 'none';
    if (viewChanged) schedule3d(true);
  } else {
    resize2d();
  }
  syncLayout();   // 视图切换后浮层可见性变了，重新堆叠
  updateGuideExpanded(step === 'selection');
  updateSliceSummary();
  updateExportOptions();
  updateDockSummary();
  invalidate3d();
}

$('btnExperimentalFeatureClose').onclick = () => $('experimentalFeatureDialog').close();

function update3dModeButtons() {
  document.querySelectorAll('#mode3dSeg button').forEach(b => {
    const on = b.dataset.mode3d === (S.showGrid ? 'grid' : 'solid');
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', String(on));
  });
}

function toggleGrid() {
  S.showGrid = !S.showGrid;
  update3dModeButtons();
  draw2d();
  invalidate3d();
}

document.querySelectorAll('#mode3dSeg button').forEach(b => {
  b.onclick = () => {
    S.showGrid = b.dataset.mode3d === 'grid';
    update3dModeButtons();
    invalidate3d();
  };
});
update3dModeButtons();

function resize2d() {
  const r = cv2d.getBoundingClientRect();
  cv2d.width = Math.round(r.width * dpr);
  cv2d.height = Math.round(r.height * dpr);
  draw2d();
}

document.querySelectorAll('#viewSeg button').forEach(b => {
  b.onclick = () => setStep(b.dataset.step);
});

document.querySelectorAll('#layerSeg button').forEach(b => {
  b.onclick = () => {
    S.layer = b.dataset.layer;
    document.querySelectorAll('#layerSeg button').forEach(x =>
      x.classList.toggle('on', x === b));
    requestTile(true);
  };
});

$('btnSpawn').onclick = () => {
  if (!S.info) return;
  S.gx = S.info.spawn.x;
  S.gz = S.info.spawn.z;
  S.span = 192;
  draw2d();
  requestTile(true);
  if (S.view === '3d') schedule3d(true);
};

$('btnFitSel').onclick = () => {
  if (!S.sel) return;
  const s = S.sel;
  S.gx = ((s.cx0 + s.cx1 + 1) * CH) / 2;
  S.gz = ((s.cz0 + s.cz1 + 1) * CH) / 2;
  S.span = clamp((s.cx1 - s.cx0 + 1) * CH * 1.6, MIN_SPAN, MAX_SPAN);
  draw2d();
  requestTile(true);
};

// 复位 2.5D 视角：重新适配选区包围球（拖飞丢失时一键找回）
function reset3dView() {
  if (slicePreviewBounds()) {
    fitSliceCamera(slicePreview.bounds);
    return;
  }
  fitKey = '';
  if (lastBuiltMeta) {
    const W = lastBuiltMeta.w, D = lastBuiltMeta.d;
    const H = lastBuiltMeta.yMax - lastBuiltMeta.yMin + 1;
    const radius = Math.sqrt((W / 2) ** 2 + (H / 2) ** 2 + (D / 2) ** 2);
    const fov = 70 * Math.PI / 180;
    cam.target.set(W / 2, H / 2, D / 2);
    cam.dist = radius / Math.sin(fov / 2) * 1.35;
    cam.theta = Math.PI * 0.28;
    cam.phi = Math.PI * 0.30;
    invalidate3d();
  } else if (S.view === '3d') {
    schedule3d(true);
  }
}
$('btnReset3d').onclick = reset3dView;

function applyY() {
  useManualHeight();
  const previous = [S.y0, S.y1].join(',');
  const a = parseInt($('yBotIn').value, 10);
  const b = parseInt($('yTopIn').value, 10);
  if (Number.isFinite(a)) S.y0 = clamp(a, S.info?.minY ?? -64, S.info?.maxY ?? 319);
  if (Number.isFinite(b)) S.y1 = clamp(b, S.info?.minY ?? -64, S.info?.maxY ?? 319);
  if (S.y0 !== null && S.y1 !== null && S.y0 >= S.y1) {
    S.y0 = S.y1 - 1;
    $('yBotIn').value = S.y0;
  }
  updateBar();
  if ([S.y0, S.y1].join(',') !== previous) schedule3d(true);
}

$('yBotIn').addEventListener('change', applyY);
$('yTopIn').addEventListener('change', applyY);



/* ---------- 非整区块切片：X/Z 方块级范围 ---------- */
function applyBox() {
  const get = id => parseInt($(id).value, 10);
  const x0 = get('x0In'), x1 = get('x1In'), z0 = get('z0In'), z1 = get('z1In');
  if (![x0, x1, z0, z1].every(Number.isFinite)) {
    S.box = null;
  } else {
    S.box = {
      x0: Math.min(x0, x1), x1: Math.max(x0, x1),
      z0: Math.min(z0, z1), z1: Math.max(z0, z1),
    };
    // 同步显示规范化后的值
    $('x0In').value = S.box.x0; $('x1In').value = S.box.x1;
    $('z0In').value = S.box.z0; $('z1In').value = S.box.z1;
  }
  updateBar();
  draw2d();
  if (S.view === '3d') schedule3d(true);
}
function clearBox() {
  S.box = null;
  for (const id of ['x0In', 'x1In', 'z0In', 'z1In']) $(id).value = '';
  updateBar();
  draw2d();
  if (S.view === '3d') schedule3d(true);
}
function syncBoxFromInputs(rebuild = false) {
  const previous = JSON.stringify([S.box, S.y0, S.y1]);
  const get = id => parseInt($(id).value, 10);
  const x0v = get('x0In'), x1v = get('x1In'), z0v = get('z0In'), z1v = get('z1In');
  const y0v = parseInt($('yBotIn').value, 10);
  const y1v = parseInt($('yTopIn').value, 10);

  if ([x0v, x1v, z0v, z1v].some(Number.isFinite)) {
    const defX0 = typeof structBox !== 'undefined' ? structBox.x0 : 0;
    const defX1 = typeof structBox !== 'undefined' && typeof lastBuiltMeta !== 'undefined' && lastBuiltMeta ? structBox.x0 + lastBuiltMeta.w - 1 : defX0;
    const defZ0 = typeof structBox !== 'undefined' ? structBox.z0 : 0;
    const defZ1 = typeof structBox !== 'undefined' && typeof lastBuiltMeta !== 'undefined' && lastBuiltMeta ? structBox.z0 + lastBuiltMeta.d - 1 : defZ0;
    const x0 = Number.isFinite(x0v) ? x0v : defX0;
    const x1 = Number.isFinite(x1v) ? x1v : defX1;
    const z0 = Number.isFinite(z0v) ? z0v : defZ0;
    const z1 = Number.isFinite(z1v) ? z1v : defZ1;
    S.box = {
      x0: Math.min(x0, x1), x1: Math.max(x0, x1),
      z0: Math.min(z0, z1), z1: Math.max(z0, z1),
    };
  }
  if (Number.isFinite(y0v)) S.y0 = clamp(y0v, S.info?.minY ?? -64, S.info?.maxY ?? 319);
  if (Number.isFinite(y1v)) S.y1 = clamp(y1v, S.info?.minY ?? -64, S.info?.maxY ?? 319);

  updateBar();
  invalidate3d();
  if (rebuild && S.view === '3d' && S.y0 <= S.y1 &&
      JSON.stringify([S.box, S.y0, S.y1]) !== previous) {
    schedule3d(false);
  }
}

$('btnSliceClear').onclick = clearBox;
for (const id of ['x0In', 'x1In', 'z0In', 'z1In']) {
  $(id).addEventListener('input', () => syncBoxFromInputs(true));
  $(id).addEventListener('change', () => syncBoxFromInputs(true));
  $(id).addEventListener('keydown', e => {
    if (e.key === 'Enter') applyBox();
    if (e.key === 'Escape') { e.target.blur(); }
  });
}
$('yBotIn').addEventListener('input', () => { useManualHeight(); syncBoxFromInputs(true); });
$('yTopIn').addEventListener('input', () => { useManualHeight(); syncBoxFromInputs(true); });
$('yBotIn').addEventListener('keydown', e => { if (e.key === 'Enter') applyY(); });
$('yTopIn').addEventListener('keydown', e => { if (e.key === 'Enter') applyY(); });

for (const id of ['scaleSel', 'sliceScale']) {
  const syncScale = () => {
    for (const other of ['scaleSel', 'sliceScale']) $(other).value = $(id).value;
    updateBar();
    refreshSlicePreviewSettings();
  };
  $(id).addEventListener('input', syncScale);
  $(id).addEventListener('change', syncScale);
}
$('sliceThickness').addEventListener('input', () => { updateBar(); refreshSlicePreviewSettings(); });

// 右上角导出菜单：点按钮展开/收起，点外面关闭
$('btnExport').onclick = e => {
  e.stopPropagation();
  $('exportMenu').classList.toggle('open');
};
document.addEventListener('click', () => $('exportMenu').classList.remove('open'));
$('exportMenu').addEventListener('click', e => e.stopPropagation());

// 导出：先弹 Windows 另存为弹窗（选位置/改文件名），确认后生成并保存到该位置
async function saveExport(kind = 'glb') {
  try { if (!await ensureAutoHeight()) return; }
  catch (error) { showErr('地表高度探测失败：' + error.message); return; }
  const scale = parseFloat($('scaleSel').value);
  if (!Number.isFinite(scale) || scale <= 0) {
    showErr('比例必须是大于 0 的有限数值。');
    return;
  }
  const r = activeRange();
  if (r.n > 64) {   // 体素化导出上限 64 区块（与 2.5D 预览一致）
    alert('导出范围超过 64 区块（体素化上限），请缩小选区');
    return;
  }
  const q = new URLSearchParams({
    cx0: r.cx0, cz0: r.cz0, cx1: r.cx1, cz1: r.cz1,
    scale,
  });
  // 切片盒也参与导出（与预览范围一致）
  if (S.box) {
    q.set('x0', S.box.x0); q.set('x1', S.box.x1);
    q.set('z0', S.box.z0); q.set('z1', S.box.z1);
  }
  if (S.y0 !== null) q.set('y0', S.y0);
  if (S.y1 !== null) q.set('y1', S.y1);
  if (kind === '3mf') q.set('colors', $('sliceColors').value);
  if (kind === 'glb' || kind === 'obj') q.set('decorations', S.includeDecorations ? '1' : '0');
  const b = S.box;
  const xz = b
    ? `${b.x0}-${b.x1}_${b.z0}-${b.z1}`
    : `${r.cx0 * CH}-${r.cx1 * CH - 1}_${r.cz0 * CH}-${r.cz1 * CH - 1}`;
  const ext = kind === 'obj' ? 'zip' : kind;
  const prefix = 'MC切片';
  const suggestedName = `${prefix}_${xz}.${ext}`;

  // 1) 先弹系统"另存为"（必须在点击手势内；用户取消则中止）
  let handle = null;
  if (window.showSaveFilePicker) {
    try {
      handle = await window.showSaveFilePicker({
        suggestedName,
        types: [{
          description: kind === 'glb' ? 'GLB 内嵌贴图模型' : kind === 'obj' ? 'OBJ 多色模型包' : kind === '3mf' ? 'AMS 多色 3MF' : 'STL 模型',
          accept: { 'application/octet-stream': ['.' + ext] },
        }],
      });
    } catch (e) {
      if (e && e.name === 'AbortError') return;   // 用户点了取消
      handle = null;                               // 其它失败 → 走传统下载
    }
  }

  // 2) 生成 + 写入所选位置
  setLoading(true, '正在生成导出文件…');
  if (kind === '3mf') {
    $('exportStatus').style.display = 'block';
    $('exportStatus').textContent = '正在生成基础分色 3MF…';
  }
  try {
    const resp = await fetchRetry('/api/export_voxel.' + kind + '?' + q.toString());
    if (!resp.ok) {
      let msg = '导出生成失败';
      try { msg = (await resp.json()).error || msg; } catch {}
      throw new Error(msg);
    }
    const blob = await resp.blob();
    const native = !handle && await saveNativeBlob(blob, suggestedName);
    if (native && native.cancelled) {
      showErr('已取消保存。', 'info');
      if (kind === '3mf') $('exportStatus').textContent = '已取消保存。';
      return;
    }
    if (native && native.saved) {
      showErr('已保存：' + suggestedName, 'success');
    } else if (handle) {
      const w = await handle.createWritable();
      await w.write(blob);
      await w.close();
      showErr('已保存：' + (handle.name || suggestedName), 'success');
    } else {
      const a = document.createElement('a');
      const u = URL.createObjectURL(blob);
      a.href = u;
      a.download = suggestedName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(u), 5000);
      showErr('已生成下载：' + suggestedName, 'success');
    }
    if (kind === '3mf') $('exportStatus').textContent = `已生成 ${resp.headers.get('X-Colors')} 色 3MF。`;
  } catch (e) {
    console.error(e);
    showErr('导出失败：' + e.message);
    if (kind === '3mf') $('exportStatus').textContent = '导出失败：' + e.message;
  } finally {
    setLoading(false);
  }
}

let sliceObjectUrl = null;
let sliceExporting = false;

function updateExportOptions() {
  const format = $('sliceFormat').value;
  $('exportTitle').textContent = '导出当前模型';
  $('sliceFormat').closest('label').hidden = false;
  $('sliceExportModeField').hidden = true;
  $('btnSliceExport').textContent = '导出模型';
  $('sliceColorsField').hidden = format !== '3mf';
  $('decorationExportField').hidden = !['glb', 'obj'].includes(format);
  $('btnSliceExport').disabled = false;
  $('sliceFormatHint').textContent = {
    glb: 'GLB 将模型和贴图保存在同一个文件中。',
    obj: 'OBJ 保留材质和贴图。请将 ZIP 解压后导入，并保持 OBJ、MTL、PNG 在同一目录。',
    stl: 'STL 只包含单色几何，不保存颜色。',
    '3mf': '3MF 将方块代表色归并为所选颜色数量。',
  }[format];
}
$('sliceFormat').onchange = updateExportOptions;
$('includeDecorations').onchange = () => {
  S.includeDecorations = $('includeDecorations').checked;
  if (S.view === '3d') schedule3d(true);
};

document.querySelectorAll('#sliceAxisSeg button').forEach(b => {
  b.onclick = () => {
    $('sliceAxis').value = b.dataset.axis;
    document.querySelectorAll('#sliceAxisSeg button').forEach(x => {
      x.classList.toggle('on', x === b);
      x.setAttribute('aria-pressed', String(x === b));
    });
    updateBar();
    changeSliceDirection();
  };
});

function setSliceStatus(text, visible = true) {
  const el = $('sliceStatus');
  el.textContent = text;
  el.style.display = visible ? 'block' : 'none';
}

async function saveNativeBlob(blob, suggestedName) {
  const api = window.pywebview && window.pywebview.api;
  if (api && typeof api.save_file === 'function') {
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    const result = await api.save_file(suggestedName, btoa(binary));
    if (result && result.ok) return { saved: true };
    if (result && result.cancelled) return { cancelled: true };
    throw new Error((result && result.msg) || '保存文件失败');
  }
  return null;
}

async function saveSliceBlob(blob, suggestedName) {
  const native = await saveNativeBlob(blob, suggestedName);
  if (native) return native;

  if (sliceObjectUrl) URL.revokeObjectURL(sliceObjectUrl);
  sliceObjectUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = sliceObjectUrl;
  a.download = suggestedName;
  a.textContent = '下载片化 ZIP';
  a.className = 'slice-download-link';
  $('sliceResult').replaceChildren(a);
  $('sliceResult').style.display = 'block';
  a.click();
  return { saved: false, link: a };
}

async function runSliceExport() {
  if (sliceExporting) return;
  try { if (!await ensureAutoHeight()) return; }
  catch (error) { showErr('地表高度探测失败：' + error.message); return; }
  const r = activeRange();
  if (r.n > 64) {
    setSliceStatus('导出范围超过 64 区块，请缩小选区。');
    return;
  }
  const b = S.box;
  const x0 = b ? b.x0 : r.cx0 * CH;
  const z0 = b ? b.z0 : r.cz0 * CH;
  const x1 = b ? b.x1 : r.cx1 * CH - 1;
  const z1 = b ? b.z1 : r.cz1 * CH - 1;
  const axis = $('sliceAxis').value;
  if (!['x', 'y', 'z'].includes(axis)) return;
  const thickness = parseFloat($('sliceThickness').value);
  const scale = parseFloat($('sliceScale').value);
  if (!Number.isFinite(thickness) || !Number.isFinite(scale) || thickness <= 0 || scale <= 0) {
    setSliceStatus('厚度和比例必须是大于 0 的有限数值。');
    return;
  }
  const stand = $('sliceStand').checked;
  const format = $('sliceFormat').value;
  const mode = $('sliceExportMode').value;
  if (mode === 'single' && (slicePreview.focus === null || slicePreview.busy)) {
    setSliceStatus('请先生成预览并双击要导出的切片，再导出当前单片。');
    return;
  }
  if (mode === 'selected' && (!slicePreview.enabled || slicePreview.busy || !slicePreview.selected?.length)) {
    setSliceStatus('请先在右侧选择要导出的切片。');
    return;
  }
  const q = new URLSearchParams({
    x0, z0, x1, z1,
    y0: S.y0 ?? -64,
    y1: S.y1 ?? 320,
    axis, mode, thickness, scale, format,
    colors: $('sliceColors').value,
    stand: stand ? '1' : '0',
  });
  if (mode === 'single') q.set('index', slicePreview.focus);
  if (mode === 'selected') q.set('indices', slicePreview.selected.join(','));
  const suffix = mode === 'single' ? `_${slicePreview.focus}`
    : mode === 'selected' ? `_所选${slicePreview.selected.length}片` : '';
  const suggestedName = `MC片化_${axis}_${format}${suffix}.zip`;
  sliceExporting = true;
  $('btnSliceExport').disabled = true;
  $('sliceResult').style.display = 'none';
  $('sliceResult').replaceChildren();
  setSliceStatus('正在生成片化切片…');
  setLoading(true, '正在生成片化切片…');
  try {
    const resp = await fetchRetry('/api/export_slices.zip?' + q.toString());
    if (!resp.ok) {
      let msg = '片化导出失败';
      try { msg = (await resp.json()).error || msg; } catch {}
      throw new Error(msg);
    }
    const blob = await resp.blob();
    const result = await saveSliceBlob(blob, suggestedName);
    if (result.cancelled) {
      setSliceStatus('已取消保存。');
      showErr('已取消保存。', 'info');
    } else if (result.saved) {
      setSliceStatus(`已生成并保存 ${suggestedName}（${Math.ceil(blob.size / 1024)} KB）。`);
      showErr('已保存：' + suggestedName, 'success');
    } else {
      setSliceStatus(`已生成 ${suggestedName}（${Math.ceil(blob.size / 1024)} KB），如未自动下载请点击下方链接。`);
      showErr('已生成下载：' + suggestedName, 'success');
    }
  } catch (e) {
    setSliceStatus('片化失败：' + e.message);
    showErr('片化失败：' + e.message);
  } finally {
    setLoading(false);
    sliceExporting = false;
    $('btnSliceExport').disabled = false;
  }
}
$('btnSliceExport').onclick = async () => {
  $('exportMenu').classList.remove('open');
  if (S.step === 'slices') return runSliceExport();
  $('btnSliceExport').disabled = true;
  try { await saveExport($('sliceFormat').value); }
  finally { $('btnSliceExport').disabled = false; }
};

function updateDockSummary() {
  $('dockSelectionText').textContent = $('sChunks').textContent;
  $('dockSizeText').textContent = $('sSize').textContent;
}
$('btnDockRange').onclick = () => {
  const expanded = $('btnDockRange').getAttribute('aria-expanded') !== 'true';
  $('btnDockRange').setAttribute('aria-expanded', String(expanded));
  $('selbar').classList.toggle('range-open', expanded);
  syncLayout();
};
new MutationObserver(updateDockSummary).observe($('selInfo'), { childList: true, subtree: true, characterData: true });

window.addEventListener('keydown', e => {
  const formInput = ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName) || e.target.isContentEditable;
  const sliceChoice = e.target.matches('#sliceList input');
  if (formInput && !(sliceChoice && e.key === 'Escape')) return;
  if (e.key === 'Escape' && S.step === 'slices') {
    if (!exitSliceFocus() && (slicePreview.enabled || slicePreview.busy)) resetSlicePreview();
    return;
  }
  if (e.key === 'Escape') clearSelection();
  if (e.key === '2') setView('2d');
  if (e.key === '3') setView('3d');
  if (e.key === 'g' || e.key === 'G') toggleGrid();
  if (e.key === 'r' || e.key === 'R') reset3dView();
  if (e.key === ' ') { e.preventDefault(); setView(S.view === '3d' ? '2d' : '3d'); }
});

/* ---------- 底部浮层堆叠同步 ----------
   底栏控件会随窗口宽度换行、数值变长后整条变高，任何写死的 bottom 迟早重叠。
   这里实测真实高度，回灌成 CSS 变量（--selbar-h / --hud-h），
   HUD、操作指引、缩略图全部靠变量定位。 */
function syncLayout() {
  const bar = $('selbar'), hud = $('hud');
  if (!bar || !hud) return;
  const bh = Math.ceil(bar.getBoundingClientRect().height);
  const hh = Math.ceil(hud.getBoundingClientRect().height) || 36;
  const root = document.documentElement.style;
  // 只在真的变了才写，避免 ResizeObserver 反复触发
  if (root.getPropertyValue('--selbar-h') !== bh + 'px') {
    root.setProperty('--selbar-h', bh + 'px');
  }
  if (hh && root.getPropertyValue('--hud-h') !== hh + 'px') {
    root.setProperty('--hud-h', hh + 'px');
  }
}

let layoutRO = null;
function watchLayout() {
  syncLayout();
  if (layoutRO) return;
  if (typeof ResizeObserver === 'undefined') {
    window.addEventListener('resize', syncLayout);
    return;
  }
  // 底栏排版高度 + HUD 自身高度，任一变化都重新堆叠
  layoutRO = new ResizeObserver(() => requestAnimationFrame(syncLayout));
  [$('selbar'), $('hud')].forEach(el => el && layoutRO.observe(el));
  window.addEventListener('resize', syncLayout);
  // 字体加载完成后字形度量会变，补测一次
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(syncLayout);
}
watchLayout();

if ($('btnTogglePanel')) {
  $('btnTogglePanel').onclick = e => {
    e.stopPropagation();
    const p = $('panel');
    if (p) p.classList.toggle('collapsed');
  };
}

function updateGuideExpanded(isOpen) {
  const w = $('guideWrap');
  const btn = $('btnToggleGuide');
  if (w) w.classList.toggle('open', isOpen);
  if (btn) btn.setAttribute('aria-expanded', String(isOpen));
}

if ($('btnToggleGuide')) {
  $('btnToggleGuide').onclick = e => {
    e.stopPropagation();
    const w = $('guideWrap');
    if (w) {
      const isOpen = !w.classList.contains('open');
      updateGuideExpanded(isOpen);
    }
  };
}

if (typeof renderIconsIn === 'function') {
  renderIconsIn(document.body);
}

// 绑定自绘 −/+ 步进器按钮交互
document.addEventListener('click', e => {
  const btn = e.target.closest('.step-btn');
  if (!btn) return;
  const targetId = btn.dataset.for;
  const input = targetId ? $(targetId) : null;
  if (!input) return;
  const isUp = btn.classList.contains('step-up');
  const step = parseInt(input.step, 10) || 1;
  const min = input.min !== '' ? parseInt(input.min, 10) : -Infinity;
  const max = input.max !== '' ? parseInt(input.max, 10) : Infinity;
  let val = parseInt(input.value, 10);
  if (!Number.isFinite(val)) {
    const bounds = typeof getBoxBounds === 'function' ? getBoxBounds() : null;
    if (bounds) {
      if (targetId === 'x0In') val = bounds.bx0;
      else if (targetId === 'x1In') val = bounds.bx1;
      else if (targetId === 'z0In') val = bounds.bz0;
      else if (targetId === 'z1In') val = bounds.bz1;
      else if (targetId === 'yBotIn') val = bounds.by0;
      else if (targetId === 'yTopIn') val = bounds.by1;
      else val = 0;
    } else {
      val = 0;
    }
  } else {
    val = isUp ? val + step : val - step;
  }
  input.value = clamp(val, min, max);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
});

window.addEventListener('resize', () => {
  dpr = Math.min(window.devicePixelRatio || 1, 2);
  resize2d();
  resize3d();
  syncLayout();
});


/* ---------- 选区预设（localStorage） ---------- */
const PRESET_KEY = 'mc3d_presets';
function loadPresets() {
  try { return JSON.parse(localStorage.getItem(PRESET_KEY)) || {}; } catch (e) { return {}; }
}
function savePresets(obj) {
  try { localStorage.setItem(PRESET_KEY, JSON.stringify(obj)); } catch (e) { /* 忽略 */ }
}
function renderPresetList() {
  const box = $('presetList');
  const all = loadPresets();
  const names = Object.keys(all);
  if (!names.length) {
    box.innerHTML = '<div class="preset-empty">还没有预设：填名字点保存</div>';
    return;
  }
  box.innerHTML = '';
  for (const name of names) {
    const row = document.createElement('div');
    row.className = 'preset-item';
    const label = document.createElement('span');
    label.textContent = name;
    label.style.flex = '1';
    label.onclick = ev => { ev.stopPropagation(); applyPreset(all[name]); $('presetMenu').classList.remove('open'); };
    const del = document.createElement('button');
    del.className = 'del'; del.innerHTML = getSvgIcon('trash', 15); del.title = '删除该预设';
    del.setAttribute('aria-label', '删除该预设');
    del.onclick = ev => {
      ev.stopPropagation();
      const cur = loadPresets(); delete cur[name]; savePresets(cur); renderPresetList();
    };
    row.appendChild(label); row.appendChild(del);
    box.appendChild(row);
  }
}
function capturePreset() {
  return {
    sel: S.sel, y0: S.y0, y1: S.y1, box: S.box,
    x0: $('x0In').value, x1: $('x1In').value, z0: $('z0In').value, z1: $('z1In').value,
    scale: $('scaleSel').value, span: S.span, gx: S.gx, gz: S.gz,
  };
}
function applyPreset(p) {
  if (!p) return;
  S.sel = p.sel || null;
  if (Number.isFinite(p.y0) && Number.isFinite(p.y1)) useManualHeight();
  else resetAutoHeight();
  S.y0 = p.y0 ?? null;
  S.y1 = p.y1 ?? null;
  $('yBotIn').value = S.y0 ?? '';
  $('yTopIn').value = S.y1 ?? '';
  const vals = [p.x0 || '', p.x1 || '', p.z0 || '', p.z1 || ''];
  ['x0In', 'x1In', 'z0In', 'z1In'].forEach((id, i) => { $(id).value = vals[i]; });
  if (p.scale) $('scaleSel').value = p.scale;
  if (p.span) S.span = p.span;
  if (p.gx !== undefined) S.gx = p.gx;
  if (p.gz !== undefined) S.gz = p.gz;
  updateAutoHeight();
  updateBar();
  draw2d();
  requestTile(true);
  if (S.view === '3d') schedule3d(true);
}
$('btnPreset').onclick = e => {
  e.stopPropagation();
  renderPresetList();
  $('presetMenu').classList.toggle('open');
};
$('btnPresetSave').onclick = e => {
  e.stopPropagation();
  const name = ($('presetName').value || '').trim();
  if (!name) return;
  const all = loadPresets();
  all[name] = capturePreset();
  savePresets(all);
  $('presetName').value = '';
  renderPresetList();
};
$('presetMenu').addEventListener('click', e => e.stopPropagation());
document.addEventListener('click', () => $('presetMenu').classList.remove('open'));


/* ---------- 存档切换（顶栏世界名） ---------- */
async function renderWorldMenu() {
  const menu = $('worldMenu');
  menu.innerHTML = '<div class="preset-empty">读取存档列表…</div>';
  try {
    const resp = await fetch('/api/worlds');
    const data = await resp.json();
    menu.innerHTML = '';
    for (const w of data.worlds || []) {
      const item = document.createElement('div');
      item.className = 'world-item' + (w.path === data.current ? ' current' : '');
      const n = document.createElement('div'); n.className = 'wname'; n.textContent = w.name;
      const m = document.createElement('div'); m.className = 'wmeta';
      m.textContent = `${w.regions} 个区域 · ${w.path}`;
      item.appendChild(n); item.appendChild(m);
      item.onclick = async ev => {
        ev.stopPropagation();
        if (w.path === data.current) { menu.classList.remove('open'); return; }
        menu.innerHTML = '<div class="preset-empty">切换中…</div>';
        try {
          const r = await fetch('/api/world?path=' + encodeURIComponent(w.path));
          const info = await r.json();
          if (info.error) throw new Error(info.error);
          location.reload();   // 重新加载页面：清空 Worker 缓存与所有前端状态
        } catch (e) {
          menu.innerHTML = `<div class="preset-empty">切换失败：${e.message}</div>`;
        }
      };
      menu.appendChild(item);
    }
    if (!(data.worlds || []).length) {
      menu.innerHTML = '<div class="preset-empty">未找到其他存档（可在 config.json 指定）</div>';
    }
    // 底部：复制诊断信息（提 issue 用）
    const diagRow = document.createElement('div');
    diagRow.className = 'preset-empty';
    diagRow.style.cssText = 'cursor:pointer;border-top:1px solid var(--line);margin-top:6px;padding-top:8px;color:var(--dim)';
    diagRow.textContent = '复制诊断信息';
    diagRow.onclick = async ev => {
      ev.stopPropagation();
      try {
        const r = await fetch('/api/diag');
        const d = await r.json();
        d.ua = navigator.userAgent;
        d.screen = `${screen.width}x${screen.height}@${devicePixelRatio}`;
        await navigator.clipboard.writeText(JSON.stringify(d, null, 2));
        diagRow.textContent = '已复制 ✓';
      } catch (e) {
        diagRow.textContent = '复制失败：' + e.message;
      }
    };
    menu.appendChild(diagRow);
  } catch (e) {
    menu.innerHTML = `<div class="preset-empty">读取失败：${e.message}</div>`;
  }
}
$('worldWrap').onclick = e => {
  e.stopPropagation();
  const menu = $('worldMenu');
  if (!menu.classList.contains('open')) renderWorldMenu();
  menu.classList.toggle('open');
};
$('worldMenu').addEventListener('click', e => e.stopPropagation());
document.addEventListener('click', () => $('worldMenu').classList.remove('open'));
