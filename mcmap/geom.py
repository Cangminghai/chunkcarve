"""方块模型几何体 → 三角面转换。

把 vanilla.parse_elements 解析出的 elements（长方体 + 每面 UV + 贴图）
转成三角面（顶点 + UV + 贴图引用），供前端 2.5D 渲染与 OBJ 真贴图导出共用。

Minecraft 模型坐标约定：
  - 方块本地坐标 0~16（1 方块 = 16 像素），Y 轴向上
  - 面名 up/down/north/south/west/east，对应法线方向
  - 每个面 4 个角按「从面外侧看逆时针」排列，保证三角形法线朝外
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .vanilla import _Element, _Geometry, VanillaAssets

# 面 → (外法线, 4 个角的本地坐标相对偏移 (fx, fy, fz)，从面外侧看严格逆时针 CCW)
# 本地坐标：+x 朝东、+y 朝上、+z 朝南
_FACE_DEF = {
    "up":    ((0, 1, 0),  [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)]),
    "down":  ((0, -1, 0), [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),
    "north": ((0, 0, -1), [(1, 0, 0), (0, 0, 0), (0, 1, 0), (1, 1, 0)]),
    "south": ((0, 0, 1),  [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]),
    "west":  ((-1, 0, 0), [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)]),
    "east":  ((1, 0, 0),  [(1, 0, 1), (1, 0, 0), (1, 1, 0), (1, 1, 1)]),
}


def _rotate_point(p: tuple[float, float, float],
                  origin: tuple[float, float, float],
                  axis: str, angle_deg: float) -> tuple[float, float, float]:
    """绕 axis（x/y/z）旋转 angle_deg 度（Minecraft 约定：-22.5/22.5/45/-45/90/-90）。"""
    if angle_deg == 0:
        return p
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    x, y, z = p
    ox, oy, oz = origin
    x -= ox; y -= oy; z -= oz
    if axis == "y":
        x, z = x * c + z * s, -x * s + z * c
    elif axis == "x":
        y, z = y * c - z * s, y * s + z * c
    elif axis == "z":
        x, y = x * c + y * s, -x * s + y * c
    return (x + ox, y + oy, z + oz)


def element_to_tris(el: _Element, scale: float = 1.0,
                    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
                    thicken: bool = True,
                    ) -> list[dict]:
    """单个 element → 三角面列表。

    每面生成 2 个三角形（4 顶点）。返回 [{
        "verts": [(x,y,z)*4],       # 世界坐标（已缩放，模型左下角对齐原点）
        "uvs":   [(u,v)*4],         # 贴图像素 UV（未归一化，0~16 或更大）
        "tex":   "贴图短名",
    }, ...]

    scale 把 1 方块（16 本地单位）映射到 scale 世界单位。
    origin 是方块在模型里的左下角世界坐标。
    """
    x0, y0, z0 = el.frm
    x1, y1, z1 = el.to
    rot = el.rotation

    # 零厚度处理：铁轨/地毯/压力板等模型是零厚度平面（from==to），
    # 直接生成三角形会退化（面积 0），导致破面/消失。给每个轴一个最小厚度。
    MIN_T = 0.1  # 最小厚度（本地单位，约 1.6 像素）
    if thicken and abs(x1 - x0) < 1e-6:
        x0, x1 = (x0 + x1) / 2 - MIN_T / 2, (x0 + x1) / 2 + MIN_T / 2
    if thicken and abs(y1 - y0) < 1e-6:
        # 高度轴向下扩展，让薄片贴在地面（铁轨/地毯是贴地的）
        y0, y1 = y0 - MIN_T / 2, y0 + MIN_T / 2
    if thicken and abs(z1 - z0) < 1e-6:
        z0, z1 = (z0 + z1) / 2 - MIN_T / 2, (z0 + z1) / 2 + MIN_T / 2

    # 8 个角坐标字典，直接按 (fx, fy, fz) 寻址，杜绝位移错乱
    local_dict = {
        (fx, fy, fz): (x1 if fx else x0, y1 if fy else y0, z1 if fz else z0)
        for fx in (0, 1) for fy in (0, 1) for fz in (0, 1)
    }

    if rot is not None:
        o = tuple(rot.get("origin", (8, 8, 8)))
        axis = rot.get("axis", "y")
        ang = float(rot.get("angle", 0))
        if rot.get('rescale'):
            factors = np.full(3, 1 / math.cos(math.radians(ang)))
            factors['xyz'.index(axis)] = 1
            local_dict = {k: tuple((np.asarray(c) - o) * factors + o)
                          for k, c in local_dict.items()}
        local_dict = {k: _rotate_point(c, o, axis, ang) for k, c in local_dict.items()}

    rx, ry = el.block_rotation
    if rx or ry:
        local_dict = {k: _rotate_point(
            _rotate_point(c, (8, 8, 8), "x", -rx), (8, 8, 8), "y", -ry)
            for k, c in local_dict.items()}

    # 本地坐标 → 世界坐标（16 本地单位 = 1 方块；scale 是每方块世界尺寸）
    k = scale / 16.0
    ox, oy, oz = origin
    world_dict = {
        k_coord: (c[0] * k + ox, c[1] * k + oy, c[2] * k + oz)
        for k_coord, c in local_dict.items()
    }

    tris: list[dict] = []
    for face, (tex, uv) in el.faces.items():
        if face not in _FACE_DEF:
            continue
        _, coords = _FACE_DEF[face]
        v0 = world_dict[coords[0]]
        v1 = world_dict[coords[1]]
        v2 = world_dict[coords[2]]
        v3 = world_dict[coords[3]]

        # UV：模型里 uv = [u0, v0, u1, v1]，u 沿水平、v 沿竖直（Minecraft 贴图坐标 v 向下）
        u0, v0u, u1, v1u = uv
        uv_corners = _face_uv_corners(face, u0, v0u, u1, v1u)
        tris.append({
            "verts": [v0, v1, v2, v3],
            "uvs": uv_corners,
            "tex": tex,
        })
    return tris


def _face_uv_corners(face: str, u0, v0, u1, v1):
    """返回面 4 个角（顺序与 _FACE_DEF 一致）的 UV。

    Minecraft UV 约定：u 向右，v 向下。up/down 面 uv 对应 (x, z)，
    其它面对应水平轴 + 竖直 y。
    """
    if face == "up":
        # 逆时针 4 角: 西北(u0,v0), 西南(u0,v1), 东南(u1,v1), 东北(u1,v0)
        return [(u0, v0), (u0, v1), (u1, v1), (u1, v0)]
    if face == "down":
        # 逆时针 4 角: 西北(u0,v0), 东北(u1,v0), 东南(u1,v1), 西南(u0,v1)
        return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    # north/south/west/east 侧面：4 角顺序均为 [底左, 底右, 顶右, 顶左]
    return [(u0, v1), (u1, v1), (u1, v0), (u0, v0)]


def geometry_to_tris(geo: _Geometry, scale: float = 1.0,
                     origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
                     cap_missing: bool = False,
                     ) -> list[dict]:
    """整个模型 → 三角面列表（多个 element 拼接）。"""
    elements = geo.elements
    if cap_missing:
        elements = []
        for el in geo.elements:
            faces = dict(el.faces)
            for face in _FACE_DEF:
                if face not in faces:
                    faces[face] = (next(iter(el.faces.values()))[0],
                                   tuple(VanillaAssets._default_uv(face, el.frm, el.to)))
            elements.append(replace(el, faces=faces))
    parts = [element_to_tris(el, scale, origin) for el in elements]
    out = [face for part in parts for face in part]
    if out and all(len(part) >= 4 for part in parts):
        out = _union_box_faces(out, [np.asarray([f["verts"] for f in part]).reshape(-1, 3)
                                     for part in parts])
    return remove_shared_faces(out)


def _union_box_faces(faces: list[dict], boxes: list[np.ndarray]) -> list[dict]:
    """Union axis-aligned model boxes without losing their face textures/UVs.

    Use common boundary coordinates so partial contacts in stairs and fences
    remove internal faces and produce matching edges on the outside.
    Rotated non-rectilinear elements keep their existing mesh path.
    """
    points = np.round(np.asarray([f["verts"] for f in faces]), 9)
    for quad in points:
        if (np.count_nonzero(np.abs(quad[1] - quad[0]) > 1e-8) != 1 or
                np.count_nonzero(np.abs(quad[3] - quad[0]) > 1e-8) != 1):
            return faces
    boxes_lo = np.round([box.min(axis=0) for box in boxes], 9)
    boxes_hi = np.round([box.max(axis=0) for box in boxes], 9)
    cuts = [np.unique(points[:, :, axis]) for axis in range(3)]
    result = []
    for face, quad in zip(faces, points):
        p0 = quad[0]
        u, v = quad[1] - p0, quad[3] - p0
        normal = np.cross(u, v)
        normal /= np.linalg.norm(normal)
        ui, vi = int(np.argmax(np.abs(u))), int(np.argmax(np.abs(v)))
        us = np.sort((cuts[ui] - p0[ui]) / u[ui])
        vs = np.sort((cuts[vi] - p0[vi]) / v[vi])
        us, vs = us[(us >= 0) & (us <= 1)], vs[(vs >= 0) & (vs <= 1)]
        uv = np.asarray(face["uvs"])
        for u0, u1 in zip(us[:-1], us[1:]):
            for v0, v1 in zip(vs[:-1], vs[1:]):
                probe = p0 + (u0 + u1) / 2 * u + (v0 + v1) / 2 * v + normal * 1e-7
                if np.any(np.all((probe >= boxes_lo) & (probe <= boxes_hi), axis=1)):
                    continue
                corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
                result.append({"verts": [p0 + a * u + b * v for a, b in corners],
                               "uvs": [(1-a)*(1-b)*uv[0] + a*(1-b)*uv[1] +
                                       a*b*uv[2] + (1-a)*b*uv[3] for a, b in corners],
                               "tex": face["tex"]})
    return result


def remove_shared_faces(faces: list[dict]) -> list[dict]:
    """去掉精确重合的内部接触面，并合并同向重复面（与材质无关）。

    仅处理完整重合的面；局部重叠或旋转元素相交需要实体布尔运算，
    不能通过顶点焊接推断出新的封闭外壳。
    """
    unique = {}
    for face in faces:
        points = np.round(np.asarray(face["verts"], dtype=float), 5)
        key = tuple(sorted(tuple(p) for p in points))
        normal = np.cross(points[1] - points[0], points[2] - points[0])
        if key not in unique:
            unique[key] = (face, normal)
        elif unique[key] is not None:
            if np.dot(unique[key][1], normal) < 0:
                unique[key] = None
    return [item[0] for item in unique.values() if item is not None]


def collect_textures(tris: list[dict]) -> list[str]:
    """按出现顺序去重，返回贴图短名列表。"""
    seen: list[str] = []
    for t in tris:
        if t["tex"] not in seen:
            seen.append(t["tex"])
    return seen


def full_cube_tris(top_tex: str, side_tex: str, bottom_tex: str,
                   scale: float = 1.0,
                   origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
                   ) -> list[dict]:
    """完整方块（16³）→ 六面三角面，顶/侧/底三张贴图。

    供 OBJ 导出与前端渲染共用：完整方块用真实三面贴图 + 规范朝外法线与精确 UV。
    """
    faces = [
        ("up", top_tex),
        ("down", bottom_tex),
        ("north", side_tex),
        ("south", side_tex),
        ("west", side_tex),
        ("east", side_tex),
    ]
    return full_cube_tris_culled(faces, scale=scale, origin=origin)


def full_cube_tris_culled(faces: list[tuple[str, str]],
                          scale: float = 1.0,
                          origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
                          ) -> list[dict]:
    """完整方块的面剔除版本：只生成 faces 里列出的面。

    faces 是 [(面方向, 贴图名), ...]，面方向支持精确朝向 up/down/north/south/west/east。
    若传入旧版 'side'，则生成四个侧面（兼容历史调用）。
    每个面只生成 1 个四边形（拆为两个朝外法线三角形），彻底消除重叠面与自穿插内部壁。
    """
    ox, oy, oz = origin
    k = scale
    x0, x1 = ox, ox + k
    y0, y1 = oy, oy + k
    z0, z1 = oz, oz + k

    pts = {
        (0, 0, 0): (x0, y0, z0),
        (1, 0, 0): (x1, y0, z0),
        (1, 0, 1): (x1, y0, z1),
        (0, 0, 1): (x0, y0, z1),
        (0, 1, 0): (x0, y1, z0),
        (1, 1, 0): (x1, y1, z0),
        (1, 1, 1): (x1, y1, z1),
        (0, 1, 1): (x0, y1, z1),
    }

    # 规范外法线 CCW 面定义与 0~16 像素 UV
    face_map = {
        "up": (
            [pts[(0,1,0)], pts[(0,1,1)], pts[(1,1,1)], pts[(1,1,0)]],
            [(0, 0), (0, 16), (16, 16), (16, 0)],
        ),
        "down": (
            [pts[(0,0,0)], pts[(1,0,0)], pts[(1,0,1)], pts[(0,0,1)]],
            [(0, 0), (16, 0), (16, 16), (0, 16)],
        ),
        "north": (
            [pts[(1,0,0)], pts[(0,0,0)], pts[(0,1,0)], pts[(1,1,0)]],
            [(0, 16), (16, 16), (16, 0), (0, 0)],
        ),
        "south": (
            [pts[(0,0,1)], pts[(1,0,1)], pts[(1,1,1)], pts[(0,1,1)]],
            [(0, 16), (16, 16), (16, 0), (0, 0)],
        ),
        "west": (
            [pts[(0,0,0)], pts[(0,0,1)], pts[(0,1,1)], pts[(0,1,0)]],
            [(0, 16), (16, 16), (16, 0), (0, 0)],
        ),
        "east": (
            [pts[(1,0,1)], pts[(1,0,0)], pts[(1,1,0)], pts[(1,1,1)]],
            [(0, 16), (16, 16), (16, 0), (0, 0)],
        ),
    }

    tris: list[dict] = []
    for kind, tex in faces:
        if kind in face_map:
            verts, uvs = face_map[kind]
            tris.append({"verts": verts, "uvs": uvs, "tex": tex})
        elif kind == "side":
            for s_dir in ("north", "south", "west", "east"):
                verts, uvs = face_map[s_dir]
                tris.append({"verts": verts, "uvs": uvs, "tex": tex})
    return tris


__all__ = ["element_to_tris", "geometry_to_tris", "collect_textures",
           "full_cube_tris", "full_cube_tris_culled", "remove_shared_faces"]
