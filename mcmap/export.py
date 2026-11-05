"""OBJ + MTL + PNG 真贴图导出（多色 3D 打印用）。

把体素方块（完整方块 + 非完整方块）导出为带真实贴图与规范流形网格的 OBJ 模型：
  - OBJ：共享几何顶点 + 精确 UV，完整体素接触面消隐，按材质分组
  - MTL：多材质定义（含漫反射基色 Kd 与贴图引用 map_Kd）
  - PNG：所有用到的方块贴图打包成高精度无缝图集

复杂细节的部分相交尚无通用布尔保证；AMS 槽位绑定使用 multicolor 模块的 3MF 路线。
"""

from __future__ import annotations

import base64
import io
import json
import os
import zipfile
from collections.abc import Sequence

import numpy as np
from PIL import Image

from . import geom, gltf, multicolor, palette, vanilla, voxel


def _texture_bytes(assets: vanilla.VanillaAssets, tex_name: str) -> bytes | None:
    """取贴图 PNG 字节（含 biome 着色），无则 None。"""
    if assets is None:
        return None
    png = assets._read_texture_png(tex_name)
    if png is None:
        return None
    from . import texture
    from .vanilla import tint_color
    # 应用 tint（草/树叶/水等灰度图需要上色）
    tc = tint_color(tex_name)
    if tc is None:
        return png
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    arr = np.asarray(img, dtype=np.float32)
    arr[:, :, 0] *= tc[0]
    arr[:, :, 1] *= tc[1]
    arr[:, :, 2] *= tc[2]
    if len(tc) > 3:
        arr[:, :, 3] *= tc[3]
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA").save(buf, format="PNG")
    return buf.getvalue()


