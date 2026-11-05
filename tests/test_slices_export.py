import io
import json
import zipfile

import numpy as np
import pytest
from PIL import Image

from mcmap import export


def _zip_manifest(data):
    zf = zipfile.ZipFile(io.BytesIO(data))
    manifest = json.loads(zf.read("manifest.json"))
    return zf, manifest


def test_batch_slices_three_axes_keep_all_components_and_air_transparent():
    # 两个相邻方块加一个断开的方块；中间没有空气 STL 面不会被误删。
    xyz = np.array([
        [0, 10, 0], [1, 10, 0], [3, 10, 0], [3, 10, 1],
    ], dtype=np.int16)
    names = ["minecraft:stone"] * len(xyz)
    box = (0, 0, 3, 1, 10, 10)

    for axis, expected_count in (("y", 1), ("x", 3), ("z", 2)):
        data, manifest = export.export_batch_slices(
            xyz, names, box=box, axis=axis, scale=2.0, thickness=2.0, model_format="stl")
        assert manifest["axis"] == axis
        assert manifest["px_per_block"] == 16
        assert manifest["mm_per_px"] == 0.125
        assert len(manifest["slices"]) == expected_count

        zf, parsed = _zip_manifest(data)
        assert parsed == manifest
        assert "manifest.json" in zf.namelist()
        for item in manifest["slices"]:
            body = zf.read(item["body"])
            assert body[:4] != b"PK\x03\x04"
            assert int.from_bytes(body[80:84], "little") > 0
            image = Image.open(io.BytesIO(zf.read(item["top"]))).convert("RGBA")
            assert image.size == (
                item["size_blocks"]["x"] * 16 if axis != "x" else item["size_blocks"]["z"] * 16,
                item["size_blocks"]["z"] * 16 if axis == "y" else item["size_blocks"]["y"] * 16,
            )
        zf.close()


@pytest.mark.parametrize("axis", ["y", "x", "z"])
def test_flat_slice_manifest_sizes_match_stl_and_png_print_coordinates(axis):
    origin = np.array([-4, 10, 20])
    xyz = np.array([[x, y, z] for x in range(2) for y in range(3)
                    for z in range(4)], dtype=np.int16) + origin
    scale, thickness = 2.5, 1.25
    data, manifest = export.export_batch_slices(
        xyz, axis=axis, scale=scale, thickness=thickness, model_format="stl")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for item in manifest["slices"]:
            stl = zf.read(item["body"])
            records = np.frombuffer(stl, offset=84, dtype=np.dtype([
                ("normal", "<f4", 3), ("vertices", "<f4", (3, 3)),
                ("attribute", "<u2")]))
            vertices = records["vertices"].reshape(-1, 3)
            expected = [item["size_mm"][dimension] for dimension in ("x", "y", "z")]
            assert vertices.min(axis=0) == pytest.approx([0, 0, 0])
            assert vertices.max(axis=0) == pytest.approx(expected)
            assert item["size_mm"]["z"] == thickness
            png = Image.open(io.BytesIO(zf.read(item["top"])))
            assert np.array(png.size) * item["mm_per_px"] == pytest.approx(expected[:2])


@pytest.mark.parametrize("parameter", ["scale", "thickness"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 0, -1])
def test_slice_dimensions_must_be_finite_positive_numbers(parameter, value):
    with pytest.raises(ValueError):
        export.export_batch_slices(np.array([[0, 0, 0]]), **{parameter: value})
