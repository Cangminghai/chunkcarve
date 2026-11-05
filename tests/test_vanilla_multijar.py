"""测试 VanillaAssets 多 jar 命名空间合并、资源包优先与贴图解析。"""
import io
import json
import zipfile
from mcmap import vanilla


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_vanilla_multijar_namespace(tmp_path):
    # 模拟原版 jar
    vanilla_jar = tmp_path / "1.21.1.jar"
    vanilla_jar.write_bytes(_make_zip({
        "assets/minecraft/textures/block/stone.png": b"stone_png",
        "assets/minecraft/models/block/cube_all.json": json.dumps({"parent": "block/block"}).encode(),
    }))

    va = vanilla.VanillaAssets(str(vanilla_jar))

    # 模拟主模组 jar (ae2)
    ae2_main = tmp_path / "appliedenergistics2.jar"
    ae2_main.write_bytes(_make_zip({
        "assets/ae2/blockstates/quartz_glass.json": json.dumps({"variants": {"": {"model": "ae2:block/quartz_glass"}}}).encode(),
        "assets/ae2/models/item/quartz_glass.json": json.dumps({"textures": {"all": "ae2:block/glass/quartz_glass_item"}}).encode(),
        "assets/ae2/textures/block/glass/quartz_glass_item.png": b"quartz_glass_bytes",
    }))

    # 模拟附加模组 jar (merequester)，同样声明 assets/ae2/
    ae2_addon = tmp_path / "merequester.jar"
    ae2_addon.write_bytes(_make_zip({
        "assets/ae2/textures/block/requester.png": b"requester_bytes",
    }))

    # 注册两个 jar
    va.add_mod_jar(str(ae2_main), "ae2")
    va.add_mod_jar(str(ae2_addon), "ae2")

    # 验证主模组和附加包资源均可正常读取，无覆盖冲突
    defs = va.resolve_blockdefs(["ae2:quartz_glass"])
    assert "ae2:quartz_glass" in defs["blockstates"]
    assert va.read_texture("ae2:block/glass/quartz_glass_item") == b"quartz_glass_bytes"
    assert va.read_texture("ae2:block/requester") == b"requester_bytes"


def test_resource_pack_override(tmp_path):
    vanilla_jar = tmp_path / "vanilla.jar"
    vanilla_jar.write_bytes(_make_zip({
        "assets/minecraft/textures/block/dirt.png": b"dirt_vanilla",
    }))
    va = vanilla.VanillaAssets(str(vanilla_jar))

    # 材质包
    rp = tmp_path / "highres_pack.zip"
    rp.write_bytes(_make_zip({
        "assets/minecraft/textures/block/dirt.png": b"dirt_highres",
    }))
    va.add_resource_pack(str(rp))

    # 材质包应覆盖原版贴图
    assert va.read_texture("block/dirt") == b"dirt_highres"
    assert va.read_texture("minecraft:block/dirt") == b"dirt_highres"
