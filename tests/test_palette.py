"""palette 分类回归测试（渲染正确性依赖这些分类）。"""
from mcmap import palette


def test_air_set():
    for n in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air"):
        assert n in palette.AIR


def test_plants_are_decorative():
    for n in ("minecraft:grass", "minecraft:short_grass", "minecraft:tall_grass",
              "minecraft:fern", "minecraft:poppy", "minecraft:dandelion",
              "minecraft:oak_sapling", "minecraft:torch", "minecraft:red_mushroom"):
        assert palette.is_decorative(n), n


def test_full_blocks_are_not_decorative():
    # 草方块/泥土是全方块，绝不能被当装饰跳过（历史 bug：草把下面草方块顶面剔掉）
    for n in ("minecraft:grass_block", "minecraft:dirt", "minecraft:stone"):
        assert not palette.is_decorative(n), n


def test_detail_blocks():
    for n in ("minecraft:oak_slab", "minecraft:oak_stairs", "minecraft:oak_fence",
              "minecraft:rail", "minecraft:oak_trapdoor"):
        assert palette.is_detail(n), n

def test_lever_is_decorative_not_detail():
    # 拉杆归入装饰（不遮挡邻居），不要在 detail 与 decorative 之间混淆
    assert palette.is_decorative("minecraft:lever")
    assert not palette.is_detail("minecraft:lever")


def test_full_blocks_are_not_detail():
    for n in ("minecraft:stone", "minecraft:dirt", "minecraft:grass_block",
              "minecraft:sea_lantern", "minecraft:jack_o_lantern"):
        assert not palette.is_detail(n), n


def test_transparent_blocks():
    for n in ("minecraft:water", "minecraft:glass", "minecraft:air"):
        assert palette.is_transparent(n), n
    assert not palette.is_transparent("minecraft:stone")


def test_resolve_returns_rgba():
    rgba = palette.resolve("minecraft:stone")
    assert len(rgba) == 4
    assert all(0 <= c <= 255 for c in rgba)


def test_grass_on_grass_block_not_culled():
    """回归测试：草方块上方的植物（草/花/树苗等）绝不能遮挡草方块导致顶面/方块被误剔除产生穿透破面。"""
    import numpy as np
    from mcmap import render

    coords = []
    # 3x3 泥土位于 y=62
    for x in range(3):
        for z in range(3):
            coords.append([x, 62, z])
    # 3x3 草方块位于 y=63
    for x in range(3):
        for z in range(3):
            coords.append([x, 63, z])
    # 中心位置 y=64 长草
    coords.append([1, 64, 1])

    xyz = np.array(coords, dtype=np.int16)
    # flags: 泥土与草方块实心(0)，草(2)
    fl = np.zeros(len(coords), dtype=np.uint8)
    fl[-1] = 2  # 植物非实心
    ids = np.zeros(len(coords), dtype=np.uint16)

    xyz_exp, _, _ = render.filter_exposed(xyz, ids, fl)
    # 验证中心草方块 (1, 63, 1) 必须被正确保留，绝不能被剔除
    center_gb = np.array([1, 63, 1])
    assert any(np.array_equal(row, center_gb) for row in xyz_exp)
