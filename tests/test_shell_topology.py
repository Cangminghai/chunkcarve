"""Check exported shells by topology and enclosed volume, not face-count claims."""

from collections import Counter
import io
import zipfile

import numpy as np
import pytest

from mcmap import export, geom, voxel


def _volume(triangles):
    triangles = np.asarray(triangles, dtype=np.float64)
    return np.einsum("ij,ij->i", triangles[:, 0],
                     np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6


def _assert_closed_oriented_shell(triangles):
    edges = Counter()
    directions = Counter()
    faces = set()
    for triangle in np.asarray(triangles).reshape(-1, 3, 3):
        points = [tuple(np.round(p, 6)) for p in triangle]
        assert np.linalg.norm(np.cross(triangle[1] - triangle[0],
                                       triangle[2] - triangle[0])) > 0
        face = tuple(sorted(points))
        assert face not in faces, "duplicate triangle"
        faces.add(face)
        for a, b in zip(points, points[1:] + points[:1]):
            edge = tuple(sorted((a, b)))
            edges[edge] += 1
            directions[edge] += 1 if a < b else -1
    assert set(edges.values()) == {2}, "open or non-manifold edge"
    assert set(directions.values()) == {0}, "inconsistent face winding"


def _stl_triangles(data):
    count = int.from_bytes(data[80:84], "little")
    records = np.frombuffer(data, offset=84, count=count,
                            dtype=np.dtype([("normal", "<f4", 3),
                                            ("points", "<f4", (3, 3)),
                                            ("attr", "<u2")]))
    return records["points"]


def _component_count(triangles):
    adjacent = {}
    for triangle in triangles:
        points = [tuple(np.round(point, 6)) for point in triangle]
        for point in points:
            adjacent.setdefault(point, set()).update(points)
    count = 0
    while adjacent:
        count += 1
        pending = [next(iter(adjacent))]
        while pending:
            pending.extend(adjacent.pop(pending.pop(), set()))
    return count


@pytest.mark.parametrize("kind,volume,triangles", [
    ("isolated", 1, 12),
    ("adjacent_materials", 2, 20),
    ("solid", 27, 108),
    ("cavity", 26, 120),
])
def test_voxel_shell_preserves_volume_and_cavities(kind, volume, triangles):
    if kind == "isolated":
        xyz = [[0, 0, 0]]
    elif kind == "adjacent_materials":
        xyz = [[0, 0, 0], [1, 0, 0]]
    else:
        xyz = [[x, y, z] for x in range(3) for y in range(3) for z in range(3)
               if kind != "cavity" or (x, y, z) != (1, 1, 1)]
    xyz = np.asarray(xyz, dtype=np.int16)
    colors = np.zeros((len(xyz), 3), dtype=np.uint8)
    colors[::2] = (255, 0, 0)
    colors[1::2] = (0, 0, 255)
    vertices, _ = voxel.build_voxel_mesh(xyz, colors)
    mesh = _stl_triangles(voxel.write_stl_bytes(vertices)[0])
    assert len(mesh) == triangles
    _assert_closed_oriented_shell(mesh)
    assert _volume(mesh) == pytest.approx(volume)
    assert _component_count(mesh) == (2 if kind == "cavity" else 1)


@pytest.mark.parametrize("adjacent,expected_vertices,expected_volume", [
    (False, 8, 1), (True, 12, 2),
])
def test_obj_uses_shared_geometry_indices_and_removes_contact_faces(
        tmp_path, adjacent, expected_vertices, expected_volume):
    quads = geom.full_cube_tris("red", "red", "red")
    if adjacent:
        quads += geom.full_cube_tris("blue", "blue", "blue", origin=(1, 0, 0))
    path = tmp_path / "shell.zip"
    export.export_obj_zip(None, quads, [], 1, str(path), "shell")
    with zipfile.ZipFile(path) as zf:
        lines = zf.read("shell.obj").decode().splitlines()
    vertices = [list(map(float, line.split()[1:]))
                for line in lines if line.startswith("v ")]
    indices = [[int(word.split("/")[0]) - 1 for word in line.split()[1:]]
               for line in lines if line.startswith("f ")]
    assert len(vertices) == expected_vertices
    mesh = np.asarray(vertices)[indices]
    _assert_closed_oriented_shell(mesh)
    assert _volume(mesh) == pytest.approx(expected_volume)
    index_edges = Counter(tuple(sorted((a, b))) for face in indices
                          for a, b in zip(face, face[1:] + face[:1]))
    assert set(index_edges.values()) == {2}, "OBJ indices must be welded"


@pytest.mark.parametrize("stand,depth,volume", [
    (False, 1, 16), (True, 1, 20), (True, 3, 40),
])
def test_slice_stand_is_union_shell_without_overlapping_cells(stand, depth, volume):
    xyz = np.asarray([[0, 10, 0], [2, 10, depth - 1]], dtype=np.int16)
    data, manifest = export.export_batch_slices(
        xyz, box=(0, 0, 2, depth - 1, 10, 10), scale=2, thickness=2,
        stand=stand, model_format="stl")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        mesh = _stl_triangles(zf.read(manifest["slices"][0]["body"]))
    _assert_closed_oriented_shell(mesh)
    assert _volume(mesh) == pytest.approx(volume)
    assert _component_count(mesh) == (1 if stand else 2)


@pytest.mark.parametrize("offset", [(1, 1, 0), (1, 1, 1)])
def test_index_welding_keeps_edge_and_corner_contacts_as_separate_shells(offset):
    xyz = np.asarray([(0, 0, 0), offset], dtype=np.int16)
    soup, _ = voxel.build_voxel_mesh(xyz, np.zeros((2, 3), dtype=np.uint8))
    vertices, triangles = voxel.index_mesh(soup)
    assert len(vertices) == 16
    assert len(np.unique(vertices, axis=0)) < 16
    edges = Counter(tuple(sorted((a, b))) for face in triangles.tolist()
                    for a, b in zip(face, face[1:] + face[:1]))
    assert set(edges.values()) == {2}
    connected = {int(i): set() for i in triangles.flat}
    for face in triangles:
        for i in face:
            connected[int(i)].update(map(int, face))
    count = 0
    while connected:
        count += 1
        pending = [next(iter(connected))]
        while pending:
            pending.extend(connected.pop(pending.pop(), set()))
    assert count == 2
    assert _volume(vertices[triangles]) == pytest.approx(2)


def test_concave_voxel_shell_preserves_missing_corner():
    xyz = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.int16)
    vertices, _ = voxel.build_voxel_mesh(xyz, np.zeros((3, 3), dtype=np.uint8))
    _assert_closed_oriented_shell(vertices)
    assert _volume(vertices.reshape(-1, 3, 3)) == pytest.approx(3)


def test_heightfield_side_edges_match_neighbor_height_steps():
    surface = voxel.Surface(0, 0, np.array([[1, 3], [1, 2]], dtype=np.int16),
                            np.zeros((2, 2, 3), dtype=np.uint8),
                            np.zeros((2, 2), dtype=bool), 0, 3)
    vertices, _ = voxel.build_triangles(surface)
    _assert_closed_oriented_shell(vertices)
    assert _volume(vertices.reshape(-1, 3, 3)) == pytest.approx(11)


@pytest.mark.parametrize("scale", [0.1, 0.2, 1.1])
def test_heightfield_decimal_scale_has_no_degenerate_extra_layer(scale):
    surface = voxel.Surface(0, 0, np.array([[30]], dtype=np.int16),
                            np.zeros((1, 1, 3), dtype=np.uint8),
                            np.zeros((1, 1), dtype=bool), 0, 30)
    vertices, _ = voxel.build_triangles(surface, scale=scale)
    assert len(vertices) // 3 == 4 + 8 * 31
    _assert_closed_oriented_shell(vertices)
    assert _volume(vertices.reshape(-1, 3, 3)) == pytest.approx(31 * scale ** 3)
