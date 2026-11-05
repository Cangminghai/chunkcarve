"""方块贴图。

首选从 Minecraft 原版 jar 读取真实材质（顶/侧/底三面各不相同，还原度最高）；
对 jar 里没有的（模组方块）或解析失败的，回退到程序化像素合成（单色 + 噪点）。

原版路径由 vanilla.VanillaAssets 提供，通过 set_assets() 注入；未注入时
全部走程序化合成（离线可复现，不依赖外部资源）。
"""

from __future__ import annotations

import zlib
from io import BytesIO

import numpy as np
from PIL import Image

from . import palette

TILE = 16
COLS = 8          # 图集每行放 8 个方块
FACES = ("top", "side", "bottom")

# 全局原版资源库（可选注入）
_assets = None


def set_assets(assets) -> None:
    """注入原版资源库（vanilla.VanillaAssets 实例）。注入后 tile() 优先用原版贴图。"""
    global _assets
    _assets = assets

# 矿石 → 矿斑颜色
ORE_SPECKS: dict[str, tuple[int, int, int]] = {
    "coal": (26, 26, 28),
    "iron": (206, 172, 148),
    "copper": (214, 136, 86),
    "gold": (246, 214, 88),
    "redstone": (198, 34, 34),
    "lapis": (44, 82, 190),
    "diamond": (112, 236, 236),
    "emerald": (44, 222, 112),
    "quartz": (242, 238, 228),
    "ancient_debris": (128, 82, 66),
}

METALS = {
    "iron", "gold", "copper", "diamond", "emerald", "coal", "redstone",
    "lapis", "netherite", "quartz", "amethyst",
}

_cache: dict[tuple[str, str], np.ndarray] = {}
_atlas_cache: dict[tuple, tuple] = {}


# ---------------------------------------------------------------- 工具

def _seed(name: str, face: str) -> int:
    """稳定种子：不能用 hash()，它在每个进程里都不一样。"""
    return zlib.crc32(f"{name}|{face}".encode()) & 0x7FFFFFFF


def _fine(rng, sigma: float, t: int = TILE) -> np.ndarray:
    return rng.normal(0.0, sigma, (t, t))


def _clusters(rng, n: int, amp: float, t: int = TILE) -> np.ndarray:
    """n×n 的粗糙色块放大到整格——像素材质的"斑驳"就是这么来的。"""
    step = t // n
    c = rng.normal(0.0, amp, (n, n))
    out = np.repeat(np.repeat(c, step, 0), step, 1)
    if out.shape[0] < t:
        out = np.pad(out, ((0, t - out.shape[0]), (0, t - out.shape[1])), mode="edge")
    return out[:t, :t]


def _speckle(rng, n: int, factor: float, t: int = TILE) -> np.ndarray:
    """随机撒点（深色颗粒）。"""
    f = np.ones((t, t))
    for _ in range(n):
        y, x = int(rng.integers(0, t)), int(rng.integers(0, t))
        f[y, x] *= factor
    return f


def _voronoi(rng, n: int, lo: float, hi: float,
             seam: float = 0.66, width: float = 1.1,
             t: int = TILE) -> np.ndarray:
    """不规则碎石：随机站点 + 最近站点分配，站点交界处压暗。

    均匀网格画出来的"碎石"一眼就是方格纸，必须用不规则站点才像石头。
    """
    pts = rng.uniform(0, t, (n, 2))
    yy, xx = np.mgrid[0:t, 0:t]
    d = np.sqrt((yy[None] - pts[:, 0, None, None]) ** 2 +
                (xx[None] - pts[:, 1, None, None]) ** 2)
    order = np.argsort(d, axis=0)
    nearest = order[0]
    gap = d[order[1], yy, xx] - d[order[0], yy, xx]
    f = rng.uniform(lo, hi, n)[nearest]
    f[gap < width] *= seam
    return f


