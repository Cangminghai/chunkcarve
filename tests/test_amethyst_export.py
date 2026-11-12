"""Crystal sprites must export as cutout solids with opaque edge colours."""

import io
import json
import struct

import numpy as np
from PIL import Image
import pytest

from mcmap import decorations, export, palette, render, vanilla


@pytest.mark.parametrize('name', [
    'amethyst_cluster', 'large_amethyst_bud', 'medium_amethyst_bud', 'small_amethyst_bud',
])
def test_crystal_sprites_are_non_solid_decorations(name):
    name = 'minecraft:' + name
    assert palette.is_decorative(name)
    assert palette.is_noncube(name)
    assert render._classify([name]).tolist() == [render.C_SKIP]


@pytest.mark.parametrize('name', ['amethyst_block', 'budding_amethyst'])
def test_amethyst_base_blocks_remain_full_cubes(name):
    name = 'minecraft:' + name
    assert not palette.is_decorative(name)
    assert not palette.is_noncube(name)
    assert render._classify([name]).tolist() == [render.C_SOLID]


@pytest.fixture
def crystal():
    rgba = np.zeros((16, 16, 4), dtype=np.uint8)
    rgba[8:16, 6:10] = (160, 90, 210, 255)
    rgba[7, 5] = (210, 150, 245, 255)  # diagonal contact needs a bridge
    data = io.BytesIO()
    Image.fromarray(rgba).save(data, format='PNG')

    class Assets:
        def read_texture(self, name):
            return data.getvalue()

        def _read_texture_png(self, name):
            return data.getvalue()

    model = vanilla._Geometry([vanilla._Element(
        (0, 0, 8), (16, 16, 8),
        {side: ('crystal', (0, 0, 16, 16)) for side in ('north', 'south')},
        rotation={'origin': (8, 8, 8), 'axis': 'y', 'angle': 45})])

    return model, Assets(), rgba


def test_cutout_edges_and_bridged_pixels_use_opaque_texture_pixels(crystal):
    model, assets, rgba = crystal
    faces = decorations.printable_quads(model, assets, 2)

    assert faces
    for face in faces:
        # Check vertices and triangle interiors, as slicers may sample either.
        uv = np.asarray(face['uvs'])
        samples = np.concatenate((uv, [uv[:3].mean(0), uv[[0, 2, 3]].mean(0)]))
        xy = np.clip(np.floor(samples).astype(int), 0, 15)
        assert np.all(rgba[xy[:, 1], xy[:, 0], 3] == 255), 'edge maps to transparent black'


@pytest.mark.parametrize('subdivide', [1, 8, 16])
@pytest.mark.parametrize('scale', [1, 2, 4])
def test_pixel_solid_survives_all_glb_subdivision_options(crystal, subdivide, scale):
    model, assets, _ = crystal
    faces = decorations.printable_quads(model, assets, scale)
    for face in faces:
        face['verts'] = (np.asarray(face['verts']) * scale).tolist()

    data, stats = export.export_glb(assets, faces, subdivide=subdivide)

    length = struct.unpack_from('<I', data, 12)[0]
    document = json.loads(data[20:20+length])
    assert document['accessors'][3]['count'] // 3 == len(faces) * 2
    assert stats['triangles'] == len(faces) * 2
