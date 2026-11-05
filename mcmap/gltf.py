"""Self-contained glTF 2.0 binary models with embedded Minecraft textures."""

import json
import struct

import numpy as np


def write_glb(quads: list[dict], png: bytes, uv_map: dict,
              name: str, z_up: bool = False) -> bytes:
    """Convert millimetre geometry to Y-up metres, embedding its texture atlas."""
    positions = np.asarray([face["verts"] for face in quads], dtype="<f4")
    corners = positions.shape[1]
    if z_up:
        positions = positions[:, :, [0, 2, 1]].copy()
        positions[:, :, 2] *= -1
    positions *= 0.001
    normals = np.cross(positions[:, 1] - positions[:, 0],
                       positions[:, 2] - positions[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals /= np.where(lengths > 0, lengths, 1)
    normals = np.repeat(normals[:, None, :], corners, axis=1)
    uvs = []
    for face in quads:
        u0, v0, u1, v1 = uv_map[face["tex"]]
        local = np.clip(np.asarray(face["uvs"], dtype=np.float32) / 16, 0, 1)
        uvs.extend(local * [u1 - u0, v1 - v0] + [u0, v0])
    indices = (np.arange(len(quads), dtype=np.uint32)[:, None] * corners +
               ([0, 1, 2, 0, 2, 3] if corners == 4 else [0, 1, 2])).astype("<u4")
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

    position_view = append(positions.astype("<f4").tobytes(), 34962)
    normal_view = append(normals.astype("<f4").tobytes(), 34962)
    uv_view = append(np.asarray(uvs, dtype="<f4").tobytes(), 34962)
    index_view = append(indices.tobytes(), 34963)
    image_view = append(png)
    vertices = positions.reshape(-1, 3)
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
             "count": len(vertices), "type": "VEC3",
             "min": vertices.min(axis=0).tolist(), "max": vertices.max(axis=0).tolist()},
            {"bufferView": normal_view, "componentType": 5126,
             "count": len(vertices), "type": "VEC3"},
            {"bufferView": uv_view, "componentType": 5126,
             "count": len(vertices), "type": "VEC2"},
            {"bufferView": index_view, "componentType": 5125,
             "count": indices.size, "type": "SCALAR"},
        ],
    }
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    return (struct.pack("<4sII", b"glTF", 2, length) +
            struct.pack("<II", len(encoded), 0x4E4F534A) + encoded +
            struct.pack("<II", len(binary), 0x004E4942) + binary)
