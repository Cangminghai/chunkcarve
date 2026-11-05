"""路径配置解析：CLI 参数 > 环境变量 > config.json > 自动探测 > 默认。

开源版必需：不能把作者的机器路径写死在代码里。其他 Agent 接手时：
- 用户机器的 Minecraft 安装位置通过 detect_minecraft() 自动探测（官方启动器 / PCL / HMCL 常见布局）
- 覆盖优先级见 resolve() 注释
"""
from __future__ import annotations

import base64
import glob
import json
import os
import sys
import time

ROOT = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 打包（PyInstaller）后：资源在临时解包目录 _MEIPASS，可写目录在 exe 旁边
FROZEN = bool(getattr(sys, "frozen", False))
APPDIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else ROOT
CONFIG_FILE = os.path.join(APPDIR, "config.json")


def read_config_file() -> dict:
    if not os.path.isfile(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def detect_minecraft_dirs() -> list[str]:
    """探测常见 Minecraft 安装目录（.minecraft），返回候选列表。"""
    roots: list[str] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, ".minecraft"))
    home = os.path.expanduser("~")
    roots.append(os.path.join(home, ".minecraft"))
    roots.append(os.path.join(ROOT, ".minecraft"))
    # PCL / HMCL / 整合包常见布局：各盘符下的 game 目录 / PCL 目录
    for drive in "CDEFGH":
        roots += glob.glob(f"{drive}:/game/*/.minecraft")
        roots += glob.glob(f"{drive}:/PCL*/.minecraft")
        roots += glob.glob(f"{drive}:/*/PCL*/.minecraft")
        roots += glob.glob(f"{drive}:/*/*/.minecraft")
    seen, out = set(), []
    for r in roots:
        r = os.path.normpath(r)
        if r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


def find_mods_dir(mc_dir: str) -> str | None:
    """找模组目录：全局 mods 优先，其次 versions/<版本>/mods（整合包常见），取 jar 最多者。"""
    cands = []
    md = os.path.join(mc_dir, "mods")
    if os.path.isdir(md):
        cands.append(md)
    for vd in glob.glob(os.path.join(mc_dir, "versions", "*", "mods")):
        if os.path.isdir(vd):
            cands.append(vd)
    if not cands:
        return None
    def count_jars(p):
        try:
            return sum(1 for f in os.listdir(p) if f.endswith(".jar"))
        except Exception:
            return 0
    cands.sort(key=count_jars, reverse=True)
    return cands[0] if count_jars(cands[0]) > 0 else None


def find_version_jar(mc_dir: str, prefer: tuple[str, ...] = ("1.20.1", "1.20", "1.21")) -> str | None:
    """在 <mc_dir>/versions/*/*.jar 中找版本 jar，优先 1.20.1。"""
    cands = glob.glob(os.path.join(mc_dir, "versions", "*", "*.jar"))
    cands = [c for c in cands if not os.path.basename(c).endswith("-sources.jar")]
    for p in prefer:
        for c in cands:
            if p in os.path.basename(os.path.dirname(c)) or p in os.path.basename(c):
                return c
    return cands[0] if cands else None


def find_world_dir(mc_dir: str, prefer: tuple[str, ...] = ("world",)) -> str | None:
    """在 <mc_dir>/saves/* 中找存档目录（含 region/ 的）。"""
    saves = glob.glob(os.path.join(mc_dir, "saves", "*"))
    saves = [s for s in saves if os.path.isdir(os.path.join(s, "region"))]
    for p in prefer:
        for s in saves:
            if os.path.basename(s).lower() == p:
                return s
    return saves[0] if saves else None


