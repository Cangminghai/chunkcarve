"""Turn texture-only decorations into closed, textured thin solids."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from . import geom, voxel


def is_sheet_model(geometry) -> bool:
    return geometry is not None and any(
        len(element.faces) < 4 or any(abs(a - b) < 1e-8
                                     for a, b in zip(element.frm, element.to))
        for element in geometry.elements)


def _bridge_pixel_contacts(owners):
    """Join diagonal pixel contacts with one grid cell instead of pinched edges."""
    changed = True
    while changed:
        changed = False
        for first, second in ((0, 1), (0, 2), (1, 2)):
            views = []
            for a, b in ((0, 0), (0, 1), (1, 0), (1, 1)):
                section = [slice(None)] * 3
                section[first] = slice(a, owners.shape[first] - 1 + a)
                section[second] = slice(b, owners.shape[second] - 1 + b)
                views.append(owners[tuple(section)])
            a, b, c, d = views
            diagonal = (a >= 0) & (d >= 0) & (b < 0) & (c < 0)
            reverse = (b >= 0) & (c >= 0) & (a < 0) & (d < 0)
            if diagonal.any() or reverse.any():
                b[diagonal] = a[diagonal]
                a[reverse] = b[reverse]
                changed = True


def printable_quads(geometry, assets, scale: float) -> list[dict]:
    """Rasterize visible faces on the native pixel grid; join intersections in 3D.

    Geometry remains in block units. Zero-thickness faces become 0.2 mm sheets;
    volumetric model faces grow inward so torch shafts keep their outer size.
    Colour comes from the original PNG and UVs, without palette reduction.
    """
    faces = []
    images = {}
    opaque_pixels = {}
    for element in geometry.elements:
        sheet = any(abs(a - b) < 1e-8 for a, b in zip(element.frm, element.to))
        for face in geom.element_to_tris(element, thicken=False):
            name = face['tex']
            if name not in images:
                data = assets.read_texture(name)
                if data is None:
                    raise ValueError(f'无法读取面片装饰贴图：{name}')
                image = Image.open(io.BytesIO(data)).convert('RGBA')
                if image.height > image.width:
                    image = image.crop((0, 0, image.width, image.width))
                images[name] = np.asarray(image)
                opaque_pixels[name] = np.argwhere(images[name][:, :, 3] >= 128)[:, ::-1]
            faces.append({**face, 'sheet': sheet})
    if not faces:
        return []
    pitch = 1 / 16
    thickness = max(0.2 / scale, pitch)
    points = np.asarray([face['verts'] for face in faces])
    grid_min = np.floor((points.min(axis=(0, 1)) - thickness / 2) / pitch) * pitch
    shape = np.ceil((points.max(axis=(0, 1)) + thickness / 2 - grid_min) / pitch).astype(int)
    axes = [grid_min[i] + (np.arange(n) + .5) * pitch for i, n in enumerate(shape)]
    centres = np.stack(np.meshgrid(*axes, indexing='ij'), axis=-1).reshape(-1, 3)
    owner = np.full(len(centres), -1, dtype=np.int32)
    distance = np.full(len(centres), np.inf)
    for index, face in enumerate(faces):
        quad = np.asarray(face['verts'])
        u, v = quad[1] - quad[0], quad[3] - quad[0]
        normal = np.cross(u, v)
        length = np.linalg.norm(normal)
        if length < 1e-10:
            continue
        normal /= length
        relative = centres - quad[0]
        a, b = relative @ u / (u @ u), relative @ v / (v @ v)
        depth = relative @ normal
        lower, upper = (-thickness / 2, thickness / 2) if face['sheet'] else (-thickness, 0)
        inside = ((a >= 0) & (a < 1) & (b >= 0) & (b < 1) &
                  (depth >= lower - 1e-9) & (depth <= upper + 1e-9))
        candidates = np.flatnonzero(inside)
        if not len(candidates):
            continue
        uv = np.asarray(face['uvs'])
        sampled = uv[0] + a[candidates, None] * (uv[1] - uv[0]) + b[candidates, None] * (uv[3] - uv[0])
        image = images[face['tex']]
        xy = np.floor(sampled / 16 * [image.shape[1], image.shape[0]]).astype(int)
        xy = np.clip(xy, [0, 0], [image.shape[1] - 1, image.shape[0] - 1])
        opaque = image[xy[:, 1], xy[:, 0], 3] >= 128
        candidates = candidates[opaque]
        nearer = np.abs(depth[candidates]) < distance[candidates]
        candidates = candidates[nearer]
        owner[candidates] = index
        distance[candidates] = np.abs(depth[candidates])
    owners = owner.reshape(tuple(shape))
    _bridge_pixel_contacts(owners)
    occupied = owners >= 0
    result = []
    for direction, corners in voxel._FACES:
        axis = int(np.argmax(np.abs(direction)))
        sign = direction[axis]
        neighbour = np.roll(occupied, -sign, axis=axis)
        boundary = [slice(None)] * 3
        boundary[axis] = -1 if sign > 0 else 0
        neighbour[tuple(boundary)] = False
        exposed = occupied & ~neighbour
        other = [i for i in range(3) if i != axis]
        for position in range(shape[axis]):
            visible = np.take(exposed, position, axis=axis)
            if not visible.any():
                continue
            labels = np.where(visible, np.take(owners, position, axis=axis), -1)
            for row in range(labels.shape[0]):
                for col in range(labels.shape[1]):
                    label = int(labels[row, col])
                    if label < 0:
                        continue
                    end_col = col + 1
                    end_row = row + 1
                    lo = grid_min.copy()
                    hi = grid_min.copy()
                    lo[axis] = hi[axis] = grid_min[axis] + (position + (sign > 0)) * pitch
                    lo[other] += np.array([row, col]) * pitch
                    hi[other] += np.array([end_row, end_col]) * pitch
                    vertices = np.array([np.where(corner, hi, lo) for corner in corners])
                    face = faces[label]
                    quad = np.asarray(face['verts'])
                    u, v = quad[1] - quad[0], quad[3] - quad[0]
                    centre = (lo + hi) / 2
                    centre[axis] -= sign * pitch / 2
                    a = np.clip((centre - quad[0]) @ u / (u @ u), 0, 1)
                    b = np.clip((centre - quad[0]) @ v / (v @ v), 0, 1)
                    uv = np.asarray(face['uvs'])
                    image = images[face['tex']]
                    size = np.array([image.shape[1], image.shape[0]])
                    sampled = (uv[0] + a * (uv[1] - uv[0]) + b * (uv[3] - uv[0])) / 16 * size
                    pixel = np.clip(np.floor(sampled).astype(int), 0, size - 1)
                    if image[pixel[1], pixel[0], 3] < 128:
                        pixels = opaque_pixels[face['tex']]
                        pixel = pixels[np.argmin(np.sum((pixels + .5 - sampled) ** 2, axis=1))]
                    # Each solid pixel, including its sides, samples an opaque pixel centre.
                    uvs = np.tile((pixel + .5) / size * 16, (4, 1))
                    result.append({'verts': vertices, 'uvs': uvs, 'tex': face['tex']})
    return result


def union_model(body, models, instances, scale):
    """Join block solids while retaining texture IDs and interpolated UVs."""
    import manifold3d as kernel

    names = sorted({face['tex'] for faces in [body, *[m for m in models.values() if m]]
                    for face in faces})
    texture_ids = {name: i for i, name in enumerate(names)}

    def solid(faces, factor=1):
        corners = [(face, i) for face in faces for i in
                   ((0, 1, 2, 0, 2, 3) if len(face['verts']) == 4 else (0, 1, 2))]
        points = np.array([face['verts'][i] for face, i in corners], dtype=np.float64) * factor
        properties = np.array([[*face['uvs'][i], texture_ids[face['tex']]]
                               for face, i in corners], dtype=np.float64)
        vertices, indices = voxel.index_mesh(points)
        mapping = indices.ravel()
        representative = np.empty(len(vertices), dtype=np.uint64)
        representative[mapping] = np.arange(len(mapping), dtype=np.uint64)
        targets = representative[mapping]
        sources = np.flatnonzero(targets != np.arange(len(mapping))).astype(np.uint64)
        mesh = kernel.Mesh64(np.column_stack((points, properties)),
                             np.arange(len(points), dtype=np.uint64).reshape(-1, 3),
                             merge_from_vert=sources, merge_to_vert=targets[sources])
        result = kernel.Manifold(mesh)
        if result.status() != kernel.Error.NoError:
            raise ValueError('方块打印实体未闭合：' + str(result.status()))
        return result

    parts = [solid(body)] if body else []
    native = {state: solid(faces, scale) for state, faces in models.items() if faces}
    parts.extend(native[state].translate(offset) for state, offset in instances if state in native)
    combined = kernel.Manifold.batch_boolean(parts, kernel.OpType.Add)
    if combined.status() != kernel.Error.NoError:
        raise ValueError('方块打印实体合并失败：' + str(combined.status()))
    combined = combined.simplify(1e-7)
    mesh = combined.to_mesh64()
    properties = np.asarray(mesh.vert_properties)
    faces = []
    for triangle in np.asarray(mesh.tri_verts):
        points = properties[triangle]
        texture = int(round(points[0, 5]))
        faces.append({'verts': points[:, :3], 'uvs': points[:, 3:5], 'tex': names[texture]})
    return faces
