/* ============================================================
   3D 渲染 Worker：deepslate 跑在后台线程（OffscreenCanvas WebGL）
   —— 主线程只管相机/输入/红框，构建与绘制全在这里，界面永不冻结
   消息协议：
     main → worker: init{canvas,w,h} / resize{w,h} / build{q} / draw{view,grid,projection}
                    slice{enabled,axis,gap,thicknessRatio,focus,index,requestId,generation}
                    slice_pick{origin,direction,requestId,generation}
     worker → main: ready / built{meta} / progress{text} / error{msg}
                    slices{indices,counts,bounds,sliceBounds,stats,requestId,generation}
                    slice_progress{text} / slice_error{msg} / slice_picked{index}
   ============================================================ */
'use strict';

importScripts('/deepslate.umd.js');
importScripts('/slice-preview-worker.js');
const ds = self.deepslate;
const slices = self.MC3dSlicePreview;

let gl = null, renderer = null, structure = null;
let offCanvas = null;
const SKIP_EMPTY = true;   // 对照实验开关：false=渲染所有方块（含无几何体的）

// deepslate 的投影矩阵用 gl.canvas.clientWidth/Height 算纵横比——
// OffscreenCanvas 没有布局尺寸（恒 0）→ NaN 投影 → 全黑。
// 构造/缩放后必须手动覆写投影矩阵。
function perspMatrix(fovY, aspect, near, far) {
  const f = 1 / Math.tan(fovY / 2);
  const out = new Float32Array(16);
  out[0] = f / aspect;
  out[5] = f;
  out[10] = (far + near) / (near - far);
  out[11] = -1;
  out[14] = (2 * far * near) / (near - far);
  return out;
}
function fixProjection() {
  if (renderer) {
    renderer.projMatrix = perspMatrix(70 * Math.PI / 180, viewport.w / Math.max(1, viewport.h), 0.1, 1000000);
  }
}
let atlas = null, atlasDirty = true, dsResources = null;
let resourcesReady = null;
let activeGeneration = 0;
let buildInProgress = false;
let pendingBuild = null;
const blockDefinitions = {}, blockModels = {}, textureBlobs = {};
const emptyRenderNames = new Set();
const decorNames = new Set();
let viewport = { w: 1, h: 1 };
let activeBuildRevision = 0, activeQuery = null, activeMeta = null;
let sliceBundle = null, sliceTask = null, sliceEpoch = 0;
let fullSourcePromise = null, fullSourceAbort = null;
let sliceConfig = { enabled: false, axis: 'y', gap: 0.25, thicknessRatio: 1, focus: false, index: null, selectedIndices: null, requestId: 0 };

