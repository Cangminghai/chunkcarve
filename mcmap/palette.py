"""方块名 → 颜色映射。

策略分三级：
  1. 精确匹配（EXACT）
  2. 规则匹配（按 *_leaves / *_planks / *_wool 等后缀推断）
  3. 兜底（灰度，保证模组方块不会变成纯黑）
"""

from __future__ import annotations

import re

RGB = tuple[int, int, int]

# alpha < 255 表示需要与下层混合
EXACT: dict[str, tuple[int, int, int, int]] = {
    # --- 地形基础 ---
    "minecraft:grass_block": (145, 189, 89, 255),
    "minecraft:dirt": (134, 96, 67, 255),
    "minecraft:coarse_dirt": (119, 85, 59, 255),
    "minecraft:rooted_dirt": (144, 103, 76, 255),
    "minecraft:podzol": (91, 61, 30, 255),
    "minecraft:mycelium": (111, 98, 101, 255),
    "minecraft:farmland": (110, 75, 43, 255),
    "minecraft:dirt_path": (148, 121, 65, 255),
    "minecraft:mud": (60, 57, 61, 255),
    "minecraft:clay": (160, 167, 180, 255),
    "minecraft:gravel": (131, 110, 110, 255),
    "minecraft:sand": (219, 211, 160, 255),
    "minecraft:red_sand": (190, 100, 40, 255),
    "minecraft:sandstone": (216, 205, 160, 255),
    "minecraft:red_sandstone": (184, 90, 31, 255),
    "minecraft:smooth_sandstone": (224, 214, 170, 255),
    "minecraft:stone": (127, 127, 127, 255),
    "minecraft:cobblestone": (122, 122, 122, 255),
    "minecraft:mossy_cobblestone": (110, 122, 95, 255),
    "minecraft:stone_bricks": (122, 122, 122, 255),
    "minecraft:mossy_stone_bricks": (105, 118, 88, 255),
    "minecraft:andesite": (138, 138, 135, 255),
    "minecraft:diorite": (196, 196, 192, 255),
    "minecraft:granite": (160, 113, 90, 255),
    "minecraft:deepslate": (79, 79, 82, 255),
    "minecraft:cobbled_deepslate": (77, 77, 80, 255),
    "minecraft:tuff": (109, 106, 95, 255),
    "minecraft:calcite": (224, 224, 220, 255),
    "minecraft:basalt": (79, 75, 81, 255),
    "minecraft:blackstone": (45, 35, 39, 255),
    "minecraft:bedrock": (86, 86, 86, 255),
    "minecraft:obsidian": (21, 18, 30, 255),
    "minecraft:netherrack": (111, 54, 54, 255),
    "minecraft:soul_sand": (81, 64, 58, 255),
    "minecraft:end_stone": (220, 226, 166, 255),
    "minecraft:snow": (249, 254, 254, 255),
    "minecraft:snow_block": (240, 240, 240, 255),
    "minecraft:powder_snow": (248, 253, 253, 255),
    "minecraft:ice": (125, 173, 235, 255),
    "minecraft:packed_ice": (141, 180, 250, 255),
    "minecraft:blue_ice": (116, 167, 253, 255),
    "minecraft:water": (63, 118, 228, 200),
    "minecraft:lava": (207, 92, 19, 255),
    # --- 植被 ---
    "minecraft:oak_leaves": (60, 110, 45, 255),
    "minecraft:spruce_leaves": (58, 95, 44, 255),
    "minecraft:birch_leaves": (107, 163, 74, 255),
    "minecraft:jungle_leaves": (61, 143, 40, 255),
    "minecraft:acacia_leaves": (104, 140, 46, 255),
    "minecraft:dark_oak_leaves": (52, 96, 30, 255),
    "minecraft:azalea_leaves": (92, 130, 56, 255),
    "minecraft:mangrove_leaves": (74, 128, 47, 255),
    "minecraft:cherry_leaves": (226, 152, 180, 255),
    "minecraft:grass": (107, 143, 58, 255),
    "minecraft:short_grass": (107, 143, 58, 255),
    "minecraft:tall_grass": (107, 143, 58, 255),
    "minecraft:fern": (94, 128, 52, 255),
    "minecraft:large_fern": (94, 128, 52, 255),
    "minecraft:dead_bush": (107, 78, 40, 255),
    "minecraft:sugar_cane": (143, 186, 99, 255),
    "minecraft:cactus": (87, 118, 60, 255),
    "minecraft:bamboo": (108, 156, 51, 255),
    "minecraft:vine": (72, 110, 42, 255),
    "minecraft:lily_pad": (56, 110, 38, 255),
    "minecraft:moss_block": (89, 109, 45, 255),
    "minecraft:moss_carpet": (89, 109, 45, 255),
    "minecraft:pumpkin": (192, 118, 21, 255),
    "minecraft:carved_pumpkin": (196, 122, 24, 255),
    "minecraft:melon": (125, 155, 47, 255),
    "minecraft:hay_block": (179, 148, 38, 255),
    "minecraft:wheat": (168, 155, 60, 255),
    "minecraft:poppy": (200, 60, 50, 255),
    "minecraft:dandelion": (230, 200, 60, 255),
    "minecraft:torchflower": (222, 160, 60, 255),
    "minecraft:blue_orchid": (70, 150, 210, 255),
    # --- 木材 ---
    "minecraft:oak_log": (107, 83, 51, 255),
    "minecraft:spruce_log": (58, 40, 23, 255),
    "minecraft:birch_log": (216, 215, 210, 255),
    "minecraft:jungle_log": (86, 67, 27, 255),
    "minecraft:acacia_log": (103, 96, 86, 255),
    "minecraft:dark_oak_log": (60, 46, 26, 255),
    "minecraft:mangrove_log": (89, 53, 40, 255),
    "minecraft:cherry_log": (54, 33, 44, 255),
    "minecraft:oak_planks": (184, 148, 95, 255),
    "minecraft:spruce_planks": (115, 85, 49, 255),
    "minecraft:birch_planks": (198, 185, 134, 255),
    "minecraft:jungle_planks": (184, 135, 100, 255),
    "minecraft:acacia_planks": (186, 99, 55, 255),
    "minecraft:dark_oak_planks": (74, 49, 24, 255),
    "minecraft:mangrove_planks": (117, 54, 48, 255),
    "minecraft:cherry_planks": (226, 178, 172, 255),
    "minecraft:bookshelf": (154, 127, 82, 255),
    "minecraft:crafting_table": (139, 98, 57, 255),
    "minecraft:chest": (160, 117, 53, 255),
    "minecraft:barrel": (124, 92, 52, 255),
    "minecraft:furnace": (110, 110, 110, 255),
    "minecraft:glass": (208, 232, 232, 90),
    "minecraft:tinted_glass": (60, 60, 70, 160),
    # --- 建筑与装饰 ---
    "minecraft:bricks": (150, 87, 74, 255),
    "minecraft:nether_bricks": (44, 23, 26, 255),
    "minecraft:quartz_block": (236, 233, 226, 255),
    "minecraft:white_wool": (233, 236, 236, 255),
    "minecraft:iron_block": (216, 216, 216, 255),
    "minecraft:gold_block": (250, 236, 79, 255),
    "minecraft:diamond_block": (98, 237, 228, 255),
    "minecraft:emerald_block": (46, 219, 107, 255),
    "minecraft:coal_block": (16, 16, 16, 255),
    "minecraft:redstone_block": (165, 18, 18, 255),
    "minecraft:lapis_block": (29, 78, 158, 255),
    "minecraft:copper_block": (192, 111, 79, 255),
    "minecraft:exposed_copper": (161, 125, 104, 255),
    "minecraft:weathered_copper": (108, 153, 110, 255),
    "minecraft:oxidized_copper": (82, 162, 132, 255),
    "minecraft:iron_bars": (190, 190, 190, 255),
    "minecraft:ladder": (140, 105, 60, 255),
    "minecraft:oak_fence": (160, 128, 78, 255),
    "minecraft:oak_door": (172, 137, 82, 255),
    "minecraft:oak_stairs": (184, 148, 95, 255),
    "minecraft:oak_slab": (184, 148, 95, 255),
    "minecraft:cobblestone_wall": (122, 122, 122, 255),
    "minecraft:lantern": (240, 200, 110, 255),
    "minecraft:sea_lantern": (210, 230, 220, 255),
    "minecraft:glowstone": (240, 210, 140, 255),
    "minecraft:shroomlight": (240, 160, 80, 255),
    # --- 矿石 ---
    "minecraft:coal_ore": (107, 107, 107, 255),
    "minecraft:iron_ore": (143, 139, 132, 255),
    "minecraft:copper_ore": (156, 125, 92, 255),
    "minecraft:gold_ore": (156, 144, 104, 255),
    "minecraft:redstone_ore": (139, 107, 107, 255),
    "minecraft:lapis_ore": (90, 107, 139, 255),
    "minecraft:diamond_ore": (123, 155, 155, 255),
    "minecraft:emerald_ore": (107, 155, 107, 255),
    "minecraft:deepslate_coal_ore": (74, 74, 76, 255),
    "minecraft:deepslate_iron_ore": (94, 92, 90, 255),
    "minecraft:deepslate_gold_ore": (100, 94, 72, 255),
    "minecraft:deepslate_diamond_ore": (78, 100, 100, 255),
    "minecraft:deepslate_redstone_ore": (92, 72, 72, 255),
    "minecraft:deepslate_lapis_ore": (62, 72, 92, 255),
    "minecraft:deepslate_emerald_ore": (72, 100, 72, 255),
}

