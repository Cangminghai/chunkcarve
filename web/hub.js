/* hub.js — 开屏动画、项目主页、整合包探测与重生点渲染管理 */

'use strict';

let currentProjects = [];
let currentModpacks = [];
let detectedSaves = [];
let selectedSavePath = '';
let editingProjectId = null;
let activeProjectId = null;
let splashDone = false;
let detectRequestId = 0;

/* ============================================================
   1. 开屏启动动画
   ============================================================ */
function initSplash() {
  const splash = $('splashScreen');
  if (!splash) return;

  const urlParams = new URLSearchParams(window.location.search);
  const skip = urlParams.get('skipSplash') === '1' || urlParams.get('test') === '1';

  function dismissSplash(immediate = false) {
    if (splashDone) return;
    splashDone = true;
    document.body.classList.remove('booting');
    if (immediate) {
      splash.style.display = 'none';
      return;
    }
    splash.classList.add('fade-out');
    setTimeout(() => {
      splash.style.display = 'none';
    }, 450);
  }

  window.skipSplash = () => dismissSplash(true);

  if (skip) {
    dismissSplash(true);
    return;
  }

  const fill = $('splashFill');
  const status = $('splashStatus');

  const steps = [
    { p: 25, t: '启动 WebGL 3D 渲染引擎...' },
    { p: 55, t: '扫描本地 Minecraft 实例与整合包...' },
    { p: 85, t: '载入项目工作区与存档配置...' },
    { p: 100, t: '系统就绪，正在进入工作区...' },
  ];

  let stepIdx = 0;
  const interval = setInterval(() => {
    if (splashDone) {
      clearInterval(interval);
      return;
    }
    const s = steps[stepIdx++];
    if (s) {
      if (fill) fill.style.width = s.p + '%';
      if (status) status.textContent = s.t;
    }
    if (stepIdx >= steps.length) {
      clearInterval(interval);
      setTimeout(() => dismissSplash(false), 300);
    }
  }, 260);

  // 点击或按键立即跳过
  splash.addEventListener('click', () => dismissSplash(false));
  window.addEventListener('keydown', e => {
    if (!splashDone && (e.key === 'Escape' || e.key === ' ' || e.key === 'Enter')) {
      dismissSplash(false);
    }
  });
}

/* ============================================================
   2. 项目主页面（Project Hub）
   ============================================================ */
function showHub() {
  const hub = $('projectHub');
  if (hub) {
    hub.classList.add('on');
    hub.style.display = 'flex';
  }
  loadProjects();
  loadModpacks();
  // 若当前已有活跃存档，显示“进入观察器”按钮
  const btnBack = $('btnBackToViewer');
  if (btnBack) {
    btnBack.style.display = S.info ? 'inline-flex' : 'none';
  }
}

function hideHub() {
  const hub = $('projectHub');
  if (hub) {
    hub.classList.remove('on');
    setTimeout(() => {
      hub.style.display = 'none';
    }, 250);
  }
}

async function loadProjects() {
  try {
    const res = await fetch('/api/projects');
    const data = await res.json();
    currentProjects = data.projects || [];
    if (data.activeId) {
      activeProjectId = data.activeId;
    }
    renderProjects(currentProjects, data.active, activeProjectId);
  } catch (err) {
    console.error('加载项目失败:', err);
  }
}

async function loadModpacks() {
  const listEl = $('hubModpackList');
  try {
    const res = await fetch('/api/modpacks');
    currentModpacks = await res.json();
    renderModpackList(currentModpacks);
  } catch (err) {
    if (listEl) listEl.innerHTML = '<div class="hub-hint-empty">暂未探测到本地启动器实例</div>';
  }
}

