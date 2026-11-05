"""从 Minecraft 原版 jar 提取方块贴图。

解析官方资源格式，为每个方块确定顶/侧/底三面各用哪张贴图：

  assets/minecraft/blockstates/<block>.json   变体 → 模型
  assets/minecraft/models/block/<model>.json  模型 → 面(texture 变量) + parent 继承
  assets/minecraft/textures/block/<tex>.png   实际像素贴图

只关心「整块立方体」方块的顶(up)/侧(side)/底(down)三面；对台阶、栅栏、
门、植物等非立方体方块，退化到取 particle/第一张贴图填满三面。

对外只暴露一个稳定接口：get_textures(block_name) -> (top, side, bottom)
三个贴图名字符串。图集的实际拼接仍在 texture.py 完成，但贴图内容改为
从 jar 读取原版像素，而非程序化单色合成。
"""

from __future__ import annotations

import json
import os
import threading
import zipfile
from dataclasses import dataclass, replace

# ---------------------------------------------------------------- 常量

# 内置父模型：这些模型把六面贴图变量重定向，需要特殊解析
# 键 = model 短名（无 minecraft:block/ 前缀），值 = 各面纹理变量名
_BUILTIN_PARENTS: dict[str, dict[str, str]] = {
    "cube_all": {
        "up": "#all", "down": "#all", "north": "#all",
        "south": "#all", "west": "#all", "east": "#all", "particle": "#all",
    },
    "cube": {
        "up": "#up", "down": "#down", "north": "#north",
        "south": "#south", "west": "#west", "east": "#east",
    },
    "cube_bottom_top": {
        "up": "#top", "down": "#bottom", "north": "#side",
        "south": "#side", "west": "#side", "east": "#side", "particle": "#side",
    },
    "cube_top": {
        "up": "#top", "down": "#top", "north": "#side",
        "south": "#side", "west": "#side", "east": "#side", "particle": "#side",
    },
    "cube_column": {
        "up": "#end", "down": "#end", "north": "#side",
        "south": "#side", "west": "#side", "east": "#side", "particle": "#side",
    },
    "cube_column_horizontal": {
        "up": "#side", "down": "#side", "north": "#end",
        "south": "#end", "west": "#side", "east": "#side", "particle": "#side",
    },
    "cube_mirrored_all": {
        "up": "#all", "down": "#all", "north": "#all",
        "south": "#all", "west": "#all", "east": "#all", "particle": "#all",
    },
    # 模板父模型：elements 用 #xxx 变量，变量值由子模型 textures 提供
    "template_wall_post": {
        "up": "#wall", "down": "#wall", "north": "#wall",
        "south": "#wall", "west": "#wall", "east": "#wall", "particle": "#wall",
    },
    "template_wall_side": {
        "up": "#wall", "down": "#wall", "north": "#wall",
        "south": "#wall", "west": "#wall", "east": "#wall", "particle": "#wall",
    },
    "template_fence_post": {
        "up": "#texture", "down": "#texture", "north": "#texture",
        "south": "#texture", "west": "#texture", "east": "#texture", "particle": "#texture",
    },
    "template_fence_side": {
        "up": "#texture", "down": "#texture", "north": "#texture",
        "south": "#texture", "west": "#texture", "east": "#texture", "particle": "#texture",
    },
    "template_glass_pane_post": {
        "up": "#pane", "down": "#pane", "north": "#pane",
        "south": "#pane", "west": "#pane", "east": "#pane", "particle": "#pane",
    },
    "template_glass_pane_side": {
        "up": "#pane", "down": "#pane", "north": "#pane",
        "south": "#pane", "west": "#pane", "east": "#pane", "particle": "#pane",
    },
    "template_piston": {
        "up": "#side", "down": "#side", "north": "#side",
        "south": "#side", "west": "#side", "east": "#side", "particle": "#side",
    },
    "template_piston_head": {
        "up": "#side", "down": "#side", "north": "#side",
        "south": "#side", "west": "#side", "east": "#side", "particle": "#side",
    },
}

# 纯立方体内置模型：这些模型**没有 elements**（几何是隐含的 16³ 完整立方体），
# 只有「面→贴图变量」映射。遇到它们应停止向上递归并兜底成完整立方体。
# 与之相对的 template_* 模型在 jar 里有真实 elements（细柱/薄片），不能在此列。
_CUBE_PARENTS = {
    "cube", "cube_all", "cube_bottom_top", "cube_top", "cube_column",
    "cube_column_horizontal", "cube_mirrored_all",
}

# 特殊贴图变量名 → 面的映射（模型里 faces 用的键）
_FACE_KEYS = ("up", "down", "north", "south", "west", "east")

# 非立方体方块：这些方块没有标准六面，退化为单贴图
_NONCUBE_SUFFIX = (
    "_stairs", "_slab", "_fence", "_fence_gate", "_wall", "_door",
    "_trapdoor", "_button", "_pressure_plate", "_sign", "_torch",
    "_sapling", "_flower", "_tulip", "_crop", "_plant", "_carpet",
    "_rail", "_ladder", "_pane", "_chain", "_banner", "_bed",
    "_candle", "_rod", "_skull", "_head", "_vines", "_roots",
    "_bush", "_fern", "_mushroom", "_fungus", "_lichen", "_coral",
    "_anvil", "_hopper", "_cauldron", "_piston", "_observer",
    "_dispenser", "_dropper", "_furnace", "_chest", "_barrel",
)