WOOL_COLORS = {
    "white": (233, 236, 236), "orange": (240, 118, 19), "magenta": (189, 68, 179),
    "light_blue": (58, 175, 217), "yellow": (248, 198, 39), "lime": (112, 185, 25),
    "pink": (238, 141, 172), "gray": (62, 68, 71), "light_gray": (142, 142, 134),
    "cyan": (21, 137, 145), "purple": (121, 42, 172), "blue": (53, 57, 157),
    "brown": (114, 71, 40), "green": (84, 109, 27), "red": (161, 39, 34),
    "black": (20, 21, 25),
}

# 规则兜底：(正则, 颜色, 说明)
RULES: list[tuple[str, tuple[int, int, int]]] = [
    (r"_leaves$", (74, 124, 52)),
    (r"_planks$", (170, 136, 88)),
    (r"_(log|wood|stem|hyphae)$", (100, 78, 48)),
    (r"_sapling$", (90, 140, 60)),
    (r"_ore$", (120, 118, 112)),
    (r"_block$", (150, 150, 145)),
    (r"_(bricks?|tiles?)$", (150, 90, 78)),
    (r"_(stairs|slab|wall|fence|gate|door|trapdoor)$", (150, 124, 82)),
    (r"_(concrete|terracotta)$", (150, 120, 100)),
    (r"_(glass|pane)$", (200, 225, 225)),
    (r"_(flower|coral|tulip|orchid|rose|blossom)$", (200, 100, 120)),
    (r"_(sign|banner|carpet|button|lever|rail)$", (150, 130, 100)),
]

AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
# 渲染时直接穿透（不视为地表）的方块
TRANSPARENT = {
    "minecraft:air", "minecraft:cave_air", "minecraft:void_air",
    "minecraft:glass", "minecraft:glass_pane", "minecraft:ice",
    "minecraft:water", "minecraft:lava",
}
# 不参与遮挡的纯装饰
DECORATION = {
    "minecraft:short_grass", "minecraft:grass", "minecraft:tall_grass",
    "minecraft:fern", "minecraft:large_fern", "minecraft:dead_bush",
    "minecraft:dandelion", "minecraft:poppy", "minecraft:torch",
    "minecraft:wall_torch", "minecraft:sugar_cane", "minecraft:vine",
    "minecraft:snow",
}

_cache: dict[str, tuple[int, int, int, int]] = {}


def resolve(name: str) -> tuple[int, int, int, int]:
    """返回 (r, g, b, a)。未知方块回落到灰阶，不会崩。"""
    hit = _cache.get(name)
    if hit is not None:
        return hit
    col = _resolve_uncached(name)
    _cache[name] = col
    return col


def _resolve_uncached(name: str) -> tuple[int, int, int, int]:
    if name in AIR:
        return (0, 0, 0, 0)
    if name in EXACT:
        return EXACT[name]

    short = name.split(":")[-1]
    if ":" in name and not name.startswith("minecraft:"):
        base = "minecraft:" + short
        if base in EXACT:
            c = EXACT[base]
            return (int(c[0] * 0.85), int(c[1] * 0.85), int(c[2] * 0.85), c[3])

    for color_name, rgb in WOOL_COLORS.items():
        if short.startswith(color_name + "_"):
            rest = short[len(color_name) + 1:]
            if rest in ("wool", "concrete", "concrete_powder", "terracotta",
                        "stained_glass", "stained_glass_pane", "carpet",
                        "shulker_box", "bed", "banner"):
                return (rgb[0], rgb[1], rgb[2], 255)

    for pattern, rgb in RULES:
        if re.search(pattern, short):
            return (rgb[0], rgb[1], rgb[2], 255)

    h = 0
    for ch in short:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    base = 110 + (h % 60)
    return (base, base, int(base * 0.96), 255)


