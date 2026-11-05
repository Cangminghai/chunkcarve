"""NBT 解析基础测试（最小合成数据，不依赖真实存档）。"""
import struct
import zlib

from mcmap import nbt


def _empty_compound() -> bytes:
    # tag=10(compound) + 空名称(0x0000) + end(0x00)
    return zlib.compress(b"\x0a\x00\x00\x00")


def test_parse_empty_compound():
    assert nbt.parse(_empty_compound()) == {}


def test_parse_simple_int():
    # compound { "a": int(42) }  → 0a 0000 03 0001 61 0000002a 00
    payload = b"\x0a\x00\x00" + b"\x03\x00\x01a" + struct.pack(">i", 42) + b"\x00"
    tree = nbt.parse(zlib.compress(payload))
    assert tree["a"] == 42


def test_dig_missing_returns_default():
    assert nbt.dig({}, "a", "b", default="x") == "x"


def test_dig_reads_nested():
    assert nbt.dig({"a": {"b": 1}}, "a", "b") == 1
