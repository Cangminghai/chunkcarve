"""针对拓竹 / Bambu Studio 3D 打印水密流形性、法线方向与 UV 的专项测试。"""

import os
import zipfile
import numpy as np
import pytest
from mcmap import geom, export


def test_cube_outward_normals_all_six_faces():
    """验证所有 6 个面的两个三角形法线均严格指向方块外侧（点积 > 0）。"""
    quads = geom.full_cube_tris("top", "side", "bottom", scale=1.0, origin=(0.0, 0.0, 0.0))
    assert len(quads) == 6

    expected_normals = {
        0: np.array([0.0, 1.0, 0.0]),   # up
        1: np.array([0.0, -1.0, 0.0]),  # down
        2: np.array([0.0, 0.0, -1.0]),  # north
        3: np.array([0.0, 0.0, 1.0]),   # south
        4: np.array([-1.0, 0.0, 0.0]),  # west
        5: np.array([1.0, 0.0, 0.0]),   # east
    }

    for idx, q in enumerate(quads):
        v0, v1, v2, v3 = [np.array(v) for v in q["verts"]]
        # 三角形 1: v0 v1 v2
        n1 = np.cross(v1 - v0, v2 - v0)
        # 三角形 2: v0 v2 v3
        n2 = np.cross(v2 - v0, v3 - v0)

        exp = expected_normals[idx]
        assert np.dot(n1, exp) > 0.99, f"Face {idx} triangle 1 normal inverted! n1={n1}, exp={exp}"
        assert np.dot(n2, exp) > 0.99, f"Face {idx} triangle 2 normal inverted! n2={n2}, exp={exp}"


def test_face_culling_no_internal_or_duplicate_faces():
    """验证面剔除不会产生多余侧面或重复面。

    暴露 1 个侧面时，应该且仅应该生成 1 个四边形（而非旧代码中生成 4 个侧面）。
    """
    # 只暴露东面
    quads_east = geom.full_cube_tris_culled([("east", "side_tex")])
    assert len(quads_east) == 1
    assert quads_east[0]["tex"] == "side_tex"

    # 只暴露北面与顶面
    quads_two = geom.full_cube_tris_culled([("up", "top_tex"), ("north", "side_tex")])
    assert len(quads_two) == 2


def test_two_adjacent_cubes_watertight_manifold():
    """两块相邻方块紧密贴合，总面数必须严格等于 10 面（内部接触面 2 面全部剔除）。"""
    # 模拟两块沿 X 轴相邻的方块：Block A at (0,0,0), Block B at (1,0,0)
    solid_occ = {(0, 0, 0), (1, 0, 0)}

    def cull_block(cx, cy, cz):
        faces = []
        if (cx, cy + 1, cz) not in solid_occ: faces.append(("up", "top"))
        if (cx, cy - 1, cz) not in solid_occ: faces.append(("down", "bot"))
        if (cx + 1, cy, cz) not in solid_occ: faces.append(("east", "side"))
        if (cx - 1, cy, cz) not in solid_occ: faces.append(("west", "side"))
        if (cx, cy, cz + 1) not in solid_occ: faces.append(("south", "side"))
        if (cx, cy, cz - 1) not in solid_occ: faces.append(("north", "side"))
        return geom.full_cube_tris_culled(faces, scale=1.0, origin=(cx, cy, cz))

    quads_a = cull_block(0, 0, 0)
    quads_b = cull_block(1, 0, 0)

    # A 在 (0,0,0)：+X (east) 紧邻 B，被剔除，剩下 5 面
    assert len(quads_a) == 5
    # B 在 (1,0,0)：-X (west) 紧邻 A，被剔除，剩下 5 面
    assert len(quads_b) == 5

    total_quads = len(quads_a) + len(quads_b)
    # 合计严格为 10 个外表面四边形（20 个三角形），内部完全水密中空
    assert total_quads == 10


def test_export_obj_zip_manifold_and_materials(tmp_path):
    """测试导出的 OBJ/MTL 文件合规性：顶点数、面索引、多材质声明。"""
    quads = geom.full_cube_tris("grass_top", "grass_side", "dirt", scale=2.0)
    out_zip = str(tmp_path / "test_model.zip")

    # 构造 mock assets（无真实 jar 时 fallback 生成）
    class MockAssets:
        def _read_texture_png(self, name):
            return None

    stats = export.export_obj_zip(
        MockAssets(), quads, [], scale=2.0, out_zip=out_zip, model_name="test_cube")

    assert stats["triangles"] == 12
    assert stats["textures"] == 3
    assert stats["vertices"] == 8

    with zipfile.ZipFile(out_zip, "r") as zf:
        namelist = zf.namelist()
        assert "test_cube.obj" in namelist
        assert "test_cube.mtl" in namelist
        assert "test_cube.png" in namelist

        obj_content = zf.read("test_cube.obj").decode("utf-8")
        mtl_content = zf.read("test_cube.mtl").decode("utf-8")

        # 检查 OBJ 中包含了材质引用和面定义
        assert "mtllib test_cube.mtl" in obj_content
        assert "usemtl mat_grass_top" in obj_content
        assert "usemtl mat_grass_side" in obj_content
        assert "usemtl mat_dirt" in obj_content
        assert "f " in obj_content

        # 检查 MTL 中各材质包含了 Kd 与 map_Kd
        assert "newmtl mat_grass_top" in mtl_content
        assert "Kd " in mtl_content
        assert "map_Kd test_cube.png" in mtl_content