def resolve(world: str | None = None, jar: str | None = None, mods: str | None = None) -> dict:
    """解析最终路径。优先级：入参(CLI) > 环境变量 > config.json > 自动探测 > 项目内默认。

    返回 {"world": 存档根目录(含 region/), "jar": 原版版本jar, "mods": 模组目录或 None}
    """
    cfg = read_config_file()
    world = world or os.environ.get("MCMAP_WORLD") or cfg.get("world")
    jar = jar or os.environ.get("MCMAP_VANILLA_JAR") or os.environ.get("MCMAP_JAR") or cfg.get("jar")
    mods = mods or os.environ.get("MCMAP_MODS_DIR") or os.environ.get("MCMAP_MODS") or cfg.get("mods")

    # 项目内默认：data/world（老布局，保持兼容；打包后在 exe 旁边找）
    if not world:
        legacy = os.path.join(APPDIR, "data", "world")
        if os.path.isdir(os.path.join(legacy, "region")):
            world = legacy

    # 自动探测：找到一个 .minecraft 目录后补齐 jar / mods / world
    if not jar or not mods or not world:
        for mc in detect_minecraft_dirs():
            if not jar:
                jar = find_version_jar(mc)
            if not mods:
                mods = find_mods_dir(mc)
            if not world:
                world = find_world_dir(mc)
            if jar and (mods or world):
                break

    return {
        "world": os.path.abspath(world) if world else None,
        "jar": os.path.abspath(jar) if jar else None,
        "mods": os.path.abspath(mods) if mods else None,
    }


def write_config_file(world: str | None, jar: str | None, mods: str | None) -> None:
    """把解析结果写回 config.json（首次运行便利，便于用户直接改文件）。"""
    data = {}
    if os.path.isfile(CONFIG_FILE):
        data = read_config_file()
    if world:
        data["world"] = world
    if jar:
        data["jar"] = jar
    if mods:
        data["mods"] = mods
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


PROJECTS_FILE = os.path.join(APPDIR, "projects.json")


def get_active_project_id() -> str | None:
    cfg = read_config_file()
    return cfg.get("active_project_id")


def set_active_project_id(pid: str | None) -> None:
    data = read_config_file() if os.path.isfile(CONFIG_FILE) else {}
    if pid:
        data["active_project_id"] = pid
    else:
        data.pop("active_project_id", None)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def dedup_projects(projects: list[dict]) -> list[dict]:
    """按规范化绝对路径（或唯一 ID）对项目列表严格去重。"""
    seen = set()
    deduped = []
    for p in projects:
        if not isinstance(p, dict):
            continue
        w = os.path.realpath(p.get("world", "")).lower() if p.get("world") else ""
        key = w if w else p.get("id")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(p)
    return deduped


def read_projects() -> list[dict]:
    """读取所有已保存的项目。若不存在则基于当前配置生成初始项目，并按存档绝对路径严格去重。"""
    if os.path.isfile(PROJECTS_FILE):
        try:
            with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    deduped = dedup_projects(data)
                    if len(deduped) != len(data):
                        write_projects(deduped)
                    return deduped
        except Exception:
            pass

    # 首次自动从当前环境生成默认项目
    cfg = resolve()
    projects = []
    if cfg.get("world"):
        world_name = "我的世界"
        world_ver = "1.20.1"
        try:
            from mcmap import nbt
            lp = os.path.join(cfg["world"], "level.dat")
            if os.path.exists(lp):
                dat = nbt.load_file(lp).get("Data", {}) or {}
                world_name = dat.get("LevelName", world_name)
                world_ver = dat.get("Version", {}).get("Name", world_ver)
        except Exception:
            pass
        modpack_path = ""
        if cfg.get("mods"):
            modpack_path = os.path.dirname(cfg["mods"])
        instance_name = os.path.basename(os.path.normpath(modpack_path)) if modpack_path else ""
        project_name = f"{instance_name} · {world_name}" if instance_name else f"默认项目 · {world_name}"
        p = {
            "id": "proj_default",
            "name": project_name,
            "description": f"游戏版本 {world_ver}，自动探测生成的项目",
            "world": cfg["world"],
            "jar": cfg.get("jar"),
            "mods": cfg.get("mods"),
            "modpack": modpack_path,
            "version": world_ver,
            "updatedAt": time.strftime("%Y-%m-%d %H:%M"),
        }
        projects.append(p)
        write_projects(projects)
    return projects


