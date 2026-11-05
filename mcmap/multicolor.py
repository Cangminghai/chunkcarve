"""Deterministic Lab colour reduction and aligned multi-material 3MF export.

The meshes use the 3MF core format. Bambu/Orca part ``extruder`` metadata
maps the resulting materials to their 1-based virtual filament slots.
"""

from __future__ import annotations

import io
import json
import zipfile
from xml.etree import ElementTree as ET

import numpy as np

from . import voxel


CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_TYPE_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_D65 = np.array([0.95047, 1.0, 1.08883])
_RGB_TO_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                        [0.2126729, 0.7151522, 0.0721750],
                        [0.0193339, 0.1191920, 0.9503041]])


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    srgb = np.asarray(rgb, dtype=np.float64) / 255.0
    linear = np.where(srgb <= 0.04045, srgb / 12.92,
                      ((srgb + 0.055) / 1.055) ** 2.4)
    xyz = (linear @ _RGB_TO_XYZ.T) / _D65
    delta = 6 / 29
    f = np.where(xyz > delta ** 3, np.cbrt(xyz),
                 xyz / (3 * delta ** 2) + 4 / 29)
    return np.column_stack((116 * f[:, 1] - 16,
                            500 * (f[:, 0] - f[:, 1]),
                            200 * (f[:, 1] - f[:, 2])))


def _lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[:, 0] + 16) / 116
    f = np.column_stack((fy + lab[:, 1] / 500, fy,
                         fy - lab[:, 2] / 200))
    delta = 6 / 29
    xyz = np.where(f > delta, f ** 3, 3 * delta ** 2 * (f - 4 / 29)) * _D65
    linear = np.clip(xyz @ np.linalg.inv(_RGB_TO_XYZ).T, 0, 1)
    srgb = np.where(linear <= 0.0031308, linear * 12.92,
                    1.055 * linear ** (1 / 2.4) - 0.055)
    return np.rint(srgb * 255).astype(np.uint8)


