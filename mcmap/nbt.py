"""纯 Python NBT 解析器（只读），无第三方依赖。

支持 Java 版 1.13+ 的全部标签类型，可直接读取 level.dat（gzip）与
region 文件内的区块 NBT（zlib）。
"""

from __future__ import annotations

import gzip
import io
import struct
import zlib

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


class NBTError(Exception):
    pass


class _Reader:
    __slots__ = ("buf", "pos", "size")

    def __init__(self, buf: bytes):
        self.buf = buf
        self.pos = 0
        self.size = len(buf)

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if end > self.size:
            raise NBTError(f"NBT 数据越界: 需要 {n} 字节，剩余 {self.size - self.pos}")
        out = self.buf[self.pos:end]
        self.pos = end
        return out

    def u1(self) -> int:
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def i1(self) -> int:
        v = struct.unpack_from(">b", self.buf, self.pos)[0]
        self.pos += 1
        return v

    def i2(self) -> int:
        v = struct.unpack_from(">h", self.buf, self.pos)[0]
        self.pos += 2
        return v

    def u2(self) -> int:
        v = struct.unpack_from(">H", self.buf, self.pos)[0]
        self.pos += 2
        return v

    def i4(self) -> int:
        v = struct.unpack_from(">i", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def i8(self) -> int:
        v = struct.unpack_from(">q", self.buf, self.pos)[0]
        self.pos += 8
        return v

    def f4(self) -> float:
        v = struct.unpack_from(">f", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def f8(self) -> float:
        v = struct.unpack_from(">d", self.buf, self.pos)[0]
        self.pos += 8
        return v

    def string(self) -> str:
        n = self.u2()
        return self.take(n).decode("utf-8", errors="replace")


def _read_payload(r: _Reader, tag: int):
    if tag == TAG_BYTE:
        return r.i1()
    if tag == TAG_SHORT:
        return r.i2()
    if tag == TAG_INT:
        return r.i4()
    if tag == TAG_LONG:
        return r.i8()
    if tag == TAG_FLOAT:
        return r.f4()
    if tag == TAG_DOUBLE:
        return r.f8()
    if tag == TAG_BYTE_ARRAY:
        n = r.i4()
        return list(r.take(n))
    if tag == TAG_STRING:
        return r.string()
    if tag == TAG_LIST:
        item = r.u1()
        n = r.i4()
        if item == TAG_END:
            return []
        return [_read_payload(r, item) for _ in range(n)]
    if tag == TAG_COMPOUND:
        out = {}
        while True:
            t = r.u1()
            if t == TAG_END:
                return out
            name = r.string()
            out[name] = _read_payload(r, t)
    if tag == TAG_INT_ARRAY:
        n = r.i4()
        return list(struct.unpack_from(f">{n}i", r.take(n * 4)))
    if tag == TAG_LONG_ARRAY:
        n = r.i4()
        return list(struct.unpack_from(f">{n}q", r.take(n * 8)))
    raise NBTError(f"未知 NBT 标签类型: {tag}")


def decompress(raw: bytes) -> bytes:
    """自动识别 gzip / zlib / 未压缩。"""
    if len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B:
        return gzip.decompress(raw)
    if len(raw) >= 2 and raw[0] == 0x78:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            pass
    return raw


def parse(raw: bytes) -> dict:
    """解析 NBT 顶层复合标签，返回 dict。"""
    data = decompress(raw)
    r = _Reader(data)
    tag = r.u1()
    if tag != TAG_COMPOUND:
        raise NBTError(f"顶层标签应为 Compound(10)，实际为 {tag}")
    r.string()
    return _read_payload(r, TAG_COMPOUND)


def load_file(path) -> dict:
    with open(path, "rb") as f:
        return parse(f.read())


def dig(tree: dict, *path, default=None):
    """安全地按路径取值，例如 dig(nbt, "Data", "SpawnX")。"""
    cur = tree
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def pretty(tree, indent=0, max_depth=6, _prefix=""):
    """把 NBT 树打印成可读文本，用于探查未知字段。"""
    pad = "  " * indent
    lines = []
    if indent > max_depth:
        return [f"{pad}..."]
    if isinstance(tree, dict):
        for k, v in tree.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.extend(pretty(v, indent + 1, max_depth))
            else:
                s = repr(v)
                if len(s) > 120:
                    s = s[:117] + "..."
                lines.append(f"{pad}{k}: {s}")
    elif isinstance(tree, list):
        if tree and not isinstance(tree[0], (dict, list)):
            s = repr(tree)
            if len(s) > 160:
                s = s[:157] + "..."
            lines.append(f"{pad}{s}")
        else:
            for i, v in enumerate(tree[:20]):
                lines.append(f"{pad}[{i}]")
                lines.extend(pretty(v, indent + 1, max_depth))
            if len(tree) > 20:
                lines.append(f"{pad}... 共 {len(tree)} 项")
    else:
        lines.append(f"{pad}{tree!r}")
    return lines


__all__ = ["parse", "load_file", "dig", "pretty", "decompress", "NBTError"]
