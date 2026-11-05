import io
import json
import zipfile
from collections import Counter
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from mcmap import multicolor


NS = {"m": multicolor.CORE_NS}


def _read(data):
    archive = zipfile.ZipFile(io.BytesIO(data))
    return archive, ET.fromstring(archive.read("3D/3dmodel.model"))


def _mesh(obj):
    vertices = np.array([[float(v.get(k)) for k in ("x", "y", "z")]
                         for v in obj.findall("m:mesh/m:vertices/m:vertex", NS)])
    faces = np.array([[int(f.get(k)) for k in ("v1", "v2", "v3")]
                      for f in obj.findall("m:mesh/m:triangles/m:triangle", NS)])
    return vertices, faces


def _assert_closed(vertices, faces):
    assert faces.min() == 0
    assert faces.max() < len(vertices)
    directed = Counter()
    for a, b, c in faces:
        for first, second in ((a, b), (b, c), (c, a)):
            directed[(int(first), int(second))] += 1
    assert all(count == 1 and directed[(b, a)] == 1
               for (a, b), count in directed.items())
    triangles = vertices[faces]
    volume = np.sum(np.einsum("ij,ij->i", triangles[:, 0],
                              np.cross(triangles[:, 1], triangles[:, 2]))) / 6
    assert volume > 0
    return volume


def test_lab_reference_and_round_trip():
    rgb = np.array([[0, 0, 0], [255, 255, 255], [255, 0, 0], [15, 129, 244]])
    lab = multicolor._rgb_to_lab(rgb)
    np.testing.assert_allclose(lab[0], [0, 0, 0], atol=1e-6)
    np.testing.assert_allclose(lab[1], [100, 0, 0], atol=2e-5)
    np.testing.assert_allclose(lab[2], [53.2408, 80.0925, 67.2032], atol=1e-4)
    np.testing.assert_array_equal(multicolor._lab_to_rgb(lab), rgb)


@pytest.mark.parametrize("count", [4, 8])
def test_colour_count_limit_determinism_and_assignment(count):
    rng = np.random.default_rng(123)
    rgb = rng.integers(0, 256, size=(200, 3), dtype=np.uint8)
    palette, labels = multicolor.cluster_colors(rgb, count)
    assert len(palette) <= count
    assert len(labels) == len(rgb)
    assert set(labels) == set(range(len(palette)))
    palette2, labels2 = multicolor.cluster_colors(rgb[::-1], count)
    np.testing.assert_array_equal(palette, palette2)
    np.testing.assert_array_equal(labels, labels2[::-1])


def test_3mf_package_slots_components_and_materials():
    xyz = np.array([[-2, 5, -3], [-1, 5, -3]])
    data, stats = multicolor.export_3mf(xyz, [[255, 0, 0], [0, 0, 255]], scale=2)
    archive, model = _read(data)
    assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model",
            "Metadata/model_settings.config", "Metadata/project_settings.config"} <= set(archive.namelist())
    assert model.get("unit") == "millimeter"
    assert model.find("m:metadata[@name='Application']", NS).text == "BambuStudio-0.0.0+ChunkCarve"
    assert model.find("m:metadata[@name='Designer']", NS).text == "ChunkCarve"
    assert model.find("m:metadata[@name='bamboo_slicer:Version3mf']", NS).text == "1"
    bases = model.findall("m:resources/m:basematerials/m:base", NS)
    assert {base.get("displaycolor") for base in bases} == {"#FF0000FF", "#0000FFFF"}
    objects = model.findall("m:resources/m:object", NS)
    assert len(objects) == 3
    assembly = objects[-1]
    component_ids = [c.get("objectid") for c in assembly.findall("m:components/m:component", NS)]
    assert component_ids == [obj.get("id") for obj in objects[:-1]]
    assert model.find("m:build/m:item", NS).get("objectid") == assembly.get("id")
    config = ET.fromstring(archive.read("Metadata/model_settings.config"))
    parts = config.findall("object/part")
    assert [p.get("id") for p in parts] == component_ids
    assert [p.find("metadata[@key='extruder']").get("value") for p in parts] == ["1", "2"]
    instance = config.find("plate/model_instance")
    assert instance.find("metadata[@key='object_id']").get("value") == assembly.get("id")
    assert instance.find("metadata[@key='instance_id']").get("value") == "0"
    settings = json.loads(archive.read("Metadata/project_settings.config"))
    assert settings["filament_colour"] == stats["colors"]
    assert stats["slots"] == [1, 2]
    assert stats["size_mm"] == [4, 2, 2]
    assert stats["triangles"] == 24
    for obj in objects[:-1]:
        vertices, faces = _mesh(obj)
        assert _assert_closed(vertices, faces) == pytest.approx(8)
    all_vertices = np.vstack([_mesh(obj)[0] for obj in objects[:-1]])
    np.testing.assert_allclose(all_vertices.min(axis=0), [0, 0, 0])
    np.testing.assert_allclose(all_vertices.max(axis=0), [4, 2, 2])


