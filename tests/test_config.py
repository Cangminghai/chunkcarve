"""路径配置解析测试：环境变量优先、显式参数最高、自动探测兜底。"""
import os

from mcmap import config


def _mk_world(tmp_path):
    w = tmp_path / "world"
    (w / "region").mkdir(parents=True)
    return w


def test_cli_arg_wins(monkeypatch, tmp_path):
    w = _mk_world(tmp_path)
    monkeypatch.setenv("MCMAP_WORLD", "/nonexistent/env/world")
    r = config.resolve(world=str(w))
    assert r["world"] == os.path.abspath(str(w))


def test_env_used_when_no_arg(monkeypatch, tmp_path):
    w = _mk_world(tmp_path)
    monkeypatch.setenv("MCMAP_WORLD", str(w))
    r = config.resolve(world=None)
    assert r["world"] == os.path.abspath(str(w))


def test_find_version_jar_prefers_1201(tmp_path):
    mc = tmp_path / ".minecraft"
    for v in ("1.21.1", "1.20.1-Forge"):
        d = mc / "versions" / v
        d.mkdir(parents=True)
        (d / f"{v}.jar").write_bytes(b"x")
    jar = config.find_version_jar(str(mc))
    assert jar is not None and "1.20.1" in jar


def test_find_world_dir(tmp_path):
    mc = tmp_path / ".minecraft"
    (mc / "saves" / "world" / "region").mkdir(parents=True)
    assert config.find_world_dir(str(mc)).endswith("world")


def test_resolve_returns_keys():
    r = config.resolve()
    assert set(r.keys()) >= {"world", "jar", "mods"}


def test_project_crud(tmp_path, monkeypatch):
    pf = tmp_path / "projects.json"
    monkeypatch.setattr(config, "PROJECTS_FILE", str(pf))
    proj = {
        "id": "p_test_1",
        "name": "测试项目",
        "world": "/fake/world",
        "jar": "/fake/jar.jar",
        "mods": "/fake/mods",
    }
    saved = config.save_project(proj)
    assert saved["id"] == "p_test_1"
    all_p = config.read_projects()
    assert any(p["id"] == "p_test_1" for p in all_p)
    # delete
    assert config.delete_project("p_test_1") is True
    assert not any(p["id"] == "p_test_1" for p in config.read_projects())


def test_project_deduplication_by_world(tmp_path, monkeypatch):
    pf = tmp_path / "projects.json"
    pf.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECTS_FILE", str(pf))
    w = str(tmp_path / "saves" / "world1")
    p1 = {"id": "p1", "name": "项目1", "world": w}
    config.save_project(p1)
    assert len(config.read_projects()) == 1

    # 保存同一存档路径但带有新 id 的项目，应当自动去重并更新，而不是生成两个项目
    p2 = {"id": "p2_new", "name": "项目1更新名", "world": w}
    config.save_project(p2)
    projs = config.read_projects()
    assert len(projs) == 1
    assert projs[0]["name"] == "项目1更新名"

    # 保存不同存档路径的项目，正常新增
    w2 = str(tmp_path / "saves" / "world2")
    p3 = {"id": "p3", "name": "项目2", "world": w2}
    config.save_project(p3)
    assert len(config.read_projects()) == 2


def test_detect_instance(tmp_path):
    inst = tmp_path / "modpack"
    save_dir = inst / "saves" / "test_world" / "region"
    save_dir.mkdir(parents=True)
    mods_dir = inst / "mods"
    mods_dir.mkdir(parents=True)
    (mods_dir / "test_mod.jar").write_bytes(b"PK")

    res = config.detect_instance_saves_and_assets(str(inst))
    assert res["ok"] is True
    assert len(res["saves"]) >= 1
    assert res["mods"]["count"] == 1


def test_detect_versions_directory_returns_instance_candidates(tmp_path):
    mc_dir = tmp_path / ".minecraft"
    instance = mc_dir / "versions" / "ElementPack"
    (instance / "mods").mkdir(parents=True)
    (instance / "mods" / "example.jar").write_bytes(b"PK")
    (instance / "saves" / "world" / "region").mkdir(parents=True)
    (instance / "ElementPack.jar").write_bytes(b"PK")

    result = config.detect_instance_saves_and_assets(str(mc_dir / "versions"))

    assert result["ok"] is True
    assert len(result["instances"]) == 1
    assert result["instances"][0]["path"] == str(instance)