# ---------------------------------------------------------------- 数据结构

@dataclass
class _Model:
    """解析后的模型：六面各自的贴图名（已解析完变量）。"""
    textures: dict[str, str]  # 变量名 → 贴图短名（如 "#top" → "grass_block_top"）
    faces: dict[str, str]     # 面名 up/down/... → 贴图短名


@dataclass
class _Element:
    """模型里的一个长方体元素（解析完贴图变量与 parent 继承）。"""
    frm: tuple[float, float, float]   # from 坐标 [x,y,z]（0~16，Minecraft 空间）
    to: tuple[float, float, float]    # to 坐标
    # 面 → (贴图短名, uv [u0,v0,u1,v1])，缺面的为 None
    faces: dict[str, tuple[str, tuple[float, float, float, float]]]
    rotation: dict | None = None      # {origin, axis, angle, rescale}
    block_rotation: tuple[int, int] = (0, 0)  # blockstate x/y around (8,8,8)


@dataclass
class _Geometry:
    """完整模型几何体：多个元素。"""
    elements: list[_Element]


class VanillaAssets:
    """原版 + 整合包模组资源库：从多个 jar 中按命名空间读取并缓存资源。

    jar_path 是原版（minecraft 命名空间）的 jar；额外的模组 jar 通过
    add_mod_jar() 注册，按各自命名空间（assets/<ns>/...）读取。
    """

    def __init__(self, jar_path: str):
        self.jar_path = jar_path
        self._zf: zipfile.ZipFile | None = None
        self._zfs: dict[str, list[zipfile.ZipFile]] = {}  # 命名空间 → [模组 jar / 材质包 zip] 列表
        self._dirs: dict[str, list[str]] = {}             # 命名空间 → [assets 目录] 列表
        self._ns_of_jar: dict[str, str] = {}
        self._blockstates: dict[str, dict] = {}
        self._models: dict[str, dict] = {}
        self._textures: dict[str, bytes] = {}
        self._resolved: dict[str, _Model] = {}
        self._missing: set[str] = set()
        # zipfile 不支持并发读，贴图/模型的并发请求必须串行化，
        # 否则并发 seek 读同一 jar 会损坏数据或卡死服务。
        self._lock = threading.Lock()
        if not os.path.exists(jar_path):
            raise FileNotFoundError(f"未找到原版资源 jar：{jar_path}")

    def add_mod_jar(self, jar_path: str, namespace: str) -> None:
        """注册一个模组 jar 及其命名空间（如 create / biomeswevegone）。

        多模组共用命名空间（如 ae2/create 扩展）时，把同名主模组排在前面。
        """
        if not os.path.exists(jar_path):
            return
        try:
            zf = zipfile.ZipFile(jar_path)
            lst = self._zfs.setdefault(namespace, [])
            base = os.path.basename(jar_path).lower().replace("-", "_")
            if namespace in base:
                lst.insert(0, zf)
            else:
                lst.append(zf)
        except Exception:
            return

    def add_mod_dir(self, assets_dir: str, namespace: str) -> None:
        """注册一个模组资源文件夹（如 kubejs 的 assets 目录，非 jar）。

        assets_dir 是 assets 根目录（例如 .../kubejs/assets），其下按
        assets/<ns>/textures|models|blockstates/... 组织。
        """
        if os.path.isdir(assets_dir):
            self._dirs.setdefault(namespace, []).append(assets_dir)

    def add_resource_pack(self, pack_path: str) -> None:
        """注册一个材质包/资源包（.zip 或文件夹）。材质包具有最高优先级，插在最前。"""
        if not os.path.exists(pack_path):
            return
        if os.path.isfile(pack_path) and pack_path.lower().endswith(".zip"):
            try:
                zf = zipfile.ZipFile(pack_path)
                ns_set = set()
                for n in zf.namelist():
                    if n.startswith("assets/") and n.count("/") >= 2:
                        parts = n.split("/")
                        if len(parts) >= 2 and parts[0] == "assets" and parts[1]:
                            ns_set.add(parts[1])
                for ns in ns_set:
                    self._zfs.setdefault(ns, []).insert(0, zf)
            except Exception:
                pass
        elif os.path.isdir(pack_path):
            assets_root = os.path.join(pack_path, "assets")
            if os.path.isdir(assets_root):
                try:
                    for ns in os.listdir(assets_root):
                        ns_dir = os.path.join(assets_root, ns)
                        if os.path.isdir(ns_dir):
                            self._dirs.setdefault(ns, []).insert(0, pack_path)
                except Exception:
                    pass

    # ---- 底层 zip 读取 ----
    def _zip(self) -> zipfile.ZipFile:
        if self._zf is None:
            self._zf = zipfile.ZipFile(self.jar_path)
        return self._zf

    def _zips_for_ns(self, ns: str) -> list[zipfile.ZipFile]:
        """按命名空间获取所有注册的 zip/jar；minecraft 优先查材质包再退到原版。"""
        zfs = list(self._zfs.get(ns, []))
        if ns == "minecraft":
            zfs.append(self._zip())
        return zfs

    @staticmethod
    def _split_ns(ref: str) -> tuple[str, str]:
        """把 'minecraft:block/x' / 'create:block/y' / 'block/x' 拆成 (ns, 相对路径)。

        ref 可能带 ':' 前缀（命名空间）或不带（默认 minecraft）。
        返回 (namespace, 去掉命名空间和 block/ 前缀后的短名)。
        """
        ns = "minecraft"
        rest = ref
        if ":" in ref:
            ns, rest = ref.split(":", 1)
        if rest.startswith("block/"):
            rest = rest[len("block/"):]
        return ns, rest

    def _read_json(self, path: str, ns: str = "minecraft") -> dict | None:
        # 1) 从文件夹（材质包文件夹、kubejs assets 等）读取
        for d in self._dirs.get(ns, []):
            rel = path[len("assets/"):] if path.startswith("assets/") else path
            for full in (os.path.join(d, rel), os.path.join(d, path)):
                if os.path.isfile(full):
                    try:
                        with open(full, "r", encoding="utf-8") as f:
                            return json.load(f)
                    except Exception:
                        pass
        # 2) 从 zip/jar 读取（材质包排前，主模组排前，原版兜底）
        with self._lock:   # zipfile 并发读不安全
            for zf in self._zips_for_ns(ns):
                try:
                    data = zf.read(path)
                    return json.loads(data.decode("utf-8"))
                except (KeyError, Exception):
                    continue
        return None

    def _read_bytes(self, path: str, ns: str = "minecraft") -> bytes | None:
        """按 jar/文件夹读原始字节（贴图 PNG 等）。path 形如 assets/<ns>/...。"""
        # 1) 从文件夹读取
        for d in self._dirs.get(ns, []):
            rel = path[len("assets/"):] if path.startswith("assets/") else path
            for full in (os.path.join(d, rel), os.path.join(d, path)):
                if os.path.isfile(full):
                    try:
                        with open(full, "rb") as f:
                            return f.read()
                    except Exception:
                        pass
        # 2) 从 zip/jar 读取
        with self._lock:   # zipfile 并发读不安全
            for zf in self._zips_for_ns(ns):
                try:
                    return zf.read(path)
                except (KeyError, Exception):
                    continue
        return None

    def _read_texture_png(self, name: str) -> bytes | None:
        """name 形如 'grass_block_top' 或 'create:block/zinc_ore'，返回 PNG 字节。"""
        return self.read_texture(name)

    # ---- model 解析 ----
    def _load_model(self, model_ref: str) -> dict | None:
        """model_ref 形如 'minecraft:block/grass_block' 或 'block/grass_block'。"""
        ns, short = self._split_ns(model_ref)
        key = f"{ns}:{short}" if ns != "minecraft" else short
        if key in self._models:
            return self._models[key]
        data = self._read_json(f"assets/{ns}/models/block/{short}.json", ns)
        self._models[key] = data if data is not None else {}
        return self._models[key]

    def _resolve_model(self, model_ref: str, depth: int = 0) -> _Model | None:
        """递归解析模型，返回六面贴图。"""
        if depth > 8:
            return None
        if model_ref in self._resolved:
            return self._resolved[model_ref]

        short = model_ref
        if short.startswith("minecraft:"):
            short = short[len("minecraft:"):]
        if short.startswith("block/"):
            short = short[len("block/"):]

        data = self._load_model(model_ref)
        if data is None:
            return None

        textures: dict[str, str] = {}
        faces: dict[str, str] = {}

        # 1) 先处理 parent（继承纹理变量与元素）
        parent = data.get("parent")
        parent_faces: dict[str, str] = {}
        parent_textures: dict[str, str] = {}
        if parent:
            # 内置父模型：直接取变量名映射（内置父模型都在 minecraft 命名空间）
            _, pshort = self._split_ns(parent)
            if pshort in _BUILTIN_PARENTS:
                # 内置父模型只给出「面→变量」映射，变量值由本模型 textures 提供
                parent_faces = dict(_BUILTIN_PARENTS[pshort])
            else:
                pm = self._resolve_model(parent, depth + 1)
                if pm is not None:
                    parent_textures = dict(pm.textures)
                    parent_faces = dict(pm.faces)

        # 2) 合并本模型的 textures（覆盖父级）
        textures.update(parent_textures)
        own_tex = data.get("textures", {})
        for k, v in own_tex.items():
            textures[k] = self._tex_short(v)

        # 3) 合并父级 faces（face → 变量名），再用本模型 textures 解析变量
        for face, var in parent_faces.items():
            faces[face] = self._deref(var, textures)

        # 4) 本模型自己的 elements[].faces 覆盖（优先非 overlay/tint 的主贴图）
        for elem in data.get("elements", []):
            for face, fdef in elem.get("faces", {}).items():
                tex_var = fdef.get("texture")
                if tex_var is None:
                    continue
                # 带 tintindex 的是着色/叠加层（如草方块侧面的 overlay），
                # 只作为兜底，不覆盖已有主贴图
                is_overlay = fdef.get("tintindex") is not None
                resolved = self._deref(tex_var, textures)
                if resolved is None:
                    continue
                if is_overlay and face in faces and faces[face] is not None:
                    continue
                faces[face] = resolved

        # 5) 若仍无面定义，尝试用常见变量兜底（fence 的 #texture、stairs 的 #top/#side/#bottom）
        if not faces:
            for var_name in ("texture", "side", "top", "bottom", "all"):
                if var_name in textures:
                    p = self._deref("#" + var_name, textures)
                    if p:
                        for f in _FACE_KEYS:
                            faces[f] = p
                        break
        if not faces and "particle" in textures:
            p = self._deref("#particle", textures)
            if p:
                for f in _FACE_KEYS:
                    faces[f] = p

        if not faces:
            self._resolved[model_ref] = None
            return None

        result = _Model(textures=textures, faces=faces)
        self._resolved[model_ref] = result
        return result

    # ---- elements 几何体解析（完整模型还原）----
    def parse_elements(self, block: str, properties: dict | None = None) -> _Geometry | None:
        """解析某方块的完整几何体（elements），还原真实形状。

        返回 _Geometry（多个 element），每个 element 的贴图变量已解析为
        实际贴图短名，UV 为原始像素坐标（0~16，或更大）。含 parent 继承、
        element rotation、face rotation/uv。解析失败返回 None。

        与 _resolve_model（只取六面贴图）不同，这里保留每个 element 的
        from/to 几何与每面独立 UV，用于铁轨/栅栏/活板门/蛋糕/床等
        非完整方块的忠实渲染与 OBJ 导出。
        """
        if properties is None:
            variants = [{"model": self._first_variant_model(block)}]
        else:
            ns, short = self._split_ns(block)
            data = self._read_json(f"assets/{ns}/blockstates/{short}.json", ns)
            if data is None:
                return None
            variants = []
            if "variants" in data:
                for key, value in data["variants"].items():
                    if not key or all(properties.get(k) == v
                                      for k, v in (item.split("=", 1) for item in key.split(","))):
                        variants.append(value)
                        break
            else:
                variants = [part["apply"] for part in data.get("multipart", [])
                            if self._matches_condition(part.get("when", {}), properties)]
        elements = []
        ns, _ = self._split_ns(block)
        for variant in variants:
            # Use the first weighted alternative, as the preview renderer does.
            if isinstance(variant, list):
                variant = variant[0] if variant else {}
            model_ref = variant.get("model")
            if not model_ref:
                continue
            if ":" not in model_ref and ns != "minecraft":
                model_ref = f"{ns}:{model_ref}"
            rotation = (int(variant.get("x", 0)), int(variant.get("y", 0)))
            elements.extend(replace(element, block_rotation=rotation)
                            for element in self._collect_elements(model_ref, 0))
        if not elements:
            return None
        return _Geometry(elements=elements)

    @staticmethod
    def _matches_condition(condition: dict, properties: dict) -> bool:
        if "OR" in condition:
            return any(VanillaAssets._matches_condition(c, properties) for c in condition["OR"])
        if "AND" in condition:
            return all(VanillaAssets._matches_condition(c, properties) for c in condition["AND"])
        return all(properties.get(key) in str(value).split("|")
                   for key, value in condition.items())

    def _collect_elements(self, model_ref: str, depth: int) -> list[_Element]:
        """递归收集模型链（含 parent）的全部 elements。

        关键：先自底向上合并整条链的 textures（子覆盖父），再用这份
        「完整 textures」解析每一层的 elements——因为父模型 elements 引用的
        #变量 常常由子模型定义（如 fence_post 的 #texture 由 oak_fence_post 提供）。
        """
        if depth > 8:
            return []
        chain = self._model_chain(model_ref, depth)
        if not chain:
            return []

        # 1) 自底向上（父→子）合并 textures
        textures: dict[str, str] = {}
        for data in chain:  # chain 已按父→子排序
            for k, v in data.get("textures", {}).items():
                textures[k] = self._tex_short(v)

        # 2) 用完整 textures 解析每一层的 elements（子层优先，父层兜底）
        #    Minecraft 语义：子模型有自己的 elements 时整体替换父级的；
        #    这里按「最深层有 elements 的层」优先。
        elements: list[_Element] = []
        for data in reversed(chain):  # 子→父，先看子模型自己的 elements
            own = [self._parse_element(e, textures)
                   for e in data.get("elements", [])]
            own = [e for e in own if e is not None]
            if own:
                return own  # 子模型有 elements，用它
        # 链里没有显式 elements，但可能指向内置「完整立方体」父模型（cube/cube_all 等）
        full = self._builtin_cube_element(chain[0], textures)
        if full is not None:
            return [full]
        return elements

    def _builtin_cube_element(self, root_data: dict, textures: dict[str, str]) -> _Element | None:
        """当模型链最终落到内置立方体父模型时，生成一个 16³ 完整立方体 element。"""
        # 找到链里第一个（最父级）的 parent 短名
        # root_data 是 chain[0]（最父级模型），它的 parent 才是内置模型
        parent = root_data.get("parent")
        if not parent:
            return None
        _, pshort = self._split_ns(parent)
        faces_map = _BUILTIN_PARENTS.get(pshort)
        if faces_map is None:
            return None
        # faces_map 形如 {"up": "#top", "down": "#bottom", ...}
        faces: dict[str, tuple[str, tuple]] = {}
        for face, var in faces_map.items():
            if face == "particle":
                continue
            resolved = self._deref(var, textures)
            if resolved is None:
                continue
            # 完整立方体的默认 UV：每面 0~16
            faces[face] = (resolved, (0.0, 0.0, 16.0, 16.0))
        if not faces:
            return None
        return _Element(
            frm=(0.0, 0.0, 0.0), to=(16.0, 16.0, 16.0),
            faces=faces, rotation=None,
        )

    def _model_chain(self, model_ref: str, depth: int) -> list[dict]:
        """返回 model_ref 及其所有 parent 的原始 JSON，按「父→子」排序。"""
        chain: list[dict] = []
        cur = model_ref
        seen = set()
        for _ in range(10):
            if cur in seen:
                break
            seen.add(cur)
            data = self._load_model(cur)
            if data is None:
                break
            chain.append(data)
            parent = data.get("parent")
            if not parent:
                break
            _, pshort = self._split_ns(parent)
            # 只有「纯立方体内置模型」（无 elements，几何是隐含完整立方体）才在此终止；
            # 模板模型（template_wall_post 等）在 jar 里有真实 elements，必须继续解析。
            if pshort in _CUBE_PARENTS:
                break
            cur = parent
        # 反转为父→子
        chain.reverse()
        return chain

    def _parse_element(self, elem: dict, textures: dict[str, str]) -> _Element | None:
        frm = elem.get("from")
        to = elem.get("to")
        if not frm or not to:
            return None
        faces: dict[str, tuple[str, tuple]] = {}
        for face, fdef in elem.get("faces", {}).items():
            if not isinstance(fdef, dict):
                continue
            tex_var = fdef.get("texture")
            if tex_var is None:
                continue
            resolved = self._deref(tex_var, textures)
            if resolved is None:
                continue
            uv = fdef.get("uv")
            if uv is None:
                # 无显式 UV：用 from/to 在对应轴投影（16 像素 = 1 方块）
                uv = self._default_uv(face, frm, to)
            faces[face] = (resolved, tuple(float(v) for v in uv))
        if not faces:
            return None
        return _Element(
            frm=tuple(float(v) for v in frm),
            to=tuple(float(v) for v in to),
            faces=faces,
            rotation=elem.get("rotation"),
        )

    @staticmethod
    def _default_uv(face: str, frm, to) -> list[float]:
        """按 from/to 在对应轴投影生成默认 UV（16px = 1 方块）。"""
        x0, y0, z0 = frm
        x1, y1, z1 = to
        if face == "up":
            return [x0, z0, x1, z1]
        if face == "down":
            return [x0, z0, x1, z1]
        if face in ("north", "south"):
            return [x0, y0, x1, y1]
        if face in ("west", "east"):
            return [z0, y0, z1, y1]
        return [0, 0, 16, 16]

    @staticmethod
    def _tex_short(v: str) -> str:
        """贴图引用 → 短名（保留非 minecraft 命名空间）。

        'minecraft:block/grass_top' / 'block/grass_top' → 'grass_top'
        'create:block/zinc_ore' → 'create:zinc_ore'（保留 create 前缀）
        '#top' 这类变量引用原样保留。
        """
        if v.startswith("#"):
            return v
        ns = "minecraft"
        rest = v
        if ":" in v:
            ns, rest = v.split(":", 1)
        if rest.startswith("block/"):
            rest = rest[len("block/"):]
        if ns == "minecraft":
            return rest
        return f"{ns}:{rest}"

    def _deref(self, var: str | None, textures: dict[str, str]) -> str | None:
        """解析变量引用：#top → textures['top']，并继续解析到实际贴图短名。"""
        if var is None:
            return None
        seen = set()
        cur = var
        while cur.startswith("#"):
            if cur in seen:
                return None  # 循环引用
            seen.add(cur)
            key = cur[1:]
            nxt = textures.get(key)
            if nxt is None:
                return None
            cur = nxt
        return cur

    # ---- 对外接口 ----
    def _first_variant_model(self, block: str) -> str | None:
        """取 blockstate 的第一个变体的 model 引用。"""
        if block in self._blockstates:
            return self._blockstates[block]
        ns, short = self._split_ns(block)
        data = self._read_json(f"assets/{ns}/blockstates/{short}.json", ns)
        if data is None:
            self._blockstates[block] = None
            return None
        model_ref = self._extract_first_model(data)
        # 若模型引用无命名空间，补上当前方块的命名空间（模组方块引用自己的模型）
        if model_ref and ":" not in model_ref and ns != "minecraft":
            model_ref = f"{ns}:{model_ref}"
        self._blockstates[block] = model_ref
        return model_ref

    @staticmethod
    def _extract_first_model(data: dict) -> str | None:
        """取第一个（且优先非 horizontal/旋转朝向）的模型引用。

        原木类方块 blockstate 里 horizontal（横放）变体常排前面，
        但默认朝向是竖放（axis=y），所以优先选不含 horizontal 的模型。
        """
        variants = data.get("variants", {})
        if variants:
            # 收集所有 model 引用，优先非 horizontal
            refs = []
            for v in variants.values():
                items = v if isinstance(v, list) else [v]
                for it in items:
                    if isinstance(it, dict) and it.get("model"):
                        refs.append(it["model"])
            # 优先非 horizontal
            for r in refs:
                if "horizontal" not in r:
                    return r
            return refs[0] if refs else None
        multipart = data.get("multipart", [])
        if multipart:
            first = multipart[0] if multipart else {}
            apply = first.get("apply", {})
            if isinstance(apply, list):
                apply = apply[0] if apply else {}
            if isinstance(apply, dict):
                return apply.get("model")
        return None

    def _fallback_texture(self, block: str) -> str | None:
        """非立方体/解析失败时的兜底：取一个能用的贴图短名。"""
        # 优先走模型解析，从 particle / 任一 face 拿真实贴图（最可靠）
        model_ref = self._first_variant_model(block)
        if model_ref:
            m = self._resolve_model(model_ref)
            if m:
                if "particle" in m.textures:
                    p = self._deref("#particle", m.textures)
                    if p and self._read_texture_png(p) is not None:
                        return p
                for f in ("up", "down", "north", "south", "west", "east"):
                    t = m.faces.get(f)
                    if t and self._read_texture_png(t) is not None:
                        return t

        # 再按名字猜（保留命名空间，模组方块去对应 jar 里找）
        ns, short = self._split_ns(block)
        prefix = f"{ns}:" if ns != "minecraft" else ""
        candidates = [prefix + short, prefix + short + "_top",
                      prefix + short + "_side", prefix + short + "_planks"]
        for c in candidates:
            if self._read_texture_png(c) is not None:
                return c
        return None

    def get_textures(self, block: str) -> tuple[str | None, str | None, str | None]:
        """返回 (top, side, bottom) 三张贴图短名；None 表示无原版贴图。

        用完整模型解析（parse_elements），而不是旧的 _resolve_model——
        旧版对 orientable 类方块（熔炉/南瓜灯等）解析错（top 贴成 front）。
        """
        # 用完整解析，从「完整立方体 element」提取三面贴图
        g = self.parse_elements(block)
        if g is not None:
            for e in g.elements:
                # 只取完整立方体（16³）element 的三面；非完整方块交给 detail 几何
                if (abs(e.frm[0]) < 1e-6 and abs(e.frm[1]) < 1e-6 and abs(e.frm[2]) < 1e-6
                        and abs(e.to[0] - 16) < 1e-6 and abs(e.to[1] - 16) < 1e-6
                        and abs(e.to[2] - 16) < 1e-6):
                    top = e.faces.get("up", (None, None))[0]
                    # 侧面：从 4 个侧面里取「出现最多的贴图」作为 side。
                    # 因为熔炉/南瓜灯等方块的 north（或 west）是 front 面（特殊贴图），
                    # 而真正的 side 面在 4 个侧面里占多数。统计最稳。
                    side_vals = [e.faces[f][0] for f in ("north", "south", "west", "east")
                                 if f in e.faces]
                    if side_vals:
                        cnt: dict[str, int] = {}
                        for v in side_vals:
                            cnt[v] = cnt.get(v, 0) + 1
                        side = max(cnt, key=cnt.get)
                    else:
                        side = None
                    bottom = e.faces.get("down", (None, None))[0]
                    if top is None:
                        top = self._fallback_texture(block)
                    if side is None:
                        side = self._fallback_texture(block)
                    if bottom is None:
                        bottom = self._fallback_texture(block) or side
                    return (top, side, bottom)

        # 解析失败或非完整方块：兜底
        t = self._fallback_texture(block)
        return (t, t, t)

    # ---- deepslate 前端按需资源解析 ----
    def read_texture(self, ref: str) -> bytes | None:
        """按完整引用读贴图 PNG 字节。

        ref 形如 'block/stone'（默认 minecraft）或 'create:block/palettes/x'。
        支持智能回退：
          assets/{ns}/textures/{rest}.png
          assets/{ns}/textures/block/{rest}.png
          assets/{ns}/textures/{short}.png
          assets/{ns}/textures/item/{short}.png
        """
        if ref in self._textures:
            return self._textures[ref]
        if ":" in ref:
            ns, rest = ref.split(":", 1)
        else:
            ns, rest = "minecraft", ref

        if rest.endswith(".png"):
            rest = rest[:-4]

        candidates = [
            f"assets/{ns}/textures/{rest}.png",
        ]
        if not rest.startswith("block/"):
            candidates.append(f"assets/{ns}/textures/block/{rest}.png")
        if rest.startswith("block/"):
            short = rest[6:]
            candidates.append(f"assets/{ns}/textures/{short}.png")
            candidates.append(f"assets/{ns}/textures/item/{short}.png")
        elif not rest.startswith("item/"):
            candidates.append(f"assets/{ns}/textures/item/{rest}.png")
        if "_block/" in rest:
            candidates.append(f"assets/{ns}/textures/block/{rest.replace('_block/', '_')}.png")
        if "/" in rest:
            tail = rest.split("/")[-1]
            candidates.append(f"assets/{ns}/textures/block/{tail}.png")

        # 兼容 1.20 与 1.21：grass <-> short_grass
        if ns == "minecraft":
            if "grass" in rest and "short_grass" not in rest and "grass_block" not in rest:
                candidates.append("assets/minecraft/textures/block/short_grass.png")
            elif "short_grass" in rest:
                candidates.append("assets/minecraft/textures/block/grass.png")

        # 兼容 1.20 与 1.21：grass <-> short_grass
        if ns == "minecraft":
            if "grass" in rest and "short_grass" not in rest and "grass_block" not in rest:
                candidates.append("assets/minecraft/textures/block/short_grass.png")
            elif "short_grass" in rest:
                candidates.append("assets/minecraft/textures/block/grass.png")

        # 兼容 1.20 与 1.21：grass <-> short_grass
        if ns == "minecraft":
            if "grass" in rest and "short_grass" not in rest and "grass_block" not in rest:
                candidates.append("assets/minecraft/textures/block/short_grass.png")
            elif "short_grass" in rest:
                candidates.append("assets/minecraft/textures/block/grass.png")

        # 兼容 1.20 与 1.21：grass <-> short_grass
        if ns == "minecraft":
            if "grass" in rest and "short_grass" not in rest and "grass_block" not in rest:
                candidates.append("assets/minecraft/textures/block/short_grass.png")
            elif "short_grass" in rest:
                candidates.append("assets/minecraft/textures/block/grass.png")

        # 兼容 1.20 与 1.21：grass <-> short_grass
        if ns == "minecraft":
            if "grass" in rest and "short_grass" not in rest and "grass_block" not in rest:
                candidates.append("assets/minecraft/textures/block/short_grass.png")
            elif "short_grass" in rest:
                candidates.append("assets/minecraft/textures/block/grass.png")

        for path in candidates:
            data = self._read_bytes(path, ns)
            if data is not None:
                self._textures[ref] = data
                return data

        self._missing.add(ref)
        return None

    @staticmethod
    def _model_refs(bs: dict):
        """从 blockstate JSON 收集所有 model 引用。"""
        for v in (bs.get("variants") or {}).values():
            items = v if isinstance(v, list) else [v]
            for it in items:
                if isinstance(it, dict) and isinstance(it.get("model"), str):
                    yield it["model"]
        for part in bs.get("multipart") or []:
            ap = part.get("apply")
            items = ap if isinstance(ap, list) else [ap]
            for it in items:
                if isinstance(it, dict) and isinstance(it.get("model"), str):
                    yield it["model"]

    @staticmethod
    def _norm_ref(ref: str) -> tuple[str, str]:
        """资源引用 → (命名空间, 相对路径)。裸引用默认 minecraft（与 deepslate 一致）。"""
        if ":" in ref:
            ns, rest = ref.split(":", 1)
        else:
            ns, rest = "minecraft", ref
        return ns, rest

    @staticmethod
    def _has_elements(models: dict, key: str, seen: set) -> bool:
        """沿 parent 链检查模型最终是否有几何体（elements）。"""
        if key in seen:
            return False
        seen.add(key)
        m = models.get(key)
        if m is None:
            return False
        els = m.get("elements")
        if isinstance(els, list):
            return len(els) > 0
        parent = m.get("parent")
        if isinstance(parent, str):
            pns, prest = VanillaAssets._norm_ref(parent)
            return VanillaAssets._has_elements(models, f"{pns}:{prest}", seen)
        return False

    def resolve_blockdefs(self, names: list[str], max_names: int = 2000) -> dict:
        """按需解析一组方块名的 blockstate / 模型 / 贴图闭包（deepslate 前端用）。

        names 形如 ["create:zinc_ore", "kubejs:candy_block"]。返回 {
          "blockstates": {完整名: blockstate JSON},
          "models":      {完整引用: 模型 JSON},
          "textureIds":  [完整引用, ...],
          "emptyRender": [完整名, ...],  # 有定义但模型链无几何体，渲染为空
        }，完整引用 = 'ns:path'（与前端 deepslate 查找键一致）。
        """
        blockstates: dict[str, dict] = {}
        models: dict[str, dict] = {}
        textures: set[str] = set()
        empty_render: list[str] = []
        model_queue: list[str] = []

        for name in list(dict.fromkeys(names))[:max_names]:
            ns, path = self._norm_ref(name)
            bs = self._read_json(f"assets/{ns}/blockstates/{path}.json", ns)
            if bs is None and ns == "minecraft":
                alt = "short_grass" if path == "grass" else ("grass" if path == "short_grass" else None)
                if alt:
                    bs = self._read_json(f"assets/{ns}/blockstates/{alt}.json", ns)
            if bs is None:
                continue
            key = f"{ns}:{path}"
            blockstates[key] = bs
            model_queue.extend(self._model_refs(bs))

        seen_models: set[str] = set()
        while model_queue:
            mref = model_queue.pop()
            mns, mpath = self._norm_ref(mref)
            key = f"{mns}:{mpath}"
            if key in seen_models:
                continue
            seen_models.add(key)
            m = self._read_json(f"assets/{mns}/models/{mpath}.json", mns)
            # 如果没找到，或者模型是空对象 {}，尝试从 block/ 或 item/ 补充
            if not m:
                if not mpath.startswith("block/"):
                    m = self._read_json(f"assets/{mns}/models/block/{mpath}.json", mns)
                if not m:
                    short = mpath[6:] if mpath.startswith("block/") else mpath
                    m = self._read_json(f"assets/{mns}/models/item/{short}.json", mns)
            if m is None:
                continue
            models[key] = m
            parent = m.get("parent")
            if isinstance(parent, str):
                model_queue.append(parent)
            for tv in (m.get("textures") or {}).values():
                if isinstance(tv, str) and not tv.startswith("#"):
                    tns, tpath = self._norm_ref(tv)
                    textures.add(f"{tns}:{tpath}")

        # 整条模型链都没有 elements 的方块 → deepslate 渲染为空（多为自定义模型加载器）
        for name, bs in blockstates.items():
            has = False
            for mref in self._model_refs(bs):
                mns, mpath = self._norm_ref(mref)
                if self._has_elements(models, f"{mns}:{mpath}", set()):
                    has = True
                    break
            if not has:
                empty_render.append(name)

        return {
            "blockstates": blockstates,
            "models": models,
            "textureIds": sorted(textures),
            "emptyRender": empty_render,
        }


