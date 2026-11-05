from types import SimpleNamespace

import numpy as np
import pytest

from mcmap import height_range, server
from mcmap.anvil import Section


def chunk(surface, name='minecraft:grass_block'):
    sections = {}
    if surface is not None:
        blocks = np.zeros((16, 16, 16), dtype=np.uint16)
        blocks[surface % 16, 8, 8] = 1
        sections[surface // 16] = Section(surface // 16, ['minecraft:air', name], blocks.ravel())
    return SimpleNamespace(sections=sections)


@pytest.mark.parametrize('surface,expected', [(64, (14, 113)), (220, (170, 269)),
                                            (-60, (-64, 35)), (310, (220, 319))])
def test_terrain_window_is_one_hundred_blocks_and_clamped(tmp_path, monkeypatch, surface, expected):
    (tmp_path / 'r.0.0.mca').write_bytes(b'\0' * 8192)
    monkeypatch.setattr(height_range.render, '_get_region', lambda path:
                        SimpleNamespace(read_chunk=lambda x, z: chunk(surface)))
    result = height_range.suggest(str(tmp_path), 0, 0, 1, 1, 64)
    assert (result['y0'], result['y1']) == expected
    assert result['y1'] - result['y0'] + 1 == result['height_blocks'] == 100
    assert result['surface_y'] == surface
    assert result['source'] == 'chunk-centres'


def test_each_loaded_chunk_centre_has_equal_weight_and_missing_chunks_are_ignored(tmp_path, monkeypatch):
    for rx in (-1, 0):
        (tmp_path / f'r.{rx}.0.mca').write_bytes(b'\0' * 8192)
    chunks = {(-1, 0): chunk(40), (0, 0): chunk(80), (1, 0): chunk(None)}
    monkeypatch.setattr(height_range.render, '_get_region', lambda path:
                        SimpleNamespace(read_chunk=lambda x, z: chunks.get((x, z))))
    result = height_range.suggest(str(tmp_path), -1, 0, 3, 1, 250)
    assert result['surface_y'] == 60
    assert len(result['samples']) == 2
    assert (result['y0'], result['y1']) == (10, 109)


def test_flat_homogeneous_sections_and_leaves_and_water_are_sampled_correctly():
    ground = chunk(-61)
    ground.sections[4] = Section(4, ['minecraft:oak_leaves'], np.zeros(4096, dtype=np.uint16))
    assert height_range.centre_surface(ground, -64, 319) == -61
    flat = SimpleNamespace(sections={4: Section(4, ['minecraft:stone'], np.zeros(4096, dtype=np.uint16))})
    assert height_range.centre_surface(flat, -64, 319) == 79
    assert height_range.centre_surface(chunk(63, 'minecraft:water'), -64, 319) == 63


def test_no_surface_uses_current_world_spawn_instead_of_fixed_height(tmp_path):
    result = height_range.suggest(str(tmp_path), 0, 0, 1, 1, 205)
    assert (result['y0'], result['y1']) == (155, 254)
    assert result['source'] == 'spawn-fallback'


@pytest.mark.parametrize('bounds', [(0, 0, 0, 1), (0, 0, 9, 9), (1, 0, 0, 1)])
def test_invalid_or_oversized_range_is_rejected(tmp_path, bounds):
    with pytest.raises(ValueError, match='64'):
        height_range.suggest(str(tmp_path), *bounds, 64)


def test_world_height_limits_follow_version_or_inline_dimension_type():
    world = server.World.__new__(server.World)
    world.level = {}
    world.data_version = 2730
    assert world.height_limits() == (0, 255)
    world.data_version = 3465
    assert world.height_limits() == (-64, 319)
    world.level = {'Data': {'WorldGenSettings': {'dimensions': {'minecraft:overworld': {
        'type': {'min_y': -128, 'height': 512}}}}}}
    assert world.height_limits() == (-128, 383)


def test_homogeneous_ground_is_visible_in_map_and_preview_and_export(tmp_path, monkeypatch):
    from mcmap import render
    section = Section(-4, ['minecraft:stone'], np.zeros(4096, dtype=np.uint16))
    solid = SimpleNamespace(sections={-4: section})
    assert np.all(render.chunk_heightmap(solid)[0] == -49)
    (tmp_path / 'r.0.0.mca').write_bytes(b'\0' * 8192)
    monkeypatch.setattr(render, '_get_region', lambda path: SimpleNamespace(read_chunk=lambda x, z: solid))
    xyz, ids, flags = render.extract_voxels(str(tmp_path), 0, 0, 1, 1, -49, -49)
    assert len(xyz) == len(ids) == len(flags) == 256
    assert np.all(xyz[:, 1] == -49)
    xyz, states, flags, table = render.extract_voxels_states(str(tmp_path), 0, 0, 1, 1, -49, -49)
    assert len(xyz) == len(states) == len(flags) == 256
    assert table[0][0] == 'minecraft:stone'
