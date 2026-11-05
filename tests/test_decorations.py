"""Optional decorations retain alpha outlines and form closed print solids."""

from collections import Counter
import io
import struct
import json
import zipfile

import numpy as np
from PIL import Image
import pytest

from mcmap import decorations, geom, server, vanilla, voxel


def plant():
    return vanilla._Geometry([vanilla._Element(
        (0, 0, 8), (16, 16, 8),
        {face: ('plant', (0, 0, 16, 16)) for face in ('north', 'south')},
        rotation={'origin': (8, 8, 8), 'axis': 'y', 'angle': angle})
        for angle in (-45, 45)])


class Assets:
    def __init__(self, transparent=False):
        rgba = np.zeros((16, 16, 4), dtype=np.uint8)
        rgba[:, :, :3] = (25, 170, 60)
        if not transparent:
            rgba[4:, 7:9, 3] = 255
            rgba[4:8, 4:12, 3] = 255
        buffer = io.BytesIO()
        Image.fromarray(rgba).save(buffer, format='PNG')
        self.png = buffer.getvalue()

    def read_texture(self, name):
        return self.png

    def _read_texture_png(self, name):
        return self.png

    def parse_elements(self, name, properties=None):
        if name == 'minecraft:grass':
            return plant()
        return vanilla._Geometry([vanilla._Element((0, 0, 0), (16, 8, 16),
            {face: ('slab', (0, 0, 16, 16)) for face in geom._FACE_DEF})])


def triangles(quads):
    return np.array([q['verts'][i] for q in quads for i in (0, 1, 2, 0, 2, 3)])


def test_alpha_cutout_is_closed_at_cross_intersection():
    quads = decorations.printable_quads(plant(), Assets(), 2)
    assert quads
    points = triangles(quads)
    vertices, faces = voxel.index_mesh(points)
    edges = Counter(tuple(sorted((a, b))) for face in faces.tolist()
                    for a, b in zip(face, face[1:] + face[:1]))
    assert set(edges.values()) == {2}, 'thin surfaces and intersections must be closed'
    volume = np.einsum('ij,ij->i', points[::3],
                       np.cross(points[1::3], points[2::3])).sum() / 6
    assert volume > 0
    assert points[:, 1].min() >= -.001
    assert points[:, 1].max() == pytest.approx(.75)
    assert np.ptp(points[:, 0]) < .8, 'transparent outside margins must not become solid'


def test_fully_transparent_decoration_exports_no_faces():
    assert decorations.printable_quads(plant(), Assets(transparent=True), 2) == []


def test_solid_ground_with_diagonal_contacts_has_no_open_edges():
    cells = np.array([[0, 0, 1], [0, 2, 0], [0, 2, 2], [1, 0, 0], [1, 0, 2],
                      [1, 2, 0], [1, 2, 1], [1, 2, 2], [2, 0, 2], [2, 1, 0],
                      [2, 1, 1], [2, 1, 2], [2, 2, 1], [2, 2, 2]])
    points, _ = voxel.build_voxel_mesh(cells, np.zeros((len(cells), 3), np.uint8))
    _, faces = voxel.index_mesh(points)
    edges = Counter(tuple(sorted((a, b))) for face in faces.tolist()
                    for a, b in zip(face, face[1:] + face[:1]))
    assert set(edges.values()) == {2}


def test_door_half_is_capped_before_joining_its_neighbours():
    element = vanilla._Element((0, 0, 0), (3, 16, 16),
        {face: ('door', (0, 0, 16, 16)) for face in geom._FACE_DEF if face != 'up'})
    geometry = vanilla._Geometry([element])
    quads = geom.geometry_to_tris(geometry, cap_missing=True)
    result = decorations.union_model([], {0: quads}, [(0, (0, 0, 0)), (0, (0, 2, 0))], 2)
    points = np.array([face['verts'] for face in result])
    volume = np.einsum('ij,ij->i', points[:, 0], np.cross(points[:, 1], points[:, 2])).sum()/6
    assert volume == pytest.approx(3)
    # Retain UV coordinates and texture identity through the geometric union.
    assert {face['tex'] for face in result} == {'door'}
    assert all(np.isfinite(face['uvs']).all() for face in result)


def test_sheet_classification_keeps_solid_slab():
    assets = Assets()
    assert decorations.is_sheet_model(plant())
    assert not decorations.is_sheet_model(assets.parse_elements('minecraft:oak_slab'))


@pytest.mark.parametrize('include', [False, True])
@pytest.mark.parametrize('model_format', ['glb', 'obj'])
def test_export_option_excludes_only_sheet_decorations(monkeypatch, include, model_format):
    xyz = np.array([[0, 10, 0], [2, 10, 0]], dtype=np.int16)
    monkeypatch.setattr(server.render, 'extract_voxels_states', lambda *args:
                        (xyz, np.array([0, 1]), np.array([2, 2]),
                         [('minecraft:oak_slab', {'type': 'bottom'}), ('minecraft:grass', None)]))
    monkeypatch.setattr(server, '_vanilla_assets', Assets())
    data, _, stats = server.World.__new__(server.World).export_obj_voxel(
        0, 0, 1, 1, 2, True, 10, 10, (0, 0, 2, 0),
        model_format=model_format, include_decorations=include)
    assert stats['skipped_decorations'] == (0 if include else 1)
    assert stats['triangles'] > 12 if include else stats['triangles'] == 12
    if model_format == 'glb':
        length = struct.unpack_from('<I', data, 12)[0]
        doc = json.loads(data[20:20+length])
        assert doc['accessors'][0]['max'][0] > .004 if include else doc['accessors'][0]['max'][0] == pytest.approx(.002)
    else:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert any(name.endswith('.obj') for name in archive.namelist())


@pytest.mark.parametrize('include,count', [(False, 1), (True, 2)])
def test_preview_uses_same_decoration_filter(monkeypatch, include, count):
    xyz = np.array([[0, 10, 0], [2, 10, 0]], dtype=np.int16)
    monkeypatch.setattr(server.render, 'extract_voxels_states', lambda *args:
                        (xyz, np.array([0, 1]), np.array([2, 2]),
                         [('minecraft:oak_slab', {'type': 'bottom'}), ('minecraft:grass', None)]))
    monkeypatch.setattr(server, '_vanilla_assets', Assets())
    result = server.World.__new__(server.World).voxels_states(
        0, 0, 1, 1, 10, 10, False, include_decorations=include)
    assert result['count'] == count