def cluster_colors(colors: np.ndarray, color_count: int = 4
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Return (RGB palette, per-voxel indices), with at most 4 or 8 colours.

    Lloyd iterations run on unique CIE-Lab colours, weighted by voxel count.
    Deterministic farthest-point seeds make repeated exports reproducible.
    """
    if color_count not in (4, 8):
        raise ValueError("color_count 必须是 4 或 8")
    rgb = np.asarray(colors)
    if (rgb.ndim != 2 or rgb.shape[1] != 3 or not len(rgb)
            or not np.isfinite(rgb).all() or np.any((rgb < 0) | (rgb > 255))):
        raise ValueError("colors 必须是非空 N×3 RGB 数组（0~255）")
    rgb = np.rint(rgb).astype(np.uint8)
    unique, inverse, counts = np.unique(rgb, axis=0, return_inverse=True,
                                         return_counts=True)
    if len(unique) <= color_count:
        return unique, inverse
    lab = _rgb_to_lab(unique)
    centers = [lab[int(np.argmax(counts))]]
    for _ in range(1, color_count):
        distances = np.min(np.sum((lab[:, None] - centers) ** 2, axis=2), axis=1)
        centers.append(lab[int(np.argmax(distances * counts))])
    centers = np.asarray(centers)
    labels = np.full(len(unique), -1, dtype=np.int64)
    for _ in range(64):
        assigned = np.argmin(np.sum((lab[:, None] - centers) ** 2, axis=2), axis=1)
        if np.array_equal(assigned, labels):
            break
        labels = assigned
        for index in range(len(centers)):
            mask = labels == index
            if np.any(mask):
                centers[index] = np.average(lab[mask], axis=0, weights=counts[mask])
    used, remap = np.unique(labels, return_inverse=True)
    palette = _lab_to_rgb(centers[used])
    # RGB quantisation may collapse two nearly identical Lab centres.
    palette, palette_map = np.unique(palette, axis=0, return_inverse=True)
    return palette, palette_map[remap][inverse]


def _xml(element: ET.Element) -> bytes:
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


def export_3mf(xyz: np.ndarray, colors: np.ndarray, color_count: int = 4,
               scale: float = 2.0, model_name: str = "mcmap") -> tuple[bytes, dict]:
    """Export full solid voxels as one aligned assembly of closed colour parts.

    ``xyz`` uses Minecraft (X, Y-up, Z) block coordinates, ``colors`` RGB bytes.
    Output is millimetres with Z up and its minimum corner at the origin.
    Water and decorative non-solid blocks must be filtered by the caller.
    """
    xyz = np.asarray(xyz)
    if (xyz.ndim != 2 or xyz.shape[1] != 3 or not len(xyz)
            or not np.isfinite(xyz).all() or np.any(xyz != np.floor(xyz))):
        raise ValueError("xyz 必须是非空 N×3 整数坐标数组")
    if len(colors) != len(xyz):
        raise ValueError("colors 与 xyz 长度不一致")
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("scale 必须是大于 0 的有限数")
    xyz = xyz.astype(np.int64)
    if len(np.unique(xyz, axis=0)) != len(xyz):
        raise ValueError("xyz 不能含有重复坐标")
    palette, labels = cluster_colors(colors, color_count)
    origin = tuple(int(v) for v in xyz.min(axis=0))
    size = (xyz.max(axis=0) - xyz.min(axis=0) + 1) * scale
    meshes = []
    for i, color in enumerate(palette):
        mask = labels == i
        flat, _ = voxel.build_voxel_mesh(xyz[mask],
                                         np.tile(color, (int(mask.sum()), 1)),
                                         scale=scale, origin=origin)
        # Rotate from Y-up to Z-up without changing orientation/normal winding.
        printer = flat[:, [0, 2, 1]].copy()
        printer[:, 1] = size[2] - printer[:, 1]
        meshes.append(voxel.index_mesh(printer))
    return _package_3mf(meshes, palette, size[[0, 2, 1]], model_name)


def export_slice_3mf(cells: np.ndarray, colors: np.ndarray, scale: float,
                     layer_height: float, color_count: int = 4,
                     model_name: str = "slice") -> tuple[bytes, dict]:
    """Keep each colour solid closed, using the slice's flat millimetre frame."""
    palette, labels = cluster_colors(colors, color_count)
    meshes = []
    for i, color in enumerate(palette):
        mask = labels == i
        flat, _ = voxel.build_voxel_mesh(cells[mask][:, [0, 2, 1]],
                                         np.tile(color, (int(mask.sum()), 1)),
                                         scale=scale, origin=(0, 0, 0))
        printer = flat[:, [0, 2, 1]].copy()
        printer[:, 2] *= layer_height / scale
        # Swapping Y/Z reverses handedness; restore outward triangle winding.
        printer = printer.reshape(-1, 3, 3)[:, [0, 2, 1], :].reshape(-1, 3)
        meshes.append(voxel.index_mesh(printer))
    size = (cells.max(axis=0) + 1) * [scale, scale, layer_height]
    return _package_3mf(meshes, palette, size, model_name)


def _package_3mf(meshes, palette, size, model_name):
    hex_colors = ["#" + "".join(f"{int(c):02X}" for c in rgb) for rgb in palette]
    count = len(palette)
    assembly_id = count + 2
    model = ET.Element("model", {"xmlns": CORE_NS, "unit": "millimeter",
                                "xml:lang": "en-US",
                                "xmlns:bamboo_slicer": "http://schemas.bambulab.com/package/2021"})
    # Bambu's importer gates filament config loading on this dialect prefix.
    # Record the actual generator separately; no slicer execution is implied.
    ET.SubElement(model, "metadata", name="Application").text = "BambuStudio-0.0.0+ChunkCarve"
    ET.SubElement(model, "metadata", name="Designer").text = "ChunkCarve"
    ET.SubElement(model, "metadata", name="Description").text = (
        "Generated by ChunkCarve. Application identifies the Bambu-compatible import dialect.")
    # The supported legacy key is an XSD-valid QName; "3mfVersion" is not.
    ET.SubElement(model, "metadata", name="bamboo_slicer:Version3mf").text = "1"
    ET.SubElement(model, "metadata", name="Title").text = model_name
    resources = ET.SubElement(model, "resources")
    materials = ET.SubElement(resources, "basematerials", id="1")
    for i, color in enumerate(hex_colors):
        ET.SubElement(materials, "base", name=f"Colour {i + 1}",
                      displaycolor=color + "FF")

    config = ET.Element("config")
    obj_config = ET.SubElement(config, "object", id=str(assembly_id))
    ET.SubElement(obj_config, "metadata", key="name", value=model_name)
    ET.SubElement(obj_config, "metadata", key="extruder", value="1")
    triangle_count = 0
    vertex_count = 0
    active_objects = []
    for i, (vertices, triangles) in enumerate(meshes):
        if not len(triangles):
            continue
        obj_id = str(i + 2)
        active_objects.append(obj_id)
        obj = ET.SubElement(resources, "object", id=obj_id, type="model",
                            name=f"Colour {i + 1}", pid="1", pindex=str(i))
        mesh = ET.SubElement(obj, "mesh")
        vertex_xml = ET.SubElement(mesh, "vertices")
        for x, y, z in vertices:
            ET.SubElement(vertex_xml, "vertex", x=f"{x:.9g}", y=f"{y:.9g}",
                          z=f"{z:.9g}")
        triangle_xml = ET.SubElement(mesh, "triangles")
        for a, b, c in triangles:
            ET.SubElement(triangle_xml, "triangle", v1=str(a), v2=str(b), v3=str(c))
        triangle_count += len(triangles)
        vertex_count += len(vertices)
        part = ET.SubElement(obj_config, "part", id=obj_id, subtype="normal_part")
        ET.SubElement(part, "metadata", key="name", value=f"Colour {i + 1}")
        ET.SubElement(part, "metadata", key="extruder", value=str(i + 1))

    assembly = ET.SubElement(resources, "object", id=str(assembly_id),
                             type="model", name=model_name)
    components = ET.SubElement(assembly, "components")
    for obj_id in active_objects:
        ET.SubElement(components, "component", objectid=obj_id)
    ET.SubElement(ET.SubElement(model, "build"), "item",
                  objectid=str(assembly_id))
    plate = ET.SubElement(config, "plate")
    ET.SubElement(plate, "metadata", key="plater_id", value="1")
    instance = ET.SubElement(plate, "model_instance")
    ET.SubElement(instance, "metadata", key="object_id", value=str(assembly_id))
    ET.SubElement(instance, "metadata", key="instance_id", value="0")

    types = ET.Element("Types", xmlns=_TYPE_NS)
    ET.SubElement(types, "Default", Extension="rels",
                  ContentType="application/vnd.openxmlformats-package.relationships+xml")
    ET.SubElement(types, "Default", Extension="model",
                  ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml")
    ET.SubElement(types, "Override", PartName="/Metadata/model_settings.config",
                  ContentType="application/xml")
    ET.SubElement(types, "Override", PartName="/Metadata/project_settings.config",
                  ContentType="application/json")
    rels = ET.Element("Relationships", xmlns=_REL_NS)
    ET.SubElement(rels, "Relationship", Id="rel-1", Target="/3D/3dmodel.model",
                  Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel")
    settings = {"filament_colour": hex_colors, "filament_type": ["PLA"] * count,
                "filament_settings_id": ["Generic PLA"] * count,
                "filament_diameter": ["1.75"] * count}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xml(types))
        archive.writestr("_rels/.rels", _xml(rels))
        archive.writestr("3D/3dmodel.model", _xml(model))
        archive.writestr("Metadata/model_settings.config", _xml(config))
        archive.writestr("Metadata/project_settings.config", json.dumps(settings))
    return output.getvalue(), {"triangles": triangle_count, "vertices": vertex_count,
                               "colors": hex_colors, "slots": list(range(1, count + 1)),
                               "color_count": count,
                               "size_mm": [float(v) for v in size]}
