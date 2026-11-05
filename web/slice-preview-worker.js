/* Shared-atlas slice meshes for worker3d.js. No renderer or texture per slice. */
'use strict';

self.MC3dSlicePreview = (() => {
  const ds = self.deepslate;
  const dimensions = { x: 0, y: 1, z: 2 };
  const directions = ['up', 'down', 'west', 'east', 'north', 'south'];
  const cuts = { x: new Set(['west', 'east']), y: new Set(['up', 'down']), z: new Set(['north', 'south']) };
  const bufferKeys = ['posBuffer', 'colorBuffer', 'textureBuffer', 'textureLimitBuffer', 'normalBuffer', 'indexBuffer'];

  function sourceFromArrays(xyz, states, palette, baseOrigin, visibleState) {
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < states.length; i++) for (let d = 0; d < 3; d++) {
      const p = xyz[i * 3 + d] - baseOrigin[d];
      lo[d] = Math.min(lo[d], p);
      hi[d] = Math.max(hi[d], p);
    }
    const size = lo.map((v, d) => Math.max(0, hi[d] - v + 1));
    const position = i => [xyz[i * 3] - baseOrigin[0], xyz[i * 3 + 1] - baseOrigin[1], xyz[i * 3 + 2] - baseOrigin[2]];
    const volume = size[0] * size[1] * size[2];
    if (!Number.isSafeInteger(volume) || volume > 0xffffffff) throw new Error('切片体素空间超出寻址范围');
    let occupiedCount = 0;
    for (let i = 0; i < states.length; i++) if (visibleState(states[i])) occupiedCount++;
    const capacity = 2 ** Math.ceil(Math.log2(Math.max(2, Math.ceil(occupiedCount * 1.5))));
    if (!Number.isSafeInteger(capacity) || capacity > 0xffffffff) throw new Error('切片占用索引超出寻址范围');
    const keys = new Uint32Array(capacity), values = new Uint16Array(capacity);
    const key = p => ((p[0] - lo[0]) * size[1] + p[1] - lo[1]) * size[2] + p[2] - lo[2] + 1;
    const hash = value => {
      value = Math.imul(value ^ (value >>> 16), 0x45d9f3b);
      value = Math.imul(value ^ (value >>> 16), 0x45d9f3b);
      return (value ^ (value >>> 16)) >>> 0;
    };
    const find = value => {
      let slot = hash(value) & (capacity - 1);
      while (keys[slot] && keys[slot] !== value) slot = (slot + 1) & (capacity - 1);
      return slot;
    };
    for (let i = 0; i < states.length; i++) if (visibleState(states[i])) {
      const packed = key(position(i)), slot = find(packed);
      keys[slot] = packed;
      values[slot] = states[i];
    }
    const stateAt = p => {
      if (p.some((v, d) => v < lo[d] || v > hi[d])) return 0;
      const packed = key(p), slot = find(packed);
      return keys[slot] === packed ? values[slot] + 1 : 0;
    };
    return {
      xyz, states, palette, baseOrigin, position, visibleState, stateAt, min: lo, max: hi,
      occupiedCount,
      getBlock(p) { const state = stateAt(p); return state ? { pos: p, state: palette[state - 1] } : null; },
      getSize() { return size; },
    };
  }

  function dispose(gl, bundle) {
    if (!bundle) return;
    for (const record of bundle.records) for (const mesh of record.meshes) {
      for (const key of bufferKeys) if (mesh[key]) { gl.deleteBuffer(mesh[key]); mesh[key] = null; }
    }
  }

  function upload(gl, mesh) {
    const uploaded = [];
    let part = null;
    // WebGL1 deepslate uses unsigned-short element indices.
    try {
      for (let start = 0; start < mesh.quads.length; start += 16383) {
        part = new ds.Mesh(mesh.quads.slice(start, start + 16383));
        part.rebuild(gl, { pos: true, color: true, texture: true, normal: true });
        const count = part.quads.length;
        const gpu = { quadVertices: () => count * 4, quadIndices: () => count * 6, lineVertices: () => 0 };
        for (const key of bufferKeys) gpu[key] = part[key];
        uploaded.push(gpu);
        // Only the GPU buffers and counts survive. Millions of Vertex/Quad objects do not.
        part.quads = [];
        part = null;
      }
    } catch (error) {
      for (const allocated of [...uploaded, part]) if (allocated) {
        for (const key of bufferKeys) if (allocated[key]) gl.deleteBuffer(allocated[key]);
      }
      throw error;
    }
    return uploaded;
  }

  async function build(gl, source, resources, axis, alive, progress) {
    const dim = dimensions[axis], chunkSize = [16, 16, 16];
    chunkSize[dim] = 1;
    const recordsByIndex = new Map(), groups = new Map();
    const chunkMin = source.min.map((v, d) => Math.floor(v / chunkSize[d]));
    const chunkCounts = source.max.map((v, d) => Math.floor(v / chunkSize[d]) - chunkMin[d] + 1);
    const groupKey = pos => {
      const cx = Math.floor(pos[0] / chunkSize[0]) - chunkMin[0];
      const cy = Math.floor(pos[1] / chunkSize[1]) - chunkMin[1];
      const cz = Math.floor(pos[2] / chunkSize[2]) - chunkMin[2];
      return (cx * chunkCounts[1] + cy) * chunkCounts[2] + cz;
    };
    for (let i = 0; i < source.states.length; i++) {
      if (!source.visibleState(source.states[i])) continue;
      const pos = source.position(i), index = pos[dim] + source.baseOrigin[dim];
      let record = recordsByIndex.get(index);
      if (!record) {
        record = { index, coordinate: pos[dim], count: 0, min: pos.slice(), max: pos.map(v => v + 1), anchor: pos.map(v => v + 0.5), meshes: [] };
        recordsByIndex.set(index, record);
      }
      record.count++;
      for (let d = 0; d < 3; d++) { record.min[d] = Math.min(record.min[d], pos[d]); record.max[d] = Math.max(record.max[d], pos[d] + 1); }
      const key = groupKey(pos);
      let group = groups.get(key);
      if (!group) groups.set(key, group = { record, count: 0, cursor: 0, blocks: null });
      group.count++;
    }
    for (const group of groups.values()) group.blocks = new Uint32Array(group.count);
    for (let i = 0; i < source.states.length; i++) if (source.visibleState(source.states[i])) {
      const group = groups.get(groupKey(source.position(i)));
      group.blocks[group.cursor++] = i;
    }
    const records = [...recordsByIndex.values()].sort((a, b) => a.index - b.index);
    records.forEach((record, ordinal) => { record.ordinal = ordinal; });
    const bundle = { axis, dim, records, source, stats: { meshes: 0, quads: 0 } };
    const builder = new ds.ChunkBuilder(gl, null, resources, chunkSize);
    builder.structure = source;
    const ordinaryCull = builder.needsCull;
    builder.needsCull = function (block, direction) { return cuts[axis].has(direction) ? false : ordinaryCull.call(this, block, direction); };
    let completed = 0;
    try {
      for (const { record, blocks } of groups.values()) {
        if (!alive()) { dispose(gl, bundle); return null; }
        const opaque = new ds.Mesh(), transparent = new ds.Mesh();
        for (const i of blocks) {
          const pos = source.position(i), block = { pos, state: source.palette[source.states[i]] };
          const name = block.state.getName(), props = block.state.getProperties();
          const definition = resources.getBlockDefinition(name);
          const cull = {};
          for (const direction of directions) cull[direction] = builder.needsCull(block, direction);
          const mesh = new ds.Mesh();
          if (definition) mesh.merge(definition.getMesh(name, props, resources, resources, cull));
          mesh.merge(ds.SpecialRenderers.getBlockMesh(block.state, block.nbt, resources, cull));
          if (mesh.isEmpty()) continue;
          builder.finishChunkMesh(mesh, pos);
          const target = resources.getBlockFlags(name)?.semi_transparent ? transparent : opaque;
          target.merge(mesh);
        }
        for (const mesh of [opaque, transparent]) {
          bundle.stats.quads += mesh.quads.length;
          const buffers = upload(gl, mesh);
          for (const buffer of buffers) buffer.transparent = mesh === transparent;
          record.meshes.push(...buffers);
          bundle.stats.meshes += buffers.length;
        }
        if (++completed % 12 === 0) {
          progress(completed, groups.size);
          await new Promise(resolve => setTimeout(resolve, 0));
        }
      }
      if (!alive()) { dispose(gl, bundle); return null; }
      return bundle;
    } catch (error) { dispose(gl, bundle); throw error; }
  }

  function offsetFor(record, ordinal, config) {
    return record.coordinate * (1 - config.thicknessRatio) + ordinal * config.gap;
  }
  function transformed(point, record, ordinal, config, dim) {
    const result = point.slice();
    result[dim] = point[dim] * config.thicknessRatio + offsetFor(record, ordinal, config);
    return result;
  }
  function describe(bundle, config) {
    const sliceBounds = bundle.records.map((record, ordinal) => ({
      index: record.index, count: record.count,
      min: transformed(record.min, record, ordinal, config, bundle.dim),
      max: transformed(record.max, record, ordinal, config, bundle.dim),
      anchor: transformed(record.anchor, record, ordinal, config, bundle.dim),
    }));
    const selectedIndices = Array.isArray(config.selectedIndices) ? config.selectedIndices : config.focus ? [config.index] : null;
    const selected = selectedIndices === null ? sliceBounds : sliceBounds.filter(b => selectedIndices.includes(b.index));
    const bounds = { min: [0, 0, 0], max: [0, 0, 0] };
    if (selected.length) {
      bounds.min = [0, 1, 2].map(d => Math.min(...selected.map(b => b.min[d])));
      bounds.max = [0, 1, 2].map(d => Math.max(...selected.map(b => b.max[d])));
    }
    return {
      enabled: true, axis: bundle.axis,
      indices: bundle.records.map(r => r.index), counts: bundle.records.map(r => r.count),
      bounds, sliceBounds, focus: selectedIndices !== null,
      index: selectedIndices?.length === 1 ? selectedIndices[0] : null, selectedIndices,
      sourceOrigin: bundle.source.baseOrigin, stats: bundle.stats,
    };
  }
  function draw(renderer, bundle, config, view) {
    renderer.setShader(renderer.shaderProgram);
    renderer.setTexture(renderer.atlasTexture, renderer.resources.getPixelSize?.());
    const matrix = new Float32Array(view);
    // Match deepslate's opaque pass before its transparent pass.
    const visible = record => Array.isArray(config.selectedIndices)
      ? config.selectedIndices.includes(record.index) : !config.focus || record.index === config.index;
    const visibleRecords = bundle.records.filter(visible);
    const transparentRecords = visibleRecords.filter(record => record.meshes.some(mesh => mesh.transparent)).sort((a, b) => {
      const ap = transformed(a.anchor, a, a.ordinal, config, bundle.dim);
      const bp = transformed(b.anchor, b, b.ordinal, config, bundle.dim);
      const az = view[2] * ap[0] + view[6] * ap[1] + view[10] * ap[2] + view[14];
      const bz = view[2] * bp[0] + view[6] * bp[1] + view[10] * bp[2] + view[14];
      return az - bz;
    });
    for (const transparent of [false, true]) {
      const records = transparent ? transparentRecords : visibleRecords;
      for (const record of records) {
        const ordinal = record.ordinal;
        if (!record.meshes.some(mesh => mesh.transparent === transparent)) continue;
        const offset = offsetFor(record, ordinal, config), dim = bundle.dim;
        matrix.set(view);
        for (let row = 0; row < 4; row++) {
          matrix[dim * 4 + row] = view[dim * 4 + row] * config.thicknessRatio;
          matrix[12 + row] = view[12 + row] + view[dim * 4 + row] * offset;
        }
        renderer.prepareDraw(matrix);
        for (const mesh of record.meshes) if (mesh.transparent === transparent) {
          renderer.drawMesh(mesh, { pos: true, color: true, texture: true, normal: true });
        }
      }
    }
  }

  function rayBounds(origin, direction, min, max) {
    let near = 0, far = Infinity;
    for (let d = 0; d < 3; d++) {
      if (Math.abs(direction[d]) < 1e-12) { if (origin[d] < min[d] || origin[d] > max[d]) return null; continue; }
      const a = (min[d] - origin[d]) / direction[d], b = (max[d] - origin[d]) / direction[d];
      near = Math.max(near, Math.min(a, b)); far = Math.min(far, Math.max(a, b));
      if (far < near) return null;
    }
    return [near, far];
  }
  function pick(bundle, config, origin, direction) {
    let best = Infinity, result = null;
    bundle.records.forEach((record, ordinal) => {
      if (Array.isArray(config.selectedIndices) ? !config.selectedIndices.includes(record.index)
        : config.focus && record.index !== config.index) return;
      const localOrigin = origin.slice(), localDirection = direction.slice(), dim = bundle.dim;
      localOrigin[dim] = (origin[dim] - offsetFor(record, ordinal, config)) / config.thicknessRatio;
      localDirection[dim] /= config.thicknessRatio;
      const range = rayBounds(localOrigin, localDirection, record.min, record.max);
      if (!range || range[0] > best) return;
      const t = range[0] + 1e-7;
      const cell = localOrigin.map((v, d) => Math.floor(v + localDirection[d] * t));
      const step = localDirection.map(v => Math.sign(v));
      const delta = localDirection.map(v => v ? Math.abs(1 / v) : Infinity);
      const next = cell.map((v, d) => step[d] ? ((step[d] > 0 ? v + 1 : v) - localOrigin[d]) / localDirection[d] : Infinity);
      let distance = range[0];
      while (distance <= Math.min(range[1], best)) {
        if (cell[dim] === record.coordinate && bundle.source.stateAt(cell)) { best = distance; result = record.index; break; }
        const axis = next.indexOf(Math.min(...next));
        distance = next[axis];
        if (!Number.isFinite(distance)) break;
        cell[axis] += step[axis]; next[axis] += delta[axis];
      }
    });
    return result;
  }
  return { sourceFromArrays, build, dispose, describe, draw, pick };
})();