@pytest.mark.parametrize("count", [4, 8])
def test_reduced_full_package_has_matching_filament_and_part_counts(count):
    rng = np.random.default_rng(456)
    rgb = rng.integers(0, 256, size=(40, 3), dtype=np.uint8)
    xyz = np.column_stack((np.arange(40), np.zeros((40, 2), dtype=np.int64)))
    data, stats = multicolor.export_3mf(xyz, rgb, color_count=count)
    archive, model = _read(data)
    settings = json.loads(archive.read("Metadata/project_settings.config"))
    bases = model.findall("m:resources/m:basematerials/m:base", NS)
    parts = ET.fromstring(archive.read("Metadata/model_settings.config")).findall("object/part")
    assert 1 <= len(bases) == len(parts) == stats["color_count"] <= count
    assert len(settings["filament_colour"]) == len(settings["filament_settings_id"]) == len(parts)
    assert stats["slots"] == list(range(1, len(parts) + 1))
    for obj in model.findall("m:resources/m:object", NS)[:-1]:
        _assert_closed(*_mesh(obj))


def test_same_colour_contact_culled_different_colour_contact_closed():
    xyz = np.array([[0, 0, 0], [1, 0, 0]])
    _, same = multicolor.export_3mf(xyz, [[128, 128, 128]] * 2)
    _, different = multicolor.export_3mf(xyz, [[0, 0, 0], [255, 255, 255]])
    assert same["triangles"] == 20
    assert different["triangles"] == 24
    assert same["color_count"] == 1


@pytest.mark.parametrize("other", [[1, 1, 0], [1, 1, 1]])
def test_edge_and_corner_contacts_remain_individually_closed(other):
    data, _ = multicolor.export_3mf(np.array([[0, 0, 0], other]), [[80, 90, 100]] * 2, scale=1)
    _, model = _read(data)
    vertices, faces = _mesh(model.find("m:resources/m:object", NS))
    assert len(vertices) == 16
    assert _assert_closed(vertices, faces) == pytest.approx(2)


def test_cavity_preserved_as_empty_interior():
    xyz = np.array([(x, y, z) for x in range(3) for y in range(3) for z in range(3)
                    if (x, y, z) != (1, 1, 1)])
    data, _ = multicolor.export_3mf(xyz, [[120, 120, 120]] * len(xyz), scale=1)
    _, model = _read(data)
    vertices, faces = _mesh(model.find("m:resources/m:object", NS))
    assert _assert_closed(vertices, faces) == pytest.approx(26)
    assert len(faces) == 120


@pytest.mark.parametrize("kwargs", [{"color_count": 6}, {"scale": 0}, {"scale": float("nan")}])
def test_export_rejects_invalid_options(kwargs):
    with pytest.raises(ValueError):
        multicolor.export_3mf(np.array([[0, 0, 0]]), [[1, 2, 3]], **kwargs)
