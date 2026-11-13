"""Unit tests for ChunkCarve 1.0 open-source enhancements.

Covers:
1. Pixel-grid submesh subdivision (subdivide_quads & GLB/OBJ subdivide export)
2. GLB vertex welding and degenerate zero-area face elimination
3. Non-full block 1:1 scale & offset fidelity without global boolean lockup
4. Player position & username discovery (World.get_players)
5. Project creation / rename persistence & activeId synchronization
"""

import io
import json
import os
import struct
import tempfile
import zipfile

import numpy as np
import pytest

import mcmap
from mcmap import config, export, geom, gltf, server, vanilla


def test_version_metadata_consistency():
    """验证包版本号、服务端版本标识及 capabilities 描述一致为 1.0.5。"""
    assert mcmap.__version__ == "1.0.5"
    assert server.VERSION == "ChunkCarve/1.0.5"
    caps = server.capabilities()
    assert caps["service"] == "chunkcarve"
    assert caps["version"] == "ChunkCarve/1.0.5"
    assert caps["features"] == ["selection", "preview"]


def test_subdivide_quads_bilinear_orthogonality():
    """验证 subdivide_quads 将单面正交切分为 n*n 微小四边形且保留精确边界与 UV。"""
    quads = geom.full_cube_tris_culled([("up", "block/grass_block_top")], scale=2.0, origin=(4.0, 6.0, 8.0))
    assert len(quads) == 1

    sub8 = geom.subdivide_quads(quads, 8)
    assert len(sub8) == 64

    verts = np.asarray([q["verts"] for q in sub8], dtype=np.float32)
    uvs = np.asarray([q["uvs"] for q in sub8], dtype=np.float32)

    # 顶面高度恒为 6.0 + 2.0 = 8.0，X 范围 [4, 6]，Z 范围 [8, 10]
    assert np.allclose(verts[:, :, 1], 8.0)
    assert verts[:, :, 0].min() == pytest.approx(4.0)
    assert verts[:, :, 0].max() == pytest.approx(6.0)
    assert verts[:, :, 2].min() == pytest.approx(8.0)
    assert verts[:, :, 2].max() == pytest.approx(10.0)

    # UV 范围完整覆盖 [0, 16]
    assert uvs.min() == pytest.approx(0.0)
    assert uvs.max() == pytest.approx(16.0)

    # 每个子面法线严格朝上 (+Y)
    for q in sub8:
        v0, v1, v2, _ = [np.asarray(v) for v in q["verts"]]
        normal = np.cross(v1 - v0, v2 - v0)
        assert normal[1] > 0 and abs(normal[0]) < 1e-6 and abs(normal[2]) < 1e-6


def test_glb_welds_vertices_and_drops_degenerate_triangles():
    """验证 write_glb 焊接同属性共点顶点，并自动剔除零面积退化面。"""
    valid_quads = geom.full_cube_tris("top", "side", "bot", scale=2.0)
    # 注入一个四点共线的零面积退化四边形
    degenerate = {
        "verts": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [3.0, 0.0, 0.0]],
        "uvs": [[0.0, 0.0], [8.0, 0.0], [16.0, 0.0], [16.0, 0.0]],
        "tex": "top",
    }
    png = export.generate_fallback_texture("top")
    uv_map = {"top": (0.0, 0.0, 1.0, 1.0), "side": (0.0, 0.0, 1.0, 1.0), "bot": (0.0, 0.0, 1.0, 1.0)}
    glb = gltf.write_glb(valid_quads + [degenerate], png, uv_map, "weld_test")

    size = struct.unpack_from("<I", glb, 12)[0]
    doc = json.loads(glb[20:20 + size])
    assert doc["asset"]["generator"] == "ChunkCarve"
    # 立方体 6 个面，每面 4 个独立法线顶点 = 24（比未焊接的 36 少），且退化面被剔除，索引严格为 36 (12 三角形)
    assert doc["accessors"][0]["count"] <= 24
    assert doc["accessors"][3]["count"] == 36


