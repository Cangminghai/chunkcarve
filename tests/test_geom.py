"""几何生成回归测试：立方体面数/剔除行为（导出水密性的基础）。"""
from mcmap import geom


def test_full_cube_face_count():
    quads = geom.full_cube_tris("top", "side", "bottom")
    # 6 个面（四边形），每面 4 个顶点
    assert len(quads) == 6
    for q in quads:
        assert "verts" in q and len(q["verts"]) == 4


# 兼容旧测试用例名称
test_full_cube_tris_face_count = test_full_cube_face_count


def test_full_cube_culled_single_face():
    quads = geom.full_cube_tris_culled([("up", "top")])
    assert len(quads) == 1          # 只保留顶面
    assert quads[0]["tex"] == "top"


# 兼容旧测试用例名称
test_full_cube_tris_culled_single_face = test_full_cube_culled_single_face


def test_full_cube_culled_none():
    assert geom.full_cube_tris_culled([]) == []


def test_bottom_face_uses_bottom_texture():
    quads = geom.full_cube_tris("top", "side", "bottom")
    texs = [q["tex"] for q in quads]
    assert texs.count("top") == 1
    assert texs.count("bottom") == 1
    assert texs.count("side") == 4


def test_collect_textures_unique():
    quads = geom.full_cube_tris("top", "side", "bottom")
    texs = geom.collect_textures(quads)
    assert set(texs) == {"top", "side", "bottom"}