function renderProjects(projects, activePath, activeId) {
  const grid = $('hubProjectGrid');
  const countEl = $('hubProjCount');
  if (countEl) countEl.textContent = projects.length;
  if (!grid) return;

  if (!projects || projects.length === 0) {
    grid.innerHTML = `
      <div class="hub-empty-card">
        <div class="hub-empty-icon">${typeof getSvgIcon === 'function' ? getSvgIcon('folder', 40) : '📁'}</div>
        <h3>暂无项目</h3>
        <p>创建新项目并选择本地整合包，软件将自动探测所有存档与贴图，并为您 3D 渲染重生点。</p>
        <button class="btn btn-primary" onclick="openProjectModal()"><span class="btn-icon-svg">${typeof getSvgIcon === 'function' ? getSvgIcon('plus', 14) : '+'}</span><span>创建第一个项目</span></button>
      </div>
    `;
    return;
  }

  const query = ($('hubSearchInp')?.value || '').toLowerCase().trim();
  const filtered = projects.filter(p => {
    if (!query) return true;
    return (p.name || '').toLowerCase().includes(query) ||
           (p.description || '').toLowerCase().includes(query) ||
           (p.modpack || '').toLowerCase().includes(query) ||
           (p.world || '').toLowerCase().includes(query);
  });

  grid.innerHTML = filtered.map((p, idx) => {
    // 优先按 activeId 匹配，若无 activeId 则按路径严格匹配
    let isActive = false;
    if (activeId) {
      isActive = (p.id === activeId);
    } else if (activePath && p.world) {
      isActive = (p.world.replace(/\\/g, '/').toLowerCase() === activePath.replace(/\\/g, '/').toLowerCase());
    }

    const saveName = p.world ? p.world.replace(/\\/g, '/').split('/').filter(Boolean).pop() : '未知存档';
    const modpackName = p.modpack ? p.modpack.replace(/\\/g, '/').split('/').filter(Boolean).pop() : '原版/未指定';
    const pathTail = p.world ? p.world.replace(/\\/g, '/').split('/').slice(-3).join('/') : '';
    const pkgIcon = getSvgIcon('package', 14);
    const globeIcon = getSvgIcon('globe', 14);
    const zapIcon = getSvgIcon('zap', 14);
    const folderIcon = getSvgIcon('folder', 14);
    const renderIcon = getSvgIcon('render', 16);

    return `
      <div class="hub-project-card ${isActive ? 'active' : ''}" data-id="${p.id}" data-idx="${idx}">
        <div class="card-head">
          <div class="card-thumb">
            <div class="cube-icon"></div>
          </div>
          <div class="card-title-wrap">
            <h4 class="card-title" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</h4>
            <span class="card-subtitle" title="${escapeHtml(p.description || '')}">${escapeHtml(p.description || 'ChunkCarve 世界选区项目')}</span>
          </div>
          ${isActive ? '<span class="card-active-pill">当前活跃</span>' : ''}
          <button class="btn-sm danger btn-icon btn-del-proj" data-id="${p.id}" title="删除项目" aria-label="删除项目">${getSvgIcon('trash', 16)}</button>
        </div>
        <div class="card-pills">
          <span class="pill pill-modpack" title="整合包">${pkgIcon} ${escapeHtml(modpackName)}</span>
          <span class="pill pill-save" title="存档">${globeIcon} ${escapeHtml(saveName)}</span>
          ${p.version ? `<span class="pill pill-ver">${zapIcon} ${escapeHtml(p.version)}</span>` : ''}
        </div>
        ${pathTail ? `<div class="card-path-tail mono" title="${escapeHtml(p.world || '')}"><span class="tail-icon">${folderIcon}</span><span>${escapeHtml(pathTail)}</span></div>` : ''}
        <div class="card-foot">
          <span class="card-time">${p.updatedAt || '刚刚'}</span>
          <div class="card-actions">
            <button class="btn-sm ghost btn-edit-proj" data-id="${p.id}" title="编辑项目">${getSvgIcon('edit', 15)}<span>编辑</span></button>
            <button class="btn-sm primary btn-launch-proj" data-id="${p.id}">${renderIcon}<span>开始渲染</span></button>
          </div>
        </div>
      </div>
    `;
  }).join('');

  // 绑定卡片内按钮事件，避免任何行内字符串转义问题
  grid.querySelectorAll('.btn-edit-proj').forEach(btn => {
    btn.onclick = e => {
      e.stopPropagation();
      editProject(btn.dataset.id);
    };
  });
  grid.querySelectorAll('.btn-del-proj').forEach(btn => {
    btn.onclick = e => {
      e.stopPropagation();
      deleteProject(btn.dataset.id);
    };
  });
  grid.querySelectorAll('.btn-launch-proj').forEach(btn => {
    btn.onclick = e => {
      e.stopPropagation();
      launchProjectById(btn.dataset.id, btn);
    };
  });
}

