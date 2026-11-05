"""Verify actual colour model payloads, print dimensions and archive references."""

import io
import json
import struct
import zipfile
from collections import Counter
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from PIL import Image

from mcmap import export


def read_glb(data):
    magic, version, length = struct.unpack_from("<4sII", data)
    assert (magic, version, length) == (b"glTF", 2, len(data))
    size, kind = struct.unpack_from("<II", data, 12)
    assert kind == 0x4E4F534A and size % 4 == 0
    document = json.loads(data[20:20 + size])
    offset = 20 + size
    binary_size, kind = struct.unpack_from("<II", data, offset)
    assert kind == 0x004E4942 and binary_size % 4 == 0
    binary = data[offset + 8:]
    assert len(binary) == binary_size == document["buffers"][0]["byteLength"]
    assert all("uri" not in item for group in ("buffers", "images")
               for item in document[group])
    return document, binary


def accessor(document, binary, index):
    item = document["accessors"][index]
    view = document["bufferViews"][item["bufferView"]]
    size = {"VEC3": 3, "VEC2": 2, "SCALAR": 1}[item["type"]]
    dtype = {5126: "<f4", 5125: "<u4"}[item["componentType"]]
    return np.frombuffer(binary, dtype=dtype, count=item["count"] * size,
                         offset=view.get("byteOffset", 0)).reshape(-1, size)


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_default_slice_glb_embeds_texture_and_preserves_mm_dimensions(axis):
    xyz = np.array([[x, y, z] for x in range(-2, 0) for y in range(10, 13)
                    for z in range(20, 24)])
    data, manifest = export.export_batch_slices(xyz, axis=axis, scale=2.5,
                                               thickness=1.25)
    assert manifest["format"] == "glb"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for item in manifest["slices"]:
            assert item["body"].endswith(".glb")
            document, binary = read_glb(archive.read(item["body"]))
            primitive = document["meshes"][0]["primitives"][0]
            positions = accessor(document, binary, primitive["attributes"]["POSITION"])
            # glTF is Y-up and metres; printed slices are Z-up and millimetres.
            size = np.ptp(positions, axis=0)[[0, 2, 1]] * 1000
            assert size == pytest.approx(list(item["size_mm"].values()))
            image_view = document["bufferViews"][document["images"][0]["bufferView"]]
            image = Image.open(io.BytesIO(binary[image_view["byteOffset"]:
                                                image_view["byteOffset"] + image_view["byteLength"]]))
            assert image.format == "PNG"
            assert primitive["attributes"].keys() >= {"POSITION", "NORMAL", "TEXCOORD_0"}
            indices = accessor(document, binary, primitive["indices"]).ravel()
            assert indices.max() < len(positions)
            normals = accessor(document, binary, primitive["attributes"]["NORMAL"])
            assert np.linalg.norm(normals, axis=1) == pytest.approx(np.ones(len(normals)))


@pytest.mark.parametrize("model_format", ["glb", "obj", "stl", "3mf"])
def test_explicit_slice_format_and_single_selection(model_format):
    data, manifest = export.export_batch_slices(
        np.array([[0, 10, 0], [1, 10, 0], [1, 11, 0]]),
        ["minecraft:stone", "minecraft:dirt", "minecraft:stone"],
        model_format=model_format, mode="single", slice_index=10)
    assert manifest["format"] == model_format
    assert len(manifest["slices"]) == 1
    item = manifest["slices"][0]
    assert item["axis_index"] == 10
    assert item["body"].endswith("." + model_format)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        body = archive.read(item["body"])
        assert archive.read(item["top"]).startswith(b"\x89PNG")
        if model_format == "obj":
            text = body.decode()
            material = next(line.split()[1] for line in text.splitlines() if line.startswith("mtllib "))
            mtl = archive.read(material).decode()
            for line in mtl.splitlines():
                if line.startswith("map_Kd "):
                    assert archive.read(line.split()[1]).startswith(b"\x89PNG")
            assert "vt " in text and "usemtl " in text
        elif model_format == "3mf":
            with zipfile.ZipFile(io.BytesIO(body)) as package:
                root = ET.fromstring(package.read("3D/3dmodel.model"))
                assert root.attrib["unit"] == "millimeter"
                settings = json.loads(package.read("Metadata/project_settings.config"))
                assert len(settings["filament_colour"]) == 2
        elif model_format == "stl":
            assert len(body) == 84 + struct.unpack_from("<I", body, 80)[0] * 50
        else:
            read_glb(body)


def test_unsupported_slice_format_is_rejected():
    with pytest.raises(ValueError, match="format|格式"):
        export.export_batch_slices(np.array([[0, 0, 0]]), model_format="step")