def is_transparent(name: str) -> bool:
    if name in TRANSPARENT:
        return True
    short = name.split(":")[-1].lower()
    if "glass" in short or "air" in short or "barrier" in short or "structure_void" in short:
        return True
    return False


# ---------------------------------------------------------------- 天然 / 人造
# 判断侧壁该画"地层剖面"还是"整片同材质"，靠的就是这个。
_NATURAL_RE = re.compile(
    r"(_leaves$|_log$|_wood$|_stem$|_ore$|_sand$|_gravel$|_dirt$|_stone$"
    r"|_terracotta$|^grass|^snow|^ice|^water$|^lava$|^cobble|^moss|^podzol"
    r"|^mycelium$|^farmland$|^clay$|^tuff$|^calcite$|^basalt$|^blackstone$"
    r"|^bedrock$|^obsidian$|^netherrack$|^end_stone$|^deepslate$|^andesite$"
    r"|^diorite$|^granite$|^mud$)")

_NATURAL_EXACT = {
    "minecraft:grass_block", "minecraft:dirt_path", "minecraft:snow_block",
    "minecraft:powder_snow", "minecraft:packed_ice", "minecraft:blue_ice",
}


def is_natural(name: str) -> bool:
    """是否属于"地表/岩层"，用来决定侧壁是否画成剖面。"""
    if name in _NATURAL_EXACT:
        return True
    return bool(_NATURAL_RE.search(name.split(":")[-1]))


# 透明/装饰/微小方块：不该按完整立方体渲染（会破面），应跳过。
# 注意：楼梯/台阶/栅栏/墙/门/箱子等「实体但不完整」的方块**不在此列**——
# 它们渲染成立方体只是形状简化，跳过反而会丢建筑结构。
_NONCUBE_RE = re.compile(
    r"(_torch$|_pane$|_carpet$|_rail$|_lever$|_rod$|_lantern$|_candle$"
    r"|_head$|_skull$|_bed$|_chain$|_sapling$|_flower$|_tulip$|_crop$"
    r"|_plant$|_mushroom$|_fungus$|_bush$|_fern$|_vine$|_roots$|_coral$"
    r"|_lichen$|_seagrass$|_sea_pickle$|_wire$|_seat$|_proxy$|_entity$"
    r"|_campfire$|_device$|_statue$|_button$|_pressure_plate$|_sign$)"
)
_NONCUBE_EXACT = {
    "minecraft:snow", "minecraft:short_grass", "minecraft:tall_grass",
    "minecraft:large_fern", "minecraft:dead_bush", "minecraft:vine",
    "minecraft:sugar_cane", "minecraft:kelp", "minecraft:kelp_plant",
    "minecraft:seagrass", "minecraft:tall_seagrass", "minecraft:sea_pickle",
    "minecraft:bamboo", "minecraft:lily_pad", "minecraft:redstone_wire",
    "minecraft:tripwire", "minecraft:lever", "minecraft:torch",
    "minecraft:wall_torch", "minecraft:soul_torch", "minecraft:soul_wall_torch",
    "minecraft:redstone_torch", "minecraft:redstone_wall_torch",
    "minecraft:comparator", "minecraft:repeater", "minecraft:daylight_detector",
    "minecraft:glass", "minecraft:ice", "minecraft:cobweb",
    "minecraft:light_gray_stained_glass_pane", "minecraft:white_stained_glass_pane",
    "minecraft:black_stained_glass_pane", "minecraft:gray_stained_glass_pane",
    "minecraft:glass_pane",
}