function renderModpackList(modpacks) {
  const listEl = $('hubModpackList');
  if (!listEl) return;
  if (!modpacks || modpacks.length === 0) {
    listEl.innerHTML = '<div class="hub-hint-empty">暂未探测到本地启动器实例</div>';
    return;
  }
  listEl.innerHTML = modpacks.map((m, idx) => {
    return `
      <div class="modpack-item" data-idx="${idx}">
        <div class="modpack-info">
          <div class="modpack-name" title="${escapeHtml(m.name)}">${escapeHtml(m.name)}</div>
          <div class="modpack-sub">版本: ${escapeHtml(m.version || '1.20.1')} · 模组: ${m.mod_count} 个 · 存档: ${m.saves_count} 个</div>
        </div>
        <button class="btn-sm ghost btn-create-from-modpack" data-idx="${idx}">+ 新建项目</button>
      </div>
    `;
  }).join('');

  listEl.querySelectorAll('.btn-create-from-modpack').forEach(btn => {
    btn.onclick = e => {
      e.stopPropagation();
      const idx = parseInt(btn.dataset.idx, 10);
      const m = currentModpacks[idx];
      if (m) createProjectFromModpackObject(m);
    };
  });
}

/* ============================================================
   3. 项目创建与编辑模态框（向导）
   ============================================================ */
function openProjectModal(proj = null) {
  editingProjectId = proj ? proj.id : null;
  const modal = $('projModal');
  const title = $('projModalTitle');
  if (title) title.textContent = proj ? '编辑项目配置' : '新建 3D 导出项目';

  $('modalProjName').value = proj ? proj.name : 'ChunkCarve 3D 项目';

  // 填充整合包下拉菜单
  const sel = $('modalModpackSelect');
  sel.innerHTML = '<option value="">-- 请选择检测到的整合包或自定义目录 --</option>';
  for (const m of currentModpacks) {
    const opt = document.createElement('option');
    opt.value = m.path;
    opt.dataset.name = m.name;
    opt.textContent = `${m.name} (${m.version || 'MC'} · ${m.mod_count} 个模组)`;
    sel.appendChild(opt);
  }
  const customOpt = document.createElement('option');
  customOpt.value = '__custom__';
  customOpt.textContent = '【自定义指定目录...】';
  sel.appendChild(customOpt);

  let targetModpack = '';
  if (proj && proj.modpack) {
    targetModpack = proj.modpack;
  } else if (currentModpacks.length > 0) {
    targetModpack = currentModpacks[0].path;
  }

  if (targetModpack) {
    sel.value = targetModpack;
    if (sel.value !== targetModpack) {
      sel.value = '__custom__';
      $('modalManualModpackRow').style.display = 'block';
      $('modalModpackPath').value = targetModpack;
    } else {
      $('modalManualModpackRow').style.display = 'none';
      $('modalModpackPath').value = '';
    }
  }

  $('modalJarPath').value = (proj && proj.jar) || '';
  $('modalModsPath').value = (proj && proj.mods) || '';

  selectedSavePath = (proj && proj.world) || '';
  if (!proj && targetModpack) {
    $('modalProjName').value = `${sel.selectedOptions[0]?.dataset.name || targetModpack.split(/[\\/]/).filter(Boolean).pop()} 3D 项目`;
  }

  // 触发自动探测
  const pathToDetect = (sel.value === '__custom__') ? $('modalModpackPath').value : (sel.value || targetModpack);
  triggerDetect(pathToDetect);

  if (modal) modal.style.display = 'flex';
}

function closeProjectModal() {
  const modal = $('projModal');
  if (modal) modal.style.display = 'none';
  detectRequestId++;
}

function clearDetectionResults() {
  detectRequestId++;
  const badge = $('detectBadge');
  if (badge) {
    badge.textContent = '就绪';
    badge.className = 'badge';
  }
  $('modalJarPath').value = '';
  $('modalModsPath').value = '';
  $('modalSavesCount').textContent = '0';
  $('modalSavesList').innerHTML = '<div class="saves-empty-hint">请先选择具体整合包以探测存档</div>';
  $('pillSaves').textContent = '存档: 等待探测';
  $('pillJar').textContent = '版本核心: 等待探测';
  $('pillMods').textContent = '模组: 等待探测';
  $('pillRps').textContent = '材质包: 等待探测';
  $('modalDetectHint').hidden = true;
  selectedSavePath = '';
  detectedSaves = [];
}