async function decodeVoxels(encoded) {
  const raw = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
  const stream = new Blob([raw]).stream().pipeThrough(new DecompressionStream('deflate'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function cancelSlices(resetSource = false) {
  sliceEpoch++;
  sliceTask = null;
  if (sliceBundle) slices.dispose(gl, sliceBundle);
  sliceBundle = null;
  if (resetSource) {
    fullSourceAbort?.abort();
    fullSourceAbort = null;
    fullSourcePromise = null;
  }
}

function fullSliceSource(revision) {
  if (fullSourcePromise) return fullSourcePromise;
  const gen = activeGeneration, query = activeQuery, base = activeMeta;
  const controller = new AbortController();
  fullSourceAbort = controller;
  fullSourcePromise = (async () => {
    const q = new URLSearchParams(query);
    q.set('surface_only', '0');
    const response = await fetch('/api/voxels_states?' + q, { signal: controller.signal });
    if (!response.ok) throw new Error((await response.json()).error || '完整体素读取失败');
    const vox = await response.json();
    const xyz = new Int16Array((await decodeVoxels(vox.xyz)).buffer);
    const states = new Uint16Array((await decodeVoxels(vox.states)).buffer);
    if (gen !== activeGeneration || revision !== activeBuildRevision) return null;
    const names = vox.names;
    const palette = names.map((name, i) => new ds.BlockState(name, vox.props[i] || undefined));
    return slices.sourceFromArrays(xyz, states, palette, [base.x0, base.yMin, base.z0],
      state => !(SKIP_EMPTY && emptyRenderNames.has(names[state]) && !names[state].startsWith('minecraft:')));
  })();
  return fullSourcePromise;
}

function reportSlices() {
  if (!sliceBundle || !sliceConfig.enabled) return;
  if (!sliceBundle.records.some(record => record.index === sliceConfig.index)) {
    sliceConfig.index = sliceBundle.records[0]?.index ?? null;
  }
  postMessage({ t: 'slices', ...slices.describe(sliceBundle, sliceConfig),
    requestId: sliceConfig.requestId, generation: activeGeneration });
}

async function prepareSlices() {
  if (!sliceConfig.enabled || !activeMeta || !renderer) return;
  if (sliceBundle && sliceBundle.axis === sliceConfig.axis) { reportSlices(); return; }
  if (sliceTask && sliceTask.axis === sliceConfig.axis) return;
  cancelSlices();
  const epoch = sliceEpoch, revision = activeBuildRevision, gen = activeGeneration;
  const axis = sliceConfig.axis;
  const task = { axis, epoch };
  sliceTask = task;
  const alive = () => epoch === sliceEpoch && revision === activeBuildRevision && gen === activeGeneration && sliceConfig.enabled;
  const progress = text => {
    if (alive()) postMessage({ t: 'slice_progress', text, requestId: sliceConfig.requestId, generation: gen });
  };
  try {
    progress('读取完整方块，准备切片预览…');
    const source = await fullSliceSource(revision);
    if (!source || !alive()) return;
    progress('构建独立切片与截面…');
    const bundle = await slices.build(gl, source, dsResources, axis, alive,
      (done, total) => progress(`构建切片网格 ${done} / ${total}`));
    if (!bundle) return;
    if (!alive()) { slices.dispose(gl, bundle); return; }
    sliceBundle = bundle;
    reportSlices();
  } catch (error) {
    if (alive()) {
      fullSourcePromise = null;
      postMessage({ t: 'slice_error', msg: String(error?.message || error), requestId: sliceConfig.requestId, generation: gen });
    }
  } finally { if (sliceTask === task) sliceTask = null; }
}

function setSliceConfig(message) {
  if (message.generation !== undefined && message.generation !== activeGeneration) return;
  const next = { ...sliceConfig, ...message };
  if (!['x', 'y', 'z'].includes(next.axis) || !Number.isFinite(next.gap) || next.gap < 0 ||
      !Number.isFinite(next.thicknessRatio) || next.thicknessRatio <= 0) {
    postMessage({ t: 'slice_error', msg: '切片方向、间隙或厚度无效', requestId: message.requestId, generation: activeGeneration });
    return;
  }
  sliceConfig = next;
  if (!next.enabled) {
    cancelSlices();
    postMessage({ t: 'slices', enabled: false, indices: [], counts: [], requestId: next.requestId, generation: activeGeneration });
  } else prepareSlices();
}

/* ---------- 资源 ---------- */
const NON_OPAQUE = /(^|:)water$|glass|_slab$|_stairs$|_fence$|_wall$|_trapdoor$|_door$|_pane$|_bars$|_torch$|_flower$|_sapling$|_crop$|_plant$|_mushroom$|_fungus$|_fern$|_vine$|_roots$|_coral$|_seagrass$|_kelp$|lily_pad|snow_layer|_carpet$|_rail$|_bed$|_head$|_skull$|_chain$|_rod$|_candle$|_pressure_plate$|_button$|_lever$|_sign$|_chest$|_shulker|_banner$|petals?|clover|leaf_pile|fallen_leaves|leaves_carpet|needle_pile|pine_needles_carpet|pebble|twig|rock|shrub|weed|reed|anemone|sage|rose|daisy|orchid|dandelion|poppy|bluet|allium|peony|lilac|sunflower|violet|marigold|hibiscus|lotus|waterlily|cornflower|azalea|blossom|leaves$|(?:^|:)(grass|short_grass|tall_grass|fern|large_fern|dead_bush|seagrass|kelp|dandelion|poppy|blue_orchid|allium|azure_bluet|red_tulip|orange_tulip|white_tulip|pink_tulip|oxeye_daisy|cornflower|lily_of_the_valley|wither_rose|sunflower|lilac|rose_bush|peony)$/i;
const SEMI = /(^|:)water$|glass|_pane$/i;
const MODEL_SCALE = new Float32Array([0.0625, 0, 0, 0, 0, 0.0625, 0, 0, 0, 0, 0.0625, 0, 0, 0, 0, 1]);

async function fetchRetry(url, tries = 3) {
  let err;
  for (let i = 0; i < tries; i++) {
    try { return await fetch(url); } catch (e) {
      err = e;
      if (i < tries - 1) await new Promise(r => setTimeout(r, 250 * (i + 1)));
    }
  }
  throw err;
}

function modelIsFullCube(ref) {
  const key = ref.includes(':') ? ref : 'minecraft:' + ref;
  const m = blockModels[key];
  if (!m || !m.elements) return false;
  return m.elements.some(e => e.from && e.to
    && Math.abs(e.from[0]) < 1e-3 && Math.abs(e.from[1]) < 1e-3 && Math.abs(e.from[2]) < 1e-3
    && Math.abs(e.to[0] - 16) < 1e-3 && Math.abs(e.to[1] - 16) < 1e-3 && Math.abs(e.to[2] - 16) < 1e-3);
}
function nameIsOpaque(n) {
  const def = blockDefinitions[n];
  if (!def) return false;
  if (def.__fallbackCube) return true;
  const refs = [];
  const v = def.variants;
  if (v) for (const entry of Object.values(v))
    for (const e of (Array.isArray(entry) ? entry : [entry])) if (e && e.model) refs.push(e.model);
  const mp = def.multipart;
  if (mp) for (const part of mp) {
    const a = part.apply;
    for (const e of (Array.isArray(a) ? a : [a])) if (e && e.model) refs.push(e.model);
  }
  return refs.some(modelIsFullCube);
}

function makeResources() {
  const flagsCache = new Map();
  return {
    getBlockDefinition(id) { return blockDefinitions[id.toString()]; },
    getBlockModel(id) { return blockModels[id.toString()]; },
    getTextureUV(id) { return atlas.getTextureUV(id); },
    getTextureAtlas() { return atlas.getTextureAtlas(); },
    getPixelSize() { return atlas.getPixelSize(); },
    getBlockFlags(id) {
      const n = id.toString();
      let f = flagsCache.get(n);
      if (!f) {
        f = {
          opaque: !decorNames.has(n) && !NON_OPAQUE.test(n) && nameIsOpaque(n),
          semi_transparent: SEMI.test(n),
        };
        flagsCache.set(n, f);
      }
      return f;
    },
    getBlockProperties() { return null; },
    getDefaultBlockProperties() { return null; },
  };
}

function fallbackDef(name) {
  const i = name.indexOf(':');
  const ns = i < 0 ? 'minecraft' : name.slice(0, i);
  let path = i < 0 ? name : name.slice(i + 1);
  if (ns === 'minecraft' && path === 'grass') path = 'short_grass';
  const texRef = ns + ':block/' + path;
  // 植物十字交叉面（同原版），草+透明贴图“半透明”透视到底
  const isPlant = NON_OPAQUE.test(name)
    && !/glass|pane|slab|stairs|fence|wall|door|trapdoor|bars|torch|candle|chain|rod|sign|chest|bed|banner|skull|head|shulker|carpet|snow_layer|lily_pad|rail|plate|button|lever/i.test(path);
  const elements = isPlant ? [
    { from: [0.8, 0, 8], to: [15.2, 16, 8], rotation: { origin: [8, 8, 8], axis: 'y', angle: 45 },
      faces: { north: { uv: [0, 0, 16, 16], texture: '#all' }, south: { uv: [0, 0, 16, 16], texture: '#all' } } },
    { from: [0.8, 0, 8], to: [15.2, 16, 8], rotation: { origin: [8, 8, 8], axis: 'y', angle: -45 },
      faces: { north: { uv: [0, 0, 16, 16], texture: '#all' }, south: { uv: [0, 0, 16, 16], texture: '#all' } } },
  ] : [{
    from: [0, 0, 0], to: [16, 16, 16],
    faces: {
      up: { texture: '#all' }, down: { texture: '#all' },
      north: { texture: '#all' }, south: { texture: '#all' },
      east: { texture: '#all' }, west: { texture: '#all' },
    },
  }];
  const model = ds.BlockModel.fromJson({ textures: { all: texRef }, elements });
  return {
    __fallbackCube: !isPlant && !NON_OPAQUE.test(name),
    getMesh(n, props, res, mp, cull) {
      return model.getMesh(res, cull).transform(MODEL_SCALE);
    },
  };
}

/* ---------- 图集（OffscreenCanvas 版 fromBlobs） ---------- */
function upperPow2(x) { let p = 1; while (p < x) p *= 2; return p; }

function createHashedColorBlob(name) {
  let h = 0x811c9dc5;
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  const hue = Math.abs(h) % 360;
  const sat = 28 + (Math.abs(h >> 8) % 24);
  const lit = 42 + (Math.abs(h >> 16) % 24);
  const c = new OffscreenCanvas(16, 16);
  const cx = c.getContext('2d');
  cx.fillStyle = `hsl(${hue}, ${sat}%, ${lit}%)`;
  cx.fillRect(0, 0, 16, 16);
  return c.convertToBlob();
}

async function ensureAtlas() {
  if (atlas && !atlasDirty) return atlas;
  const ids = Object.keys(textureBlobs);
  const width = upperPow2(Math.sqrt(ids.length + 1));
  const px = width * 16;
  const part = 1 / width;
  const canvas = new OffscreenCanvas(px, px);
  const ctx = canvas.getContext('2d');
  // 全局保底槽（0, 0）：使用柔和中性石质灰，替代刺眼的紫黑棋盘
  ctx.fillStyle = '#7a828e';
  ctx.fillRect(0, 0, 16, 16);
  const idMap = {};
  let index = 1;
  // 分批解码，避免一次性几千个 createImageBitmap 挤爆 GPU
  const BATCH = 32;
  for (let i = 0; i < ids.length; i += BATCH) {
    await Promise.all(ids.slice(i, i + BATCH).map(async id => {
      const u = index % width, v = Math.floor(index / width);
      index += 1;
      const uv = [part * u, part * v, part * u + part, part * v + part];
      idMap[id] = uv;
      if (id.startsWith('minecraft:')) {
        idMap[id.slice(10)] = uv;
      } else if (!id.includes(':')) {
        idMap['minecraft:' + id] = uv;
      }
      const img = await createImageBitmap(textureBlobs[id]);
      // 16 宽=普通/动画条（取第一帧）；更宽=实体贴图（64×64 床/箱子等），整张压进格子
      if (img.width === 16) {
        ctx.drawImage(img, 0, 0, 16, 16, 16 * u, 16 * v, 16, 16);
      } else {
        ctx.drawImage(img, 0, 0, img.width, img.height, 16 * u, 16 * v, 16, 16);
      }
      img.close && img.close();
    }));
  }
  atlas = new ds.TextureAtlas(ctx.getImageData(0, 0, px, px), idMap);
  atlasDirty = false;
  return atlas;
}

async function loadAssets() {
  if (dsResources && !atlasDirty) return dsResources;
  if (resourcesReady) return resourcesReady;
  resourcesReady = (async () => {
    postMessage({ t: 'progress', text: '加载原版资源' });
    const assets = await (await fetchRetry('/api/assets')).json();
    for (const [id, json] of Object.entries(assets.blockstates)) {
      blockDefinitions[id.includes(':') ? id : 'minecraft:' + id] = ds.BlockDefinition.fromJson(json);
    }
    for (const [id, json] of Object.entries(assets.models)) {
      blockModels[id.includes(':') ? id : 'minecraft:' + id] = ds.BlockModel.fromJson(json);
    }
    Object.values(blockModels).forEach(m =>
      m.flatten({ getBlockModel: id => blockModels[id.toString()] }));
    const chunk = 16;
    for (let i = 0; i < assets.textureIds.length; i += chunk) {
      await Promise.all(assets.textureIds.slice(i, i + chunk).map(async id => {
        const key = id.includes(':') ? id : 'minecraft:' + id;
        if (textureBlobs[key]) return;
        const resp = await fetchRetry('/api/texture/' + encodeURI(id), 2);
        if (resp.ok) {
          const blob = await resp.blob();
          textureBlobs[key] = blob;
          textureBlobs[id] = blob;
          if (id.includes('short_grass')) {
            textureBlobs[id.replace('short_grass', 'grass')] = blob;
            textureBlobs[key.replace('short_grass', 'grass')] = blob;
          } else if (id.includes('block/grass')) {
            textureBlobs[id.replace('block/grass', 'block/short_grass')] = blob;
            textureBlobs[key.replace('block/grass', 'block/short_grass')] = blob;
          }
          if (id.includes('short_grass')) {
            textureBlobs[id.replace('short_grass', 'grass')] = blob;
            textureBlobs[key.replace('short_grass', 'grass')] = blob;
          } else if (id.includes('block/grass')) {
            textureBlobs[id.replace('block/grass', 'block/short_grass')] = blob;
            textureBlobs[key.replace('block/grass', 'block/short_grass')] = blob;
          }
          if (id.includes('short_grass')) {
            textureBlobs[id.replace('short_grass', 'grass')] = blob;
            textureBlobs[key.replace('short_grass', 'grass')] = blob;
          } else if (id.includes('block/grass')) {
            textureBlobs[id.replace('block/grass', 'block/short_grass')] = blob;
            textureBlobs[key.replace('block/grass', 'block/short_grass')] = blob;
          }
        } else {
          const fallbackBlob = await createHashedColorBlob(id);
          textureBlobs[key] = fallbackBlob;
          textureBlobs[id] = fallbackBlob;
        }
      }));
      if (i % (chunk * 4) === 0) {
        postMessage({ t: 'progress', text: `加载原版贴图 ${Math.min(i + chunk, assets.textureIds.length)} / ${assets.textureIds.length}` });
      }
    }
    if (blockDefinitions['minecraft:short_grass'] && !blockDefinitions['minecraft:grass']) {
      blockDefinitions['minecraft:grass'] = blockDefinitions['minecraft:short_grass'];
    } else if (blockDefinitions['minecraft:grass'] && !blockDefinitions['minecraft:short_grass']) {
      blockDefinitions['minecraft:short_grass'] = blockDefinitions['minecraft:grass'];
    }
    if (blockDefinitions['minecraft:short_grass'] && !blockDefinitions['minecraft:grass']) {
      blockDefinitions['minecraft:grass'] = blockDefinitions['minecraft:short_grass'];
    } else if (blockDefinitions['minecraft:grass'] && !blockDefinitions['minecraft:short_grass']) {
      blockDefinitions['minecraft:short_grass'] = blockDefinitions['minecraft:grass'];
    }
    if (blockDefinitions['minecraft:short_grass'] && !blockDefinitions['minecraft:grass']) {
      blockDefinitions['minecraft:grass'] = blockDefinitions['minecraft:short_grass'];
    } else if (blockDefinitions['minecraft:grass'] && !blockDefinitions['minecraft:short_grass']) {
      blockDefinitions['minecraft:short_grass'] = blockDefinitions['minecraft:grass'];
    }
    atlasDirty = true;
    dsResources = makeResources();
    return dsResources;
  })();
  try {
    return await resourcesReady;
  } catch (err) {
    resourcesReady = null;
    throw err;
  }
}

async function loadMissingDefs(names) {
  const missing = [...new Set(names)].filter(n => n && !blockDefinitions[n]);
  if (!missing.length) return;
  postMessage({ t: 'progress', text: '加载模组资源' });
  const resp = await fetchRetry('/api/blockdefs?names=' + encodeURIComponent(missing.join(',')));
  const defs = await resp.json();
  for (const [name, json] of Object.entries(defs.blockstates || {})) {
    blockDefinitions[name] = ds.BlockDefinition.fromJson(json);
  }
  for (const [key, json] of Object.entries(defs.models || {})) {
    if (!blockModels[key]) blockModels[key] = ds.BlockModel.fromJson(json);
  }
  if (blockDefinitions['minecraft:short_grass'] && !blockDefinitions['minecraft:grass']) {
    blockDefinitions['minecraft:grass'] = blockDefinitions['minecraft:short_grass'];
  } else if (blockDefinitions['minecraft:grass'] && !blockDefinitions['minecraft:short_grass']) {
    blockDefinitions['minecraft:short_grass'] = blockDefinitions['minecraft:grass'];
  }
  Object.values(blockModels).forEach(m =>
    m.flatten({ getBlockModel: id => blockModels[id.toString()] }));
  for (const n of defs.emptyRender || []) emptyRenderNames.add(n);
  const texIds = new Set(defs.textureIds || []);
  for (const n of missing) {
    if (!blockDefinitions[n]) {
      blockDefinitions[n] = fallbackDef(n);
      const i = n.indexOf(':');
      const ns = i < 0 ? 'minecraft' : n.slice(0, i);
      const path = i < 0 ? n : n.slice(i + 1);
      texIds.add(ns + ':block/' + path);
    }
  }
  const ids = [...texIds];
  const chunk = 16;
  const missingTexReport = [];
  for (let i = 0; i < ids.length; i += chunk) {
    await Promise.all(ids.slice(i, i + chunk).map(async id => {
      if (textureBlobs[id]) return;
      const r2 = await fetchRetry('/api/texture/' + encodeURI(id), 2);
      if (r2.ok) {
        const blob = await r2.blob();
        textureBlobs[id] = blob;
        if (id.startsWith('minecraft:')) textureBlobs[id.slice(10)] = blob;
        else if (!id.includes(':')) textureBlobs['minecraft:' + id] = blob;
      } else {
        missingTexReport.push(id);
        const fallbackBlob = await createHashedColorBlob(id);
        textureBlobs[id] = fallbackBlob;
        if (id.startsWith('minecraft:')) textureBlobs[id.slice(10)] = fallbackBlob;
        else if (!id.includes(':')) textureBlobs['minecraft:' + id] = fallbackBlob;
      }
    }));
  }
  if (missingTexReport.length > 0) {
    console.warn(`[ChunkCarve] 模组贴图缺失汇总: 共有 ${missingTexReport.length} 个模组/方块贴图未在整合包中找到，已自动回退为方块名哈希纯色。`);
  }
  if (ids.length) atlasDirty = true;
}

/* ---------- 渲染缓存（LRU 3 份） ---------- */
const renderCache = new Map();
const CACHE_MAX = 3;

function disposeRenderer(r) {
  if (!r || !gl) return;
  try {
    const meshes = (r.chunkBuilder && r.chunkBuilder.getMeshes) ? r.chunkBuilder.getMeshes() : [];
    for (const k of ['gridMesh', 'outlineMesh', 'invisibleBlocksMesh']) {
      if (r[k]) meshes.push(r[k]);
    }
    const keys = ['posBuffer', 'colorBuffer', 'textureBuffer', 'textureLimitBuffer',
      'normalBuffer', 'blockPosBuffer', 'linePosBuffer', 'lineColorBuffer', 'indexBuffer'];
    for (const m of meshes) for (const k of keys) {
      if (m[k]) { gl.deleteBuffer(m[k]); m[k] = null; }
    }
    if (r.atlasTexture) { gl.deleteTexture(r.atlasTexture); r.atlasTexture = null; }
  } catch (e) { /* ignore */ }
}

function evictIfNeeded(protect) {
  while (renderCache.size > CACHE_MAX) {
    for (const [k, v] of renderCache) {
      if (k !== protect) {
        renderCache.delete(k);
        disposeRenderer(v.renderer);
        break;
      }
    }
  }
}

/* ---------- 构建 ---------- */
async function build(q, gen, revision) {
  const current = () => gen === activeGeneration && revision === activeBuildRevision;
  if (!current()) return;
  await loadAssets();
  if (!current()) return;

  postMessage({ t: 'progress', text: '读取选区' });
  const resp = await fetchRetry('/api/voxels_states?' + q);
  if (!current()) return;
  if (!resp.ok) throw new Error((await resp.json()).error || '数据请求失败');
  const vox = await resp.json();
  if (!current()) return;

  // 缓存命中：直接换上，秒开（按代次隔离 key）
  const key = `${activeGeneration}:${q}`;
  if (renderCache.has(key) && !atlasDirty) {
    const hit = renderCache.get(key);
    renderCache.delete(key);
    renderCache.set(key, hit);   // LRU 提前
    renderer = hit.renderer;
    structure = hit.structure;
    activeQuery = q;
    activeMeta = hit.meta;
    renderer.setViewport(0, 0, viewport.w, viewport.h);
    fixProjection();
    postMessage({ t: 'built', meta: hit.meta, cached: true, generation: gen });
    if (sliceConfig.enabled) prepareSlices();
    return;
  }

  const xyz = new Int16Array((await decodeVoxels(vox.xyz)).buffer);
  const states = new Uint16Array((await decodeVoxels(vox.states)).buffer);
  if (!current()) return;
  postMessage({ t: 'progress', text: `整理 ${vox.count.toLocaleString()} 个方块` });

  await loadMissingDefs(vox.names);
  if (!current()) return;

  // 图集有新增贴图 → 重建图集后旧缓存的 UV 全部失效
  if (atlasDirty) {
    for (const [, v] of renderCache) disposeRenderer(v.renderer);
    renderCache.clear();
    await ensureAtlas();
  }
  if (!current()) return;

  postMessage({ t: 'progress', text: '构建网格…' });
  const W = vox.w, D = vox.d, H = Math.max(1, vox.yMax - vox.yMin + 1);
  const x0 = vox.x0, z0 = vox.z0, y0 = vox.yMin;
  decorNames.clear();
  (vox.decor || []).forEach((d, i) => { if (d) decorNames.add(vox.names[i]); });

  // 让 progress 消息先发出去（消息循环一空即达主线程）
  await new Promise(r => setTimeout(r, 0));
  if (!current()) return;

  const stCount = vox.names.length;
  const palette = new Array(stCount);
  for (let s = 0; s < stCount; s++) {
    palette[s] = new ds.BlockState(vox.names[s], vox.props[s] || undefined);
  }
  const blocksArr = [];
  let added = 0;
  for (let i = 0; i < vox.count; i++) {
    const st = states[i];
    if (SKIP_EMPTY && emptyRenderNames.has(vox.names[st]) && !vox.names[st].startsWith('minecraft:')) continue;
    blocksArr.push({
      pos: [xyz[i * 3] - x0, xyz[i * 3 + 1] - y0, xyz[i * 3 + 2] - z0],
      state: st,
    });
    added++;
  }

  if (!current()) return;
  // 确保资源就绪，避免向 StructureRenderer 传递 null 触发 getDefaultBlockProperties 报错
  if (!dsResources) {
    await loadAssets();
  }
  if (!current() || !dsResources) return;

  structure = new ds.Structure([W, H, D], palette, blocksArr);
  const newRenderer = new ds.StructureRenderer(gl, structure, dsResources,
    { useInvisibleBlockBuffer: false });
  if (!current()) {
    disposeRenderer(newRenderer);
    return;
  }

  renderer = newRenderer;
  renderer.setViewport(0, 0, viewport.w, viewport.h);
  fixProjection();

  // 旧的留缓存（切回秒开），只在 LRU 淘汰/图集重建时才真正销毁
  const meta = {
    count: added, yMin: vox.yMin, yMax: vox.yMax,
    x0: vox.x0, z0: vox.z0, w: W, d: D,
  };
  activeQuery = q;
  activeMeta = meta;
  renderCache.set(key, { renderer, structure, meta });
  evictIfNeeded(key);
  postMessage({ t: 'built', meta, cached: false, generation: gen });
  if (sliceConfig.enabled) prepareSlices();
}

/* ---------- 绘制 ---------- */
function draw(view, grid, projection) {
  if (!renderer) return;
  if (projection?.length === 16) renderer.projMatrix = new Float32Array(projection);
  if (sliceConfig.enabled && sliceBundle) slices.draw(renderer, sliceBundle, sliceConfig, view);
  else renderer.drawStructure(view);
  if (grid) renderer.drawGrid(view);
}

/* ---------- 消息队列与构建调度 ---------- */
async function runBuild(q, gen, revision) {
  buildInProgress = true;
  try {
    await build(q, gen, revision);
  } catch (err) {
    if (gen === activeGeneration && revision === activeBuildRevision) {
      postMessage({ t: 'error', msg: String(err && err.message || err), generation: gen });
    }
  } finally {
    buildInProgress = false;
    if (pendingBuild) {
      const next = pendingBuild;
      pendingBuild = null;
      if (next.generation === activeGeneration) {
        runBuild(next.q, next.generation, next.revision);
      }
    }
  }
}

onmessage = async e => {
  const m = e.data;
  try {
    if (m.t === 'init') {
      if (m.generation !== undefined) activeGeneration = m.generation;
      offCanvas = m.canvas;
      gl = offCanvas.getContext('webgl', { antialias: true });
      viewport = { w: m.w, h: m.h };
      offCanvas.width = m.w; offCanvas.height = m.h;
      gl.viewport(0, 0, m.w, m.h);
      postMessage({ t: 'ready' });
    } else if (m.t === 'resize') {
      viewport = { w: m.w, h: m.h };
      if (offCanvas) { offCanvas.width = m.w; offCanvas.height = m.h; }
      if (gl) gl.viewport(0, 0, m.w, m.h);
      if (renderer) { renderer.setViewport(0, 0, m.w, m.h); fixProjection(); }
    } else if (m.t === 'build') {
      const gen = m.generation !== undefined ? m.generation : activeGeneration;
      if (gen < activeGeneration) return;
      const revision = ++activeBuildRevision;
      cancelSlices(true);
      sliceConfig.enabled = false;
      activeQuery = null;
      activeMeta = null;
      if (gen > activeGeneration) {
        activeGeneration = gen;
        pendingBuild = null;
        for (const [, v] of renderCache) disposeRenderer(v.renderer);
        renderCache.clear();
        atlasDirty = true;
        atlas = null;
        resourcesReady = null;
        dsResources = null;
        renderer = null;
        structure = null;
      }
      if (buildInProgress) {
        // 正在构建时将最新构建请求排队，丢弃中间过期请求
        pendingBuild = { q: m.q, generation: gen, revision };
        return;
      }
      runBuild(m.q, gen, revision);
    } else if (m.t === 'draw') {
      draw(m.view, m.grid, m.projection);
    } else if (m.t === 'slice') {
      setSliceConfig(m);
    } else if (m.t === 'slice_pick') {
      if (m.generation !== undefined && m.generation !== activeGeneration) return;
      const validRay = Array.isArray(m.origin) && Array.isArray(m.direction) &&
        m.origin.length === 3 && m.direction.length === 3 &&
        m.origin.every(Number.isFinite) && m.direction.every(Number.isFinite) && m.direction.some(v => v !== 0);
      const index = validRay && sliceConfig.enabled && sliceBundle
        ? slices.pick(sliceBundle, sliceConfig, m.origin, m.direction) : null;
      postMessage({ t: 'slice_picked', index, requestId: m.requestId, generation: activeGeneration });
    } else if (m.t === 'reset') {
      activeGeneration = m.generation !== undefined ? m.generation : (activeGeneration + 1);
      activeBuildRevision++;
      cancelSlices(true);
      sliceConfig.enabled = false;
      activeQuery = null;
      activeMeta = null;
      pendingBuild = null;
      for (const [, v] of renderCache) disposeRenderer(v.renderer);
      renderCache.clear();
      atlasDirty = true;
      atlas = null;
      resourcesReady = null;   // 重置 promise，确保后续 loadAssets 能完整执行
      dsResources = null;
      renderer = null;
      structure = null;
      // 清除非原版 blockDefinitions 与 blockModels，防止跨整合包污染
      for (const k of Object.keys(blockDefinitions)) {
        if (!k.startsWith('minecraft:')) delete blockDefinitions[k];
      }
      for (const k of Object.keys(blockModels)) {
        if (!k.startsWith('minecraft:')) delete blockModels[k];
      }
      emptyRenderNames.clear();
      decorNames.clear();
      postMessage({ t: 'reset_done', generation: activeGeneration });
    }
  } catch (err) {
    postMessage({ t: 'error', msg: String(err && err.message || err), generation: activeGeneration });
  }
};
