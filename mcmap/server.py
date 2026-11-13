"""本地存档观察器服务。

启动后在浏览器打开 http://127.0.0.1:8770 即可浏览存档、框选区块、预览 3D 与导出 STL。
不依赖任何第三方 Web 框架。
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import socket
import sys
import threading
import time
import webbrowser
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image

from . import nbt, palette, render, texture, voxel, vanilla, geom, export, config, multicolor, height_range, decorations

ROOT = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VERSION = "ChunkCarve/1.0.5"
APPDIR = config.APPDIR   # 打包后在 exe 旁；否则=项目根目录
DATA_DIR = os.path.join(APPDIR, "data", "world")
REGION_DIR = config.world_region_dir(DATA_DIR)
WEB_DIR = os.path.join(ROOT, "web")


def _runtime_build_id() -> str:
    """Capture backend and web contents once, before a running source tree changes."""
    digest = hashlib.sha256()
    if config.FROZEN:
        files = [("application", sys.executable)]
    else:
        package_dir = os.path.dirname(os.path.abspath(__file__))
        files = [("mcmap/" + name, os.path.join(package_dir, name))
                 for name in sorted(os.listdir(package_dir)) if name.endswith('.py')]
    for parent, dirs, names in os.walk(WEB_DIR):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(parent, name)
            files.append((os.path.relpath(path, ROOT).replace('\\', '/'), path))
    for name, path in files:
        digest.update(name.encode('utf-8') + b'\0')
        with open(path, 'rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest()


API_REVISION = 1
RUNTIME_BUILD_ID = _runtime_build_id()


def capabilities() -> dict:
    return {"service": "chunkcarve", "version": VERSION, "api_revision": API_REVISION,
            "build_id": RUNTIME_BUILD_ID, "app_dir": os.path.realpath(APPDIR),
            "features": ["selection", "preview"]}

# 原版材质 jar / 模组目录：由 config 模块解析（CLI/env/config.json/自动探测），
# 不再写死任何机器路径。configure() 会在启动时覆盖。
_VANILLA_JAR = ""
_MODS_DIR = ""
_vanilla_assets = None


def configure(world_dir: str | None = None, jar: str | None = None,
              mods_dir: str | None = None) -> dict:
    """应用路径配置（存档/原版jar/模组目录），返回解析结果。"""
    global DATA_DIR, REGION_DIR, _VANILLA_JAR, _MODS_DIR, WORLD
    cfg = config.resolve(world=world_dir, jar=jar, mods=mods_dir)
    if cfg["world"]:
        DATA_DIR = cfg["world"]
        REGION_DIR = config.world_region_dir(DATA_DIR)
        WORLD = World()   # 重新加载 level.dat
    if cfg["jar"]:
        _VANILLA_JAR = cfg["jar"]
    if cfg["mods"]:
        _MODS_DIR = cfg["mods"]
    _init_vanilla()
    return cfg


def _scan_mod_namespaces(jar_path: str) -> list[str]:
    """扫一个模组 jar，返回它包含的资源命名空间（assets/<ns>/ 前缀）。"""
    try:
        import zipfile as _zip
        zf = _zip.ZipFile(jar_path)
        ns = set()
        for n in zf.namelist():
            if n.startswith("assets/") and n.count("/") >= 2:
                parts = n.split("/")
                if len(parts) >= 2 and parts[0] == "assets":
                    ns.add(parts[1])
        return sorted(ns)
    except Exception:
        return []


_loaded_vanilla_key: tuple[str, str] | None = None


def _init_vanilla(force: bool = False):
    global _vanilla_assets, _loaded_vanilla_key
    if not _VANILLA_JAR or not os.path.exists(_VANILLA_JAR):
        return
    mods_dir = _MODS_DIR if _MODS_DIR and os.path.isdir(_MODS_DIR) else ""
    key = (os.path.realpath(_VANILLA_JAR), os.path.realpath(mods_dir) if mods_dir else "")
    if not force and _vanilla_assets is not None and _loaded_vanilla_key == key:
        return
    try:
        va = vanilla.VanillaAssets(_VANILLA_JAR)
        if mods_dir:
            instance_dir = os.path.dirname(mods_dir)
            # 1. 扫描材质包/资源包 resourcepacks/，优先加载（最高优先级）
            candidate_rp_dirs = [
                os.path.join(instance_dir, "resourcepacks"),
                os.path.join(os.path.dirname(instance_dir), "resourcepacks"),
            ]
            for rp_candidate in candidate_rp_dirs:
                if os.path.isdir(rp_candidate):
                    try:
                        for entry in sorted(os.listdir(rp_candidate)):
                            p = os.path.join(rp_candidate, entry)
                            if entry.lower().endswith(".zip") or os.path.isdir(p):
                                va.add_resource_pack(p)
                    except Exception:
                        pass

            # 2. kubejs 自定义方块的资源在文件夹里（不在 jar），单独注册
            _kubejs_assets = os.path.join(instance_dir, "kubejs", "assets")
            if os.path.isdir(_kubejs_assets):
                va.add_mod_dir(_kubejs_assets, "kubejs")

            # 3. 扫描整合包 mods，注册模组命名空间
            for fname in os.listdir(mods_dir):
                if not fname.endswith(".jar"):
                    continue
                jp = os.path.join(mods_dir, fname)
                for ns in _scan_mod_namespaces(jp):
                    if ns not in ("realms",):
                        va.add_mod_jar(jp, ns)

        _vanilla_assets = va
        _loaded_vanilla_key = key
        texture.set_assets(va)
    except Exception:
        pass


# 原样导出的资源缓存（deepslate 前端渲染用）
_assets_cache: dict | None = None
_assets_lock = threading.Lock()


def _get_assets():
    """懒加载：第一次调用时从 jar 原样导出全部资源并缓存（并发安全）。"""
    global _assets_cache
    if _assets_cache is not None:
        return _assets_cache
    with _assets_lock:
        if _assets_cache is not None:
            return _assets_cache
    jar = ""
    if _vanilla_assets is not None:
        jar = _vanilla_assets.jar_path
    elif _VANILLA_JAR and os.path.exists(_VANILLA_JAR):
        jar = _VANILLA_JAR
    if not jar:
        return {"blockstates": {}, "models": {}, "textureIds": []}
    exported = vanilla.export_assets(jar)
    # 贴图 id 列表（相对路径，去掉 textures/ 前缀，保留 block/ 等子目录）
    texture_ids = sorted(exported["textures"].keys())
    _assets_cache = {
        "blockstates": exported["blockstates"],
        "models": exported["models"],
        "textureIds": texture_ids,
        "textures": exported["textures"],
    }
    return _assets_cache

MAX_TILE_CHUNKS = 65536    # 远景范围；输出采样栅格限制为约 100 万像素
MAX_SURFACE_CHUNKS = 256   # 3D 预览最多 16×16 区块
MAX_VOXEL_CHUNKS = 64      # 体素 2.5D 最多 8×8 区块（体素量大，需更严）
DEFAULT_PORT = 8770

_tile_cache: dict[tuple, bytes] = {}
_cache_lock = threading.Lock()


def list_worlds() -> list[dict]:
    """列出候选存档：当前存档 + 已探测 .minecraft/saves 下的存档。"""
    out: list[dict] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        try:
            rp = os.path.realpath(path)
        except Exception:
            return
        region = config.world_region_dir(rp)
        if rp in seen or not os.path.isdir(region):
            return
        seen.add(rp)
        name = os.path.basename(rp.rstrip("\\/")) or rp
        try:
            lvl = nbt.load_file(os.path.join(rp, "level.dat"))
            name = (lvl.get("Data", {}) or {}).get("LevelName", name) or name
        except Exception:
            pass
        try:
            regions = sum(1 for f in os.listdir(region)
                          if f.endswith(".mca"))
        except Exception:
            regions = 0
        out.append({"path": rp, "name": name, "regions": regions})

    if os.path.isdir(REGION_DIR):
        add(DATA_DIR)
    for mc in config.detect_minecraft_dirs():
        saves = os.path.join(mc, "saves")
        if os.path.isdir(saves):
            try:
                for d in sorted(os.listdir(saves)):
                    add(os.path.join(saves, d))
            except Exception:
                continue
    return out


_switch_lock = threading.RLock()


def switch_world(path: str, jar: str | None = None, mods: str | None = None) -> dict:
    """切换当前存档及相关资源：原子地 校验 → 重载 → 清渲染缓存 → 持久化到 config.json。"""
    if not path or not os.path.isdir(config.world_region_dir(path)):
        raise ValueError("存档目录无效（未找到主世界 region 地形目录）")
    with _switch_lock:
        global _MODS_DIR
        if mods is None:
            _MODS_DIR = ""
        cfg = configure(world_dir=path, jar=jar, mods_dir=mods)
        _init_vanilla(force=True)
        global _assets_cache
        with _assets_lock:
            _assets_cache = None
        render.clear_cache()
        with _cache_lock:
            _tile_cache.clear()
        try:
            config.write_config_file(cfg["world"], cfg.get("jar"), cfg.get("mods"))
        except Exception:
            pass
        return WORLD.info()


def open_project(proj: dict) -> dict:
    """打开指定项目：配置存档、版本 jar、模组目录并重载资源与世界。"""
    world = proj.get("world")
    jar = proj.get("jar")
    mods = proj.get("mods")
    pid = proj.get("id")
    if not world or not os.path.isdir(config.world_region_dir(world)):
        raise ValueError("项目存档路径无效或缺少主世界地形，请在项目中心重新选择存档")
    info = switch_world(world, jar or None, mods or None)
    saved = proj
    try:
        proj["updatedAt"] = time.strftime("%Y-%m-%d %H:%M")
        saved = config.save_project(proj)
        pid = saved.get("id") or pid
        if pid:
            config.set_active_project_id(pid)
    except Exception:
        pass
    return {
        "ok": True,
        "project": saved,
        "world": info,
        "activeId": pid,
    }


def _cleanup(path: str) -> None:
    """删除导出产生的临时文件（失败忽略）。"""
    try:
        os.remove(path)
    except OSError:
        pass


class World:
    def __init__(self):
        self.level = config.read_world_level(DATA_DIR)
        d = self.level.get("Data", {})
        self.name = d.get("LevelName", "world")
        self.version = d.get("Version", {}).get("Name", "?")
        self.data_version = d.get("DataVersion", 0)
        self.seed = d.get("WorldGenSettings", {}).get("seed", d.get("RandomSeed", 0))
        self.spawn = (d.get("SpawnX", 0), d.get("SpawnY", 64), d.get("SpawnZ", 0))
        self.modded = bool(self.level.get("fml")) or bool(d.get("WasModded"))

    def height_limits(self):
        data = self.level.get('Data', {})
        dimension = data.get('WorldGenSettings', {}).get('dimensions', {}).get('minecraft:overworld', {})
        kind = dimension.get('type') if isinstance(dimension, dict) else None
        if isinstance(kind, dict) and isinstance(kind.get('min_y'), int) and isinstance(kind.get('height'), int) and kind['height'] > 0:
            return kind['min_y'], kind['min_y'] + kind['height'] - 1
        return (0, 255) if 0 < self.data_version < 2825 else (-64, 319)

    def suggested_height(self, cx0, cz0, cx1, cz1):
        return height_range.suggest(REGION_DIR, cx0, cz0, cx1, cz1, self.spawn[1], *self.height_limits())

    def get_players(self) -> list[dict]:
        """解析存档内单人及多人玩家的最后位置与昵称，供选区快捷定位。"""
        uuid_names: dict[str, str] = {}
        if DATA_DIR and os.path.isdir(DATA_DIR):
            anc = [os.path.abspath(DATA_DIR)]
            for _ in range(5):
                parent = os.path.dirname(anc[-1])
                if not parent or parent == anc[-1]:
                    break
                anc.append(parent)
            for d in anc:
                if not d or not os.path.isdir(d):
                    continue
                uc_forge = os.path.join(d, "usernamecache.json")
                if os.path.isfile(uc_forge):
                    try:
                        with open(uc_forge, "r", encoding="utf-8") as f:
                            mp = json.load(f)
                        if isinstance(mp, dict):
                            for k, v in mp.items():
                                if isinstance(k, str) and isinstance(v, str) and v:
                                    uuid_names[k.lower().replace("-", "")] = v
                    except Exception:
                        pass
                uc_mc = os.path.join(d, "usercache.json")
                if os.path.isfile(uc_mc):
                    try:
                        with open(uc_mc, "r", encoding="utf-8") as f:
                            arr = json.load(f)
                        if isinstance(arr, list):
                            for item in arr:
                                if isinstance(item, dict) and item.get("uuid") and item.get("name"):
                                    uuid_names[str(item["uuid"]).lower().replace("-", "")] = str(item["name"])
                    except Exception:
                        pass

        def _parse_dim(raw_dim) -> str:
            if raw_dim in (None, 0, "0", "minecraft:overworld"):
                return "overworld"
            if raw_dim in (-1, "-1", "minecraft:the_nether"):
                return "the_nether"
            if raw_dim in (1, "1", "minecraft:the_end"):
                return "the_end"
            return str(raw_dim)

        players: list[dict] = []
        seen_coords: dict[tuple[int, int, int, str], int] = {}

        # 1. players/data/*.dat（26.1+）或 playerdata/*.dat（带 UUID，可匹配真实玩家昵称）
        pd_dir = os.path.join(DATA_DIR, "players", "data")
        if not os.path.isdir(pd_dir):
            pd_dir = os.path.join(DATA_DIR, "playerdata")
        if os.path.isdir(pd_dir):
            try:
                for fn in sorted(os.listdir(pd_dir)):
                    if not fn.endswith(".dat") or fn.endswith("_old.dat"):
                        continue
                    uid_raw = fn[:-4]
                    uid_clean = uid_raw.lower().replace("-", "")
                    try:
                        pdat = nbt.load_file(os.path.join(pd_dir, fn))
                        pos = pdat.get("Pos")
                        if isinstance(pos, (list, tuple)) and len(pos) >= 3:
                            px, py, pz = int(round(float(pos[0]))), int(round(float(pos[1]))), int(round(float(pos[2])))
                            dim = _parse_dim(pdat.get("Dimension"))
                            pname = uuid_names.get(uid_clean) or f"玩家 ({uid_raw[:8]})"
                            ckey = (px, py, pz, dim)
                            if ckey not in seen_coords:
                                seen_coords[ckey] = len(players)
                                players.append({
                                    "name": pname,
                                    "uuid": uid_raw,
                                    "x": px, "y": py, "z": pz,
                                    "dimension": dim,
                                })
                    except Exception:
                        continue
            except Exception:
                pass

        # 2. level.dat -> Data -> Player（单人主机玩家）
        sp = (self.level.get("Data", {}) or {}).get("Player")
        if isinstance(sp, dict):
            pos = sp.get("Pos")
            if isinstance(pos, (list, tuple)) and len(pos) >= 3:
                try:
                    px, py, pz = int(round(float(pos[0]))), int(round(float(pos[1]))), int(round(float(pos[2])))
                    dim = _parse_dim(sp.get("Dimension"))
                    ckey = (px, py, pz, dim)
                    if ckey in seen_coords:
                        idx = seen_coords[ckey]
                        if players[idx]["name"].startswith("玩家 ("):
                            players[idx]["name"] = f"主玩家 · {players[idx]['name']}"
                    else:
                        players.insert(0, {
                            "name": "存档玩家",
                            "uuid": "singleplayer",
                            "x": px, "y": py, "z": pz,
                            "dimension": dim,
                        })
                except Exception:
                    pass

        return players

    def info(self) -> dict:
        regions = 0
        if os.path.isdir(REGION_DIR):
            regions = sum(1 for n in os.listdir(REGION_DIR)
                          if n.endswith(".mca")
                          and os.path.getsize(os.path.join(REGION_DIR, n)) >= 8192)
        return {
            "name": self.name,
            "version": self.version,
            "dataVersion": self.data_version,
            "seed": str(self.seed),
            "spawn": {"x": self.spawn[0], "y": self.spawn[1], "z": self.spawn[2]},
            "spawnChunk": {"cx": self.spawn[0] // 16, "cz": self.spawn[2] // 16},
            "players": self.get_players(),
            "modded": self.modded,
            "regions": regions,
            "regionDir": REGION_DIR,
            "minY": self.height_limits()[0], "maxY": self.height_limits()[1],
        }

    def tile(self, cx0: int, cz0: int, cx1: int, cz1: int,
             px: int, layer: str) -> bytes:
        n = (cx1 - cx0) * (cz1 - cz0)
        if cx1 <= cx0 or cz1 <= cz0 or n > MAX_TILE_CHUNKS:
            raise ValueError(f"地图范围超出限制 {MAX_TILE_CHUNKS} 区块")
        width, height = (cx1 - cx0) * 16, (cz1 - cz0) * 16
        sample_step = 1
        while (width // sample_step) * (height // sample_step) > 1024 * 1024:
            sample_step *= 2
        key = (os.path.realpath(DATA_DIR), cx0, cz0, cx1, cz1, px, layer)
        with _cache_lock:
            hit = _tile_cache.get(key)
        if hit is not None:
            return hit

        hm = render.render(REGION_DIR, cx0, cz0, cx1, cz1, None,
                           show_grid=False, shade=(layer != "flat"), sample_step=sample_step)
        img = hm.image.convert("RGB")

        if layer == "height":
            img = Image.fromarray(_height_img(hm), mode="RGB")

        w, h = img.size
        if px and px != w:
            scale = px / w
            img = img.resize((px, max(1, int(round(h * scale)))), Image.NEAREST)

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=False, compress_level=1)
        data = buf.getvalue()
        with _cache_lock:
            if len(_tile_cache) > 400:
                _tile_cache.clear()
            _tile_cache[key] = data
        return data

    def surface(self, cx0: int, cz0: int, cx1: int, cz1: int,
                layers: int, y0: int | None = None,
                y1: int | None = None) -> dict:
        n = (cx1 - cx0) * (cz1 - cz0)
        if n <= 0 or n > MAX_SURFACE_CHUNKS:
            raise ValueError(f"选区 {n} 区块超出 2.5D 上限 {MAX_SURFACE_CHUNKS}")
        hm = render.render(REGION_DIR, cx0, cz0, cx1, cz1, None, show_grid=False)
        valid = hm.height[hm.height > -900]
        s = voxel.make_surface(hm.height, _rgba_of(hm), hm.water,
                               cx0 * 16, cz0 * 16, layers=layers, y0=y0, y1=y1)
        atlas, tid, nat, dsoil, rrock = _atlas_and_ids(hm.name_id, hm.height > -900)
        hb = (s.height.astype(np.int16) - s.y_base).clip(0, 255).astype(np.uint8)
        return {
            "x0": s.x0, "z0": s.z0, "w": s.width, "d": s.depth,
            "yBase": int(s.y_base), "yTop": int(s.y_top),
            "terrainMin": int(valid.min()) if valid.size else 0,
            "terrainMax": int(valid.max()) if valid.size else 0,
            "h": base64.b64encode(zlib.compress(hb.tobytes(), 6)).decode(),
            "tid": base64.b64encode(zlib.compress(tid.tobytes(), 6)).decode(),
            "water": base64.b64encode(
                zlib.compress(s.water.astype(np.uint8).tobytes(), 6)).decode(),
            "atlas": atlas,
            "nat": nat,               # 每个方块是否为天然地表
            "dirt": dsoil,            # 泥土侧面图集序号
            "rock": rrock,            # 石头侧面图集序号
        }

    def export_stl(self, cx0: int, cz0: int, cx1: int, cz1: int,
                   layers: int, scale: float,
                   y0: int | None = None, y1: int | None = None
                   ) -> tuple[bytes, str, int]:
        n = (cx1 - cx0) * (cz1 - cz0)
        if n > MAX_SURFACE_CHUNKS:
            raise ValueError(f"导出范围 {n} 区块过大，请缩小选区或分批导出")
        hm = render.render(REGION_DIR, cx0, cz0, cx1, cz1, None, show_grid=False)
        s = voxel.make_surface(hm.height, _rgba_of(hm), hm.water,
                               cx0 * 16, cz0 * 16, layers=layers, y0=y0, y1=y1)
        verts, _cols = voxel.build_triangles(s, scale=scale)
        path = os.path.join(APPDIR, "out", f"chunk_{cx0}_{cz0}_{cx1}_{cz1}.stl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tris = voxel.write_stl(verts, path, name=f"mc {cx0},{cz0}")
        with open(path, "rb") as f:
            data = f.read()
        _cleanup(path)   # 临时文件用后即删
        return data, os.path.basename(path), tris

    def export_stl_voxel(self, cx0: int, cz0: int, cx1: int, cz1: int,
                         scale: float,
                         y0: int | None = None, y1: int | None = None,
                         box: tuple[int, int, int, int] | None = None
                         ) -> tuple[bytes, str, int]:
        """体素化 STL 导出：忠实还原方块结构（侧面凹凸、洞穴、悬空都保留）。

        与 export_stl（高度图向下填实，侧面实心）不同，这里用体素数据做
        逐方块面剔除，只为暴露在空气的面生成三角形。去掉水面。
        box=(x0,z0,x1,z1) 为方块级精确切片（非整区块）。
        """
        n = (cx1 - cx0) * (cz1 - cz0)
        if n <= 0 or n > MAX_VOXEL_CHUNKS:
            raise ValueError(f"导出范围 {n} 区块超出体素上限 {MAX_VOXEL_CHUNKS}")

        _y0 = y0 if y0 is not None else render.Y_MIN
        _y1 = y1 if y1 is not None else render.Y_MAX
        xyz, ids, flags = render.extract_voxels(
            REGION_DIR, cx0, cz0, cx1, cz1, _y0, _y1)

        # 方块级切片：裁剪到精确 XZ 范围
        if box is not None and ids.size:
            bx0, bz0, bx1, bz1 = box
            m = ((xyz[:, 0] >= bx0) & (xyz[:, 0] <= bx1)
                 & (xyz[:, 2] >= bz0) & (xyz[:, 2] <= bz1))
            xyz, ids, flags = xyz[m], ids[m], flags[m]

        # 去掉水面（flags==1），只保留实体方块（flags==0）
        if ids.size:
            solid = flags == 0
            xyz = xyz[solid]
            ids = ids[solid]

        if ids.size == 0:
            raise ValueError("选区内没有可导出的实体方块")

        # 每个方块的颜色（用 palette 基础色；STL 单色，颜色仅用于 OBJ 查看）
        colors = np.zeros((len(ids), 3), dtype=np.uint8)
        for i, pid in enumerate(ids):
            colors[i] = palette.resolve(render.id_name(int(pid)))[:3]

        verts, _cols = voxel.build_voxel_mesh(xyz, colors, scale=scale)
        if verts.size == 0:
            raise ValueError("选区内没有可导出的实体方块")
        path = os.path.join(APPDIR, "out", f"voxel_{cx0}_{cz0}_{cx1}_{cz1}.stl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tris = voxel.write_stl(verts, path, name=f"mc voxel {cx0},{cz0}")
        with open(path, "rb") as f:
            data = f.read()
        _cleanup(path)
        return data, os.path.basename(path), tris

    def export_3mf_voxel(self, cx0: int, cz0: int, cx1: int, cz1: int,
                         scale: float, color_count: int = 4,
                         y0: int | None = None, y1: int | None = None,
                         box: tuple[int, int, int, int] | None = None
                         ) -> tuple[bytes, str, dict]:
        """以实体体素外壳生成少色 AMS 3MF，范围与单色 STL 一致。"""
        n = (cx1 - cx0) * (cz1 - cz0)
        if n <= 0 or n > MAX_VOXEL_CHUNKS:
            raise ValueError(f"导出范围 {n} 区块超出体素上限 {MAX_VOXEL_CHUNKS}")
        if color_count not in (4, 8):
            raise ValueError("AMS 色数必须是 4 或 8")
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError("比例必须是大于 0 的有限数值")
        xyz, ids, flags = render.extract_voxels(
            REGION_DIR, cx0, cz0, cx1, cz1,
            y0 if y0 is not None else render.Y_MIN,
            y1 if y1 is not None else render.Y_MAX)
        keep = flags == 0
        if box is not None and ids.size:
            bx0, bz0, bx1, bz1 = box
            keep &= ((xyz[:, 0] >= bx0) & (xyz[:, 0] <= bx1) &
                     (xyz[:, 2] >= bz0) & (xyz[:, 2] <= bz1))
        xyz, ids = xyz[keep], ids[keep]
        if not ids.size:
            raise ValueError("选区内没有可导出的实体方块")
        colors = np.asarray([palette.resolve(render.id_name(int(pid)))[:3]
                             for pid in ids], dtype=np.uint8)
        data, stats = multicolor.export_3mf(
            xyz, colors, color_count=color_count, scale=scale)
        return data, f"chunkcarve_ams_{color_count}.3mf", stats

    def export_obj_voxel(self, cx0: int, cz0: int, cx1: int, cz1: int,
                         scale: float, include_detail: bool,
                         y0: int | None = None, y1: int | None = None,
                         box: tuple[int, int, int, int] | None = None,
                         model_format: str = "obj", include_decorations: bool = False,
                         subdivide: int = 1,
                         ) -> tuple[bytes, str, dict]:
        """OBJ + MTL + PNG 或内嵌贴图 GLB 导出。

        完整方块用真实三面贴图，非完整方块（include_detail=True 时）用
        解析出的真实几何体。返回 (zip bytes, 文件名, 统计)。
        box=(x0,z0,x1,z1) 为方块级精确切片（非整区块）。
        subdivide>1 时将暴露面正交细分为 subdivide×subdivide 微网格，消除切片软件斜切锯齿。
        """
        n = (cx1 - cx0) * (cz1 - cz0)
        if n <= 0 or n > MAX_VOXEL_CHUNKS:
            raise ValueError(f"导出范围 {n} 区块超出体素上限 {MAX_VOXEL_CHUNKS}")

        if not np.isfinite(scale) or scale <= 0:
            raise ValueError("比例必须是大于 0 的有限数值")
        if subdivide < 1 or subdivide > 16:
            raise ValueError("网格细分倍数必须在 1 到 16 之间")

        _y0 = y0 if y0 is not None else render.Y_MIN
        _y1 = y1 if y1 is not None else render.Y_MAX
        xyz, ids, flags, state_table = render.extract_voxels_states(
            REGION_DIR, cx0, cz0, cx1, cz1, _y0, _y1)

        # 方块级切片：裁剪到精确 XZ 范围
        if box is not None and ids.size:
            bx0, bz0, bx1, bz1 = box
            m = ((xyz[:, 0] >= bx0) & (xyz[:, 0] <= bx1)
                 & (xyz[:, 2] >= bz0) & (xyz[:, 2] <= bz1))
            xyz, ids, flags = xyz[m], ids[m], flags[m]

        if ids.size == 0:
            raise ValueError("选区内没有可导出的方块")

        x0 = box[0] if box is not None else cx0 * 16
        z0 = box[1] if box is not None else cz0 * 16

        # 面剔除：建立「实体完整方块」占用表（只有 flags==0 的完整方块遮挡面）
        solid_occ: set[tuple[int, int, int]] = set()
        for i in range(xyz.shape[0]):
            if int(flags[i]) == 0:
                solid_occ.add((int(xyz[i, 0]), int(xyz[i, 1]), int(xyz[i, 2])))

        solid_tris: list[dict] = []
        detail_tris: list[dict] = []
        detail_models: dict[int, list[dict]] = {}
        detail_instances = []
        skipped_decorations = 0

        for i in range(xyz.shape[0]):
            bx = int(xyz[i, 0]) - x0
            by = int(xyz[i, 1])
            bz = int(xyz[i, 2]) - z0
            state = int(ids[i])
            name, properties = state_table[state]
            fl = int(flags[i])
            cx, cy, cz = int(xyz[i, 0]), int(xyz[i, 1]), int(xyz[i, 2])

            if fl == 1:
                continue  # 水不导出（多色打印水体意义不大）
            if fl == 2:
                # 非完整方块：不遮挡邻居，直接导出全部几何
                if not include_detail or _vanilla_assets is None:
                    continue
                if state not in detail_models:
                    g = _vanilla_assets.parse_elements(name, properties or {})
                    if decorations.is_sheet_model(g):
                        detail_models[state] = (decorations.printable_quads(g, _vanilla_assets, scale)
                                                if include_decorations else None)
                    else:
                        detail_models[state] = geom.geometry_to_tris(g, cap_missing=True) if g is not None else []
                if detail_models[state] is None:
                    skipped_decorations += 1
                    continue
                offset = np.array([bx, by, bz], dtype=np.float64) * scale
                for q in detail_models[state]:
                    v = (np.asarray(q["verts"], dtype=np.float64) * scale + offset).tolist()
                    detail_tris.append({
                        "verts": v,
                        "uvs": q["uvs"],
                        "tex": q["tex"],
                    })
            else:
                # 完整方块：真实三面贴图 + 面剔除
                if _vanilla_assets is not None:
                    top, side, bottom = _vanilla_assets.get_textures(name)
                else:
                    top = side = bottom = None
                if not top or not side or not bottom:
                    # 无贴图：用 palette 颜色兜底（导出层用占位色）
                    top = side = bottom = f"_fallback/{name}"
                faces = []
                # 顶面
                if (cx, cy + 1, cz) not in solid_occ:
                    faces.append(("up", top))
                # 底面
                if (cx, cy - 1, cz) not in solid_occ:
                    faces.append(("down", bottom))
                # 侧面：东(+X)、西(-X)、南(+Z)、北(-Z) 精确暴露判定，避免内壁穿插与重复生成
                if (cx + 1, cy, cz) not in solid_occ:
                    faces.append(("east", side))
                if (cx - 1, cy, cz) not in solid_occ:
                    faces.append(("west", side))
                if (cx, cy, cz + 1) not in solid_occ:
                    faces.append(("south", side))
                if (cx, cy, cz - 1) not in solid_occ:
                    faces.append(("north", side))
                s_tris = geom.full_cube_tris_culled(
                    faces, scale=scale,
                    origin=(bx * scale, by * scale, bz * scale))
                solid_tris.extend(s_tris)

        if not solid_tris and not detail_tris:
            raise ValueError("选区内没有可导出的方块")

        if model_format == "glb":
            model_name = f"mc_{cx0}_{cz0}_{cx1}_{cz1}"
            data, stats = export.export_glb(
                _vanilla_assets, solid_tris + detail_tris, model_name,
                subdivide=subdivide)
            stats['skipped_decorations'] = skipped_decorations
            return data, model_name + ".glb", stats

        zip_path = os.path.join(APPDIR, "out",
                                f"voxel_obj_{cx0}_{cz0}_{cx1}_{cz1}.zip")
        os.makedirs(os.path.dirname(zip_path), exist_ok=True)
        stats = export.export_obj_zip(
            _vanilla_assets, solid_tris, detail_tris, scale, zip_path,
            model_name=f"mc_{cx0}_{cz0}_{cx1}_{cz1}",
            subdivide=subdivide)
        stats['skipped_decorations'] = skipped_decorations
        with open(zip_path, "rb") as f:
            data = f.read()
        _cleanup(zip_path)   # 临时文件用后即删
        return data, os.path.basename(zip_path), stats

    def export_batch_slices(self, box: tuple[int, int, int, int, int, int],
                            axis: str = "y", mode: str = "batch",
                            thickness: float = 2.0, scale: float = 2.0,
                            stand: bool = False, step: int = 1,
                            slice_index: int | None = None,
                            model_format: str = "glb", color_count: int = 4,
                            slice_indices: list[int] | None = None,
                            source_region=None, source_assets=None) -> tuple[bytes, dict]:
        """提取选区体素并调用切片批处理引擎。"""
        x0, z0, x1, z1, y0, y1 = box
        cx0, cx1 = x0 // 16, x1 // 16 + 1
        cz0, cz1 = z0 // 16, z1 // 16 + 1
        n = (cx1 - cx0) * (cz1 - cz0)
        if n <= 0 or n > MAX_VOXEL_CHUNKS:
            raise ValueError(f"导出范围 {n} 区块超出体素上限 {MAX_VOXEL_CHUNKS}")
        xyz, ids, _flags = render.extract_voxels(
            source_region or REGION_DIR, cx0, cz0, cx1, cz1, y0, y1)
        if ids.size:
            keep = ((xyz[:, 0] >= x0) & (xyz[:, 0] <= x1) &
                    (xyz[:, 2] >= z0) & (xyz[:, 2] <= z1) &
                    (xyz[:, 1] >= y0) & (xyz[:, 1] <= y1))
            xyz, ids = xyz[keep], ids[keep]
        names = [render.id_name(int(pid)) for pid in ids]
        return export.export_batch_slices(
            xyz, names, box=box, axis=axis, step=step,
            thickness=thickness, scale=scale, assets=source_assets or _vanilla_assets,
            mode=mode, slice_index=slice_index, stand=stand,
            model_format=model_format, color_count=color_count, slice_indices=slice_indices)

    def voxels(self, cx0: int, cz0: int, cx1: int, cz1: int,
               y0: int | None = None, y1: int | None = None) -> dict:
        """返回选区内所有实体方块的体素数据，供 2.5D 真实体素渲染。

        与 surface/STL 的「向下填实」不同，这里忠实返回每个方块的位置与类型，
        房子下方的悬空、水面、岩层层次都原样保留。
        """
        n = (cx1 - cx0) * (cz1 - cz0)
        if n <= 0 or n > MAX_VOXEL_CHUNKS:
            raise ValueError(f"选区 {n} 区块超出 2.5D 上限 {MAX_VOXEL_CHUNKS}")

        _y0 = y0 if y0 is not None else render.Y_MIN
        _y1 = y1 if y1 is not None else render.Y_MAX
        xyz, ids, flags = render.extract_voxels(
            REGION_DIR, cx0, cz0, cx1, cz1, _y0, _y1)

        # 只统计出现的方块，构建图集
        present = np.unique(ids) if ids.size else np.empty(0, dtype=np.int16)
        names = [render.id_name(int(i)) for i in present]
        atlas, top_idx, side_idx, bot_idx, nat, wat, dirt, rock = _voxel_atlas(names)

        # 把 name_id 映射成图集里的 top 索引（体素里用 top 索引，side=top+1，bottom=top+2）
        lut = {}
        for i, pid in enumerate(present):
            lut[int(pid)] = top_idx[names[i]]
        tile = np.array([lut.get(int(i), 0) for i in ids], dtype=np.uint16) \
            if ids.size else np.empty(0, dtype=np.uint16)

        # 非完整方块（flags==2）的几何数据：解析真实模型，生成三角面。
        # 原点用「选区中心」，与前端 buildMesh 的坐标约定一致（X = x - cx）。
        center_x = cx0 * 16 + (cx1 - cx0) * 8
        center_z = cz0 * 16 + (cz1 - cz0) * 8
        detail = self._detail_geometry(xyz, ids, flags, center_x, center_z)

        # 压缩传输：xyz 用差分 + zlib，tile/flags 直接 zlib
        return {
            "x0": cx0 * 16, "z0": cz0 * 16,
            "w": (cx1 - cx0) * 16, "d": (cz1 - cz0) * 16,
            "count": int(ids.size),
            "yMin": int(xyz[:, 1].min()) if ids.size else 0,
            "yMax": int(xyz[:, 1].max()) if ids.size else 0,
            "xyz": base64.b64encode(zlib.compress(xyz.tobytes(), 6)).decode(),
            "tile": base64.b64encode(zlib.compress(tile.tobytes(), 6)).decode(),
            "flags": base64.b64encode(zlib.compress(flags.tobytes(), 6)).decode(),
            "atlas": atlas,
            "waterTile": wat,
            "dirtTile": dirt,
            "rockTile": rock,
            "detail": detail,
        }

    def _detail_geometry(self, xyz, ids, flags, x0, z0) -> dict | None:
        """为 flags==2 的非完整方块解析真实模型几何，生成三角面数据。

        返回 None（无非完整方块或未加载资源）或 {
            "pos":   base64(顶点数组 float32，每面 12 顶点 = 4 角 * 3 坐标),
            "uv":    base64(UV 数组 float32，像素坐标 0~16),
            "tex":   [贴图短名...]（按面出现顺序，去重后为索引表）,
            "texId": base64(每面贴图索引 uint16),
            "atlas": dataURL（非完整方块专用图集）,
            "count": 面数,
        }
        """
        if _vanilla_assets is None or not ids.size:
            return None
        mask = flags == 2
        if not mask.any():
            return None

        detail_xyz = xyz[mask]
        detail_ids = ids[mask]

        # 逐方块解析几何，收集三角面
        all_verts: list[np.ndarray] = []
        all_uvs: list[np.ndarray] = []
        all_tex_names: list[str] = []
        tex_set: list[str] = []

        for i in range(detail_xyz.shape[0]):
            name = render.id_name(int(detail_ids[i]))
            g = _vanilla_assets.parse_elements(name)
            bx = int(detail_xyz[i, 0]) - x0
            by = int(detail_xyz[i, 1])
            bz = int(detail_xyz[i, 2]) - z0
            if g is None:
                # 解析失败（床/箱子/头颅等 entity 模型）：退化成完整立方体，
                # 用三面贴图兜底，至少不消失。
                top, side, bottom = _vanilla_assets.get_textures(name)
                side = side or top or "_fallback"
                tris = geom.full_cube_tris(
                    top or side, side, bottom or side,
                    scale=1.0, origin=(float(bx), float(by), float(bz)))
            else:
                tris = geom.geometry_to_tris(g, scale=1.0, origin=(float(bx), float(by), float(bz)))
            for t in tris:
                vs = np.array(t["verts"], dtype=np.float32)  # (4,3)
                uvs = np.array(t["uvs"], dtype=np.float32)   # (4,2) 像素坐标
                tri_verts = vs[[0, 1, 2, 0, 2, 3]]           # (6,3)
                tri_uvs = uvs[[0, 1, 2, 0, 2, 3]]            # (6,2)
                all_verts.append(tri_verts.reshape(-1))
                all_uvs.append(tri_uvs.reshape(-1, 2))
                if t["tex"] not in tex_set:
                    tex_set.append(t["tex"])
                all_tex_names.append(t["tex"])

        if not all_verts:
            return None

        verts = np.concatenate(all_verts).astype(np.float32)          # (M*3,)
        pixel_uvs = np.concatenate(all_uvs).astype(np.float32)        # (M*2, 2)
        # 面贴图索引
        tex_id = np.array([tex_set.index(tn) for tn in all_tex_names], dtype=np.uint16)

        # 构建非完整方块专用图集
        png, uv_map, cols, rows = export.build_texture_atlas(
            _vanilla_assets, tex_set)

        # 把像素 UV（0~16）映射到图集归一化坐标
        norm_uvs = np.zeros_like(pixel_uvs)
        for fi, tn in enumerate(all_tex_names):
            tu0, tv0, tu1, tv1 = uv_map[tn]
            tw, th = tu1 - tu0, tv1 - tv0
            base = fi * 6
            for k in range(6):
                pu = pixel_uvs[base + k, 0] / 16.0   # 归一化到 0~1
                pv = pixel_uvs[base + k, 1] / 16.0
                norm_uvs[base + k, 0] = tu0 + pu * tw
                norm_uvs[base + k, 1] = tv0 + pv * th

        return {
            "pos": base64.b64encode(zlib.compress(verts.tobytes(), 6)).decode(),
            "uv": base64.b64encode(zlib.compress(norm_uvs.tobytes(), 6)).decode(),
            "tex": tex_set,
            "texId": base64.b64encode(zlib.compress(tex_id.tobytes(), 6)).decode(),
            "atlas": "data:image/png;base64," + base64.b64encode(png).decode(),
            "count": int(len(all_tex_names)),
        }

    def voxels_states(self, cx0: int, cz0: int, cx1: int, cz1: int,
                      y0: int | None = None, y1: int | None = None,
                      surface_only: bool = True,
                      box: tuple[int, int, int, int] | None = None,
                      include_decorations: bool = True) -> dict:
        """返回选区内所有非空气方块的体素数据 + 完整 blockstate，供 deepslate 渲染。

        surface_only=True 时先做面剔除，只保留「有暴露面」的方块——
        完整地下约 80~90% 是被完全包围的内部实心方块，剔除后 deepslate
        内存降低 5~10 倍，可渲染 20 区块以上完整地下。
        box=(x0, z0, x1, z1) 为方块级精确范围（含端点）：非整区块切片，
        提取后裁剪到该范围，返回的 x0/z0/w/d 以 box 为准。
        """
        n = (cx1 - cx0) * (cz1 - cz0)
        if n <= 0 or n > MAX_VOXEL_CHUNKS:
            raise ValueError(f"选区 {n} 区块超出 2.5D 上限 {MAX_VOXEL_CHUNKS}")

        _y0 = y0 if y0 is not None else render.Y_MIN
        _y1 = y1 if y1 is not None else render.Y_MAX
        xyz, states, flags, state_table = render.extract_voxels_states(
            REGION_DIR, cx0, cz0, cx1, cz1, _y0, _y1)
        total = int(states.size)

        # 方块级切片：裁剪到精确 XZ 范围
        if box is not None:
            bx0, bz0, bx1, bz1 = box
            if states.size:
                m = ((xyz[:, 0] >= bx0) & (xyz[:, 0] <= bx1)
                     & (xyz[:, 2] >= bz0) & (xyz[:, 2] <= bz1))
                xyz, states, flags = xyz[m], states[m], flags[m]
            ox, oz, w, d = bx0, bz0, bx1 - bx0 + 1, bz1 - bz0 + 1
        else:
            ox, oz = cx0 * 16, cz0 * 16
            w, d = (cx1 - cx0) * 16, (cz1 - cz0) * 16

        if not include_decorations and states.size:
            excluded = set()
            for state in np.unique(states[flags == 2]):
                name, properties = state_table[int(state)]
                is_sheet = False
                if _vanilla_assets is not None:
                    is_sheet = decorations.is_sheet_model(_vanilla_assets.parse_elements(name, properties or {}))
                if is_sheet or palette.is_decorative(name):
                    excluded.add(int(state))
            keep = ~np.isin(states, list(excluded))
            xyz, states, flags = xyz[keep], states[keep], flags[keep]

        if surface_only and states.size:
            xyz, states, flags = render.filter_exposed(xyz, states, flags)

        # state_table: [(name, properties_dict_or_None)]
        names = [t[0] for t in state_table]
        props = [t[1] for t in state_table]
        # 标记每个 state 是否为「实体不完整方块」（栅栏/台阶/铁轨/床等），
        # 供前端「非完整方块」开关过滤。
        detail = [palette.is_detail(n) for n in names]
        # 装饰/植物标记：这些方块不能遮挡邻居（否则草会剔掉草方块顶面）
        decor = [palette.is_decorative(n) for n in names]

        return {
            "x0": ox, "z0": oz,
            "w": w, "d": d,
            "count": int(states.size),
            "total": total,           # 面剔除前的方块总数（供进度/统计）
            "yMin": int(xyz[:, 1].min()) if states.size else 0,
            "yMax": int(xyz[:, 1].max()) if states.size else 0,
            "xyz": base64.b64encode(zlib.compress(xyz.tobytes(), 6)).decode(),
            "states": base64.b64encode(zlib.compress(states.tobytes(), 6)).decode(),
            "flags": base64.b64encode(zlib.compress(flags.tobytes(), 6)).decode(),
            "names": names,
            "props": props,
            "detail": detail,
            "decor": decor,
        }


def _rgba_of(hm) -> np.ndarray:
    """从渲染结果取每格 RGB：图像本就是 1 像素 = 1 格。"""
    img = np.array(hm.image.convert("RGB"))
    h, w = hm.height.shape
    if img.shape[0] != h or img.shape[1] != w:
        img = np.array(Image.fromarray(img).resize((w, h), Image.NEAREST))
    return img.astype(np.uint8)


_atlas_cache: dict[tuple, tuple] = {}


def _atlas_and_ids(nameid, valid=None):
    """按选区里出现的方块，生成纹理图集并把每格映射成图集序号。

    图集里每个方块占相邻两格：(top, side)，所以 side 序号恒为 top+1，
    这样前端只需要一份 tid 数组即可同时贴顶面和侧面。
    无数据格用 0xFFFF 标记，前端直接跳过。

    另外固定附带「泥土 / 石头」两个侧面图块，供剖面的地层分层使用；
    并给出每个方块是否属于天然地表（决定侧壁画剖面还是整片同材质）。
    """
    nid = nameid if nameid is not None else np.zeros((1, 1), dtype=np.uint16)
    present = np.unique(nid)
    names = [render.id_name(int(i)) for i in present]
    key = tuple(names)
    hit = _atlas_cache.get(key)
    if hit is None:
        entries = texture.atlas_entries(names)
        extra = [(n, "side") for n in ("minecraft:dirt", "minecraft:stone")
                 if (n, "side") not in entries]
        entries = entries + extra
        img, idx, cols, rows = texture.build_atlas(entries)
        buf = BytesIO()
        img.save(buf, format="PNG", compress_level=6)
        lut = np.zeros(int(present.max()) + 1, dtype=np.uint16) if present.size else \
            np.zeros(1, dtype=np.uint16)
        for k, pid in enumerate(present):
            lut[pid] = 3 * k                     # top = 3k（atlas 每方块 3 格）
        hit = ("data:image/png;base64," +
               base64.b64encode(buf.getvalue()).decode(), lut,
               [1 if palette.is_natural(n) else 0 for n in names],
               idx[("minecraft:dirt", "side")], idx[("minecraft:stone", "side")])
        if len(_atlas_cache) > 8:
            _atlas_cache.clear()
        _atlas_cache[key] = hit
    data, lut, nat, dsoil, rrock = hit
    tid = lut[np.minimum(nid, len(lut) - 1)].astype(np.uint16)
    if valid is not None:
        tid = np.where(valid, tid, np.uint16(0xFFFF)).astype(np.uint16)
    return data, tid, nat, dsoil, rrock


_voxel_atlas_cache: dict[tuple, tuple] = {}


def _voxel_atlas(names: list[str]):
    """根据体素里出现的方块名列表，构建纹理图集 + 名称→图集序号映射。

    每个方块占相邻三格 (top, side, bottom)。额外附带水/泥土/石头侧面。
    返回 (dataURL, top->index dict, side->index dict, bottom->index, flags 描述)。
    """
    key = tuple(names)
    hit = _voxel_atlas_cache.get(key)
    if hit is not None:
        return hit
    entries = texture.atlas_entries(names)
    for n in ("minecraft:water", "minecraft:dirt", "minecraft:stone"):
        if (n, "side") not in entries:
            entries.append((n, "side"))
    img, idx, cols, rows = texture.build_atlas(entries)
    buf = BytesIO()
    img.save(buf, format="PNG", compress_level=6)
    data = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    top_idx = {n: idx[(n, "top")] for n in names}
    side_idx = {n: idx[(n, "side")] for n in names}
    bot_idx = {n: idx[(n, "bottom")] for n in names}
    nat = {n: (1 if palette.is_natural(n) else 0) for n in names}
    out = (data, top_idx, side_idx, bot_idx, nat,
           idx[("minecraft:water", "side")],
           idx[("minecraft:dirt", "side")],
           idx[("minecraft:stone", "side")])
    if len(_voxel_atlas_cache) > 8:
        _voxel_atlas_cache.clear()
    _voxel_atlas_cache[key] = out
    return out


def _height_img(hm) -> np.ndarray:
    h = hm.height
    valid = h > -900
    out = np.zeros((*h.shape, 3), dtype=np.uint8)
    stops = [
        (40, (32, 58, 120)), (62, (40, 110, 170)), (70, (86, 160, 130)),
        (90, (150, 200, 100)), (110, (222, 208, 120)), (130, (190, 140, 90)),
        (155, (170, 150, 150)), (185, (240, 240, 245)),
    ]
    hs = np.clip(h.astype(np.float32), stops[0][0], stops[-1][0])
    for i in range(len(stops) - 1):
        y0, c0 = stops[i]
        y1, c1 = stops[i + 1]
        m = (hs >= y0) & (hs <= y1)
        t = np.zeros_like(hs)
        t[m] = (hs[m] - y0) / max(1, (y1 - y0))
        for k in range(3):
            ch = (c0[k] + (c1[k] - c0[k]) * t).astype(np.uint8)
            out[:, :, k][m] = ch[m]
    out[~valid] = (16, 17, 20)
    return out


WORLD = World()

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = VERSION

    def log_message(self, fmt, *args):
        if os.environ.get("MCMAP_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _err(self, msg: str, code: int = 400):
        self._json({"error": str(msg)}, code)

    def _read_json(self) -> dict:
        clen = int(self.headers.get("Content-Length", 0))
        if clen <= 0:
            return {}
        body = self.rfile.read(clen)
        return json.loads(body.decode("utf-8")) if body else {}

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self._read_json()
            if u.path == "/api/project/open":
                return self._json(open_project(body))
            if u.path == "/api/projects/save":
                saved = config.save_project(body)
                return self._json({"ok": True, "project": saved})
            if u.path == "/api/projects/delete":
                ok = config.delete_project(body.get("id", ""))
                return self._json({"ok": ok})
            return self._err("未知的 POST 接口", 404)
        except ValueError as e:
            return self._err(str(e))
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._err(f"{type(e).__name__}: {e}", 500)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/modpacks":
                return self._json(config.detect_modpacks())
            if u.path == "/api/detect":
                return self._json(config.detect_instance_saves_and_assets(q.get("path", "")))
            if u.path in ("/favicon.ico", "/favicon.svg", "/icon.svg", "/icon.png"):
                ext = os.path.splitext(u.path)[1].lower()
                target_fname = "icon.ico" if ext == ".ico" else ("icon.svg" if ext == ".svg" else "icon.png")
                target_file = os.path.join(ROOT, "assets", target_fname)
                if not os.path.isfile(target_file):
                    target_file = os.path.join(WEB_DIR, target_fname)
                if os.path.isfile(target_file):
                    mime = MIME.get(ext, "image/x-icon" if ext == ".ico" else "application/octet-stream")
                    with open(target_file, "rb") as icf:
                        return self._send(200, icf.read(), mime)





            if u.path == "/api/projects":
                return self._json({
                    "projects": config.read_projects(),
                    "active": os.path.realpath(DATA_DIR) if os.path.exists(DATA_DIR) else None,
                    "activeId": config.get_active_project_id(),
                })
            if u.path == "/api/project/open":
                pid = q.get("id")
                projs = config.read_projects()
                target = None
                if pid:
                    target = next((p for p in projs if p.get("id") == pid), None)
                if not target:
                    target = {
                        "world": q.get("world", ""),
                        "jar": q.get("jar", ""),
                        "mods": q.get("mods", ""),
                        "name": q.get("name", "未命名项目")
                    }
                return self._json(open_project(target))
            if u.path == "/api/info":
                return self._json(WORLD.info())
            if u.path == '/api/height-range':
                if not all(key in q for key in ('cx0', 'cz0', 'cx1', 'cz1')):
                    raise ValueError('缺少地表高度探测范围')
                return self._json(WORLD.suggested_height(*(int(q[key]) for key in ('cx0', 'cz0', 'cx1', 'cz1'))))
            if u.path == "/api/capabilities":
                return self._json(capabilities())
            if u.path == "/api/diag":
                return self._json({
                    "version": VERSION,
                    "world": os.path.realpath(DATA_DIR),
                    "regions": WORLD.info().get("regions"),
                    "jar": _VANILLA_JAR or None,
                    "mods": _MODS_DIR or None,
                    "frozen": config.FROZEN,
                    "dataVersion": WORLD.info().get("dataVersion"),
                })
            if u.path == "/api/worlds":
                return self._json({"current": os.path.realpath(DATA_DIR),
                                   "worlds": list_worlds()})
            if u.path == "/api/world":
                try:
                    return self._json(switch_world(q.get("path", "")))
                except ValueError as e:
                    return self._err(str(e))
            if u.path == "/api/tile":
                return self._tile(q)
            if u.path == "/api/surface":
                return self._surface(q)
            if u.path == "/api/voxels":
                return self._voxels(q)
            if u.path == "/api/voxels_states":
                return self._voxels_states(q)
            if u.path == "/api/assets":
                return self._assets()
            if u.path == "/api/blockdefs":
                return self._blockdefs(q)
            if u.path.startswith("/api/texture/"):
                return self._texture(u.path[len("/api/texture/"):])
            if u.path == "/api/export.stl":
                return self._stl(q)
            if u.path == "/api/export_voxel.stl":
                return self._stl_voxel(q)
            if u.path == "/api/export_voxel.obj":
                return self._obj_voxel(q)
            if u.path == "/api/export_voxel.glb":
                return self._obj_voxel(q, model_format="glb")
            if u.path == "/api/export_voxel.3mf":
                return self._3mf_voxel(q)
            if u.path == "/api/export_slices.zip":
                return self._export_slices(q)
            return self._static(u.path)
        except ValueError as e:
            return self._err(e)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return self._err(f"{type(e).__name__}: {e}", 500)

    def _tile(self, q):
        cx0, cz0, cx1, cz1 = (int(q.get(k, 0)) for k in ("cx0", "cz0", "cx1", "cz1"))
        px = int(q.get("px", 512))
        layer = q.get("layer", "terrain")
        data = WORLD.tile(cx0, cz0, cx1, cz1, px, layer)
        self._send(200, data, "image/png")

    def _surface(self, q):
        cx0, cz0, cx1, cz1 = (int(q.get(k, 0)) for k in ("cx0", "cz0", "cx1", "cz1"))
        layers = int(q.get("layers", 0))
        y0 = int(q["y0"]) if "y0" in q and q["y0"] != "" else None
        y1 = int(q["y1"]) if "y1" in q and q["y1"] != "" else None
        self._json(WORLD.surface(cx0, cz0, cx1, cz1, layers, y0, y1))

    def _stl(self, q):
        cx0, cz0, cx1, cz1 = (int(q.get(k, 0)) for k in ("cx0", "cz0", "cx1", "cz1"))
        layers = int(q.get("layers", 0))
        scale = float(q.get("scale", 5))
        y0 = int(q["y0"]) if "y0" in q and q["y0"] != "" else None
        y1 = int(q["y1"]) if "y1" in q and q["y1"] != "" else None
        data, fname, tris = WORLD.export_stl(cx0, cz0, cx1, cz1, layers, scale, y0, y1)
        self._send(200, data, "model/stl", {
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Triangles": str(tris),
        })

    def _stl_voxel(self, q):
        cx0, cz0, cx1, cz1 = (int(q.get(k, 0)) for k in ("cx0", "cz0", "cx1", "cz1"))
        scale = float(q.get("scale", 5))
        y0 = int(q["y0"]) if "y0" in q and q["y0"] != "" else None
        y1 = int(q["y1"]) if "y1" in q and q["y1"] != "" else None
        box = self._box(q)
        data, fname, tris = WORLD.export_stl_voxel(
            cx0, cz0, cx1, cz1, scale, y0, y1, box)
        self._send(200, data, "model/stl", {
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Triangles": str(tris),
        })

    @staticmethod
    def _box(q) -> tuple[int, int, int, int] | None:
        """解析方块级切片参数（x0/z0/x1/z1），缺省返回 None。"""
        if all(k in q and q[k] != "" for k in ("x0", "z0", "x1", "z1")):
            bx0, bz0, bx1, bz1 = (int(q[k]) for k in ("x0", "z0", "x1", "z1"))
            return (min(bx0, bx1), min(bz0, bz1), max(bx0, bx1), max(bz0, bz1))
        return None

    def _obj_voxel(self, q, model_format="obj"):
        cx0, cz0, cx1, cz1 = (int(q.get(k, 0)) for k in ("cx0", "cz0", "cx1", "cz1"))
        scale = float(q.get("scale", 5))
        include_detail = q.get("detail", "1") != "0"
        y0 = int(q["y0"]) if "y0" in q and q["y0"] != "" else None
        y1 = int(q["y1"]) if "y1" in q and q["y1"] != "" else None
        box = self._box(q)
        subdivide = int(q.get("subdivide", 1)) if q.get("subdivide", "") != "" else 1
        data, fname, stats = WORLD.export_obj_voxel(
            cx0, cz0, cx1, cz1, scale, include_detail, y0, y1, box,
            model_format=model_format, include_decorations=q.get('decorations', '0') == '1',
            subdivide=subdivide)
        ctype = "model/gltf-binary" if model_format == "glb" else "application/zip"
        self._send(200, data, ctype, {
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Triangles": str(stats["triangles"]),
            "X-Textures": str(stats["textures"]),
            "X-Skipped-Decorations": str(stats['skipped_decorations']),
        })

    def _export_slices(self, q):
        required = ("x0", "z0", "x1", "z1", "y0", "y1")
        if not all(k in q and q[k] != "" for k in required):
            raise ValueError("缺少切片选区参数")
        x0, z0, x1, z1, y0, y1 = (int(q[k]) for k in required)
        box = (min(x0, x1), min(z0, z1), max(x0, x1), max(z0, z1),
               min(y0, y1), max(y0, y1))
        axis = q.get("axis", "y").lower()
        mode = q.get("mode", "batch").lower()
        thickness = float(q.get("thickness", 2.0))
        scale = float(q.get("scale", 2.0))
        step = int(q.get("step", 1))
        stand = q.get("stand", "0").lower() in ("1", "true", "yes")
        slice_index = int(q["index"]) if q.get("index", "") != "" else None
        model_format = q.get("format", "glb").lower()
        color_count = int(q.get("colors", 4))
        slice_indices = [int(value) for value in q.get("indices", "").split(",") if value != ""]
        data, manifest = WORLD.export_batch_slices(
            box, axis=axis, mode=mode, thickness=thickness, scale=scale,
            stand=stand, step=step, slice_index=slice_index,
            model_format=model_format, color_count=color_count, slice_indices=slice_indices)
        fname = f"chunkcarve_slices_{axis}_{model_format}.zip"
        self._send(200, data, "application/zip", {
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Slices": str(len(manifest["slices"])),
            "X-Model-Format": model_format,
        })

    def _3mf_voxel(self, q):
        cx0, cz0, cx1, cz1 = (int(q.get(k, 0)) for k in ("cx0", "cz0", "cx1", "cz1"))
        y0 = int(q["y0"]) if q.get("y0", "") != "" else None
        y1 = int(q["y1"]) if q.get("y1", "") != "" else None
        data, fname, stats = WORLD.export_3mf_voxel(
            cx0, cz0, cx1, cz1, float(q.get("scale", 2)),
            int(q.get("colors", 4)), y0, y1, self._box(q))
        self._send(200, data, "model/3mf", {
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Triangles": str(stats["triangles"]),
            "X-Colors": str(stats["color_count"]),
        })

    def _voxels(self, q):
        cx0, cz0, cx1, cz1 = (int(q.get(k, 0)) for k in ("cx0", "cz0", "cx1", "cz1"))
        y0 = int(q["y0"]) if "y0" in q and q["y0"] != "" else None
        y1 = int(q["y1"]) if "y1" in q and q["y1"] != "" else None
        self._json(WORLD.voxels(cx0, cz0, cx1, cz1, y0, y1))

    def _voxels_states(self, q):
        cx0, cz0, cx1, cz1 = (int(q.get(k, 0)) for k in ("cx0", "cz0", "cx1", "cz1"))
        y0 = int(q["y0"]) if "y0" in q and q["y0"] != "" else None
        y1 = int(q["y1"]) if "y1" in q and q["y1"] != "" else None
        surface_only = q.get("surface_only", "1") != "0"
        box = None
        if all(k in q and q[k] != "" for k in ("x0", "z0", "x1", "z1")):
            bx0, bz0, bx1, bz1 = (int(q[k]) for k in ("x0", "z0", "x1", "z1"))
            box = (min(bx0, bx1), min(bz0, bz1), max(bx0, bx1), max(bz0, bz1))
        self._json(WORLD.voxels_states(cx0, cz0, cx1, cz1, y0, y1, surface_only, box,
                                      include_decorations=q.get('decorations', '1') == '1'))

    def _assets(self):
        """返回原版资源（blockstates/models 原始 JSON + 贴图 id 列表）。"""
        a = _get_assets()
        payload = json.dumps({
            "blockstates": a["blockstates"],
            "models": a["models"],
            "textureIds": a["textureIds"],
        }, ensure_ascii=False, separators=(",", ":")).encode()
        gz = gzip.compress(payload, 6)
        self._send(200, gz, "application/json; charset=utf-8",
                   {"Content-Encoding": "gzip"})

    def _blockdefs(self, q):
        """按需返回一组方块名的 blockstate/模型/贴图闭包（模组方块懒加载）。"""
        names = [n for n in (q.get("names", "") or "").split(",") if n]
        if not names:
            return self._err("names 参数为空")
        if len(names) > 2000:
            return self._err("names 过多（>2000）")
        va = _vanilla_assets
        if va is None:
            return self._json({"blockstates": {}, "models": {},
                               "textureIds": [], "emptyRender": []})
        self._json(va.resolve_blockdefs(names))

    def _texture(self, rel: str):
        """返回单张贴图 PNG。rel 形如 block/grass_block_top 或 create:block/xxx。"""
        import urllib.parse
        rel = urllib.parse.unquote(rel).lstrip("/")
        a = _get_assets()
        tex = a["textures"].get(rel)
        if tex is None and _vanilla_assets is not None:
            tex = _vanilla_assets.read_texture(rel)   # 模组/命名空间贴图按需读
        if tex is None:
            # 缺失纹理时改用中性 fallback 纹理（按方块/贴图名哈希着色，避免紫黑格或 404）
            tex = export.generate_fallback_texture(rel)
        tex = vanilla.apply_tint_to_png(rel, tex)
        self._send(200, tex, "image/png")

    def _static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/").replace("\\", "/")
        # 安全 join：规范化后必须仍在 web/ 目录内（防路径穿越）
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not (full == WEB_DIR or full.startswith(WEB_DIR + os.sep)):
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        if not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            body = f.read()
        self._send(200, body, MIME.get(ext, "application/octet-stream"))


def _free_port(start: int) -> int:
    for p in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")


def main(port: int | None = None, open_browser: bool | None = None):
    if open_browser is None:
        open_browser = not os.environ.get("MCMAP_NO_BROWSER")
    # 应用路径配置（环境变量 / config.json / 自动探测；CLI 已在入口处处理）
    cfg = configure()
    if not os.path.isdir(REGION_DIR):
        print(f"[提示] 未指定初始存档，将在主页项目中心供用户选择")
    else:
        _init_vanilla()
    info = WORLD.info()
    port = port or _free_port(DEFAULT_PORT)
    url = f"http://127.0.0.1:{port}/"
    print("=" * 58)
    print("  ChunkCarve · 取方")
    print("=" * 58)
    print(f"  世界     : {info['name']}   ({info['version']}, DataVersion {info['dataVersion']})")
    print(f"  存档目录 : {REGION_DIR}")
    print(f"  种子     : {info['seed']}")
    print(f"  出生点   : {info['spawn']['x']}, {info['spawn']['y']}, {info['spawn']['z']}")
    print(f"  区域文件 : {info['regions']} 个")
    print(f"  原版材质 : {'已加载 ' + _VANILLA_JAR if _vanilla_assets else '未找到，回退程序化贴图'}")
    print(f"  模组目录 : {_MODS_DIR or '（未配置，模组方块将用兜底贴图）'}")
    print(f"  地址     : {url}")
    print("=" * 58)
    print("  按 Ctrl+C 停止")
    # 默认 backlog 只有 5，前端批量拉贴图时多余连接直接被拒。
    # 加大队列 + 已在 vanilla 读取处加 zipfile 锁。
    ThreadingHTTPServer.request_queue_size = 128
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    # 异步预热资源缓存，避免首个贴图请求吃 10~30 秒冷启动，且不阻塞 HTTP 服务立即响应
    threading.Thread(target=_get_assets, daemon=True, name="assets-preheat").start()
    httpd.daemon_threads = True
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="ChunkCarve · 取方（Minecraft 世界选区与 2.5D/3D 预览）")
    ap.add_argument("--world", help="存档根目录（兼容旧版及 26.1+ 地形目录）")
    ap.add_argument("--jar", help="原版版本 jar（versions/<版本>/<版本>.jar）")
    ap.add_argument("--mods", help="模组目录（含 *.jar），用于模组方块贴图")
    ap.add_argument("--port", type=int, help="监听端口（默认 8770，占用时自动 +1）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--save-config", action="store_true", help="把解析出的路径写入 config.json")
    ap.add_argument("--version", action="version", version=VERSION)
    args = ap.parse_args()
    cfg = configure(args.world, args.jar, args.mods)
    if args.save_config:
        config.write_config_file(cfg["world"], cfg["jar"], cfg["mods"])
        print(f"[配置] 已写入 {config.CONFIG_FILE}")
    sys.exit(main(args.port, open_browser=False if args.no_browser else None))