async function triggerDetect(modpackPath) {
  if (!modpackPath || modpackPath === '__custom__') return;
  const requestId = ++detectRequestId;
  $('modalDetectHint').hidden = true;
  const badge = $('detectBadge');
  if (badge) {
    badge.textContent = '探测中...';
    badge.className = 'badge loading';
  }

  try {
    const res = await fetch('/api/detect?path=' + encodeURIComponent(modpackPath));
    const data = await res.json();
    if (requestId !== detectRequestId) return;
    if (!data.ok) return;

    if (Array.isArray(data.instances)) {
      const select = $('modalModpackSelect');
      const known = new Set(Array.from(select.options, option => option.value));
      const customOption = Array.from(select.options).find(option => option.value === '__custom__');
      for (const instance of data.instances) {
        if (known.has(instance.path)) continue;
        const option = document.createElement('option');
        option.value = instance.path;
        option.dataset.name = instance.name;
        option.textContent = `${instance.name} (${instance.version || 'MC'} · ${instance.mod_count} 个模组 · ${instance.saves_count} 个存档)`;
        select.insertBefore(option, customOption || null);
      }
      select.value = '__custom__';
      $('modalJarPath').value = '';
      $('modalModsPath').value = '';
      $('modalSavesCount').textContent = '0';
      $('modalSavesList').innerHTML = '<div class="saves-empty-hint">请从上方选择一个具体整合包</div>';
      selectedSavePath = '';
      detectedSaves = [];
      if (!editingProjectId) $('modalProjName').value = 'ChunkCarve 3D 项目';
      $('pillSaves').textContent = `实例: ${data.instances.length} 个`;
      $('pillJar').textContent = '版本核心: 选择实例后探测';
      $('pillMods').textContent = '模组: 选择实例后探测';
      $('pillRps').textContent = '材质包: 选择实例后探测';
      $('modalDetectHint').textContent = '此目录包含多个整合包，请从上方下拉菜单选择具体实例。';
      $('modalDetectHint').hidden = false;
      if (badge) {
        badge.textContent = '请选择实例';
        badge.className = 'badge';
      }
      return;
    }

    // 更新探测标签
    const fIcon = typeof getSvgIcon === 'function' ? getSvgIcon('folder', 13) : '';
    const pIcon = typeof getSvgIcon === 'function' ? getSvgIcon('package', 13) : '';
    const zIcon = typeof getSvgIcon === 'function' ? getSvgIcon('puzzle', 13) : '';
    const plIcon = typeof getSvgIcon === 'function' ? getSvgIcon('palette', 13) : '';

    $('pillSaves').innerHTML = `${fIcon} 存档: ${data.saves.length} 个`;
    $('pillJar').innerHTML = `${pIcon} 版本核心: ` + (data.jars.length > 0 ? escapeHtml(data.jars[0].name) : '未探测到');
    $('pillMods').innerHTML = `${zIcon} 模组: ${data.mods.count} 个`;
    $('pillRps').innerHTML = `${plIcon} 材质包: ${data.resourcepacks.length} 个`;

    if (badge) {
      badge.textContent = '探测完成';
      badge.className = 'badge success';
    }

    // 自动填充高级设置中的 jar 与 mods
    if (!$('modalJarPath').value && data.jars.length > 0) {
      const folderName = (modpackPath.split(/[\/]/).filter(Boolean).pop() || '').toLowerCase();
      const matched = data.jars.find(j => folderName && j.name.toLowerCase().includes(folderName));
      $('modalJarPath').value = (matched || data.jars[0]).path;
    }
    if (!$('modalModsPath').value && data.mods.dir) {
      $('modalModsPath').value = data.mods.dir;
    }

    // 渲染存档列表
    detectedSaves = data.saves || [];
    renderModalSaves(detectedSaves);

  } catch (err) {
    if (requestId !== detectRequestId) return;
    console.error('探测失败:', err);
    if (badge) {
      badge.textContent = '探测异常';
      badge.className = 'badge danger';
    }
  }
}