def is_noncube(name: str) -> bool:
    """是否为非立方体方块（含装饰、台阶、栅栏、楼梯等）。"""
    if name in _NONCUBE_EXACT:
        return True
    short = name.split(":")[-1].lower()
    return (bool(_NONCUBE_RE.search(short)) or is_decorative(name)
            or is_detail(name) or is_transparent(name))


# 纯装饰/植物/微小透明物：这些在体素渲染与 3D 打印里都无意义，直接跳过。
# （草/花/树苗/蘑菇/蕨/藤蔓/地衣/珊瑚/海草/红石线/告示牌/压力板等）
# 后缀正则用于兜底模组方块；原版花名五花八门（poppy/dandelion/allium...），
# 必须精确枚举，否则会被误判成完整方块渲染成破面立方体。
_DECORATIVE_RE = re.compile(
    r"(?:^|_)(sapling|flower|tulip|crop|plant|mushroom|fungus|bush|fern"
    r"|vine|roots|sprouts|lichen|seagrass|sea_pickle|wire|torch|sign"
    r"|button|pressure_plate|lever|carpet|coral|grass|propagule|petals?"
    r"|clover|leaf_pile|fallen_leaves|leaves_carpet|needle_pile|pine_needles_carpet"
    r"|pebble|pebbles|twig|twigs|rock|rocks|shrub|shrubs|weed|weeds|reed|reeds"
    r"|anemone|sage|rose|daisy|orchid|dandelion|poppy|bluet|allium|peony|lilac"
    r"|sunflower|violet|marigold|hibiscus|lotus|waterlily|cornflower|azalea"
    r"|blossom)$",
    re.IGNORECASE
)
_DECORATIVE_EXACT = {
    # 花（1 格高十字交叉面）
    "minecraft:poppy", "minecraft:dandelion", "minecraft:blue_orchid",
    "minecraft:allium", "minecraft:azure_bluet", "minecraft:red_tulip",
    "minecraft:orange_tulip", "minecraft:white_tulip", "minecraft:pink_tulip",
    "minecraft:oxeye_daisy", "minecraft:cornflower", "minecraft:lily_of_the_valley",
    "minecraft:wither_rose", "minecraft:torchflower",
    # 花（2 格高）
    "minecraft:sunflower", "minecraft:lilac", "minecraft:rose_bush",
    "minecraft:peony", "minecraft:pitcher_plant",
    # 草 / 蕨 / 灌木
    "minecraft:short_grass", "minecraft:tall_grass", "minecraft:grass",
    "minecraft:fern", "minecraft:large_fern", "minecraft:dead_bush",
    # 作物
    "minecraft:wheat", "minecraft:carrots", "minecraft:potatoes",
    "minecraft:beetroots", "minecraft:nether_wart", "minecraft:cocoa",
    "minecraft:sweet_berry_bush", "minecraft:torchflower_crop",
    "minecraft:pitcher_crop", "minecraft:melon_stem", "minecraft:pumpkin_stem",
    "minecraft:attached_melon_stem", "minecraft:attached_pumpkin_stem",
    # 树苗
    "minecraft:oak_sapling", "minecraft:spruce_sapling", "minecraft:birch_sapling",
    "minecraft:jungle_sapling", "minecraft:acacia_sapling", "minecraft:dark_oak_sapling",
    "minecraft:mangrove_propagule", "minecraft:cherry_sapling",
    "minecraft:azalea", "minecraft:flowering_azalea", "minecraft:bamboo_sapling",
    # 蘑菇 / 真菌
    "minecraft:red_mushroom", "minecraft:brown_mushroom",
    "minecraft:crimson_fungus", "minecraft:warped_fungus",
    "minecraft:crimson_roots", "minecraft:warped_roots", "minecraft:nether_sprouts",
    # 藤蔓 / 地衣 / 根系
    "minecraft:vine", "minecraft:cave_vines", "minecraft:cave_vines_plant",
    "minecraft:twisting_vines", "minecraft:twisting_vines_plant",
    "minecraft:weeping_vines", "minecraft:weeping_vines_plant",
    "minecraft:glow_lichen", "minecraft:hanging_roots",
    # 甘蔗 / 竹子
    "minecraft:sugar_cane", "minecraft:bamboo",
    # 海草 / 海带 / 海泡菜
    "minecraft:seagrass", "minecraft:tall_seagrass",
    "minecraft:kelp", "minecraft:kelp_plant", "minecraft:sea_pickle",
    # 火把
    "minecraft:torch", "minecraft:wall_torch", "minecraft:soul_torch",
    "minecraft:soul_wall_torch", "minecraft:redstone_torch", "minecraft:redstone_wall_torch",
    # 红石
    "minecraft:redstone_wire", "minecraft:tripwire", "minecraft:tripwire_hook",
    "minecraft:lever",
    # 睡莲 / 蜘蛛网 / 雪层
    "minecraft:lily_pad", "minecraft:cobweb", "minecraft:snow",
    "minecraft:small_dripleaf", "minecraft:big_dripleaf",
    "minecraft:big_dripleaf_stem",
    "minecraft:pink_petals", "minecraft:spore_blossom",
    "minecraft:sculk_vein",
    "minecraft:amethyst_cluster", "minecraft:large_amethyst_bud",
    "minecraft:medium_amethyst_bud", "minecraft:small_amethyst_bud",
}


