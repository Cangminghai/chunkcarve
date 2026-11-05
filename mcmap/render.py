"""存档俯视渲染器。

把区块范围渲染成一张带地形阴影的俯视图，无需进入游戏。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from . import palette
from .anvil import RegionFile

Y_MIN = -64
Y_MAX = 320

# 调色板分类
C_AIR = 0
C_SKIP = 1       # 纯装饰/植物/微小透明（跳过，不渲染不导出）
C_SOLID = 2      # 完整实体方块
C_WATER = 3      # 水/熔岩
C_DETAIL = 4     # 实体不完整方块（栅栏/台阶/活板门等，渲染真实几何）


_CLS_CACHE: dict[tuple, np.ndarray] = {}
_COL_CACHE: dict[tuple, np.ndarray] = {}
_RF_CACHE: dict[str, object] = {}
_CHUNK_CACHE: dict[tuple[int, int], tuple] = {}
_OVERVIEW_CACHE: dict[tuple, tuple | None] = {}
_CACHE_LIMIT = 6000

# 方块名与整数 ID 的双向映射（跨区块共享，用于纹理图集索引）
_NAME_ID: dict[str, int] = {}
_ID_NAME: list[str] = []


def name_id(name: str) -> int:
    i = _NAME_ID.get(name)
    if i is None:
        i = len(_ID_NAME)
        _NAME_ID[name] = i
        _ID_NAME.append(name)
    return i


def id_name(i: int) -> str:
    return _ID_NAME[i] if 0 <= i < len(_ID_NAME) else "minecraft:stone"


def _get_region(path: str):
    rf = _RF_CACHE.get(path)
    if rf is None:
        rf = RegionFile(path)
        if len(_RF_CACHE) > 24:
            _RF_CACHE.clear()
        _RF_CACHE[path] = rf
    return rf


def clear_cache():
    """清理所有渲染缓存及打开的区域文件句柄，在切换存档/项目时调用。"""
    _CHUNK_CACHE.clear()
    _OVERVIEW_CACHE.clear()
    _RF_CACHE.clear()
    _CLS_CACHE.clear()
    _COL_CACHE.clear()


def chunk_cached(rf, cx: int, cz: int) -> tuple | None:
    """带缓存的区块高度图计算，重复访问接近零成本。按区域文件路径隔离 key。"""
    key = (rf.path, cx, cz)
    hit = _CHUNK_CACHE.get(key)
    if hit is not None:
        return hit
    ch = rf.read_chunk(cx, cz)
    if ch is None or not ch.sections:
        _CHUNK_CACHE[key] = None
        return None
    out = chunk_heightmap(ch)
    if (out[0] < -900).all():
        _CHUNK_CACHE[key] = None
        return None
    if len(_CHUNK_CACHE) > _CACHE_LIMIT:
        _CHUNK_CACHE.clear()
    _CHUNK_CACHE[key] = out
    return out


def overview_chunk_cached(path: str, cx: int, cz: int, sample_step: int):
    """Keep small sampled arrays so far-map panning does not decode again."""
    key = (path, cx, cz, sample_step)
    if key in _OVERVIEW_CACHE:
        return _OVERVIEW_CACHE[key]
    data = chunk_cached(_get_region(path), cx, cz)
    sampled = tuple(value[::sample_step, ::sample_step].copy() for value in data) if data is not None else None
    if len(_OVERVIEW_CACHE) >= 65536:
        _OVERVIEW_CACHE.pop(next(iter(_OVERVIEW_CACHE)))
    _OVERVIEW_CACHE[key] = sampled
    return sampled


def _classify(names: list[str]) -> np.ndarray:
    key = tuple(names)
    hit = _CLS_CACHE.get(key)
    if hit is not None:
        return hit
    cls = np.full(len(names), C_SOLID, dtype=np.uint8)
    for i, n in enumerate(names):
        short = n.split(":")[-1].lower()
        if n in palette.AIR or "air" in short:
            cls[i] = C_AIR
        elif n in ("minecraft:water", "minecraft:lava") or short in ("water", "lava"):
            cls[i] = C_WATER
        elif palette.is_decorative(n) or palette.is_transparent(n):
            cls[i] = C_SKIP
        elif palette.is_detail(n) or palette.is_noncube(n):
            cls[i] = C_DETAIL
        elif "leaves" in short:
            # 树叶方块：非实心全遮挡物（避免树叶下地表泥土被遮挡剔除导致透明穿透）
            cls[i] = C_DETAIL
        else:
            cls[i] = C_SOLID
    if len(_CLS_CACHE) > 4096:
        _CLS_CACHE.clear()
    _CLS_CACHE[key] = cls
    return cls


def _colors_for(names: list[str]) -> np.ndarray:
    key = tuple(names)
    hit = _COL_CACHE.get(key)
    if hit is not None:
        return hit
    out = np.zeros((len(names), 4), dtype=np.float32)
    for i, n in enumerate(names):
        out[i] = palette.resolve(n)
    if len(_COL_CACHE) > 4096:
        _COL_CACHE.clear()
    _COL_CACHE[key] = out
    return out


@dataclass
class HeightMap:
    height: np.ndarray    # (z, x) 世界坐标的 y 值，-999 表示无数据
    top_name: np.ndarray  # 兼容字段
    water: np.ndarray     # (z, x) 该列是否为水面
    image: object = None  # PIL.Image，供服务端直接使用
    name_id: np.ndarray | None = None  # (z, x) 表层方块 ID

    def __repr__(self):
        h = self.height[self.height > -900]
        return (f"<HeightMap {self.height.shape} "
                f"有数据={h.size} Y范围={h.min() if h.size else '-'}~{h.max() if h.size else '-'}>")


def chunk_heightmap(ch) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """单个区块 → (16,16) 高度 / RGBA / 水面 / 方块名 ID。

    向量化实现：全程无 Python 逐格循环，并对纯空气 section 提前跳过。
    """
    height = np.full((16, 16), -999, dtype=np.int32)
    rgba = np.zeros((16, 16, 4), dtype=np.float32)
    water = np.zeros((16, 16), dtype=bool)
    nid = np.zeros((16, 16), dtype=np.uint16)
    remaining = np.ones((16, 16), dtype=bool)

    for sy in sorted(ch.sections, reverse=True):
        if not remaining.any():
            break
        sec = ch.sections[sy]
        cls = _classify(sec.palette)
        if not (cls == C_SOLID).any() and not (cls == C_WATER).any():
            continue

        grid = sec.blocks.reshape(16, 16, 16)  # [y, z, x]
        sym = cls[grid]
        solid = (sym == C_SOLID) | (sym == C_WATER)
        if not solid.any():
            continue

        rev = solid[::-1]
        has = rev.any(axis=0)
        ay = np.argmax(rev, axis=0)
        y_top = 15 - ay

        newly = has & remaining
        if not newly.any():
            continue

        zz, xx = np.nonzero(newly)
        ysel = y_top[zz, xx]
        height[zz, xx] = sy * 16 + ysel
        idx = grid[ysel, zz, xx]
        rgba[zz, xx] = _colors_for(sec.palette)[idx]
        water[zz, xx] = sym[ysel, zz, xx] == C_WATER
        pids = np.array([name_id(n) for n in sec.palette], dtype=np.uint16)
        nid[zz, xx] = pids[idx]
        remaining[zz, xx] = False

    return height, rgba, water, nid


def render(region_dir: str,
           cx0: int, cz0: int, cx1: int, cz1: int,
           out_path: str,
           shade: bool = True,
           show_grid: bool = True,
           grid_step: int = 1,
           highlight: list[tuple[int, int]] | None = None,
           sample_step: int = 1) -> HeightMap:
    """渲染区块范围 [cx0..cx1) × [cz0..cz1) 的俯视图。"""
    if sample_step not in (1, 2, 4, 8, 16):
        raise ValueError("sample_step 必须是 1、2、4、8 或 16")
    w = (cx1 - cx0) * 16 // sample_step
    h = (cz1 - cz0) * 16 // sample_step
    tile_size = 16 // sample_step
    height = np.full((h, w), -999, dtype=np.int32)
    rgba = np.zeros((h, w, 4), dtype=np.float32)
    water_map = np.zeros((h, w), dtype=bool)
    nameid = np.zeros((h, w), dtype=np.uint16)

    rx0, rx1 = cx0 // 32, (cx1 - 1) // 32
    rz0, rz1 = cz0 // 32, (cz1 - 1) // 32

    loaded = 0
    for rx in range(rx0, rx1 + 1):
        for rz in range(rz0, rz1 + 1):
            path = os.path.join(region_dir, f"r.{rx}.{rz}.mca")
            if not os.path.exists(path) or os.path.getsize(path) < 8192:
                continue
            rf = _get_region(path) if sample_step == 1 else None
            for cx in range(max(cx0, rx * 32), min(cx1, rx * 32 + 32)):
                for cz in range(max(cz0, rz * 32), min(cz1, rz * 32 + 32)):
                    got = chunk_cached(rf, cx, cz) if sample_step == 1 else overview_chunk_cached(path, cx, cz, sample_step)
                    if got is None:
                        continue
                    hm, col, wm, ni = got
                    ox = (cx - cx0) * tile_size
                    oz = (cz - cz0) * tile_size
                    height[oz:oz + tile_size, ox:ox + tile_size] = hm
                    water_map[oz:oz + tile_size, ox:ox + tile_size] = wm
                    rgba[oz:oz + tile_size, ox:ox + tile_size] = col
                    nameid[oz:oz + tile_size, ox:ox + tile_size] = ni
                    loaded += 1
            del rf

    if shade:
        rgba = _apply_shade(rgba, height, sample_step)
    rgba = _composite(rgba, water_map)

    img_arr = np.clip(rgba[:, :, :3], 0, 255).astype(np.uint8)
    mask = (height > -900)
    img_arr[~mask] = (18, 18, 20)

    img = Image.fromarray(img_arr, mode="RGB")
    if show_grid:
        img = _draw_grid(img, cx0, cz0, cx1, cz1, grid_step, highlight or [])
    if out_path:
        img.save(out_path)
    result = HeightMap(height=height, top_name=np.full((h, w), ""),
                       water=water_map, image=img, name_id=nameid)
    return result


def _apply_shade(rgba: np.ndarray, height: np.ndarray, sample_step: int = 1) -> np.ndarray:
    hh = height.astype(np.float32)
    valid = height > -900
    hh = np.where(valid, hh, np.nan)
    # 用最近有效值填补 NaN，避免边缘黑块
    if np.isnan(hh).any():
        hh = _fill_nan(hh)

    dx = np.gradient(hh, sample_step, axis=1) if hh.shape[1] > 1 else np.zeros_like(hh)
    dy = np.gradient(hh, sample_step, axis=0) if hh.shape[0] > 1 else np.zeros_like(hh)
    z = 0.9
    nx, ny, nz = -dx * z, -dy * z, np.ones_like(hh)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / norm, ny / norm, nz / norm

    # 光源：西北上方
    lx, ly, lz = -0.55, -0.55, 0.63
    ln = np.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / ln, ly / ln, lz / ln
    dot = nx * lx + ny * ly + nz * lz
    shade = np.clip(dot, 0.0, 1.0)
    shade = 0.45 + 1.05 * shade
    shade = np.where(valid, shade, 1.0)

    out = rgba.copy()
    out[:, :, 0] *= shade
    out[:, :, 1] *= shade
    out[:, :, 2] *= shade
    return out


def _fill_nan(a: np.ndarray) -> np.ndarray:
    out = a.copy()
    nan = np.isnan(out)
    if nan.all() or not nan.any():
        return np.nan_to_num(out, nan=0.0)
    # 简单迭代扩散
    for _ in range(64):
        if not np.isnan(out).any():
            break
        p = np.pad(out, 1, mode="edge")
        stack = np.stack([p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
        with np.errstate(invalid="ignore"):
            m = np.nanmean(stack, axis=0)
        fill = np.isnan(out) & ~np.isnan(m)
        out[fill] = m[fill]
    return np.nan_to_num(out, nan=0.0)


def _composite(rgba: np.ndarray, water: np.ndarray) -> np.ndarray:
    out = rgba.copy()
    wcol = np.array([63, 118, 228], dtype=np.float32)
    idx = water
    out[idx, :3] = out[idx, :3] * 0.45 + wcol * 0.55
    out[idx, 3] = 255
    return out


def _draw_grid(img: Image.Image, cx0, cz0, cx1, cz1, step, highlight) -> Image.Image:
    img = img.convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    ncx = cx1 - cx0
    ncz = cz1 - cz0
    px = w / ncx
    py = h / ncz
    if step > 0:
        for i in range(0, ncx + 1, step):
            x = i * px
            a = 90 if i % 8 == 0 else 40
            d.line([(x, 0), (x, h)], fill=(255, 255, 255, a), width=1)
        for j in range(0, ncz + 1, step):
            y = j * py
            a = 90 if j % 8 == 0 else 40
            d.line([(0, y), (w, y)], fill=(255, 255, 255, a), width=1)
    for (hx, hz) in highlight:
        x0 = (hx - cx0) * px
        y0 = (hz - cz0) * py
        d.rectangle([x0, y0, x0 + px, y0 + py],
                    outline=(255, 90, 60, 255), width=max(1, int(px / 6)))
    return img


def extract_voxels(region_dir: str, cx0: int, cz0: int, cx1: int, cz1: int,
                   y0: int = Y_MIN, y1: int = Y_MAX) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """提取区块范围内所有「实体」方块（含水），供 2.5D 体素渲染。

    返回 (xyz, ids, flags)：
      xyz    —— (N,3) int16，世界坐标 (x, y, z)
      ids    —— (N,)  int16，方块名 ID（经 name_id 归一）
      flags  —— (N,)  uint8，0=实体方块，1=水/熔岩，2=装饰（草/花等，透明）
    只返回「可能可见」的方块：凡六面全被同类实体遮挡的会尽量略过
    （这里先返回所有非空气，前端做面剔除，保证结构完整）。
    """
    rx0, rx1 = cx0 // 32, (cx1 - 1) // 32
    rz0, rz1 = cz0 // 32, (cz1 - 1) // 32

    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    zs: list[np.ndarray] = []
    ids: list[np.ndarray] = []
    flags: list[np.ndarray] = []

    for rx in range(rx0, rx1 + 1):
        for rz in range(rz0, rz1 + 1):
            path = os.path.join(region_dir, f"r.{rx}.{rz}.mca")
            if not os.path.exists(path) or os.path.getsize(path) < 8192:
                continue
            rf = _get_region(path)
            for cx in range(max(cx0, rx * 32), min(cx1, rx * 32 + 32)):
                for cz in range(max(cz0, rz * 32), min(cz1, rz * 32 + 32)):
                    ch = rf.read_chunk(cx, cz)
                    if ch is None or not ch.sections:
                        continue
                    for sy, sec in ch.sections.items():
                        top = sy * 16 + 15
                        bot = sy * 16
                        if top < y0 or bot > y1:
                            continue
                        cls = _classify(sec.palette)
                        # 保留：完整实体方块、水、实体不完整方块（C_DETAIL）。
                        # 丢弃：纯空气、纯装饰/植物/微小透明物（C_SKIP）。
                        keep = (cls == C_SOLID) | (cls == C_WATER) | (cls == C_DETAIL)
                        if not keep.any():
                            continue
                        pids = np.array([name_id(n) for n in sec.palette],
                                        dtype=np.int16)
                        grid = sec.blocks.reshape(16, 16, 16)  # [y, z, x]
                        sel = keep[grid]
                        yy, zz, xx = np.nonzero(sel)
                        gx = cx * 16 + xx
                        gy = sy * 16 + yy
                        gz = cz * 16 + zz
                        # Y 范围裁剪
                        ym = (gy >= y0) & (gy <= y1)
                        if not ym.any():
                            continue
                        gx, gy, gz = gx[ym], gy[ym], gz[ym]
                        idx = grid[yy[ym], zz[ym], xx[ym]]
                        xs.append(gx.astype(np.int16))
                        ys.append(gy.astype(np.int16))
                        zs.append(gz.astype(np.int16))
                        ids.append(pids[idx])
                        # flags: 0=实心(遮挡邻居) / 1=水 / 2=非实心(不遮挡邻居，含不完整方块、植物/装饰物、透明物等)
                        f = np.full(gx.shape, 2, dtype=np.uint8)
                        sc = cls[idx]
                        f[sc == C_SOLID] = 0
                        f[sc == C_WATER] = 1
                        flags.append(f)
            del rf

    if not xs:
        return (np.zeros((0, 3), dtype=np.int16),
                np.zeros((0,), dtype=np.int16), [])
    xyz = np.stack([np.concatenate(xs), np.concatenate(ys), np.concatenate(zs)],
                   axis=1).astype(np.int16)
    return xyz, np.concatenate(ids).astype(np.int16), np.concatenate(flags).astype(np.uint8)


def extract_voxels_states(region_dir: str, cx0: int, cz0: int, cx1: int, cz1: int,
                          y0: int = Y_MIN, y1: int = Y_MAX):
    """提取区块范围内所有非空气方块，含完整 blockstate（供 deepslate 完整模型渲染）。

    与 extract_voxels 的区别：保留每个方块的 Properties（axis/half/facing 等），
    并把 (方块名, properties) 去重编码成 state id。

    返回 (xyz, states, state_table)：
      xyz        —— (N,3) int16，世界坐标
      states     —— (N,)  uint16，每个方块的状态 id（state_table 下标）
      state_table —— list[(name, properties_dict_or_None)]，按 state id 排序
    """
    rx0, rx1 = cx0 // 32, (cx1 - 1) // 32
    rz0, rz1 = cz0 // 32, (cz1 - 1) // 32

    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    zs: list[np.ndarray] = []
    st: list[np.ndarray] = []
    fl: list[np.ndarray] = []

    # (name, properties_key) → state id
    state_map: dict[tuple, int] = {}
    state_table: list[tuple[str, dict | None]] = []

    def state_id(name: str, props: dict | None) -> int:
        key = (name, _props_key(props))
        i = state_map.get(key)
        if i is None:
            i = len(state_table)
            state_map[key] = i
            state_table.append((name, props))
        return i

    for rx in range(rx0, rx1 + 1):
        for rz in range(rz0, rz1 + 1):
            path = os.path.join(region_dir, f"r.{rx}.{rz}.mca")
            if not os.path.exists(path) or os.path.getsize(path) < 8192:
                continue
            rf = _get_region(path)
            for cx in range(max(cx0, rx * 32), min(cx1, rx * 32 + 32)):
                for cz in range(max(cz0, rz * 32), min(cz1, rz * 32 + 32)):
                    ch = rf.read_chunk(cx, cz)
                    if ch is None or not ch.sections:
                        continue
                    for sy, sec in ch.sections.items():
                        top = sy * 16 + 15
                        bot = sy * 16
                        if top < y0 or bot > y1:
                            continue
                        # 保留所有非空气方块（含台阶/栅栏/草/树叶等，deepslate 能渲染完整模型）
                        cls = _classify(sec.palette)
                        keep = cls != C_AIR
                        if not keep.any():
                            continue
                        props = sec.properties if sec.properties else [None] * len(sec.palette)
                        grid = sec.blocks.reshape(16, 16, 16)  # [y, z, x]
                        sel = keep[grid]
                        yy, zz, xx = np.nonzero(sel)
                        gx = cx * 16 + xx
                        gy = sy * 16 + yy
                        gz = cz * 16 + zz
                        ym = (gy >= y0) & (gy <= y1)
                        if not ym.any():
                            continue
                        gx, gy, gz = gx[ym], gy[ym], gz[ym]
                        idx = grid[yy[ym], zz[ym], xx[ym]]
                        # 逐方块编码 state id（方块数量大，但 palette 小，可用 numpy 向量化预处理）
                        uniq_idx = np.unique(idx)
                        local_map = np.zeros(int(idx.max()) + 1, dtype=np.int64)
                        for pi in uniq_idx:
                            pi = int(pi)
                            local_map[pi] = state_id(sec.palette[pi],
                                                     props[pi] if pi < len(props) else None)
                        xs.append(gx.astype(np.int16))
                        ys.append(gy.astype(np.int16))
                        zs.append(gz.astype(np.int16))
                        st.append(local_map[idx].astype(np.uint16))
                        # flags: 0=实心(遮挡邻居) / 1=水 / 2=非实心(不遮挡邻居，含不完整方块、植物/装饰物、透明物等)
                        f = np.full(gx.shape, 2, dtype=np.uint8)
                        sc = cls[idx]
                        f[sc == C_SOLID] = 0
                        f[sc == C_WATER] = 1
                        fl.append(f)
            del rf

    if not xs:
        return (np.zeros((0, 3), dtype=np.int16),
                np.zeros((0,), dtype=np.uint16),
                np.zeros((0,), dtype=np.uint8), [])
    xyz = np.stack([np.concatenate(xs), np.concatenate(ys), np.concatenate(zs)],
                   axis=1).astype(np.int16)
    states = np.concatenate(st).astype(np.uint16)
    flags = np.concatenate(fl).astype(np.uint8)
    return xyz, states, flags, state_table


def _props_key(props: dict | None) -> tuple:
    """把 properties dict 转成可哈希、可排序的稳定 key。"""
    if not props:
        return ()
    return tuple(sorted(props.items()))


def filter_exposed(xyz: np.ndarray, ids: np.ndarray, flags: np.ndarray):
    """面剔除：只保留「6 个邻居中至少一个不是实心方块」的方块。

    完整地下约 80~90% 是「被实心方块完全包围」的内部方块，它们 6 面
    全被遮挡，渲染时本就不可见。把它们提前剔除，能让 deepslate 的
    内存占用降低 5~10 倍，从而渲染 20 区块以上完整地下。

    规则：
      - 只有「实心方块」（flags==0）视为遮挡者；水（1）/非完整（2）不遮挡。
      - 选区边界处的方块，外侧邻居不在集合内 → 视为暴露（剖面正确保留）。
    """
    n = xyz.shape[0]
    if n == 0:
        return xyz, ids, flags
    solid = flags == 0
    if not solid.any():
        return xyz, ids, flags
    xs = xyz[solid]
    x0, y0, z0 = (int(v) for v in xs.min(axis=0))
    x1, y1, z1 = (int(v) for v in xs.max(axis=0))
    W, H, D = x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1
    occ = np.zeros((W, H, D), dtype=bool)
    occ[xs[:, 0] - x0, xs[:, 1] - y0, xs[:, 2] - z0] = True

    px = xyz[:, 0].astype(np.int32) - x0
    py = xyz[:, 1].astype(np.int32) - y0
    pz = xyz[:, 2].astype(np.int32) - z0
    exposed = np.zeros(n, dtype=bool)
    for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
        nx, ny, nz = px + dx, py + dy, pz + dz
        inb = (nx >= 0) & (nx < W) & (ny >= 0) & (ny < H) & (nz >= 0) & (nz < D)
        blocked = np.zeros(n, dtype=bool)
        if inb.any():
            blocked[inb] = occ[nx[inb], ny[inb], nz[inb]]
        exposed |= ~blocked
    return xyz[exposed], ids[exposed], flags[exposed]


def render_choropleth(region_dir: str, out_path: str, max_span: int = 400) -> tuple[int, int, int, int]:
    """渲染「哪些区块存在数据」的总览图。返回 (cx0, cz0, cx1, cz1)。"""
    from .anvil import scan_regions
    present: set[tuple[int, int]] = set()
    for path in scan_regions(region_dir):
        rf = RegionFile(path)
        present.update(rf.present())
        del rf
    if not present:
        raise RuntimeError("没有找到任何区块数据")

    xs = [c[0] for c in present]
    zs = [c[1] for c in present]
    cx0, cx1 = min(xs), max(xs) + 1
    cz0, cz1 = min(zs), max(zs) + 1

    if cx1 - cx0 > max_span or cz1 - cz0 > max_span:
        cx0, cz0 = -max_span // 2, -max_span // 2
        cx1, cz1 = max_span // 2, max_span // 2

    w, h = cx1 - cx0, cz1 - cz0
    arr = np.full((h, w, 3), 24, dtype=np.uint8)
    for (cx, cz) in present:
        x, z = cx - cx0, cz - cz0
        if 0 <= x < w and 0 <= z < h:
            arr[z, x] = (70, 140, 220)
    # 出生点标记
    if -1 - cx0 < w and 1 - cz0 < h:
        arr[max(0, 1 - cz0 - 1):1 - cz0 + 2, max(0, -1 - cx0 - 1):-1 - cx0 + 2] = (230, 90, 60)
    img = Image.fromarray(arr, mode="RGB")
    if max(img.size) < 900:
        img = img.resize((w * 2, h * 2), Image.NEAREST)
    img.save(out_path)
    return cx0, cz0, cx1, cz1


__all__ = ["render", "render_choropleth", "HeightMap", "chunk_heightmap",
           "extract_voxels", "extract_voxels_states", "name_id", "id_name"]