function renderModalSaves(saves) {
  const container = $('modalSavesList');
  const countEl = $('modalSavesCount');
  if (countEl) countEl.textContent = saves.length;
  if (!container) return;

  if (!saves || saves.length === 0) {
    container.innerHTML = '<div class="saves-empty-hint">该整合包目录下暂未找到有效存档（需含 region/ 目录）</div>';
    selectedSavePath = '';
    return;
  }

  // 若尚未选中有效存档，默认选中第一个
  if (!selectedSavePath || !saves.some(s => s.path === selectedSavePath)) {
    selectedSavePath = saves[0].path;
  }

  container.innerHTML = saves.map((s, idx) => {
    const isSelected = s.path === selectedSavePath;
    const thumbHtml = s.icon
      ? `<img class="save-icon-img" src="${s.icon}" alt="world icon">`
      : `<div class="save-icon-fallback"></div>`;

    return `
      <div class="modal-save-card ${isSelected ? 'selected' : ''}" data-idx="${idx}">
        <div class="save-thumb">${thumbHtml}</div>
        <div class="save-meta">
          <div class="save-title">${escapeHtml(s.name)}</div>
          <div class="save-tags">
            <span class="save-tag">${escapeHtml(s.version || '1.20.1')}</span>
            <span class="save-tag">${escapeHtml(s.gameType || '生存模式')}</span>
            <span class="save-tag">${s.regions} 区域</span>
          </div>
          <div class="save-sub">
            <span>出生点: (${s.spawn.x}, ${s.spawn.y}, ${s.spawn.z})</span>
            <span>游玩时间: ${escapeHtml(s.lastPlayed || '近期')}</span>
          </div>
        </div>
        <div class="save-radio">${isSelected ? (typeof getSvgIcon === 'function' ? getSvgIcon('check', 14) : '✓') : ''}</div>
      </div>
    `;
  }).join('');

  container.querySelectorAll('.modal-save-card').forEach(card => {
    card.onclick = () => {
      const idx = parseInt(card.dataset.idx, 10);
      const s = detectedSaves[idx];
      if (s) {
        selectedSavePath = s.path;
        renderModalSaves(detectedSaves);
      }
    };
  });
}

function createProjectFromModpackObject(m) {
  openProjectModal({
    name: `${m.name} 3D 项目`,
    modpack: m.path,
    jar: m.jar || '',
    mods: m.mods || '',
  });
}

function editProject(id) {
  const p = currentProjects.find(x => x.id === id);
  if (p) openProjectModal(p);
}

/* ============================================================
   居中通用确认 / 删除弹窗 (符合设计系统规范)
   ============================================================ */
let activeConfirmHandler = null;

function showConfirmDialog({
  title = '删除项目',
  message = '确定要删除此项目吗？',
  subtext = '此操作将从列表中移除该项配置。',
  confirmText = '确定删除',
  cancelText = '取消',
  icon = 'trash',
  type = 'danger',
  onConfirm = null
} = {}) {
  if (activeConfirmHandler) {
    activeConfirmHandler.cancel();
  }

  const modal = $('confirmModal');
  if (!modal) {
    const ok = confirm(message.replace(/<[^>]+>/g, ''));
    if (ok && typeof onConfirm === 'function') onConfirm();
    return Promise.resolve(ok);
  }

  const titleEl = $('confirmModalTitle');
  if (titleEl) titleEl.textContent = title;

  const msgEl = $('confirmModalMessage');
  if (msgEl) msgEl.innerHTML = message;

  const subEl = $('confirmModalSubtext');
  if (subEl) {
    if (subtext) {
      subEl.textContent = subtext;
      subEl.style.display = 'block';
    } else {
      subEl.style.display = 'none';
    }
  }

  const iconBox = $('confirmModalIcon');
  if (iconBox) {
    iconBox.className = `confirm-icon-box ${type}`;
    iconBox.innerHTML = typeof getSvgIcon === 'function' ? getSvgIcon(icon, 18) : '';
  }

  const btnSubmit = $('btnConfirmModalSubmit');
  const btnCancel = $('btnConfirmModalCancel');
  const btnClose = $('btnConfirmModalClose');

  if (btnCancel) {
    btnCancel.textContent = cancelText;
    btnCancel.disabled = false;
  }
  if (btnClose) btnClose.disabled = false;

  const defaultSubmitHtml = `<span id="confirmModalBtnIcon" class="btn-icon-svg">${icon && typeof getSvgIcon === 'function' ? getSvgIcon(icon, 15) : ''}</span><span id="confirmModalSubmitText">${escapeHtml(confirmText)}</span>`;

  if (btnSubmit) {
    btnSubmit.disabled = false;
    btnSubmit.className = (type === 'danger') ? 'btn btn-danger-solid' : 'btn btn-primary';
    btnSubmit.innerHTML = defaultSubmitHtml;
  }

  modal.style.display = 'flex';

  return new Promise((resolve) => {
    function cleanup() {
      modal.style.display = 'none';
      if (btnSubmit) {
        btnSubmit.disabled = false;
        btnSubmit.innerHTML = defaultSubmitHtml;
      }
      if (btnCancel) btnCancel.disabled = false;
      if (btnClose) btnClose.disabled = false;
      window.removeEventListener('keydown', handleKey);
      modal.removeEventListener('click', handleBackdrop);
      activeConfirmHandler = null;
    }

    function doCancel() {
      cleanup();
      resolve(false);
    }

    async function doSubmit() {
      if (typeof onConfirm === 'function') {
        if (btnSubmit) {
          btnSubmit.disabled = true;
          btnSubmit.innerHTML = `<span class="spin mini"></span><span>处理中...</span>`;
        }
        if (btnCancel) btnCancel.disabled = true;
        if (btnClose) btnClose.disabled = true;
        try {
          await onConfirm();
          cleanup();
          resolve(true);
        } catch (err) {
          if (btnSubmit) {
            btnSubmit.disabled = false;
            btnSubmit.innerHTML = defaultSubmitHtml;
          }
          if (btnCancel) btnCancel.disabled = false;
          if (btnClose) btnClose.disabled = false;
          resolve(false);
        }
      } else {
        cleanup();
        resolve(true);
      }
    }

    function handleKey(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        doCancel();
      } else if (e.key === 'Enter') {
        if (btnSubmit && !btnSubmit.disabled) {
          e.preventDefault();
          doSubmit();
        }
      }
    }

    function handleBackdrop(e) {
      if (e.target === modal) {
        doCancel();
      }
    }

    if (btnCancel) btnCancel.onclick = doCancel;
    if (btnClose) btnClose.onclick = doCancel;
    if (btnSubmit) btnSubmit.onclick = doSubmit;
    window.addEventListener('keydown', handleKey);
    modal.addEventListener('click', handleBackdrop);

    activeConfirmHandler = { cancel: doCancel };
  });
}
window.showConfirmDialog = showConfirmDialog;