@pytest.mark.parametrize("model_format", ["glb", "obj", "stl", "3mf"])
def test_multiple_selected_slices_keep_world_order_and_only_chosen_models(model_format):
    xyz = np.array([[0, 10, 0], [0, 11, 0], [0, 12, 0], [0, 13, 0]])
    data, manifest = export.export_batch_slices(xyz, mode="selected", slice_indices=[13, 10, 13],
                                               model_format=model_format)
    assert [item["axis_index"] for item in manifest["slices"]] == [10, 13]
    assert [item["index"] for item in manifest["slices"]] == [1, 4]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert len([name for name in archive.namelist() if name.endswith("." + model_format)]) == 2


@pytest.mark.parametrize("indices", [None, [], [11], [10, 11], [999]])
def test_invalid_multiple_slice_selection_is_rejected(indices):
    with pytest.raises(ValueError, match="切片|坐标"):
        export.export_batch_slices(np.array([[0, 10, 0]]), mode="selected", slice_indices=indices)


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("stand", [False, True])
def test_slice_3mf_colour_parts_are_closed_and_match_stl_dimensions_and_volume(axis, stand):
    xyz = np.array([[x, y, z] for x in range(2) for y in range(10, 13)
                    for z in range(4) if (x, y, z) != (0, 11, 1)])
    names = ["minecraft:stone" if x == 0 else "minecraft:dirt" for x, _, _ in xyz]
    options = dict(axis=axis, stand=stand, scale=2.5, thickness=1.25)
    data, manifest = export.export_batch_slices(xyz, names, model_format="3mf", color_count=8, **options)
    stl_data, stl_manifest = export.export_batch_slices(xyz, names, model_format="stl", **options)
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}

    def volume(triangles):
        return np.einsum("ij,ij->i", triangles[:, 0],
                         np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6

    with zipfile.ZipFile(io.BytesIO(data)) as archive, zipfile.ZipFile(io.BytesIO(stl_data)) as stls:
        for item, stl_item in zip(manifest["slices"], stl_manifest["slices"]):
            with zipfile.ZipFile(io.BytesIO(archive.read(item["body"]))) as package:
                root = ET.fromstring(package.read("3D/3dmodel.model"))
            parts = []
            for mesh in root.findall(".//m:mesh", ns):
                vertices = np.array([[float(v.get(key)) for key in ("x", "y", "z")]
                                     for v in mesh.findall("m:vertices/m:vertex", ns)])
                indices = [[int(t.get(key)) for key in ("v1", "v2", "v3")]
                           for t in mesh.findall("m:triangles/m:triangle", ns)]
                edges = Counter(tuple(sorted((a, b))) for face in indices
                                for a, b in zip(face, face[1:] + face[:1]))
                assert set(edges.values()) == {2}
                triangles = vertices[np.asarray(indices)]
                assert volume(triangles) > 0
                parts.append(triangles)
            triangles = np.concatenate(parts)
            stl = stls.read(stl_item["body"])
            records = np.frombuffer(stl, offset=84, dtype=np.dtype([
                ("normal", "<f4", 3), ("vertices", "<f4", (3, 3)), ("attr", "<u2")]))
            assert volume(triangles) == pytest.approx(volume(records["vertices"]))
            assert np.ptp(triangles.reshape(-1, 3), axis=0) == pytest.approx(
                np.ptp(records["vertices"].reshape(-1, 3), axis=0))


@pytest.mark.parametrize("axis,expected_top", [("y", (255, 0, 0)), ("x", (0, 255, 0)), ("z", (0, 255, 0))])
def test_glb_cut_surface_uses_real_texture_pixels(axis, expected_top):
    class Assets:
        def get_textures(self, name):
            return "red", "green", "blue"

        def _read_texture_png(self, name):
            color = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255)}[name]
            png = io.BytesIO()
            Image.new("RGBA", (16, 16), color + (255,)).save(png, format="PNG")
            return png.getvalue()

    data, manifest = export.export_batch_slices(np.array([[0, 0, 0]]), axis=axis, assets=Assets())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        item = manifest["slices"][0]
        document, binary = read_glb(archive.read(item["body"]))
        top = Image.open(io.BytesIO(archive.read(item["top"]))).convert("RGB")
        assert top.getpixel((8, 8)) == expected_top
    primitive = document["meshes"][0]["primitives"][0]
    normals = accessor(document, binary, primitive["attributes"]["NORMAL"])
    uv = accessor(document, binary, primitive["attributes"]["TEXCOORD_0"])
    top_uv = uv[normals[:, 1] > 0.9].mean(axis=0)
    view = document["bufferViews"][document["images"][0]["bufferView"]]
    image = Image.open(io.BytesIO(binary[view["byteOffset"]:view["byteOffset"] + view["byteLength"]])).convert("RGB")
    assert image.getpixel((int(top_uv[0] * image.width), int(top_uv[1] * image.height))) == expected_top