def test_export_obj_voxel_subdivide_and_detail_scale(monkeypatch):
    """验证 export_obj_voxel 支持网格细分，且非完整方块（如半砖/雪片）严格按 scale 1:1 比例还原。"""
    class MockSnowAssets:
        def get_textures(self, name):
            return ("snow", "snow", "snow")

        def read_texture(self, name):
            return export.generate_fallback_texture(name)

        def _read_texture_png(self, name):
            return export.generate_fallback_texture(name)

        def parse_elements(self, name, properties=None):
            # 模拟 1 层薄雪 (0..16, 0..2, 0..16) 铺满整个方块底面，不偏角不缩小
            return vanilla._Geometry([vanilla._Element(
                (0, 0, 0), (16, 2, 16),
                {face: ("snow", (0, 0, 16, 16)) for face in geom._FACE_DEF}
            )])

    xyz = np.array([[10, 64, 20], [10, 65, 20]], dtype=np.int16)
    ids = np.array([0, 1], dtype=np.uint16)
    flags = np.array([0, 2], dtype=np.uint8)
    state_table = [("minecraft:grass_block", None), ("minecraft:snow", {"layers": "1"})]

    monkeypatch.setattr(server.render, "extract_voxels_states", lambda *args: (xyz, ids, flags, state_table))
    monkeypatch.setattr(server, "_vanilla_assets", MockSnowAssets())

    w = server.World.__new__(server.World)
    scale = 4.0
    glb_std, _, stats_std = w.export_obj_voxel(0, 1, 1, 2, scale, True, 64, 65, (10, 20, 10, 20), model_format="glb", subdivide=1)
    glb_sub, _, stats_sub = w.export_obj_voxel(0, 1, 1, 2, scale, True, 64, 65, (10, 20, 10, 20), model_format="glb", subdivide=8)

    # 8x8 细分后三角形数量应为标准网格的 64 倍
    assert stats_sub["triangles"] == stats_std["triangles"] * 64

    # 验证薄雪在 X/Z 方向完整覆盖 [0, 4mm] (= [0, 0.004m])，最高点 Y = (65 + 2/16) * 4mm = 260.5mm (= 0.2605m)
    size = struct.unpack_from("<I", glb_std, 12)[0]
    doc = json.loads(glb_std[20:20 + size])
    pmin = doc["accessors"][0]["min"]
    pmax = doc["accessors"][0]["max"]
    assert pmin[0] == pytest.approx(0.0)
    assert pmax[0] == pytest.approx(0.004)
    assert pmin[2] == pytest.approx(0.0)
    assert pmax[2] == pytest.approx(0.004)
    assert pmax[1] == pytest.approx(0.2605, rel=1e-4)


def test_world_get_players_parses_single_and_multiplayer(monkeypatch, tmp_path):
    """验证 World.get_players 能提取单人 Player 与 playerdata 并映射 usercache 玩家昵称。"""
    world_dir = tmp_path / "saves" / "MyWorld"
    (world_dir / "region").mkdir(parents=True)
    (world_dir / "playerdata").mkdir(parents=True)

    uid = "12345678-1234-5678-1234-567812345678"
    (world_dir / "playerdata" / f"{uid}.dat").write_bytes(b"mock")
    (tmp_path / "usercache.json").write_text(
        json.dumps([{"uuid": uid, "name": "SteveBuilder"}]), encoding="utf-8"
    )

    def fake_load_file(path):
        if path.endswith(uid + ".dat"):
            return {"Pos": [1280.4, 72.0, -3450.2], "Dimension": "minecraft:overworld"}
        return {}

    monkeypatch.setattr(server, "DATA_DIR", str(world_dir))
    monkeypatch.setattr(server, "REGION_DIR", str(world_dir / "region"))
    monkeypatch.setattr(server.nbt, "load_file", fake_load_file)

    w = server.World.__new__(server.World)
    w.level = {"Data": {"Player": {"Pos": [-500.0, 64.0, 900.0], "Dimension": "minecraft:overworld"}}}
    w.name = "MyWorld"
    w.version = "1.20.1"
    w.data_version = 3465
    w.seed = 42
    w.spawn = (0, 64, 0)
    w.modded = False

    info = w.info()
    players = info["players"]
    assert len(players) == 2
    by_name = {p["name"]: p for p in players}
    assert "SteveBuilder" in by_name
    assert (by_name["SteveBuilder"]["x"], by_name["SteveBuilder"]["z"]) == (1280, -3450)
    assert "存档玩家" in by_name
    assert (by_name["存档玩家"]["x"], by_name["存档玩家"]["z"]) == (-500, 900)


def test_open_project_updates_name_and_syncs_active_id():
    """验证在已有默认项目的存档上新建/重命名项目时，新名称与 activeId 一致持久化。"""
    orig_world = server.DATA_DIR
    orig_jar = server._VANILLA_JAR
    orig_mods = server._MODS_DIR
    orig_active_id = config.get_active_project_id()
    orig_projects_file = config.PROJECTS_FILE
    orig_config_file = config.CONFIG_FILE

    with tempfile.TemporaryDirectory() as td:
        try:
            config.PROJECTS_FILE = os.path.join(td, "projects.json")
            config.CONFIG_FILE = os.path.join(td, "config.json")
            world_dir = os.path.join(td, "SaveWorld")
            os.makedirs(os.path.join(world_dir, "region"), exist_ok=True)
            config.write_projects([{"id": "proj_default", "name": "默认项目", "world": world_dir}])

            res = server.open_project({
                "id": "proj_new_timestamp",
                "name": "我的新城堡项目",
                "world": world_dir,
            })
            assert res["ok"] is True
            assert res["activeId"] == "proj_default"
            assert config.get_active_project_id() == "proj_default"
            projs = config.read_projects()
            assert len(projs) == 1
            assert projs[0]["name"] == "我的新城堡项目"
        finally:
            config.PROJECTS_FILE = orig_projects_file
            config.CONFIG_FILE = orig_config_file
            if orig_world and os.path.isdir(orig_world):
                server.switch_world(orig_world, orig_jar, orig_mods)
                config.write_config_file(orig_world, orig_jar, orig_mods)
            config.set_active_project_id(orig_active_id)