def generate_fallback_texture(name: str, tile_px: int = 16) -> bytes:
    """按名称哈希纯色兜底，生成中性无刺眼紫黑的 fallback PNG。"""
    h = sum((idx + 1) * ord(c) for idx, c in enumerate(name))
    r = 95 + (h * 37) % 115
    g = 95 + (h * 59) % 115
    b = 95 + (h * 83) % 115
    img = np.full((tile_px, tile_px, 4), (r, g, b, 255), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(img, "RGBA").save(buf, format="PNG")
    return buf.getvalue()


def build_texture_atlas(
    assets: vanilla.VanillaAssets,
    tex_names: list[str],
    tile_px: int = 16,
    return_colors: bool = False,
    textures: dict[str, bytes] | None = None,
):
    """把多张贴图拼成一张图集。

    返回 (PNG bytes, {tex_name: (u0,v0,u1,v1)}, cols, rows)
    或当 return_colors=True 时返回 (PNG bytes, uv_map, tex_colors, cols, rows)。
    UV 用「贴图像素坐标 → 图集归一化坐标」映射。每张贴图 16×16（或原尺寸）。
    """
    n = len(tex_names)
    if n == 0:
        empty_colors = {}
        if return_colors:
            return b"", {}, empty_colors, 0, 0
        return b"", {}, 0, 0

    cols = max(1, int(np.ceil(np.sqrt(n))))
    rows = max(1, int(np.ceil(n / cols)))

    atlas = np.zeros((rows * tile_px, cols * tile_px, 4), dtype=np.uint8)
    uv_map: dict[str, tuple[float, float, float, float]] = {}
    tex_colors: dict[str, tuple[float, float, float]] = {}

    for i, name in enumerate(tex_names):
        data = textures.get(name) if textures is not None else _texture_bytes(assets, name)
        if data is None:
            # 无贴图：按名称哈希纯色兜底
            h = sum((idx + 1) * ord(c) for idx, c in enumerate(name))
            r = 95 + (h * 37) % 115
            g = 95 + (h * 59) % 115
            b = 95 + (h * 83) % 115
            img = np.full((tile_px, tile_px, 4), (r, g, b, 255), dtype=np.uint8)
            if name.startswith("_fallback/"):
                img[:] = palette.resolve(name[len("_fallback/"):])
        else:
            im = Image.open(io.BytesIO(data)).convert("RGBA")
            if im.size != (tile_px, tile_px):
                im = im.resize((tile_px, tile_px), Image.NEAREST)
            img = np.asarray(im, dtype=np.uint8)

        # 计算不透明像素的代表漫反射基色（用于切片软件识别多色）
        alpha_mask = img[:, :, 3] > 32
        if np.any(alpha_mask):
            avg_r = float(np.mean(img[:, :, 0][alpha_mask])) / 255.0
            avg_g = float(np.mean(img[:, :, 1][alpha_mask])) / 255.0
            avg_b = float(np.mean(img[:, :, 2][alpha_mask])) / 255.0
        else:
            avg_r, avg_g, avg_b = 0.8, 0.8, 0.8
        tex_colors[name] = (avg_r, avg_g, avg_b)

        ty, tx = divmod(i, cols)
        atlas[ty * tile_px:(ty + 1) * tile_px,
              tx * tile_px:(tx + 1) * tile_px] = img

        # 图集归一化 UV 边界
        u0 = tx / cols
        v0 = ty / rows
        u1 = (tx + 1) / cols
        v1 = (ty + 1) / rows
        uv_map[name] = (u0, v0, u1, v1)

    buf = io.BytesIO()
    Image.fromarray(atlas, "RGBA").save(buf, format="PNG")
    png_bytes = buf.getvalue()

    if return_colors:
        return png_bytes, uv_map, tex_colors, cols, rows
    return png_bytes, uv_map, cols, rows


def export_obj_zip(
    assets: vanilla.VanillaAssets,
    solid_tris: list[dict],        # 完整方块三角面（每面一个 tex）
    detail_tris: list[dict],       # 非完整方块三角面
    scale: float,
    out_zip: str | io.BytesIO,
    model_name: str = "mcmap",
    textures: dict[str, bytes] | None = None,
    subdivide: int = 1,
) -> dict:
    """导出 OBJ + MTL + PNG 到 zip。

    solid_tris / detail_tris 里每项是 {"verts": [(x,y,z)*4], "uvs": [(u,v)*4], "tex": 贴图短名}
    导出行为：
    1. 完整重合接触面剔除并焊接几何索引；复杂细节部分相交仍需实体布尔；
    2. UV 像素 1:1 映射，无拉伸斜纹；
    3. MTL 保留各方块代表色与贴图映射，AMS 槽位绑定由 3MF 路线提供；
    4. 输出单一 OBJ 模型包。
    """
    all_tris = geom.remove_shared_faces(solid_tris + detail_tris)
    if subdivide > 1:
        all_tris = geom.subdivide_quads(all_tris, subdivide)
    all_tris.sort(key=lambda t: t.get("tex", ""))

    # 收集所有贴图名（去重）
    tex_names: list[str] = []
    for t in all_tris:
        if t["tex"] not in tex_names:
            tex_names.append(t["tex"])

    png_bytes, uv_map, tex_colors, cols, rows = build_texture_atlas(
        assets, tex_names, return_colors=True, textures=textures)
    atlas_name = f"{model_name}.png"

    # 生成 OBJ 与 MTL
    obj_lines: list[str] = [
        f"# mcmap export - {model_name}",
        f"mtllib {model_name}.mtl",
    ]
    obj_lines.append(f"o {model_name}")

    mtl_lines: list[str] = [
        f"# mcmap materials library",
        f"newmtl {model_name}_atlas",
        f"Ka 1.000 1.000 1.000",
        f"Kd 1.000 1.000 1.000",
        f"map_Kd {atlas_name}",
    ]

    mat_name_map = {}
    for t_name in tex_names:
        clean = "mat_" + "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in t_name)
        mat_name_map[t_name] = clean
        cr, cg, cb = tex_colors.get(t_name, (0.8, 0.8, 0.8))
        mtl_lines.extend([
            f"",
            f"newmtl {clean}",
            f"Ka {cr:.3f} {cg:.3f} {cb:.3f}",
            f"Kd {cr:.3f} {cg:.3f} {cb:.3f}",
            f"map_Kd {atlas_name}",
        ])

    # 几何顶点共享索引，UV 仍按面独立，保留材质接缝。
    soup = [t["verts"][i] for t in all_tris for i in
            ((0, 1, 2, 0, 2, 3) if len(t["verts"]) == 4 else (0, 1, 2))]
    vertices, triangles = voxel.index_mesh(np.asarray(soup))
    for v in vertices:
        obj_lines.append(f"v {v[0]:.5f} {v[1]:.5f} {v[2]:.5f}")
    for t in all_tris:
        tu0, tv0, tu1, tv1 = uv_map.get(t["tex"], (0, 0, 1, 1))
        tex_w = tu1 - tu0
        tex_h = tv1 - tv0
        for (pu, pv) in t["uvs"]:
            # Minecraft 单个瓦片 16 像素，归一化映射到图集对应瓦片
            nu = min(max(pu / 16.0, 0.0), 1.0)
            nv = min(max(pv / 16.0, 0.0), 1.0)
            gu = tu0 + nu * tex_w
            gv = tv0 + nv * tex_h
            # OBJ 的 v 坐标从底朝上，图集 v 从顶朝下，翻转 v
            obj_lines.append(f"vt {gu:.6f} {1.0 - gv:.6f}")

    # 面：每 4 顶点一个 quad（逆时针 2 三角形，法线绝对朝外）
    ti = 1
    triangle_index = 0
    current_mat = None
    for face_index, t in enumerate(all_tris):
        mat = mat_name_map.get(t["tex"], f"{model_name}_atlas")
        if mat != current_mat:
            current_mat = mat
            obj_lines.append(f"usemtl {mat}")

        uv_triangles = ((0, 1, 2), (0, 2, 3)) if len(t['verts']) == 4 else ((0, 1, 2),)
        for uv_indices in uv_triangles:
            indices = triangles[triangle_index] + 1
            obj_lines.append('f ' + ' '.join(f'{v}/{ti+uv}' for v, uv in zip(indices, uv_indices)))
            triangle_index += 1
        ti += len(t['verts'])

    obj_text = "\n".join(obj_lines) + "\n"
    mtl_text = "\n".join(mtl_lines) + "\n"

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{model_name}.obj", obj_text)
        zf.writestr(f"{model_name}.mtl", mtl_text)
        zf.writestr(atlas_name, png_bytes)
    return {
        "triangles": len(triangles),
        "textures": len(tex_names),
        "vertices": len(vertices),
        "atlas_size": f"{cols * 16}x{rows * 16}",
    }


def export_glb(assets, quads: list[dict], model_name: str = "mcmap",
               textures: dict[str, bytes] | None = None,
               z_up: bool = False,
               subdivide: int = 1) -> tuple[bytes, dict]:
    quads = geom.remove_shared_faces(quads)
    if not quads:
        raise ValueError("选区内没有可导出的面")
    if subdivide > 1:
        quads = geom.subdivide_quads(quads, subdivide)
    names = sorted({face["tex"] for face in quads})
    png, uv_map, cols, rows = build_texture_atlas(assets, names, textures=textures)
    return gltf.write_glb(quads, png, uv_map, model_name, z_up=z_up), {
        "triangles": sum(len(q['verts']) - 2 for q in quads), "textures": len(names),
        "vertices": sum(len(q['verts']) for q in quads), "atlas_size": f"{cols * 16}x{rows * 16}"}


def _pixel_uv_range(tri: dict) -> tuple[float, float, float, float]:
    """返回该面 UV 的像素范围（min/max）。保留兼容。"""
    us = [uv[0] for uv in tri["uvs"]]
    vs = [uv[1] for uv in tri["uvs"]]
    return (min(us), min(vs), max(us), max(vs))


def _slice_axes(axis: str) -> tuple[int, int, int]:
    """返回 (slice_dimension, image_u_dimension, image_v_dimension)。"""
    axis = str(axis).lower()
    if axis == "y":
        return 1, 0, 2
    if axis == "x":
        return 0, 2, 1
    if axis == "z":
        return 2, 0, 1
    raise ValueError("axis 必须是 y、x 或 z")


def _rgba_texture(assets: vanilla.VanillaAssets | None, name: str,
                  axis: str, tile_px: int = 16, surface: str | None = None) -> Image.Image:
    """为切片顶面取得一个 16px 贴图，缺失资源时使用稳定色块。"""
    tex_name = None
    if assets is not None:
        try:
            top, side, bottom = assets.get_textures(name)
            tex_name = {"top": top, "side": side, "bottom": bottom}.get(
                surface or ("top" if axis == "y" else "side"))
        except Exception:
            tex_name = None
    data = _texture_bytes(assets, tex_name) if tex_name else None
    if data is None:
        return Image.new("RGBA", (tile_px, tile_px), palette.resolve(name))
    return Image.open(io.BytesIO(data)).convert("RGBA").resize(
        (tile_px, tile_px), Image.Resampling.NEAREST)


def _prism_triangles(u: int, v: int, scale: float, thickness: float,
                     exposed: set[str], z0: float = 0.0) -> list[tuple[float, float, float]]:
    """生成单个平放棱柱的三角顶点，坐标为 (u, v, 打印高度)。"""
    x0, x1 = u * scale, (u + 1) * scale
    y0, y1 = v * scale, (v + 1) * scale
    z1 = z0 + thickness
    faces = {
        "top": ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)),
        "bottom": ((x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0)),
        "west": ((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)),
        "east": ((x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (x1, y0, z0)),
        "north": ((x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (x0, y0, z0)),
        "south": ((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)),
    }
    out: list[tuple[float, float, float]] = []
    for name in ("top", "bottom", "west", "east", "north", "south"):
        if name not in exposed:
            continue
        p0, p1, p2, p3 = faces[name]
        out.extend((p0, p1, p2, p0, p2, p3))
    return out


def _slice_manifest_entry(axis: str, index: int, axis_index: int,
                          origin: tuple[int, int, int],
                          size_blocks: tuple[int, int, int],
                          scale: float, thickness: float,
                          body_name: str, top_name: str) -> dict:
    # size_blocks 保留世界轴；size_mm 使用平放打印坐标（图片 U/V、Z 厚度）。
    _slice_dim, u_dim, v_dim = _slice_axes(axis)
    size_mm = {
        "x": round(size_blocks[u_dim] * scale, 4),
        "y": round(size_blocks[v_dim] * scale, 4),
        "z": round(thickness, 4),
    }
    return {
        "axis": axis,
        "index": index,
        "axis_index": axis_index,
        "origin_block": {"x": origin[0], "y": origin[1], "z": origin[2]},
        "size_blocks": {"x": size_blocks[0], "y": size_blocks[1], "z": size_blocks[2]},
        "size_mm": size_mm,
        "px_per_block": 16,
        "mm_per_px": round(scale / 16.0, 6),
        "body": body_name,
        "top": top_name,
    }


def export_batch_slices(
    xyz: np.ndarray,
    block_names: Sequence[str] | None = None,
    box: tuple[int, ...] | None = None,
    axis: str = "y",
    step: int = 1,
    thickness: float = 2.0,
    scale: float = 2.0,
    assets: vanilla.VanillaAssets | None = None,
    mode: str = "batch",
    slice_index: int | None = None,
    stand: bool = False,
    model_format: str = "glb",
    color_count: int = 4,
    slice_indices: Sequence[int] | None = None,
) -> tuple[bytes, dict]:
    """把体素按指定轴逐格切成平放薄片并打包 ZIP。

    ``xyz`` 是非空气方块的世界坐标；``box`` 按接口顺序为
    ``(x0, z0, x1, z1, y0, y1)``。默认输出内嵌贴图的 GLB，也支持
    OBJ/MTL/PNG、单色 STL 与 AMS 3MF；每片附顶面 PNG 和 manifest.json。
    """
    axis = str(axis).lower()
    slice_dim, u_dim, v_dim = _slice_axes(axis)
    model_format = str(model_format).lower()
    if model_format not in ("glb", "obj", "stl", "3mf"):
        raise ValueError("导出格式必须是 glb、obj、stl 或 3mf")
    if model_format == "3mf" and color_count not in (4, 8):
        raise ValueError("3MF 色数必须是 4 或 8")
    if int(step) != 1:
        raise ValueError("step 目前必须为 1")
    if mode not in ("batch", "single", "selected"):
        raise ValueError("mode 必须是 batch、single 或 selected")
    if not np.isfinite(scale) or not np.isfinite(thickness) or scale <= 0 or thickness <= 0:
        raise ValueError("scale 和 thickness 必须为有限正数")
    if xyz is None:
        xyz = np.zeros((0, 3), dtype=np.int64)
    xyz = np.asarray(xyz, dtype=np.int64)
    if xyz.ndim != 2 or xyz.shape[1] != 3:
        raise ValueError("xyz 必须是 N×3 坐标数组")
    if block_names is None:
        block_names = ["minecraft:stone"] * len(xyz)
    if len(block_names) != len(xyz):
        raise ValueError("block_names 与 xyz 长度不一致")
    if box is not None and len(box) == 6:
        x0, z0, x1, z1, y0, y1 = (int(v) for v in box)
        lo = (min(x0, x1), min(y0, y1), min(z0, z1))
        hi = (max(x0, x1), max(y0, y1), max(z0, z1))
    elif box is not None and len(box) == 4:
        x0, z0, x1, z1 = (int(v) for v in box)
        lo = (min(x0, x1), int(xyz[:, 1].min()) if len(xyz) else 0, min(z0, z1))
        hi = (max(x0, x1), int(xyz[:, 1].max()) if len(xyz) else -1, max(z0, z1))
    elif len(xyz):
        lo = tuple(int(v) for v in xyz.min(axis=0))
        hi = tuple(int(v) for v in xyz.max(axis=0))
    else:
        raise ValueError("选区内没有可导出的方块")

    keep = np.all((xyz >= np.asarray(lo)) & (xyz <= np.asarray(hi)), axis=1)
    xyz = xyz[keep]
    names = [str(block_names[i]) for i, ok in enumerate(keep) if ok]
    if not len(xyz):
        raise ValueError("选区内没有可导出的方块")

    plane_lo = (lo[u_dim], lo[v_dim])
    plane_size = (hi[u_dim] - lo[u_dim] + 1, hi[v_dim] - lo[v_dim] + 1)
    present = sorted(set(int(v) for v in xyz[:, slice_dim]))
    ordinals = {coordinate: i + 1 for i, coordinate in enumerate(present)}
    if mode == "selected":
        chosen = sorted({int(index) for index in slice_indices}) if slice_indices is not None else []
        if not chosen or not set(chosen).issubset(present):
            raise ValueError("请选择有效的非空切片，所选坐标不能超出选区")
        present = chosen
    if mode == "single":
        if slice_index is not None:
            present = [p for p in present if p == int(slice_index)]
        else:
            present = present[:1]
    if not present:
        raise ValueError("指定切片为空")

    manifest_slices: list[dict] = []
    tiles: dict[tuple[str, str], Image.Image] = {}

    def tile_for(name, surface):
        key = (name, surface)
        if key not in tiles:
            tiles[key] = _rgba_texture(assets, name, axis, surface=surface)
        return tiles[key]

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for coordinate in present:
            ordinal = ordinals[coordinate]
            mask = xyz[:, slice_dim] == coordinate
            points = xyz[mask]
            point_names = [names[i] for i, ok in enumerate(mask) if ok]
            name_by_uv = {
                (int(p[u_dim] - plane_lo[0]), int(p[v_dim] - plane_lo[1])): name
                for p, name in zip(points, point_names)
            }
            occupied = {(int(p[u_dim] - plane_lo[0]), int(p[v_dim] - plane_lo[1]))
                        for p in points}
            # 底框与主体先做占用并集，再生成外壳，避免叠放独立棱柱的内壁。
            cells = {(u, v, 0) for u, v in occupied}
            layer_height = thickness
            if stand:
                layer_height = thickness / 2
                cells.update((u, v, 1) for u, v in occupied)
                sw, sh = plane_size
                cells.update((u, v, 0) for u in range(sw) for v in range(sh)
                             if u in (0, sw - 1) or v in (0, sh - 1))
            verts: list[tuple[float, float, float]] = []
            quads = []
            textures = {}
            for u, v, layer in sorted(cells):
                name = name_by_uv.get((u, v), "minecraft:stone")
                for du, dv, dh, face in ((0, 0, 1, "top"), (0, 0, -1, "bottom"),
                                         (-1, 0, 0, "west"), (1, 0, 0, "east"),
                                         (0, -1, 0, "north"), (0, 1, 0, "south")):
                    if (u + du, v + dv, layer + dh) not in cells:
                        face_verts = _prism_triangles(u, v, scale, layer_height, {face},
                                                      z0=layer * layer_height)
                        verts.extend(face_verts)
                        if model_format in ("glb", "obj"):
                            vertical = dh if axis == "y" else dv
                            surface = "top" if vertical > 0 else "bottom" if vertical < 0 else "side"
                            texture_name = f"{name}/{surface}"
                            if texture_name not in textures:
                                png = io.BytesIO()
                                tile_for(name, surface).save(png, format="PNG")
                                textures[texture_name] = png.getvalue()
                            quads.append({"verts": [face_verts[i] for i in (0, 1, 2, 5)],
                                          "uvs": [(0, 16), (16, 16), (16, 0), (0, 0)],
                                          "tex": texture_name})

            vert_array = np.asarray(verts, dtype=np.float32)
            model_name = f"slice_{axis.upper()}{ordinal:03d}_body"
            body_name = f"{model_name}.{model_format}"
            top_name = f"slice_{axis.upper()}{ordinal:03d}_top.png"
            if model_format == "glb":
                body, _ = export_glb(None, quads, model_name, textures=textures, z_up=True)
                zf.writestr(body_name, body)
            elif model_format == "obj":
                model_zip = io.BytesIO()
                export_obj_zip(None, quads, [], scale, model_zip, model_name, textures=textures)
                with zipfile.ZipFile(model_zip) as model_files:
                    for path in model_files.namelist():
                        zf.writestr(path, model_files.read(path))
            elif model_format == "3mf":
                positions = np.asarray(sorted(cells))
                colors = []
                for u, v, _layer in positions:
                    name = name_by_uv.get((int(u), int(v)), "minecraft:stone")
                    tile = np.asarray(tile_for(name, "top" if axis == "y" else "side"))
                    opaque = tile[tile[:, :, 3] > 32, :3]
                    colors.append(opaque.mean(axis=0) if len(opaque) else (200, 200, 200))
                body, _ = multicolor.export_slice_3mf(
                    positions, np.asarray(colors), scale, layer_height, color_count, model_name)
                zf.writestr(body_name, body)
            else:
                body, _ = _write_flat_stl(vert_array, body_name)
                zf.writestr(body_name, body)

            img = Image.new("RGBA", (plane_size[0] * 16, plane_size[1] * 16), (0, 0, 0, 0))
            for (u, v) in sorted(occupied):
                name = name_by_uv[(u, v)]
                tile = tile_for(name, "top" if axis == "y" else "side")
                px = u * 16
                py = (plane_size[1] - v - 1) * 16
                img.alpha_composite(tile, (px, py))
            png = io.BytesIO()
            img.save(png, format="PNG", optimize=False)
            zf.writestr(top_name, png.getvalue())

            source_origin = [lo[0], lo[1], lo[2]]
            source_origin[slice_dim] = coordinate
            size_blocks = [hi[0] - lo[0] + 1, hi[1] - lo[1] + 1, hi[2] - lo[2] + 1]
            size_blocks[slice_dim] = 1
            entry = _slice_manifest_entry(
                axis, ordinal, coordinate, tuple(source_origin), tuple(size_blocks),
                scale, thickness, body_name, top_name)
            manifest_slices.append(entry)

        manifest = {
            "version": 2,
            "format": model_format,
            "axis": axis,
            "scale_mm_per_block": float(scale),
            "thickness_mm": float(thickness),
            "px_per_block": 16,
            "mm_per_px": round(scale / 16.0, 6),
            "slices": manifest_slices,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return output.getvalue(), manifest


def _write_flat_stl(vertices: np.ndarray, name: str) -> tuple[bytes, int]:
    """写入切片用 STL，避免让切片导出依赖临时文件。"""
    if vertices.size == 0:
        return b"", 0
    tris = vertices.reshape(-1, 3)
    n = len(tris) // 3
    v1 = tris[1::3] - tris[0::3]
    v2 = tris[2::3] - tris[0::3]
    normals = np.cross(v1, v2)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths[lengths == 0] = 1
    normals = normals / lengths
    rec = np.zeros((n, 50), dtype=np.uint8)
    values = np.concatenate([normals, tris.reshape(n, 9)], axis=1).astype("<f4")
    rec[:, :48] = values.view(np.uint8).reshape(n, 48)
    header = name.encode("ascii", "replace")[:80].ljust(80, b"\0")
    return header + np.uint32(n).astype("<u4").tobytes() + rec.tobytes(), n


__all__ = ["export_obj_zip", "export_glb", "export_batch_slices", "build_texture_atlas",
           "_texture_bytes", "generate_fallback_texture"]