def write_projects(projects: list[dict]) -> None:
    try:
        with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def save_project(proj: dict) -> dict:
    projects = read_projects()
    pid = proj.get("id")
    p_world = os.path.realpath(proj.get("world", "")).lower() if proj.get("world") else ""

    found = False
    for i, p in enumerate(projects):
        existing_world = os.path.realpath(p.get("world", "")).lower() if p.get("world") else ""
        # 优先通过 ID 匹配，或通过相同的实际存档路径匹配（防止同一存档重复创建多张卡片）
        if (pid and p.get("id") == pid) or (p_world and existing_world and p_world == existing_world):
            proj["id"] = p.get("id") or pid or ("proj_" + str(int(time.time() * 1000)))
            projects[i] = {**p, **proj}
            projects[i]["updatedAt"] = time.strftime("%Y-%m-%d %H:%M")
            proj = projects[i]
            found = True
            break
    if not found:
        if not pid:
            pid = "proj_" + str(int(time.time() * 1000))
            proj["id"] = pid
        proj["updatedAt"] = time.strftime("%Y-%m-%d %H:%M")
        projects.insert(0, proj)
    write_projects(projects)
    return proj


def delete_project(proj_id: str) -> bool:
    projects = read_projects()
    new_projs = [p for p in projects if p.get("id") != proj_id]
    if len(new_projs) != len(projects):
        write_projects(new_projs)
        return True
    return False


def detect_modpacks(mc_dirs: list[str] | None = None) -> list[dict]:
    """探测本机所有 Minecraft 整合包/实例版本。"""
    cands: list[dict] = []
    seen_paths = set()

    mc_dirs = detect_minecraft_dirs() if mc_dirs is None else [
        os.path.abspath(path) for path in mc_dirs if os.path.isdir(path)
    ]
    for mc in mc_dirs:
        v_dir = os.path.join(mc, "versions")
        if not os.path.isdir(v_dir):
            continue
        try:
            entries = os.listdir(v_dir)
        except Exception:
            continue
        for v in entries:
            vp = os.path.normpath(os.path.join(v_dir, v))
            if vp in seen_paths or not os.path.isdir(vp):
                continue
            seen_paths.add(vp)

            # 探测 jar
            jar_path = None
            v_jar = os.path.join(vp, f"{v}.jar")
            if os.path.isfile(v_jar):
                jar_path = v_jar
            else:
                try:
                    jars = [os.path.join(vp, f) for f in os.listdir(vp)
                            if f.endswith(".jar") and not f.endswith("-sources.jar")]
                    if jars:
                        jar_path = jars[0]
                except Exception:
                    pass

            # 探测 mods
            mods_dir = None
            mod_count = 0
            m_cand = os.path.join(vp, "mods")
            if os.path.isdir(m_cand):
                mods_dir = m_cand
            else:
                m_parent = os.path.join(mc, "mods")
                if os.path.isdir(m_parent):
                    mods_dir = m_parent
            if mods_dir:
                try:
                    mod_count = sum(1 for f in os.listdir(mods_dir) if f.endswith(".jar"))
                except Exception:
                    mod_count = 0

            # 探测 saves 数量
            saves_count = 0
            s_cand = os.path.join(vp, "saves")
            if not os.path.isdir(s_cand):
                s_cand = os.path.join(mc, "saves")
            if os.path.isdir(s_cand):
                try:
                    saves_count = sum(1 for f in os.listdir(s_cand)
                                      if os.path.isdir(os.path.join(s_cand, f)))
                except Exception:
                    saves_count = 0

            # 提取版本号（如果同名 json 存在）
            ver_name = v
            v_json = os.path.join(vp, f"{v}.json")
            if os.path.isfile(v_json):
                try:
                    with open(v_json, "r", encoding="utf-8") as jf:
                        jdat = json.load(jf)
                        ver_name = jdat.get("inheritsFrom") or jdat.get("id") or v
                except Exception:
                    pass

            cands.append({
                "id": v,
                "name": v,
                "version": ver_name,
                "path": vp,
                "mc_dir": mc,
                "jar": jar_path,
                "mods": mods_dir,
                "mod_count": mod_count,
                "saves_count": saves_count,
            })

    cands.sort(key=lambda x: (x["mod_count"] > 0, x["saves_count"] > 0, x["mod_count"]), reverse=True)
    return cands