# 需要 biome 着色的贴图（原版用 tintindex 上色，这里用固定色调近似）
# 键 = 贴图短名，值 = 乘以的 RGBA 系数（把灰度图染成目标色；第4项可调透明度）
_TINT = {
    # 草 / 树叶 / 植被等绿色系（原版贴图为纯灰度图，需染色）
    "grass": (0.55, 0.78, 0.35, 1.0),
    "short_grass": (0.55, 0.78, 0.35, 1.0),
    "tall_grass_bottom": (0.55, 0.78, 0.35, 1.0),
    "tall_grass_top": (0.55, 0.78, 0.35, 1.0),
    "tall_grass": (0.55, 0.78, 0.35, 1.0),
    "fern": (0.50, 0.74, 0.32, 1.0),
    "large_fern_bottom": (0.50, 0.74, 0.32, 1.0),
    "large_fern_top": (0.50, 0.74, 0.32, 1.0),
    "large_fern": (0.50, 0.74, 0.32, 1.0),
    "vine": (0.48, 0.70, 0.28, 1.0),
    "lily_pad": (0.42, 0.65, 0.25, 1.0),
    "sugar_cane": (0.62, 0.82, 0.38, 1.0),
    "grass_block_top": (0.62, 0.78, 0.38, 1.0),
    "grass_block_side_overlay": (0.62, 0.78, 0.38, 1.0),
    "oak_leaves": (0.55, 0.72, 0.32, 1.0),
    "spruce_leaves": (0.35, 0.52, 0.30, 1.0),
    "birch_leaves": (0.52, 0.72, 0.34, 1.0),
    "jungle_leaves": (0.42, 0.72, 0.28, 1.0),
    "acacia_leaves": (0.58, 0.70, 0.28, 1.0),
    "dark_oak_leaves": (0.38, 0.52, 0.28, 1.0),
    "mangrove_leaves": (0.48, 0.68, 0.30, 1.0),
    "azalea_leaves": (0.52, 0.72, 0.32, 1.0),
    "cherry_leaves": (0.82, 0.60, 0.66, 1.0),
    # 水
    "water_still": (0.30, 0.50, 0.95, 0.42),
    "water_flow": (0.30, 0.50, 0.95, 0.42),
}


