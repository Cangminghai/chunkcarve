import io
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from mcmap import render, server


def test_large_map_uses_bounded_sampling_without_cropping(monkeypatch):
    calls = []

    def draw(_region, x0, z0, x1, z1, _path, **options):
        calls.append((x0, z0, x1, z1, options))
        step = options.get('sample_step', 1)
        width, height = (x1 - x0) * 16 // step, (z1 - z0) * 16 // step
        assert width * height <= 1024 * 1024
        image = Image.new('RGB', (width, height), (40, 90, 120))
        image.putpixel((0, 0), (255, 0, 0))
        image.putpixel((width - 1, height - 1), (0, 0, 255))
        return SimpleNamespace(image=image)

    monkeypatch.setattr(server.render, 'render', draw)
    server._tile_cache.clear()
    data = server.World.__new__(server.World).tile(-128, -64, 128, 64, 1024, 'terrain')
    assert calls[0][:4] == (-128, -64, 128, 64)
    assert calls[0][4]['sample_step'] > 1
    image = Image.open(io.BytesIO(data))
    assert image.size == (1024, 512)
    assert image.getpixel((0, 0)) == (255, 0, 0)
    assert image.getpixel((1023, 511)) == (0, 0, 255)


@pytest.mark.parametrize('step', [1, 2, 4, 8, 16])
def test_sampled_map_preserves_world_alignment_and_complete_extent(tmp_path, monkeypatch, step):
    for rx in (-1, 0):
        for rz in (-1, 0):
            (tmp_path / f'r.{rx}.{rz}.mca').write_bytes(b'\0' * 8192)
    monkeypatch.setattr(render, '_get_region', lambda path: None)

    def chunk(_rf, x, z):
        colors = np.zeros((16, 16, 4), dtype=np.float32)
        colors[:, :, 0] = np.arange(16)[None, :] + (x + 1) * 64
        colors[:, :, 1] = np.arange(16)[:, None] + (z + 1) * 64
        colors[:, :, 3] = 255
        return np.full((16, 16), 60), colors, np.zeros((16, 16), bool), np.zeros((16, 16), np.uint16)

    monkeypatch.setattr(render, 'chunk_cached', chunk)
    full = render.render(str(tmp_path), -1, -1, 1, 1, None, shade=False, show_grid=False)
    sampled = render.render(str(tmp_path), -1, -1, 1, 1, None, shade=False, show_grid=False, sample_step=step)
    assert sampled.height.shape == (32 // step, 32 // step)
    np.testing.assert_array_equal(np.asarray(sampled.image), np.asarray(full.image)[::step, ::step])