def is_decorative(name: str) -> bool:
    """是否为装饰/植被/微小透光物（贴图交叉面、地表覆盖物等）。"""
    if name in _DECORATIVE_EXACT:
        return True
    short = name.split(":")[-1].lower()
    if _DECORATIVE_RE.search(short):
        return True
    if any(k in short for k in ("leaf_pile", "fallen_leaves", "leaves_carpet", "needle_pile", "clover", "petals", "_carpet")):
        return True
    return False


# 实体不完整方块（应渲染真实几何）：栅栏/台阶/楼梯/墙/门/活板门/箱子/床/蛋糕/铁轨等
# 注意：不能用 _lantern$ 这种宽松正则——sea_lantern/jack_o_lantern 是完整方块。
_DETAIL_RE = re.compile(
    r"(?:_|^)(fence_gate|fence|slab|stairs|wall|door|trapdoor|rail|bed|pane"
    r"|chain|rod|candle|head|skull|bars|gate)$"
)
_DETAIL_EXACT = {
    "minecraft:chest", "minecraft:trapped_chest",
    "minecraft:cake", "minecraft:enchanting_table", "minecraft:brewing_stand",
    "minecraft:cauldron", "minecraft:hopper", "minecraft:anvil",
    "minecraft:iron_bars", "minecraft:end_rod", "minecraft:ladder",
    "minecraft:grindstone", "minecraft:stonecutter", "minecraft:bell",
    "minecraft:composter", "minecraft:lectern", "minecraft:daylight_detector",
    "minecraft:repeater", "minecraft:comparator", "minecraft:campfire",
    "minecraft:soul_campfire",
    # 铁轨（普通铁轨名就是 rail，不带前缀，必须精确枚举）
    "minecraft:rail", "minecraft:powered_rail",
    "minecraft:detector_rail", "minecraft:activator_rail",
    # 灯笼（非完整，悬挂）：注意海晶灯 sea_lantern / 南瓜灯 jack_o_lantern 是完整方块，不在此列
    "minecraft:lantern", "minecraft:soul_lantern",
}


def is_detail(name: str) -> bool:
    """是否为「实体不完整方块」——应渲染真实几何（而非完整立方体或跳过）。"""
    if name in _DETAIL_EXACT:
        return True
    return bool(_DETAIL_RE.search(name.split(":")[-1]))


__all__ = ["resolve", "is_transparent", "is_natural", "is_noncube",
           "is_decorative", "is_detail",
           "AIR", "TRANSPARENT", "DECORATION"]
