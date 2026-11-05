"""存档内容分析：定位人工建筑、统计方块分布。

人工建筑判定思路：
  自然生成的地形由 stone/dirt/grass/sand/water/*_ore 等构成；
  玩家建造会大量使用 *_planks / *_bricks / *_concrete / 玻璃 / 金属块 等。
  按区块统计这些「建材类」方块数量，即可定位建筑热点。
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from .anvil import RegionFile, scan_regions

NATURAL_NAMES = {
    "minecraft:stone", "minecraft:dirt", "minecraft:grass_block",
    "minecraft:grass", "minecraft:short_grass", "minecraft:tall_grass",
    "minecraft:fern", "minecraft:large_fern", "minecraft:sand",
    "minecraft:red_sand", "minecraft:gravel", "minecraft:clay",
    "minecraft:water", "minecraft:lava", "minecraft:bedrock",
    "minecraft:andesite", "minecraft:diorite", "minecraft:granite",
    "minecraft:deepslate", "minecraft:tuff", "minecraft:calcite",
    "minecraft:snow", "minecraft:snow_block", "minecraft:ice",
    "minecraft:packed_ice", "minecraft:blue_ice", "minecraft:obsidian",
    "minecraft:netherrack", "minecraft:soul_sand", "minecraft:end_stone",
    "minecraft:sandstone", "minecraft:red_sandstone",
    "minecraft:dripleaf", "minecraft:big_dripleaf",
    "minecraft:lily_pad", "minecraft:seagrass", "minecraft:kelp",
    "minecraft:dead_bush", "minecraft:cactus", "minecraft:bamboo",
    "minecraft:sugar_cane", "minecraft:moss_block", "minecraft:moss_carpet",
    "minecraft:podzol", "minecraft:coarse_dirt", "minecraft:rooted_dirt",
    "minecraft:mycelium", "minecraft:mud", "minecraft:pointed_dripstone",
    "minecraft:dripstone_block", "minecraft:amethyst_block",
}
NATURAL_SUFFIX = re.compile(
    r"(_leaves|_log|_wood|_ore|_sapling|_mushroom|_flower|_tulip|_bush|"
    r"_grass|_fern|_vine|_roots|_sprouts|_coral|_sea_pickle|_kelp)$"
)
BUILT_HINT = re.compile(
    r"(_planks|_bricks|_concrete|_wool|_terracotta|_glass|_pane|_stairs|"
    r"_slab|_wall|_fence|_door|_trapdoor|_sign|_carpet|_tiles|"
    r"stone_bricks|mossy_stone|cobblestone|quartz|_block$)"
)
BUILT_STRONG = re.compile(
    r"(_planks|_bricks|_concrete|_wool|_terracotta|stone_bricks|"
    r"concrete|quartz_block|_glass$|_pane$)"
)


def is_natural(name: str) -> bool:
    if name in NATURAL_NAMES:
        return True
    short = name.split(":")[-1]
    return bool(NATURAL_SUFFIX.search(short))


def is_built(name: str) -> bool:
    if is_natural(name) or name in ("minecraft:air", "minecraft:cave_air"):
        return False
    return bool(BUILT_HINT.search(name.split(":")[-1]))


def is_built_strong(name: str) -> bool:
    if is_natural(name) or name in ("minecraft:air", "minecraft:cave_air"):
        return False
    return bool(BUILT_STRONG.search(name.split(":")[-1]))


@dataclass
class ChunkStat:
    cx: int
    cz: int
    built: int = 0
    built_strong: int = 0
    kinds: int = 0
    y_min: int = 999
    y_max: int = -999
    mod_blocks: int = 0
    top_blocks: list = field(default_factory=list)


def analyze_chunk(ch) -> ChunkStat:
    st = ChunkStat(cx=ch.cx, cz=ch.cz)
    counter: Counter[str] = Counter()
    built_names: Counter[str] = Counter()
    for sy, sec in ch.sections.items():
        names = sec.palette
        cls = np.zeros(len(names), dtype=np.int8)
        for i, n in enumerate(names):
            if n in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air"):
                continue
            if is_built(n):
                cls[i] = 1
                if is_built_strong(n):
                    cls[i] = 2
            if ":" in n and not n.startswith("minecraft:"):
                st.mod_blocks += 1
        if not cls.any():
            continue
        grid = sec.blocks
        for i, n in enumerate(names):
            c = int((grid == i).sum())
            if c:
                counter[n] += c
                if cls[i]:
                    built_names[n] += c
        m1 = (grid > 0)
        st.built += int((cls[grid] == 1).sum())
        st.built_strong += int((cls[grid] == 2).sum())
    st.kinds = len(counter)
    if built_names:
        st.top_blocks = built_names.most_common(6)
    return st


def scan_built(region_dir: str, limit_chunks: int | None = None) -> list[ChunkStat]:
    out: list[ChunkStat] = []
    for path in scan_regions(region_dir):
        rf = RegionFile(path)
        for (cx, cz) in rf.present():
            ch = rf.read_chunk(cx, cz)
            if ch is None or not ch.sections:
                continue
            out.append(analyze_chunk(ch))
            if limit_chunks and len(out) >= limit_chunks:
                return out
        del rf
    return out


def block_histogram(region_dir: str, max_regions: int | None = None) -> Counter:
    hist: Counter[str] = Counter()
    paths = scan_regions(region_dir)
    if max_regions:
        paths = paths[:max_regions]
    for path in paths:
        rf = RegionFile(path)
        for (cx, cz) in rf.present():
            ch = rf.read_chunk(cx, cz)
            if ch is None:
                continue
            for sec in ch.sections.values():
                vals, counts = np.unique(sec.blocks, return_counts=True)
                for v, c in zip(vals, counts):
                    if v == 0:
                        continue
                    hist[sec.palette[v]] += int(c)
        del rf
    return hist


__all__ = ["ChunkStat", "scan_built", "analyze_chunk", "block_histogram",
           "is_built", "is_built_strong", "is_natural"]