async function deleteProject(id) {
  const p = currentProjects.find(x => x.id === id);
  const projName = p && p.name ? p.name : '';
  const message = projName
    ? `确定要删除「<strong>${escapeHtml(projName)}</strong>」吗？`
    : '确定要删除此项目吗？';

  await showConfirmDialog({
    title: '删除项目',
    message: message,
    subtext: '删除后该项目将从列表中移除，本地 Minecraft 游戏存档不会被删除。此操作无法撤销。',
    confirmText: '确定删除',
    cancelText: '取消',
    icon: 'trash',
    type: 'danger',
    onConfirm: async () => {
      try {
        const res = await fetch('/api/projects/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id })
        });
        const d = await res.json();
        if (d.ok) {
          await loadProjects();
          showNotice('项目已成功删除');
        } else {
          showErr(d.error || '删除项目失败');
        }
      } catch (err) {
        showErr('删除项目失败: ' + err);
      }
    }
  });
}

/* ============================================================
   4. 项目启动与重生点 3D 默认渲染
   ============================================================ */
async function launchProjectById(id, clickedBtn) {
  const p = currentProjects.find(x => x.id === id);
  if (!p) return;
  await launchProject(p, clickedBtn);
}

async function launchProject(proj, clickedBtn) {
  const btnOrigHtml = clickedBtn ? clickedBtn.innerHTML : '';
  if (clickedBtn) {
    clickedBtn.disabled = true;
    clickedBtn.innerHTML = `<span class="spin mini"></span><span>启动中...</span>`;
  }

  // 1. 立即给用户明确及时的反馈：显示居中加载指示器，说明正在载入的项目
  setLoading(true, `正在载入「${projectLoadingName(proj)}」...`);

  // 2. 立即退出项目中心，无缝进入 3D 观察器，让用户第一时间感知页面流转
  hideHub();
  closeProjectModal();

  try {
    const res = await fetch('/api/project/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(proj)
    });
    const data = await res.json();
    if (!data.ok) {
      showHub();
      showErr(data.error || '加载项目失败');
      if (clickedBtn) {
        clickedBtn.disabled = false;
        clickedBtn.innerHTML = btnOrigHtml;
      }
      return;
    }

    activeProjectId = data.activeId || proj.id;

    // 通知 Worker 重置旧世界的数据结构与图集缓存，并推进 3D 渲染代次 (generation id)
    if (typeof next3dGeneration === 'function') {
      next3dGeneration();
    } else if (typeof worker !== 'undefined' && worker) {
      worker.postMessage({ t: 'reset' });
    }
    if (typeof fitKey !== 'undefined') {
      fitKey = '';
    }

    // 更新全局世界状态
    S.info = data.world;
    updateWorldInfoUI();

    // 默认选取并渲染重生点附近的区块
    renderSpawnPointDefault();

    // 重新拉取地图切片
    if (typeof requestTile === 'function') {
      requestTile(true).catch(() => {});
    }

  } catch (err) {
    showHub();
    showErr('启动项目异常: ' + err);
    if (clickedBtn) {
      clickedBtn.disabled = false;
      clickedBtn.innerHTML = btnOrigHtml;
    }
  } finally {
    setLoading(false);
  }
}

