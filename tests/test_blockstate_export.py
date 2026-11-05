"""Export actual block states, including connected parts and oriented stairs."""

from collections import Counter
import io
import json
import struct
import zipfile

import numpy as np
import pytest

from mcmap import geom, server, vanilla


@pytest.fixture
def state_assets(tmp_path):
    def box(lo, hi):
        return {"from": lo, "to": hi, "faces": {
            face: {"texture": "#all"} for face in geom._FACE_DEF}}

    base = box([0, 0, 0], [16, 8, 16])
    step = box([8, 8, 0], [16, 16, 16])
    # Minecraft omits some contact faces rather than supplying closed boxes.
    step["faces"].pop("down")
    shapes = {
        "straight": [base, step],
        "inner_left": [base, step, box([0, 8, 8], [8, 16, 16])],
        "outer_left": [base, box([8, 8, 8], [16, 16, 16])],
    }
    files = {}
    variants = {}
    for shape, elements in shapes.items():
        model = "test:block/" + shape
        files["assets/test/models/block/" + shape + ".json"] = {
            "textures": {"all": "test:block/stone"}, "elements": elements}
        for facing, y in (("east", 0), ("south", 90), ("west", 180), ("north", 270)):
            for half in ("bottom", "top"):
                variants[f"facing={facing},half={half},shape={shape}"] = {
                    "model": model, "x": 180 if half == "top" else 0, "y": y}
    files["assets/test/blockstates/stairs.json"] = {"variants": variants}
    files["assets/test/blockstates/slab.json"] = {"variants": {
        "type=bottom": {"model": "test:block/straight"},
        "type=top": {"model": "test:block/top_slab"},
        "type=double": {"model": "test:block/full"}}}
    for name, elements in {
        "top_slab": [box([0, 8, 0], [16, 16, 16])],
        "full": [box([0, 0, 0], [16, 16, 16])],
        "post": [box([6, 0, 6], [10, 16, 10])],
        "rail": [box([6, 6, 0], [10, 9, 8])],
    }.items():
        files["assets/test/models/block/" + name + ".json"] = {
            "textures": {"all": "test:block/stone"}, "elements": elements}
    files["assets/test/blockstates/fence.json"] = {"multipart": [
        {"apply": {"model": "test:block/post"}},
        {"when": {"north": "true"}, "apply": {"model": "test:block/rail"}},
        {"when": {"AND": [{"south": "true"}, {"mode": "a|b"}]},
         "apply": {"model": "test:block/rail", "y": 180}},
        {"when": {"OR": [{"east": "true"}, {"west": "true"}]},
         "apply": {"model": "test:block/rail", "y": 90}},
    ]}
    path = tmp_path / "models.jar"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, json.dumps(data))
    assets = vanilla.VanillaAssets(str(path))
    assets.add_mod_jar(str(path), "test")
    return assets


def mesh(quads):
    return np.array([q["verts"][i] for q in quads for i in (0, 1, 2, 0, 2, 3)]).reshape(-1, 3, 3)


def assert_closed(triangles):
    edges = Counter()
    winding = Counter()
    for tri in np.round(triangles, 7):
        points = list(map(tuple, tri))
        for a, b in zip(points, points[1:] + points[:1]):
            edge = tuple(sorted((a, b)))
            edges[edge] += 1
            winding[edge] += 1 if a < b else -1
    assert set(edges.values()) == {2}
    assert set(winding.values()) == {0}


@pytest.mark.parametrize("shape,volume", [("straight", .75), ("inner_left", .875), ("outer_left", .625)])
@pytest.mark.parametrize("half", ["bottom", "top"])
@pytest.mark.parametrize("facing", ["east", "south", "west", "north"])
def test_stairs_keep_shape_half_facing_and_closed_union(state_assets, shape, volume, half, facing):
    geometry = state_assets.parse_elements("test:stairs", {
        "facing": facing, "half": half, "shape": shape})
    triangles = mesh(geom.geometry_to_tris(geometry))
    assert_closed(triangles)
    actual_volume = np.einsum("ij,ij->i", triangles[:, 0],
                             np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6
    assert actual_volume == pytest.approx(volume)
    if shape == "straight":
        # The exposed tread lies in the opposite half from the upper step.
        tread = [q for q in geom.geometry_to_tris(geometry)
                 if np.allclose(np.asarray(q["verts"])[:, 1], .5)]
        centre = np.concatenate([q["verts"] for q in tread]).mean(axis=0)
        expected = {"east": (.25, .5), "south": (.5, .25),
                    "west": (.75, .5), "north": (.5, .75)}[facing]
        assert centre[[0, 2]] == pytest.approx(expected)


def test_slab_selects_top_and_double_variants(state_assets):
    top = mesh(geom.geometry_to_tris(state_assets.parse_elements("test:slab", {"type": "top"})))
    double = mesh(geom.geometry_to_tris(state_assets.parse_elements("test:slab", {"type": "double"})))
    assert top[:, :, 1].min() == pytest.approx(.5)
    assert double[:, :, 1].min() == pytest.approx(0)
    assert double[:, :, 1].max() == pytest.approx(1)


def test_multipart_keeps_only_connected_parts(state_assets):
    isolated = state_assets.parse_elements("test:fence", {"north": "false", "south": "false"})
    connected = state_assets.parse_elements("test:fence", {
        "north": "true", "south": "true", "mode": "b", "east": "true"})
    assert len(isolated.elements) == 1
    assert len(connected.elements) == 4
    assert_closed(mesh(geom.geometry_to_tris(connected)))


@pytest.mark.parametrize("model_format", ["glb", "obj"])
def test_model_export_reads_saved_states_for_detail_geometry(monkeypatch, state_assets, model_format):
    xyz = np.array([[0, 10, 0], [2, 10, 0]], dtype=np.int16)
    table = [("test:stairs", {"facing": face, "half": "bottom", "shape": "straight"})
             for face in ("east", "west")]
    monkeypatch.setattr(server.render, "extract_voxels_states", lambda *args:
                        (xyz, np.array([0, 1]), np.array([2, 2]), table))
    monkeypatch.setattr(server.render, "extract_voxels", lambda *args:
                        pytest.fail("detail export must retain saved block properties"))
    monkeypatch.setattr(server, "_vanilla_assets", state_assets)
    data, _, _ = server.World.__new__(server.World).export_obj_voxel(
        0, 0, 1, 1, 2, True, 10, 10, (0, 0, 2, 0), model_format=model_format)
    if model_format == "glb":
        length = struct.unpack_from("<I", data, 12)[0]
        doc = json.loads(data[20:20 + length])
        view = doc["bufferViews"][0]
        points = np.frombuffer(data, dtype="<f4", count=doc["accessors"][0]["count"] * 3,
                               offset=28 + length + view["byteOffset"]).reshape(-1, 3) * 1000
    else:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            text = next(archive.read(n).decode() for n in archive.namelist() if n.endswith(".obj"))
        points = np.array([list(map(float, line.split()[1:])) for line in text.splitlines()
                           if line.startswith("v ")])
    upper = points[points[:, 1] > 21.5]
    assert upper[upper[:, 0] < 3, 0].min() == pytest.approx(1)
    assert upper[upper[:, 0] > 3, 0].max() == pytest.approx(5)
