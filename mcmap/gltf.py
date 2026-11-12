"""Self-contained glTF 2.0 binary models with embedded Minecraft textures."""

import json
import struct

import numpy as np


def write_glb(quads: list[dict], png: bytes, uv_map: dict,
              name: str, z_up: bool = False) -> bytes:
    """Convert millimetre geometry to Y-up metres, embedding its texture atlas.

    Triangulates quadrilaterals, eliminates degenerate zero-area faces, and welds
    co-located, co-attribute vertices to build a clean manifold mesh.
    Prevents slicing engines (e.g. Bambu Studio, Creality Print) from triggering
    faulty fan-triangulation and degenerate edge repair.
    """
    if not quads:
        raise ValueError("导出的网格没有有效面")

    verts_list = []
    uvs_list = []

    for face in quads:
        v = face["verts"]
        tex = face["tex"]
        u0, v0, u1, v1 = uv_map.get(tex, (0.0, 0.0, 1.0, 1.0))
        local_uvs = np.clip(np.asarray(face["uvs"], dtype=np.float32) / 16.0, 0.0, 1.0)
        mapped_uvs = local_uvs * [u1 - u0, v1 - v0] + [u0, v0]

        if len(v) == 4:
            verts_list.extend([v[0], v[1], v[2], v[0], v[2], v[3]])
            uvs_list.extend([mapped_uvs[0], mapped_uvs[1], mapped_uvs[2],
                             mapped_uvs[0], mapped_uvs[2], mapped_uvs[3]])
        elif len(v) == 3:
            verts_list.extend([v[0], v[1], v[2]])
            uvs_list.extend([mapped_uvs[0], mapped_uvs[1], mapped_uvs[2]])

    if not verts_list:
        raise ValueError("导出的网格没有有效顶点")

    positions = np.asarray(verts_list, dtype="<f4")
    if z_up:
        positions = positions[:, [0, 2, 1]].copy()
        positions[:, 2] *= -1
    positions *= 0.001  # mm -> m
    uvs = np.asarray(uvs_list, dtype="<f4")

    # 计算逐面外法线与面积
    p0 = positions[0::3]
    p1 = positions[1::3]
    p2 = positions[2::3]
    face_normals = np.cross(p1 - p0, p2 - p0)
    lengths = np.linalg.norm(face_normals, axis=1)

    # 坐标已换算为米，保留 1 mm 方块上的 1/16 像素面。
    valid_mask = lengths > 1e-12
    if not np.all(valid_mask):
        valid_indices = np.repeat(valid_mask, 3)
        positions = positions[valid_indices]
        uvs = uvs[valid_indices]
        face_normals = face_normals[valid_mask]
        lengths = lengths[valid_mask]

    if len(positions) == 0:
        raise ValueError("剔除退化面后网格没有有效几何")

    face_normals /= lengths[:, None]
    normals = np.repeat(face_normals, 3, axis=0).astype("<f4")

    # 顶点焊接与唯一索引构建 (Position + UV + Normal)，消除 IEEE-754 -0.0 符号位差异
    attrs = (np.column_stack([
        np.round(positions, 6),
        np.round(normals, 3),
        np.round(uvs, 5)
    ]).astype(np.float32) + np.float32(0.0))

    dt = np.dtype((np.void, attrs.dtype.itemsize * attrs.shape[1]))
    attrs_void = np.ascontiguousarray(attrs).view(dt).ravel()
    _, first_indices, inverse = np.unique(attrs_void, return_index=True, return_inverse=True)

    final_positions = positions[first_indices].astype("<f4")
    final_normals = normals[first_indices].astype("<f4")
    final_uvs = uvs[first_indices].astype("<f4")
    indices = inverse.astype("<u4")

    # 检查焊接后是否产生索引退化（3 索引中有任意 2 索引相同则剔除）
    tri_indices = indices.reshape(-1, 3)
    valid_tris = (tri_indices[:, 0] != tri_indices[:, 1]) & \
                 (tri_indices[:, 1] != tri_indices[:, 2]) & \
                 (tri_indices[:, 2] != tri_indices[:, 0])
    if not np.all(valid_tris):
        indices = tri_indices[valid_tris].ravel()

    binary = bytearray()
    views = []

    def append(data, target=None):
        view = {"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        views.append(view)
        binary.extend(data)
        binary.extend(b"\0" * (-len(binary) % 4))
        return len(views) - 1

    position_view = append(final_positions.tobytes(), 34962)
    normal_view = append(final_normals.tobytes(), 34962)
    uv_view = append(final_uvs.tobytes(), 34962)
    index_view = append(indices.tobytes(), 34963)
    image_view = append(png)

    document = {
        "asset": {"version": "2.0", "generator": "ChunkCarve"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": [{
            "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
            "indices": 3, "material": 0,
        }]}],
        "materials": [{"name": "Minecraft atlas", "alphaMode": "MASK",
                       "alphaCutoff": 0.1, "pbrMetallicRoughness": {
                           "baseColorTexture": {"index": 0},
                           "metallicFactor": 0, "roughnessFactor": 1}}],
        "textures": [{"sampler": 0, "source": 0}],
        "samplers": [{"magFilter": 9728, "minFilter": 9728,
                      "wrapS": 33071, "wrapT": 33071}],
        "images": [{"bufferView": image_view, "mimeType": "image/png"}],
        "buffers": [{"byteLength": len(binary)}], "bufferViews": views,
        "accessors": [
            {"bufferView": position_view, "componentType": 5126,
             "count": len(final_positions), "type": "VEC3",
             "min": final_positions.min(axis=0).tolist(), "max": final_positions.max(axis=0).tolist()},
            {"bufferView": normal_view, "componentType": 5126,
             "count": len(final_normals), "type": "VEC3"},
            {"bufferView": uv_view, "componentType": 5126,
             "count": len(final_uvs), "type": "VEC2"},
            {"bufferView": index_view, "componentType": 5125,
             "count": len(indices), "type": "SCALAR"},
        ],
    }
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    return (struct.pack("<4sII", b"glTF", 2, length) +
            struct.pack("<II", len(encoded), 0x4E4F534A) + encoded +
            struct.pack("<II", len(binary), 0x004E4942) + binary)