def detect_instance_saves_and_assets(target_path: str) -> dict:
    """深度探测指定整合包目录下的存档、材质包、Jar与模组。"""
    from mcmap import nbt

    if not target_path or not os.path.exists(target_path):
        target_path = APPDIR

    target_path = os.path.abspath(target_path)
    is_appdir = os.path.normpath(target_path) == os.path.normpath(APPDIR)

    leaf = os.path.basename(os.path.normpath(target_path)).casefold()
    versions_dir = (target_path if leaf == "versions" else
                    os.path.join(target_path, "versions") if leaf == ".minecraft" else None)
    if versions_dir and os.path.isdir(versions_dir):
        instances = detect_modpacks([os.path.dirname(versions_dir)])
        return {"ok": True, "path": target_path, "instances": instances}

    # 1. 探测存档
    saves: list[dict] = []
    seen_saves = set()

    # 优先实例专属 saves 目录
    cand_save_dirs = [
        os.path.join(target_path, "saves"),
        os.path.join(target_path, "world"),
    ]
    if is_appdir:
        cand_save_dirs.append(os.path.join(APPDIR, "data", "world"))

    def scan_save_directories(dirs):
        for c_dir in dirs:
            c_dir = os.path.normpath(c_dir)
            if not os.path.isdir(c_dir):
                continue

            if os.path.isfile(os.path.join(c_dir, "level.dat")) or os.path.isdir(os.path.join(c_dir, "region")):
                sub_folders = [c_dir]
            else:
                try:
                    sub_folders = [os.path.join(c_dir, d) for d in os.listdir(c_dir)]
                except Exception:
                    sub_folders = []

            for sf in sub_folders:
                rp = os.path.realpath(sf)
                if rp in seen_saves or not os.path.isdir(rp):
                    continue
                lp = os.path.join(rp, "level.dat")
                reg = os.path.join(rp, "region")
                if not (os.path.isfile(lp) or os.path.isdir(reg)):
                    continue
                seen_saves.add(rp)

                s_name = os.path.basename(rp)
                s_ver = "?"
                s_data_ver = 0
                s_spawn = {"x": 0, "y": 64, "z": 0}
                s_seed = "0"
                s_last_played = ""
                s_game_type = "生存模式"
                icon_data = None

                if os.path.isfile(lp):
                    try:
                        dat = nbt.load_file(lp).get("Data", {}) or {}
                        s_name = dat.get("LevelName") or s_name
                        v_obj = dat.get("Version", {}) or {}
                        s_ver = v_obj.get("Name", "?")
                        s_data_ver = dat.get("DataVersion", 0)
                        s_seed = str(dat.get("WorldGenSettings", {}).get("seed", dat.get("RandomSeed", 0)))
                        s_spawn = {
                            "x": dat.get("SpawnX", 0),
                            "y": dat.get("SpawnY", 64),
                            "z": dat.get("SpawnZ", 0)
                        }
                        lp_ms = dat.get("LastPlayed", 0)
                        if lp_ms:
                            s_last_played = time.strftime("%Y-%m-%d %H:%M", time.localtime(lp_ms / 1000))
                        gt = dat.get("GameType", 0)
                        game_modes = ["生存模式", "创造模式", "冒险模式", "旁观模式"]
                        s_game_type = game_modes[gt] if 0 <= gt < len(game_modes) else "生存模式"
                    except Exception:
                        pass

                # 缩略图 icon.png
                icon_path = os.path.join(rp, "icon.png")
                if os.path.isfile(icon_path):
                    try:
                        if os.path.getsize(icon_path) < 1024 * 1024:
                            with open(icon_path, "rb") as icf:
                                icon_data = "data:image/png;base64," + base64.b64encode(icf.read()).decode("ascii")
                    except Exception:
                        pass

                reg_count = 0
                if os.path.isdir(reg):
                    try:
                        reg_count = sum(1 for f in os.listdir(reg) if f.endswith(".mca"))
                    except Exception:
                        reg_count = 0

                saves.append({
                    "path": rp,
                    "name": s_name,
                    "version": s_ver,
                    "dataVersion": s_data_ver,
                    "spawn": s_spawn,
                    "spawnChunk": {"cx": s_spawn["x"] // 16, "cz": s_spawn["z"] // 16},
                    "seed": s_seed,
                    "lastPlayed": s_last_played,
                    "gameType": s_game_type,
                    "regions": reg_count,
                    "icon": icon_data,
                })

    scan_save_directories(cand_save_dirs)

    # 若实例专属目录没有找到存档，并且不是 appdir，再向启动器通用存档目录寻找
    if not saves and not is_appdir:
        cand_outer_dirs = [
            os.path.join(target_path, "..", "..", "saves"),
            os.path.join(target_path, "..", "saves"),
        ]
        scan_save_directories(cand_outer_dirs)

    # 2. 探测版本 Jar
    jars: list[dict] = []
    seen_jars = set()
    # 优先目标实例目录自身的 Jar，其次是上级与同级版本的 Jar
    self_jars = [j for j in glob.glob(os.path.join(target_path, "*.jar")) if not j.endswith("-sources.jar")]
    other_jars = (
        glob.glob(os.path.join(target_path, "versions", "*", "*.jar")) +
        glob.glob(os.path.join(target_path, "..", "*", "*.jar")) +
        glob.glob(os.path.join(target_path, "..", "..", "versions", "*", "*.jar"))
    )
    jar_cands = self_jars + other_jars
    for j in jar_cands:
        if j.endswith("-sources.jar") or not os.path.isfile(j):
            continue
        rj = os.path.realpath(j)
        if rj not in seen_jars:
            seen_jars.add(rj)
            bname = os.path.basename(rj)
            jars.append({
                "name": bname,
                "path": rj,
                "isForge": "forge" in bname.lower() or "neoforge" in bname.lower(),
                "isFabric": "fabric" in bname.lower(),
            })

    # 3. 探测 mods 目录
    mods_info = {"dir": None, "count": 0, "sample": []}
    cand_mods_dirs = [
        os.path.join(target_path, "mods"),
        os.path.join(target_path, "..", "mods"),
        os.path.join(target_path, "..", "..", "mods"),
    ]
    for md in cand_mods_dirs:
        if os.path.isdir(md):
            try:
                mod_files = [f for f in os.listdir(md) if f.endswith(".jar")]
                if mod_files:
                    mods_info["dir"] = os.path.realpath(md)
                    mods_info["count"] = len(mod_files)
                    mods_info["sample"] = [f[:-4] for f in mod_files[:6]]
                    break
            except Exception:
                continue

    # 4. 探测 resourcepacks 材质包
    resourcepacks: list[dict] = []
    cand_rp_dirs = [
        os.path.join(target_path, "resourcepacks"),
        os.path.join(target_path, "..", "resourcepacks"),
        os.path.join(target_path, "..", "..", "resourcepacks"),
    ]
    for rpd in cand_rp_dirs:
        if os.path.isdir(rpd):
            try:
                for item in os.listdir(rpd):
                    item_path = os.path.join(rpd, item)
                    if item.endswith(".zip") or os.path.isdir(item_path):
                        resourcepacks.append({
                            "name": item,
                            "path": os.path.realpath(item_path),
                            "isZip": item.endswith(".zip")
                        })
                if resourcepacks:
                    break
            except Exception:
                continue

    return {
        "ok": True,
        "path": target_path,
        "saves": saves,
        "jars": jars,
        "mods": mods_info,
        "resourcepacks": resourcepacks,
    }
