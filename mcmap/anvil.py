"""Anvil 区域文件（.mca）读取与区块解码。

面向 1.13+ / 1.18+ 区块结构：
  sections[].Y                 —— section 的 Y 索引（1.18 起可为负）
  sections[].block_states      —— {palette:[{Name,...}], data:[long...]}
  data 为 compacted long array，值不跨 64 位边界

方块在 section 内的线性序号为 (y << 8) | (z << 4) | x。
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

import numpy as np

from . import nbt

SECTOR = 4096
HEADER = SECTOR * 2
REGION_CHUNKS = 32


class RegionError(Exception):
    pass


@dataclass
class Section:
    y: int
    palette: list[str]
    blocks: np.ndarray  # shape (4096,), dtype uint16，值为 palette 下标
    # 每个 palette 条目对应的方块状态属性（如 {'axis':'y'}、{'half':'bottom'}），
    # 与 palette 下标一一对应；无属性的条目为 None。供完整模型渲染匹配 variant 用。
    properties: list[dict | None] = field(default_factory=list)


@dataclass
class Chunk:
    cx: int
    cz: int
    data_version: int
    status: str
    sections: dict[int, Section] = field(default_factory=dict)
    block_entities: list = field(default_factory=list)

    def section_ys(self) -> list[int]:
        return sorted(self.sections)


def _chunk_offset(table: bytes, idx: int) -> tuple[int, int]:
    entry = struct.unpack_from(">I", table, idx * 4)[0]
    return (entry >> 8) * SECTOR, entry & 0xFF


def _decode_long_array(longs: list[int], bits: int, count: int) -> np.ndarray:
    """解码 compacted long array（1.16+ 格式，值不跨 long 边界）。"""
    if bits <= 0:
        return np.zeros(count, dtype=np.uint16)
    per_long = 64 // bits
    mask = (1 << bits) - 1
    if not longs:
        return np.zeros(count, dtype=np.uint16)
    # 负的 int64 不能直接转 uint64，先按位重解释
    arr = np.asarray(longs, dtype=np.int64).view(np.uint64)
    # 展开成 (n_long, per_long) 的位矩阵
    shifts = (np.arange(per_long, dtype=np.uint64) * np.uint64(bits))
    vals = (arr[:, None] >> shifts[None, :]) & np.uint64(mask)
    flat = vals.reshape(-1)[:count]
    return flat.astype(np.uint16)


def _palette_names(palette: list) -> list[str]:
    out = []
    for entry in palette:
        if isinstance(entry, dict):
            out.append(entry.get("Name", "minecraft:air"))
        else:
            out.append("minecraft:air")
    return out


def _palette_properties(palette: list) -> list[dict | None]:
    """提取每个 palette 条目的方块状态属性（Properties）。无属性的返回 None。"""
    out = []
    for entry in palette:
        if isinstance(entry, dict):
            props = entry.get("Properties")
            out.append(props if isinstance(props, dict) else None)
        else:
            out.append(None)
    return out


def parse_chunk_tree(tree: dict) -> Chunk:
    cx = int(tree.get("xPos", 0))
    cz = int(tree.get("zPos", 0))
    ch = Chunk(cx=cx, cz=cz,
               data_version=int(tree.get("DataVersion", 0)),
               status=str(tree.get("Status", "")))
    for sec in tree.get("sections", []) or []:
        sy = int(sec.get("Y", 0))
        bs = sec.get("block_states")
        if not bs:
            # 老格式或空 section
            continue
        palette = _palette_names(bs.get("palette", []))
        properties = _palette_properties(bs.get("palette", []))
        longs = bs.get("data")
        n = len(palette)
        if n <= 1:
            blocks = np.zeros(4096, dtype=np.uint16)
        else:
            bits = max(4, (n - 1).bit_length())
            blocks = _decode_long_array(longs or [], bits, 4096)
        ch.sections[sy] = Section(y=sy, palette=palette, blocks=blocks,
                                  properties=properties)
    be = tree.get("block_entities")
    if isinstance(be, list):
        ch.block_entities = be
    return ch


class RegionFile:
    """单个 .mca 区域文件。"""

    def __init__(self, path: str):
        self.path = path
        self.name = os.path.basename(path)
        parts = self.name.split(".")
        self.rx = int(parts[-3])
        self.rz = int(parts[-2])
        with open(path, "rb") as f:
            self.raw = f.read()
        if len(self.raw) < HEADER:
            raise RegionError(f"{self.name} 过小，不是有效区域文件")

    def __repr__(self):
        return f"<RegionFile {self.name} {len(self.raw)//1024}KB>"

    def present(self) -> list[tuple[int, int]]:
        """返回该区域内存在数据的所有区块世界坐标 (cx, cz)。"""
        out = []
        table = self.raw[:SECTOR]
        for idx in range(REGION_CHUNKS * REGION_CHUNKS):
            entry = struct.unpack_from(">I", table, idx * 4)[0]
            if entry == 0:
                continue
            lx, lz = idx % REGION_CHUNKS, idx // REGION_CHUNKS
            out.append((self.rx * REGION_CHUNKS + lx,
                        self.rz * REGION_CHUNKS + lz))
        return out

    def read_chunk(self, cx: int, cz: int) -> Chunk | None:
        lx = cx - self.rx * REGION_CHUNKS
        lz = cz - self.rz * REGION_CHUNKS
        if not (0 <= lx < REGION_CHUNKS and 0 <= lz < REGION_CHUNKS):
            return None
        idx = lz * REGION_CHUNKS + lx
        table = self.raw[:SECTOR]
        entry = struct.unpack_from(">I", table, idx * 4)[0]
        if entry == 0:
            return None
        offset, count = _chunk_offset(table, idx)
        if offset == 0 or count == 0:
            return None
        start = offset
        if start + 5 > len(self.raw):
            return None
        length = struct.unpack_from(">I", self.raw, start)[0]
        comp = self.raw[start + 4]
        payload = self.raw[start + 5:start + 4 + length]
        if not payload:
            return None
        if comp == 1:
            import gzip
            raw = gzip.decompress(payload)
        elif comp == 2:
            import zlib
            raw = zlib.decompress(payload)
        elif comp == 3:
            raw = payload
        else:
            return None
        tree = nbt.parse(raw)
        return parse_chunk_tree(tree)


def scan_regions(region_dir: str) -> list[str]:
    if not os.path.isdir(region_dir):
        return []
    out = []
    for name in os.listdir(region_dir):
        if not name.endswith(".mca"):
            continue
        p = os.path.join(region_dir, name)
        if os.path.getsize(p) >= HEADER:
            out.append(p)
    return sorted(out)


__all__ = ["RegionFile", "Chunk", "Section", "scan_regions", "RegionError"]
