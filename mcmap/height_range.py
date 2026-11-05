"""Choose a preview height window from chunk-centre surface samples."""

from __future__ import annotations

import math
import os

import numpy as np

from . import render


def centre_surface(chunk, min_y: int, max_y: int) -> int | None:
    """Find the first solid/water surface at the centre, excluding tree leaves."""
    for sy in sorted(chunk.sections, reverse=True):
        section = chunk.sections[sy]
        classes = render._classify(section.palette)
        usable = np.isin(classes, (render.C_SOLID, render.C_WATER, render.C_DETAIL))
        usable &= np.asarray([not name.endswith('_leaves') for name in section.palette])
        column = section.blocks.reshape(16, 16, 16)[:, 8, 8]
        heights = np.arange(16) + sy * 16
        found = np.flatnonzero(usable[column] & (heights >= min_y) & (heights <= max_y))
        if len(found):
            return int(heights[found[-1]])
    return None


def suggest(region_dir: str, cx0: int, cz0: int, cx1: int, cz1: int,
            fallback_y: int, min_y: int = -64, max_y: int = 319) -> dict:
    count = (cx1 - cx0) * (cz1 - cz0)
    if cx1 <= cx0 or cz1 <= cz0 or count > 64:
        raise ValueError('自动高度探测范围须在 1～64 区块内')
    samples = []
    for cx in range(cx0, cx1):
        for cz in range(cz0, cz1):
            path = os.path.join(region_dir, f'r.{cx // 32}.{cz // 32}.mca')
            if not os.path.isfile(path) or os.path.getsize(path) < 8192:
                continue
            chunk = render._get_region(path).read_chunk(cx, cz)
            height = centre_surface(chunk, min_y, max_y) if chunk else None
            if height is not None:
                samples.append({'cx': cx, 'cz': cz, 'height': height})
    surface = sum(s['height'] for s in samples) / len(samples) if samples else fallback_y
    centre = max(min_y, min(max_y, math.floor(surface + .5)))
    span = min(100, max_y - min_y + 1)
    low = max(min_y, min(max_y - span + 1, centre - 50))
    return {'y0': low, 'y1': low + span - 1, 'surface_y': surface,
            'height_blocks': span, 'samples': samples,
            'source': 'chunk-centres' if samples else 'spawn-fallback',
            'min_y': min_y, 'max_y': max_y}