def tint_color(tex_name: str) -> tuple[float, float, float, float] | None:
    """贴图若需染色返回 RGBA；否则 None。支持传入带 block/ 或命名空间的前缀。"""
    if not tex_name:
        return None
    short = tex_name.split(":")[-1].split("/")[-1]
    return _TINT.get(short) or _TINT.get(tex_name)


def apply_tint_to_png(tex_name: str, png: bytes) -> bytes:
    """将 PNG 字节根据贴图名染色（如灰度草、树叶变绿）。"""
    if not png:
        return png
    tc = tint_color(tex_name)
    if tc is None:
        return png
    try:
        from PIL import Image
        import io
        import numpy as np
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        arr = np.asarray(img, dtype=np.float32)
        arr[:, :, 0] *= tc[0]
        arr[:, :, 1] *= tc[1]
        arr[:, :, 2] *= tc[2]
        if len(tc) > 3:
            arr[:, :, 3] *= tc[3]
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(arr, "RGBA").save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return png


# ---------------------------------------------------------------- 原版资产导出
# 为 deepslate 前端渲染用：把 jar 内 blockstates / models / textures
# 一次性提取，任何阶段 deepslate 用 fromJson / fromBlobs。

def export_assets(jar_path: str) -> dict:
    """从 jar 原样导出全部资源。

    返回 {
      "blockstates": { "grass_block": {原版 JSON}, ... },       # 键=方块短名
      "models":      { "block/grass_block": {原版 JSON}, ... }, # 键=相对路径(含 block/)
      "textures":    { "block/grass_block_top": <PNG bytes>, ... },
      "version":     "1.20.1",
    }
    """
    import zipfile as _zip
    zf = _zip.ZipFile(jar_path)
    names = zf.namelist()

    blockstates = {}
    models = {}
    textures = {}

    for n in names:
        if not n.startswith("assets/minecraft/"):
            continue
        rel = n[len("assets/minecraft/"):]
        if rel.startswith("blockstates/") and rel.endswith(".json"):
            short = rel[len("blockstates/"):-len(".json")]
            try:
                blockstates[short] = json.loads(zf.read(n).decode("utf-8"))
            except Exception:
                continue
        elif rel.startswith("models/") and rel.endswith(".json"):
            mshort = rel[len("models/"):-len(".json")]
            try:
                models[mshort] = json.loads(zf.read(n).decode("utf-8"))
            except Exception:
                continue
        elif rel.startswith("textures/") and rel.endswith(".png"):
            tshort = rel[len("textures/"):-len(".png")]
            try:
                textures[tshort] = zf.read(n)
            except Exception:
                continue

    return {
        "blockstates": blockstates,
        "models": models,
        "textures": textures,
    }


__all__ = ["VanillaAssets", "tint_color", "export_assets"]