function projectLoadingName(proj) {
  const worldParts = String(proj.world || '').split(/[\\/]+/).filter(Boolean);
  const savesIndex = worldParts.map(part => part.toLowerCase()).lastIndexOf('saves');
  let instanceName = savesIndex > 0 ? worldParts[savesIndex - 1] : '';
  if (!instanceName || ['.minecraft', 'versions', 'saves'].includes(instanceName.toLowerCase())) {
    instanceName = String(proj.modpack || '').split(/[\\/]+/).filter(Boolean).at(-1) || '';
    if (['.minecraft', 'versions'].includes(instanceName.toLowerCase())) instanceName = '';
  }
  const worldName = worldParts.at(-1) || proj.name || 'Minecraft';
  return instanceName ? `${instanceName} · ${worldName}` : worldName;
}

function updateWorldInfoUI() {
  if (!S.info) return;
  if ($('worldName')) $('worldName').textContent = S.info.name;
  if ($('iVer')) $('iVer').textContent = S.info.version;
  if ($('iSeed')) $('iSeed').textContent = S.info.seed;
  if ($('iSpawn')) $('iSpawn').textContent = `${S.info.spawn.x}, ${S.info.spawn.y}, ${S.info.spawn.z}`;
  if ($('iSpan')) $('iSpan').textContent = `${Math.round(S.span)} 格`;
  if ($('iChunk')) {
    $('iChunk').textContent = `${floorDiv(S.info.spawn.x, CH)}, ${floorDiv(S.info.spawn.z, CH)}`;
  }
  if (typeof updateQuickNavOptions === 'function') updateQuickNavOptions();
}

function renderSpawnPointDefault() {
  if (!S.info || !S.info.spawn) return;
  const spawn = S.info.spawn;
  const cx = floorDiv(spawn.x, CH);
  const cz = floorDiv(spawn.z, CH);

  // 默认选取以出生点为中心的 3×3 区块（共 9 区块，快速渲染且展示出生点周围全貌）
  const cx0 = cx - 1, cz0 = cz - 1, cx1 = cx + 1, cz1 = cz + 1;
  S.gx = spawn.x;
  S.gz = spawn.z;
  S.span = clamp(3 * CH * 2.2, MIN_SPAN, MAX_SPAN);

  // 清除旧的输入框缓存
  if ($('x0In')) $('x0In').value = '';
  if ($('x1In')) $('x1In').value = '';
  if ($('z0In')) $('z0In').value = '';
  if ($('z1In')) $('z1In').value = '';

  // 设置选区
  resetAutoHeight();
  setSelection(cx0, cz0, cx1, cz1);

  // 优先进入 2D 平面俯视地图，方便用户直观查看地图并自由框选定位
  setView('2d');
  if (typeof resize2d === 'function') resize2d();
  if (typeof draw2d === 'function') draw2d();

  // 出生点定位后，从顶部预览步骤进入实时模型。
  showNotice(`📍 已定位出生点 (${spawn.x}, ${spawn.y}, ${spawn.z})，确认选区后点击顶部「2 预览」`);
}

