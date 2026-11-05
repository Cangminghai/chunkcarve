"""柱状体素模型：3D 预览与 STL 导出共用同一份几何数据。

模型定义为「每列从底面填充到地表高度」，这与 3D 打印的向下填实策略一致：
既保证水密自支撑，又天然消除悬垂。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

# 1.18+ 世界竖直范围：Y -64 ~ 319（共 384 层）
Y_MIN = -64
Y_MAX = 320


@dataclass
class Surface:
    x0: int
    z0: int
    height: np.ndarray   # (h, w) int16，地表 Y
    color: np.ndarray    # (h, w, 3) uint8
    water: np.ndarray    # (h, w) bool
    y_base: int
    y_top: int

    def __repr__(self):
        v = self.height[self.height > -900]
        return (f"<Surface {self.width}×{self.depth} "
                f"Y {v.min() if v.size else '-'}~{v.max() if v.size else '-'} base={self.y_base}>")

    @property
    def width(self) -> int:
        return self.height.shape[1]

    @property
    def depth(self) -> int:
        return self.height.shape[0]

    def stats(self, scale_mm: float) -> dict:
        h = np.clip(self.height - self.y_base, 0, None).astype(np.int64)
        valid = self.height > -900
        cells = int((h * valid).sum())
        vol_mm3 = cells * (scale_mm ** 3)
        hollow = vol_mm3 * 0.42  # 外壳 + 低填充的经验系数
        return {
            "width_mm": round(self.width * scale_mm, 1),
            "depth_mm": round(self.depth * scale_mm, 1),
            "height_mm": round(float(h.max() if h.size else 0) * scale_mm, 1),
            "cells": cells,
            "volume_cm3": round(vol_mm3 / 1000, 1),
            "material_g": round(hollow / 1000 * 1.24, 0),
        }


def make_surface(height: np.ndarray, color: np.ndarray, water: np.ndarray,
                 x0: int, z0: int, layers: int = 0,
                 y0: int | None = None, y1: int | None = None) -> Surface:
    """把高度图裁剪成可打印的地形切片。

    y0 / y1 为绝对世界 Y 坐标，可自由指定要保留的竖直范围
    （从基岩 -64 到建筑顶端 319 都允许）。
    layers > 0 时按"从最高地表向下取 N 层"，留 0 则取选区完整高度范围——
    后者才是 2.5D 预览想要的：不会把地面以下一律压平。
    """
    valid = height > -900
    if not valid.any():
        raise ValueError("选区没有方块数据")
    h_max = int(height[valid].max())
    h_min = int(height[valid].min())

    if y1 is None:
        y1 = h_max
    if y0 is None:
        y0 = h_min if layers <= 0 else y1 - max(1, layers) + 1
    y0 = int(np.clip(y0, Y_MIN, h_max))
    y1 = int(np.clip(y1, h_min, Y_MAX))
    if y1 <= y0:
        y1 = min(Y_MAX, y0 + 1)

    h = np.clip(height, y0, y1).astype(np.int16)
    h[~valid] = y0

    c = np.asarray(color, dtype=np.uint8).copy()
    if c.ndim == 2:
        c = np.stack([c, c, c], -1)
    c = c[:, :, :3]
    c[~valid] = (40, 42, 46)

    return Surface(x0=x0, z0=z0, height=h, color=c,
                   water=water, y_base=y0, y_top=y1)


# ---------- 网格生成 ----------

def build_triangles(s: Surface, scale: float = 1.0,
                    with_bottom: bool = True,
                    with_base: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """生成三角面。返回 (vertices[N,3], colors[N,3])，法线朝外。"""
    H = s.height.astype(np.float64)
    d, w = H.shape
    y0 = float(s.y_base - 1)
    # 把高度转换成底面以上的层数
    top = (H - y0) * scale
    base = 0.0

    V: list[np.ndarray] = []
    C: list[np.ndarray] = []

    def quad(p0, p1, p2, p3, col):
        V.append(np.array([p0, p1, p2, p0, p2, p3], dtype=np.float32))
        C.append(np.repeat(np.array(col, dtype=np.uint8)[None, :], 6, axis=0))

    idx = np.arange(d * w).reshape(d, w)
    zz, xx = np.mgrid[0:d, 0:w]
    # 输出相对坐标，模型左下角对齐原点，方便切片软件居中
    X0 = xx * scale
    Z0 = zz * scale
    X1 = X0 + scale
    Z1 = Z0 + scale

    # --- 顶面 ---
    col = s.color.reshape(-1, 3)
    p = np.stack([X0, top, Z0], -1)
    q = np.stack([X1, top, Z0], -1)
    r = np.stack([X1, top, Z1], -1)
    t = np.stack([X0, top, Z1], -1)
    # 必须按「每三角形连续三点」交错，不能按顶点角色平铺
    top_tris = np.stack([p, r, q, p, t, r], axis=2)   # (d,w,6,3)
    V.append(top_tris.reshape(-1, 3))
    C.append(np.repeat(col, 6, axis=0))

    # --- 侧面：只在与邻列有高差时生成 ---
    # 边界外视作底面高度，这样才能生成选区四周的外墙，保证网格闭合
    pad = np.pad(top, 1, mode="constant", constant_values=base)
    padc = np.pad(s.color, ((1, 1), (1, 1), (0, 0)), mode="edge")

    dirs = [
        (1, 0, 0, 1),   # +x
        (-1, 0, 0, 0),  # -x
        (0, 0, 1, 1),   # +z
        (0, 0, -1, 0),  # -z
    ]
    for dz, dx, _, _ in [(1, 0, 0, 1), (-1, 0, 0, 0), (0, 1, 0, 1), (0, -1, 0, 0)]:
        nb = pad[1 + dz:1 + dz + d, 1 + dx:1 + dx + w]
        diff = top - nb
        mask = diff > 1e-4
        if not mask.any():
            continue
        zs, xs = np.nonzero(mask)
        h_top = top[zs, xs]
        h_bot = np.maximum(nb[zs, xs], base)
        ccol = padc[1 + dz:1 + dz + d, 1 + dx:1 + dx + w][zs, xs]

        x_lo = xs * scale
        x_hi = x_lo + scale
        z_lo = zs * scale
        z_hi = z_lo + scale

        for i in range(len(zs)):
            xl, xh, zl, zh = x_lo[i], x_hi[i], z_lo[i], z_hi[i]
            # 按方块层细分竖边，和相邻较矮侧壁共用边，避免 T-junction。
            for layer in range(round(h_bot[i] / scale), round(h_top[i] / scale)):
                yb, yt = layer * scale, (layer + 1) * scale
                if dz == 1:      # 朝 +z 的面（面向观察者的南面）
                    quad((xl, yb, zh), (xh, yb, zh), (xh, yt, zh), (xl, yt, zh), ccol[i])
                elif dz == -1:   # 朝 -z
                    quad((xh, yb, zl), (xl, yb, zl), (xl, yt, zl), (xh, yt, zl), ccol[i])
                elif dx == 1:    # 朝 +x
                    quad((xh, yb, zh), (xh, yb, zl), (xh, yt, zl), (xh, yt, zh), ccol[i])
                else:            # 朝 -x
                    quad((xl, yb, zl), (xl, yb, zh), (xl, yt, zh), (xl, yt, zl), ccol[i])

    if with_bottom or with_base:
        # 底面必须按格细分，否则与外圈侧壁形成 T-junction，网格不闭合
        bcol = np.array([150, 152, 158], dtype=np.uint8)
        X0b = xx * scale
        X1b = X0b + scale
        Z0b = zz * scale
        Z1b = Z0b + scale
        yb_col = np.full_like(X0b, base, dtype=np.float32)
        bp = np.stack([X0b, yb_col, Z0b], -1)
        bq = np.stack([X0b, yb_col, Z1b], -1)
        br = np.stack([X1b, yb_col, Z1b], -1)
        bt = np.stack([X1b, yb_col, Z0b], -1)
        bot_tris = np.stack([bp, br, bq, bp, bt, br], axis=2)
        V.append(bot_tris.reshape(-1, 3))
        C.append(np.tile(bcol, (d * w * 6, 1)))

    verts = np.concatenate(V, axis=0).astype(np.float32)
    cols = np.concatenate(C, axis=0)
    return verts, cols


def write_stl_bytes(verts: np.ndarray, name: str = "mcmap") -> tuple[bytes, int]:
    """生成二进制 STL 字节流。每条面记录 50 字节：12 字节法线 + 36 字节顶点 + 2 字节属性。"""
    n = len(verts) // 3
    if n == 0:
        return b"", 0
    tris = verts.reshape(n, 3, 3).astype(np.float64)
    v1 = tris[:, 1] - tris[:, 0]
    v2 = tris[:, 2] - tris[:, 0]
    nrm = np.cross(v1, v2)
    ln = np.linalg.norm(nrm, axis=1, keepdims=True)
    ln[ln == 0] = 1
    nrm = nrm / ln

    header = name.encode("ascii", "replace")[:80].ljust(80, b"\0")
    rec = np.zeros((n, 50), dtype=np.uint8)
    floats = np.concatenate([nrm, tris.reshape(n, 9)], axis=1).astype("<f4")
    rec[:, :48] = floats.view(np.uint8).reshape(n, 48)
    import io
    buf = io.BytesIO()
    buf.write(header)
    buf.write(struct.pack("<I", n))
    buf.write(rec.tobytes())
    return buf.getvalue(), n


def write_stl(verts: np.ndarray, path: str, name: str = "mcmap") -> int:
    """写二进制 STL 文件。"""
    data, n = write_stl_bytes(verts, name=name)
    with open(path, "wb") as f:
        f.write(data)
    return n


def write_obj(verts: np.ndarray, cols: np.ndarray, path: str) -> int:
    """写 OBJ（带顶点色，便于在 MeshLab/Blender 里查看配色）。"""
    n = len(verts)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# mcmap export\n")
        for v in verts:
            f.write(f"v {v[0]:.4f} {v[1]:.4f} {v[2]:.4f}\n")
        for c in cols:
            f.write(f"v {c[0]/255:.4f} {c[1]/255:.4f} {c[2]/255:.4f}\n")
        for i in range(0, n, 3):
            f.write(f"f {i+1} {i+2} {i+3}\n")
    return n // 3


# ---------- 体素网格（忠实还原方块结构） ----------

# 六个面的朝向：法线方向 → 该面的 4 个角（相对方块 [0,1]^3）
# 每个面 4 个角按逆时针排列（从法线方向看），保证三角形法线朝外。
_FACES: list[tuple[tuple[int, int, int], tuple]] = [
    # (法线, 4 个角的 (x,y,z) 相对坐标，逆时针从外侧看)
    ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),   # 顶 +y
    ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),  # 底 -y
    ((1, 0, 0), ((1, 0, 1), (1, 0, 0), (1, 1, 0), (1, 1, 1))),   # +x
    ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),  # -x
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),   # +z
    ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),  # -z
]


def build_voxel_mesh(xyz: np.ndarray, colors: np.ndarray,
                     scale: float = 1.0,
                     origin: tuple[int, int, int] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """把体素方块转成三角网格，只为暴露在空气的面生成三角形。

    忠实还原游戏结构：侧面凹凸、洞穴、悬空都保留（不向下填实）。
    与 2.5D 预览同一套面剔除逻辑，所见即所得。

    参数：
      xyz    —— (N,3) 方块世界坐标（int，含 y）
      colors —— (N,3) 每个方块的 RGB（uint8）
      scale  —— 每方块边长（mm/单位）
      origin —— (x0,y0,z0) 模型基准对齐点；None 时默认取局部最小坐标 (min_x, min_y, min_z)

    返回 (verts, cols)：verts 是 (M*3,3) 顶点（模型相对原点对齐），
    cols 是 (M*3,3) 颜色。每 3 顶点一个三角形。
    """
    if xyz.size == 0:
        return (np.zeros((0, 3), dtype=np.float32),
                np.zeros((0, 3), dtype=np.uint8))

    # 占用表：世界坐标 → 下标
    occ: dict[tuple, int] = {}
    for i in range(xyz.shape[0]):
        occ[(int(xyz[i, 0]), int(xyz[i, 1]), int(xyz[i, 2]))] = i

    # 模型原点：优先采用指定 origin，否则取局部最小 x/y/z
    if origin is not None:
        x0, y0, z0 = origin
    else:
        x0 = int(xyz[:, 0].min())
        y0 = int(xyz[:, 1].min())
        z0 = int(xyz[:, 2].min())

    V: list[np.ndarray] = []
    C: list[np.ndarray] = []

    def quad(p0, p1, p2, p3, col):
        # 两个三角形 p0-p1-p2, p0-p2-p3
        V.append(np.array([p0, p1, p2, p0, p2, p3], dtype=np.float32))
        C.append(np.repeat(np.array(col, dtype=np.uint8)[None, :], 6, axis=0))

    for (cx, cy, cz), i in occ.items():
        col = colors[i]
        bx = (cx - x0) * scale
        by = (cy - y0) * scale
        bz = (cz - z0) * scale

        for (nx, ny, nz), corners in _FACES:
            nx_, ny_, nz_ = int(nx), int(ny), int(nz)
            if (cx + nx_, cy + ny_, cz + nz_) in occ:
                continue  # 邻接方块，不生成面
            # 暴露面：把相对角转成世界绝对坐标（未缩放）
            pts = []
            for (ox, oy, oz) in corners:
                px = bx + ox * scale
                py = by + oy * scale
                pz = bz + oz * scale
                pts.append((px, py, pz))
            quad(pts[0], pts[1], pts[2], pts[3], col)

    if not V:
        return (np.zeros((0, 3), dtype=np.float32),
                np.zeros((0, 3), dtype=np.uint8))
    verts = np.concatenate(V, axis=0)
    cols = np.concatenate(C, axis=0)
    return verts, cols


def index_mesh(verts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """焊接闭合面扇区中的顶点，保留仅边/角接触的独立外壳。

    STL 是无索引三角汤；OBJ/3MF 用此函数生成共享索引。不能把所有
    同坐标点直接合并，否则两个仅沿边相触的方块会形成四面共边。
    """
    points = np.asarray(verts, dtype=np.float32).reshape(-1, 3)
    keys = [tuple(p) for p in np.round(points, 5)]
    parent = list(range(len(points)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    edges: dict[tuple, list[tuple[int, int]]] = {}
    for i in range(0, len(points), 3):
        for a, b in ((i, i + 1), (i + 1, i + 2), (i + 2, i)):
            key = tuple(sorted((keys[a], keys[b])))
            edges.setdefault(key, []).append((a, b))
    for incident in edges.values():
        pending = list(incident)
        while pending:
            a, b = pending.pop()
            opposite = [(c, d) for c, d in pending if keys[a] == keys[d] and keys[b] == keys[c]]
            if not opposite:
                continue
            # Four faces can meet along an edge when solid cells touch diagonally.
            # Pair the convex sectors, preserving separate surface fans.
            def turn(edge):
                c, d = edge
                first = points[a // 3 * 3:a // 3 * 3 + 3]
                second = points[c // 3 * 3:c // 3 * 3 + 3]
                n1 = np.cross(first[1] - first[0], first[2] - first[0])
                n2 = np.cross(second[1] - second[0], second[2] - second[0])
                return float(np.dot(np.cross(n1, n2), points[b] - points[a]))
            c, d = max(opposite, key=turn) if len(opposite) > 1 else opposite[0]
            pending.remove((c, d))
            parent[root(a)] = root(d)
            parent[root(b)] = root(c)

    unique = []
    by_root = {}
    indices = []
    for i, point in enumerate(points):
        component = root(i)
        if component not in by_root:
            by_root[component] = len(unique)
            unique.append(point)
        indices.append(by_root[component])
    return (np.asarray(unique, dtype=np.float32).reshape(-1, 3),
            np.asarray(indices, dtype=np.int64).reshape(-1, 3))


__all__ = ["Surface", "make_surface", "build_triangles", "write_stl", "write_stl_bytes",
           "write_obj", "build_voxel_mesh", "index_mesh"]