def _paint(rgb, factor: np.ndarray, rng, grain: float = 4.0) -> np.ndarray:
    base = np.array(rgb[:3], dtype=np.float32)
    tex = base[None, None, :] * factor[:, :, None]
    tex = tex + rng.normal(0.0, grain, (TILE, TILE, 1))
    return np.clip(tex, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- 各材质

def _planks(rng, rgb, face):
    f = np.ones((TILE, TILE))
    ph = 4
    for r in range(TILE // ph):
        shade = 1.0 + rng.uniform(-0.07, 0.07)       # 每块板条明暗略不同
        f[r * ph:(r + 1) * ph, :] = shade
        for _ in range(int(rng.integers(1, 3))):     # 竖向木纹
            x = int(rng.integers(0, TILE))
            f[r * ph:(r + 1) * ph, x] *= rng.uniform(0.80, 0.90)
        f[r * ph, :] *= 0.68                          # 板条之间的缝
    f *= 1.0 + _clusters(rng, 8, 0.03) + _fine(rng, 0.025)
    return _paint(rgb, f, rng)


def _bricks(rng, rgb, face, rh: int = 4, bw: int = 8, mortar: float = 0.74):
    f = np.ones((TILE, TILE))
    for r in range(TILE // rh):
        off = (r % 2) * (bw // 2)
        f[r * rh:(r + 1) * rh] = 1.0 + rng.uniform(-0.07, 0.07)
        f[r * rh, :] = mortar
        for b in range(-1, TILE // bw + 1):
            x = (b * bw + off) % TILE
            f[r * rh:(r + 1) * rh, x] = mortar
    f *= 1.0 + _fine(rng, 0.045)
    return _paint(rgb, f, rng)


def _cobble(rng, rgb, face):
    f = _voronoi(rng, 9, 0.72, 1.18, seam=0.60, width=1.3)
    f *= 1.0 + _fine(rng, 0.05)
    return _paint(rgb, f, rng)


def _stone(rng, rgb, face):
    f = 1.0 + _clusters(rng, 4, 0.075) + _fine(rng, 0.035)
    f *= _speckle(rng, 10, 0.86)
    if face == "side":
        # 侧壁：叠几道水平层理。剖面被拉高时看着像沉积岩，而不是一团糊掉的噪点
        for _ in range(3):
            y = int(rng.integers(0, TILE - 1))
            hgt = int(rng.integers(1, 3))
            f[y:y + hgt, :] *= rng.uniform(0.86, 0.94)
    return _paint(rgb, f, rng)


def _dirt(rng, rgb, face):
    f = 1.0 + _clusters(rng, 4, 0.11) + _fine(rng, 0.05)
    f *= _speckle(rng, 18, 0.80)
    return _paint(rgb, f, rng)


def _sand(rng, rgb, face):
    if face == "side":                                # 砂岩侧面：水平层理
        f = np.ones((TILE, TILE))
        for r in range(0, TILE, 3):
            f[r:r + 3] = 1.0 + rng.uniform(-0.05, 0.05)
            f[r, :] *= 0.93
        f *= 1.0 + _fine(rng, 0.03)
        return _paint(rgb, f, rng)
    f = 1.0 + _fine(rng, 0.045) + _clusters(rng, 8, 0.03)
    f *= _speckle(rng, 12, 0.90)
    return _paint(rgb, f, rng)


def _gravel(rng, rgb, face):
    f = _voronoi(rng, 16, 0.68, 1.26, seam=0.70, width=0.9)
    f *= 1.0 + _fine(rng, 0.06)
    return _paint(rgb, f, rng)


def _leaves(rng, rgb, face):
    f = 1.0 + _clusters(rng, 4, 0.20) + _fine(rng, 0.09)
    holes = rng.random((TILE, TILE)) < 0.16
    f[holes] *= 0.52                                  # 叶隙
    bright = rng.random((TILE, TILE)) < 0.09
    f[bright] *= 1.22
    return _paint(rgb, f, rng)


def _wool(rng, rgb, face):
    f = 1.0 + _clusters(rng, 8, 0.022) + _fine(rng, 0.020)
    return _paint(rgb, f, rng, grain=2.0)


def _concrete(rng, rgb, face):
    f = 1.0 + _fine(rng, 0.015) + _clusters(rng, 8, 0.012)
    return _paint(rgb, f, rng, grain=2.0)


def _terracotta(rng, rgb, face):
    f = np.ones((TILE, TILE))
    for r in range(TILE):
        f[r, :] = 1.0 + rng.uniform(-0.04, 0.04)
    f *= 1.0 + _fine(rng, 0.025)
    return _paint(rgb, f, rng)


def _metal(rng, rgb, face):
    f = np.ones((TILE, TILE))
    f[0, :] *= 0.82
    f[:, 0] *= 0.82
    f[-1, :] *= 1.08
    f[:, -1] *= 1.08
    f *= 1.0 + _fine(rng, 0.018)
    return _paint(rgb, f, rng, grain=3.0)


def _ore(rng, rgb, face):
    f = 1.0 + _clusters(rng, 4, 0.07) + _fine(rng, 0.04)
    tex = _paint(rgb, f, rng).astype(np.float32)
    short = ""
    for key in ORE_SPECKS:
        if key in _CUR[0]:
            short = key
            break
    if short:
        col = np.array(ORE_SPECKS[short], dtype=np.float32)
        for _ in range(7):
            y, x = int(rng.integers(0, TILE - 1)), int(rng.integers(0, TILE - 1))
            tex[y:y + 2, x:x + 2] = col * rng.uniform(0.9, 1.1)
    return np.clip(tex, 0, 255).astype(np.uint8)


def _grass_top(rng, rgb, face):
    f = 1.0 + _clusters(rng, 4, 0.10) + _fine(rng, 0.06)
    bright = rng.random((TILE, TILE)) < 0.10
    f[bright] *= 1.14
    return _paint(rgb, f, rng)


def _grass_side(rng, rgb, face):
    """草方块侧面：泥土底 + 顶部垂下的草边。这是最有辨识度的一张。"""
    dirt = palette.resolve("minecraft:dirt")[:3]
    grass = palette.resolve("minecraft:grass_block")[:3]
    f = 1.0 + _clusters(rng, 4, 0.11) + _fine(rng, 0.06)
    f *= _speckle(rng, 16, 0.84)
    tex = (np.array(dirt, dtype=np.float32)[None, None, :] * f[:, :, None])

    gf = 1.0 + _clusters(rng, 4, 0.10)
    grass_tex = np.array(grass, dtype=np.float32)[None, None, :] * gf[:, :, None]
    depth = rng.integers(3, 6, TILE)                   # 每列草边下垂深度
    for x in range(TILE):
        d = int(depth[x])
        tex[:d, x] = grass_tex[:d, x]
        tex[d, x] = tex[d, x] * 0.55 + grass_tex[d, x] * 0.45
    tex = tex + rng.normal(0.0, 3.0, (TILE, TILE, 1))
    return np.clip(tex, 0, 255).astype(np.uint8)


def _bark(rng, rgb, face):
    f = np.ones((TILE, TILE))
    for x in range(TILE):                              # 竖向树皮沟壑
        f[:, x] = 1.0 + rng.uniform(-0.12, 0.12)
    for _ in range(4):
        x = int(rng.integers(0, TILE))
        f[:, x] *= 0.72
    f *= 1.0 + _fine(rng, 0.04)
    return _paint(rgb, f, rng)


def _log_top(rng, rgb, face):
    yy, xx = np.mgrid[0:TILE, 0:TILE]
    d = np.sqrt((yy - 7.5) ** 2 + (xx - 7.5) ** 2)
    f = 1.0 + 0.075 * np.cos(d * 2.1)                  # 年轮
    f *= 1.0 + _fine(rng, 0.035)
    return _paint(rgb, f, rng)


def _water(rng, rgb, face):
    yy, xx = np.mgrid[0:TILE, 0:TILE]
    f = (1.0 + 0.05 * np.sin(yy * 1.6 + xx * 0.5)
         + 0.04 * np.sin(xx * 1.1 - 1.3) + _fine(rng, 0.03))
    return _paint(rgb, f * 1.05, rng)


def _snow(rng, rgb, face):
    f = 1.0 + _fine(rng, 0.012) + _clusters(rng, 8, 0.01)
    return _paint(rgb, f, rng, grain=2.0)


def _flat(rng, rgb, face):
    f = 1.0 + _fine(rng, 0.04) + _clusters(rng, 8, 0.02)
    return _paint(rgb, f, rng)


# ---------------------------------------------------------------- 分类

_CUR = ["", "side"]   # 当前正在生成的 (短名, 面)，供 _ore 取矿石类型


def _kind(short: str, face: str) -> str:
    s = short
    if "water" in s:
        return "water"
    if "lava" in s:
        return "water"
    if s.endswith("_leaves") or s in ("moss_block", "moss_carpet"):
        return "leaves"
    if "planks" in s:
        return "planks"
    if s.endswith(("_log", "_wood", "_stem", "_hyphae")):
        return "log_top" if face == "top" else "bark"
    if s == "grass_block":
        return "grass_top" if face == "top" else "grass_side"
    if "brick" in s:
        return "stone_brick" if "stone" in s else "bricks"
    if "cobblestone" in s or s.startswith("cobbled"):
        return "cobble"
    if s in ("stone", "andesite", "diorite", "granite", "deepslate",
             "tuff", "calcite", "basalt", "blackstone"):
        return "stone"
    if "sand" in s:
        return "sand"
    if "gravel" in s:
        return "gravel"
    if "dirt" in s or s in ("podzol", "mud", "farmland", "clay", "mycelium"):
        return "dirt"
    if "wool" in s or "carpet" in s:
        return "wool"
    if "concrete" in s:
        return "concrete"
    if "terracotta" in s or "glazed" in s:
        return "terracotta"
    if "ore" in s:
        return "ore"
    if s.endswith("_block") and any(m in s for m in METALS):
        return "metal"
    if "snow" in s or "ice" in s:
        return "snow"
    return "flat"


_DISPATCH = {
    "planks": _planks,
    "bricks": lambda r, c, f: _bricks(r, c, f, 4, 8, 0.74),
    "stone_brick": lambda r, c, f: _bricks(r, c, f, 8, 8, 0.70),
    "cobble": _cobble,
    "stone": _stone,
    "dirt": _dirt,
    "sand": _sand,
    "gravel": _gravel,
    "leaves": _leaves,
    "wool": _wool,
    "concrete": _concrete,
    "terracotta": _terracotta,
    "metal": _metal,
    "ore": _ore,
    "grass_top": _grass_top,
    "grass_side": _grass_side,
    "bark": _bark,
    "log_top": _log_top,
    "water": _water,
    "snow": _snow,
    "flat": _flat,
}


def _vanilla_tile(name: str, face: str) -> np.ndarray | None:
    """从原版 jar 取某方块某面的 16×16 RGBA 像素；无则返回 None。"""
    if _assets is None:
        return None
    top, side, bottom = _assets.get_textures(name)
    tex_name = {"top": top, "side": side, "bottom": bottom}.get(face)
    if tex_name is None:
        return None
    png = _assets._read_texture_png(tex_name)
    if png is None:
        return None
    img = Image.open(BytesIO(png)).convert("RGBA")
    if img.size != (TILE, TILE):
        img = img.resize((TILE, TILE), Image.NEAREST)
    arr = np.asarray(img, dtype=np.uint8)

    # biome 着色：草/树叶/水等原版是灰度图，需要按 tint 上色（含透明度）
    from . import vanilla as _van
    tc = _van.tint_color(tex_name)
    if tc is not None:
        f = arr.astype(np.float32)
        f[:, :, 0] *= tc[0]
        f[:, :, 1] *= tc[1]
        f[:, :, 2] *= tc[2]
        if len(tc) > 3:
            f[:, :, 3] *= tc[3]
        arr = np.clip(f, 0, 255).astype(np.uint8)
    return arr


def tile(name: str, face: str = "side") -> np.ndarray:
    """单个方块某一面的 16×16 RGBA 像素画。优先原版贴图，回退程序化合成。"""
    face = face if face in FACES else "side"
    key = (name, face)
    hit = _cache.get(key)
    if hit is not None:
        return hit

    # 1) 优先原版材质
    v = _vanilla_tile(name, face)
    if v is not None:
        out = v.copy()
        if len(_cache) > 8192:
            _cache.clear()
        _cache[key] = out
        return out

    # 2) 回退程序化合成
    rgb = palette.resolve(name)
    short = name.split(":")[-1]
    _CUR[0] = short
    _CUR[1] = face
    rng = np.random.default_rng(_seed(name, face))
    kind = _kind(short, face)
    tex = _DISPATCH[kind](rng, rgb, face)

    # 每格最外圈轻微压暗：让相邻同类方块的边界看得出来，
    # 这正是"能看出每一个方块"和"一坨糊在一起"的区别。
    edge = np.ones((TILE, TILE, 1), dtype=np.float32)
    edge[0, :, 0] = 0.965
    edge[-1, :, 0] = 0.965
    edge[:, 0, 0] = 0.965
    edge[:, -1, 0] = 0.965
    tex = np.clip(tex.astype(np.float32) * edge, 0, 255).astype(np.uint8)

    out = np.empty((TILE, TILE, 4), dtype=np.uint8)
    out[:, :, :3] = tex
    out[:, :, 3] = rgb[3]
    if len(_cache) > 8192:
        _cache.clear()
    _cache[key] = out
    return out


def build_atlas(entries: list[tuple[str, str]],
                tile_px: int = TILE, cols: int = COLS):
    """把 (方块名, 面) 列表拼成一张图集。

    返回 (PIL.Image RGBA, {(name,face): index}, cols, rows)。
    """
    key = (tuple(entries), tile_px, cols)
    hit = _atlas_cache.get(key)
    if hit is not None:
        return hit

    n = len(entries)
    rows = max(1, (n + cols - 1) // cols)
    atlas = np.zeros((rows * tile_px, cols * tile_px, 4), dtype=np.uint8)

    for i, (name, face) in enumerate(entries):
        tex = tile(name, face)
        if tile_px != TILE:
            tex = np.repeat(np.repeat(tex, tile_px // TILE, 0),
                            tile_px // TILE, 1)
        ty, tx = divmod(i, cols)
        atlas[ty * tile_px:(ty + 1) * tile_px,
              tx * tile_px:(tx + 1) * tile_px] = tex

    img = Image.fromarray(atlas, mode="RGBA")
    idx = {e: i for i, e in enumerate(entries)}
    out = (img, idx, cols, rows)
    if len(_atlas_cache) > 6:
        _atlas_cache.clear()
    _atlas_cache[key] = out
    return out


def atlas_entries(names: list[str]) -> list[tuple[str, str]]:
    """为一组方块名生成 (名, 面) 条目表：每个方块占三格（顶面、侧面、底面）。"""
    out: list[tuple[str, str]] = []
    for n in names:
        out.append((n, "top"))
        out.append((n, "side"))
        out.append((n, "bottom"))
    return out


__all__ = ["tile", "build_atlas", "atlas_entries", "TILE", "COLS", "FACES"]