function showNotice(msg) {
  let notice = $('hudNotice');
  if (!notice) {
    notice = document.createElement('div');
    notice.id = 'hudNotice';
    notice.className = 'hud-notice';
    document.body.appendChild(notice);
  }
  notice.textContent = msg;
  notice.classList.add('show');
  setTimeout(() => {
    notice.classList.remove('show');
  }, 3800);
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ============================================================
   5. 事件绑定与初始化
   ============================================================ */
function bindHubEvents() {
  // 顶栏项目中心按钮
  const btnHome = $('btnHome');
  if (btnHome) btnHome.onclick = showHub;

  // 搜索框过滤
  const searchInp = $('hubSearchInp');
  if (searchInp) {
    searchInp.oninput = () => renderProjects(currentProjects, S.info?.regionDir, activeProjectId);
  }

  // 新建项目按钮
  const btnNewProj = $('btnNewProj');
  if (btnNewProj) btnNewProj.onclick = () => openProjectModal();

  // 返回观察器按钮
  const btnBack = $('btnBackToViewer');
  if (btnBack) btnBack.onclick = hideHub;

  // 模态框关闭与取消
  const btnClose = $('btnProjModalClose');
  if (btnClose) btnClose.onclick = closeProjectModal;
  const btnCancel = $('btnProjModalCancel');
  if (btnCancel) btnCancel.onclick = closeProjectModal;
  const projModal = $('projModal');
  if (projModal) {
    projModal.onclick = e => {
      if (e.target === projModal) closeProjectModal();
    };
  }

  // 模态框整合包下拉选择切换
  const selModpack = $('modalModpackSelect');
  if (selModpack) {
    selModpack.onchange = () => {
      const v = selModpack.value;
      if (v === '__custom__') {
        clearDetectionResults();
        $('modalManualModpackRow').style.display = 'block';
      } else {
        $('modalManualModpackRow').style.display = 'none';
        clearDetectionResults();
        if (v) {
          const selectedName = selModpack.selectedOptions[0]?.dataset.name || v.split(/[\\/]/).filter(Boolean).pop();
          if (!editingProjectId) $('modalProjName').value = `${selectedName} 3D 项目`;
          triggerDetect(v);
        }
      }
    };
  }

  // 手动输入路径变化探测
  const inpModpackPath = $('modalModpackPath');
  if (inpModpackPath) {
    inpModpackPath.onchange = () => {
      const v = inpModpackPath.value.trim();
      clearDetectionResults();
      if (v) {
        if (!editingProjectId) $('modalProjName').value = `${v.split(/[\\/]/).filter(Boolean).pop()} 3D 项目`;
        triggerDetect(v);
      }
    };
  }

  // 浏览目录按钮（支持 pywebview 原生文件夹对话框）
  const btnBrowse = $('btnBrowseModpack');
  if (btnBrowse) {
    btnBrowse.onclick = async () => {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.pick) {
        btnBrowse.disabled = true;
        $('modalBrowseError').hidden = true;
        try {
          const p = await window.pywebview.api.pick('folder');
          if (p) {
            $('modalModpackSelect').value = '__custom__';
            $('modalManualModpackRow').style.display = 'block';
            $('modalModpackPath').value = p;
            clearDetectionResults();
            if (!editingProjectId) $('modalProjName').value = `${p.split(/[\\/]/).filter(Boolean).pop()} 3D 项目`;
            triggerDetect(p);
          }
        } catch (e) {
          $('modalBrowseError').textContent = '打开目录失败：' + (e.message || e);
          $('modalBrowseError').hidden = false;
        } finally {
          btnBrowse.disabled = false;
        }
      } else {
        const p = prompt('请输入本地整合包或 .minecraft 文件夹绝对路径:');
        if (p) {
          $('modalModpackSelect').value = '__custom__';
          $('modalManualModpackRow').style.display = 'block';
          $('modalModpackPath').value = p.trim();
          clearDetectionResults();
          if (!editingProjectId) $('modalProjName').value = `${p.trim().split(/[\\/]/).filter(Boolean).pop()} 3D 项目`;
          triggerDetect(p.trim());
        }
      }
    };
  }

  // 模态框提交
  const btnSubmit = $('btnProjModalSubmit');
  if (btnSubmit) {
    btnSubmit.onclick = async () => {
      const name = $('modalProjName').value.trim() || '未命名项目';
      const selVal = $('modalModpackSelect').value;
      const modpackPath = selVal === '__custom__' ? $('modalModpackPath').value.trim() : selVal;
      const jarPath = $('modalJarPath').value.trim();
      const modsPath = $('modalModsPath').value.trim();

      if (!selectedSavePath) {
        alert('请在探测到的列表中选择一个存档');
        return;
      }

      // 检查当前项目列表中是否已存在指向该存档的项目，避免重复创建冗余卡片
      const normSel = selectedSavePath.replace(/\\/g, '/').toLowerCase();
      const existing = currentProjects.find(p => p.world && p.world.replace(/\\/g, '/').toLowerCase() === normSel);
      const pid = editingProjectId || (existing ? existing.id : ('proj_' + Date.now()));

      const proj = {
        id: pid,
        name: name,
        modpack: modpackPath,
        world: selectedSavePath,
        jar: jarPath || undefined,
        mods: modsPath || undefined,
      };

      await launchProject(proj);
    };
  }
}

// 页面加载完成后初始化
window.addEventListener('DOMContentLoaded', () => {
  initSplash();
  bindHubEvents();
});
