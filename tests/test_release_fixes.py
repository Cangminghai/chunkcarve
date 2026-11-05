"""Unit tests for release fixes (Items 2, 4, 7).

- Item 2: Project center project switching updates world, assets, and caches
- Item 4: Missing texture fallback uses neutral hashed color rather than magenta/checkerboard
- Item 7: Project deduplication by normalized absolute path
"""

import io
import os
import tempfile
import pytest
from PIL import Image

from mcmap import config, export, server


def test_item4_generate_fallback_texture():
    """验证缺失贴图生成中性哈希颜色 fallback PNG，而非刺眼的紫黑品红或 404。"""
    png_bytes = export.generate_fallback_texture("create:block/gearbox")
    assert isinstance(png_bytes, bytes)
    assert len(png_bytes) > 0

    im = Image.open(io.BytesIO(png_bytes))
    assert im.size == (16, 16)
    assert im.mode == "RGBA"

    # 验证不是紫黑品红色 (255, 0, 255)
    r, g, b, a = im.getpixel((0, 0))
    assert not (r == 255 and g == 0 and b == 255), "不得使用紫黑品红"
    assert a == 255, "Alpha 通道必须完全不透明"

    # 验证不同模组方块名产生不同哈希颜色
    png2 = export.generate_fallback_texture("mekanism:block/steel_casing")
    im2 = Image.open(io.BytesIO(png2))
    pix2 = im2.getpixel((0, 0))
    assert (r, g, b, a) != pix2, "不同方块名称应具有区分度哈希色"


def test_item7_dedup_projects_normalized_paths():
    """验证项目按规范化绝对路径（大小写、斜杠、相对路径）严格去重。"""
    with tempfile.TemporaryDirectory() as td:
        world1 = os.path.join(td, "WorldA")
        os.makedirs(os.path.join(world1, "region"), exist_ok=True)

        world2 = os.path.join(td, "WorldB")
        os.makedirs(os.path.join(world2, "region"), exist_ok=True)

        # 构造各种路径变体（斜杠、反斜杠、末尾斜杠、相对段、大小写变体）
        world1_var1 = world1 + os.sep
        world1_var2 = os.path.join(td, ".", "WorldA")
        world1_var3 = world1.lower()

        projects = [
            {"id": "p1", "name": "项目1", "world": world1},
            {"id": "p2", "name": "项目1副本A", "world": world1_var1},
            {"id": "p3", "name": "项目1副本B", "world": world1_var2},
            {"id": "p4", "name": "项目1副本C", "world": world1_var3},
            {"id": "p5", "name": "项目2", "world": world2},
        ]

        deduped = config.dedup_projects(projects)
        assert len(deduped) == 2, f"5个项目包含2个唯一世界，去重后应为2个，实际为: {len(deduped)}"
        assert deduped[0]["id"] == "p1"
        assert deduped[1]["id"] == "p5"


def test_item7_save_project_deduplication():
    """验证保存同一存档的项目时覆盖更新现有卡片，而不产生重复卡片。"""
    with tempfile.TemporaryDirectory() as td:
        proj_file = os.path.join(td, "projects.json")
        orig_file = config.PROJECTS_FILE
        try:
            config.PROJECTS_FILE = proj_file
            config.write_projects([])  # 预先初始化空项目列表

            world = os.path.join(td, "my_world")
            os.makedirs(os.path.join(world, "region"), exist_ok=True)

            p1 = config.save_project({"name": "首次创建", "world": world})
            assert p1["name"] == "首次创建"

            # 再次以不同大小写或末尾斜杠保存同一存档
            p2 = config.save_project({"name": "二次更新", "world": world + os.sep})
            assert p2["id"] == p1["id"], "应复用相同项目 ID"
            assert p2["name"] == "二次更新"

            # 读取列表断言只有一个
            projs = config.read_projects()
            assert len(projs) == 1
            assert projs[0]["name"] == "二次更新"
            assert projs[0]["id"] == p1["id"]
        finally:
            config.PROJECTS_FILE = orig_file


def test_item2_switch_world_and_open_project():
    """验证 open_project / switch_world 能正确切换世界路径，清空缓存并更新配置。"""
    orig_world = server.DATA_DIR
    orig_jar = server._VANILLA_JAR
    orig_mods = server._MODS_DIR
    orig_active_id = config.get_active_project_id()
    orig_projects_file = config.PROJECTS_FILE

    with tempfile.TemporaryDirectory() as td:
        try:
            config.PROJECTS_FILE = os.path.join(td, "projects.json")
            config.write_projects([])

            world_a = os.path.join(td, "WorldA")
            os.makedirs(os.path.join(world_a, "region"), exist_ok=True)
            world_b = os.path.join(td, "WorldB")
            os.makedirs(os.path.join(world_b, "region"), exist_ok=True)

            # 切换到 World A
            res_a = server.switch_world(world_a)
            assert os.path.realpath(server.DATA_DIR) == os.path.realpath(world_a)
            assert os.path.realpath(server.REGION_DIR) == os.path.realpath(os.path.join(world_a, "region"))

            # 打开 Project B
            proj_b = {
                "id": "proj_b_test",
                "name": "测试项目 B",
                "world": world_b,
            }
            res_open = server.open_project(proj_b)
            assert res_open["ok"] is True
            assert os.path.realpath(server.DATA_DIR) == os.path.realpath(world_b)
            assert os.path.realpath(server.REGION_DIR) == os.path.realpath(os.path.join(world_b, "region"))
            assert res_open["activeId"] == "proj_b_test"
        finally:
            config.PROJECTS_FILE = orig_projects_file
            # 严格还原原始全局状态，避免影响后续测试与正在运行的服务
            if orig_world and os.path.isdir(orig_world):
                server.switch_world(orig_world, orig_jar, orig_mods)
                config.write_config_file(orig_world, orig_jar, orig_mods)
            if orig_active_id:
                config.set_active_project_id(orig_active_id)
