"""Exercise the export HTTP boundary and desktop binary-save bridge."""

import base64
import http.client
import io
import json
from pathlib import Path
import runpy
import struct
import threading
import zipfile

import numpy as np
import pytest

from mcmap import server


def test_preview_crops_before_hiding_interior_blocks(monkeypatch):
    xyz = np.array([[x, y, z] for x in range(3) for y in range(10, 13)
                    for z in range(3)], dtype=np.int16)
    states = np.zeros(len(xyz), dtype=np.uint16)
    flags = np.zeros(len(xyz), dtype=np.uint8)
    monkeypatch.setattr(server.render, 'extract_voxels_states', lambda *args:
                        (xyz, states, flags, [('minecraft:stone', None)]))
    world = server.World.__new__(server.World)
    result = world.voxels_states(0, 0, 1, 1, 10, 12, True, (1, 1, 1, 1))
    assert result['count'] == 3
    assert result['yMin'] == 10 and result['yMax'] == 12


@pytest.fixture
def export_http(monkeypatch):
    xyz = np.array([[0, 10, 0], [1, 10, 0], [2, 10, 0],
                    [0, 10, 1], [9, 10, 9]], dtype=np.int16)
    ids = np.arange(5)
    flags = np.array([0, 0, 1, 2, 0], dtype=np.uint8)
    monkeypatch.setattr(server.render, "extract_voxels", lambda *args: (xyz, ids, flags))
    monkeypatch.setattr(server.render, "id_name", lambda i: ["minecraft:stone", "minecraft:dirt"][i % 2])
    monkeypatch.setattr(server.render, "extract_voxels_states", lambda *args:
                        (xyz, ids, flags, [(server.render.id_name(i), None) for i in range(5)]))
    monkeypatch.setattr(server, "WORLD", server.World.__new__(server.World))
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
    yield connection
    connection.close()
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def test_3mf_http_clips_box_and_excludes_water_and_detail(export_http):
    export_http.request("GET", "/api/export_voxel.3mf?cx0=0&cz0=0&cx1=1&cz1=1"
                        "&x0=0&z0=0&x1=2&z1=1&y0=10&y1=10&scale=2&colors=4")
    response = export_http.getresponse()
    data = response.read()
    assert response.status == 200
    assert response.getheader("Content-Type") == "model/3mf"
    assert response.getheader("X-Colors") == "2"
    assert response.getheader("X-Triangles") == "24"
    assert response.getheader("Content-Disposition").endswith('chunkcarve_ams_4.3mf"')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        settings = json.loads(archive.read("Metadata/project_settings.config"))
        assert len(settings["filament_colour"]) == 2


@pytest.mark.parametrize("options", ["colors=6", "colors=4&scale=nan", "colors=4&scale=0"])
def test_3mf_http_invalid_options_are_json_400(export_http, options):
    export_http.request("GET", "/api/export_voxel.3mf?cx0=0&cz0=0&cx1=1&cz1=1&" + options)
    response = export_http.getresponse()
    assert response.status == 400
    assert json.loads(response.read())["error"]


@pytest.mark.parametrize("extension", ["zip", "stl", "glb", "3mf"])
def test_desktop_save_uses_window_dialog_and_preserves_bytes(monkeypatch, tmp_path, extension):
    pytest.importorskip("webview")
    desktop = runpy.run_path(str(Path(__file__).parents[1] / "desktop.pyw"))
    api = desktop["SetupApi"]()
    output = tmp_path / ("model." + extension)
    calls = []

    class Window:
        def create_file_dialog(self, dialog_type, **kwargs):
            calls.append(kwargs)
            return [str(output)]

    monkeypatch.setitem(api.save_file.__globals__, "SETUP_WINDOW", Window())
    payload = b"PK\x00\xff\x01\x02"
    result = api.save_file(output.name, base64.b64encode(payload).decode())
    assert result["ok"]
    assert output.read_bytes() == payload
    assert calls[0]["save_filename"] == output.name
    assert "*." + extension in calls[0]["file_types"][0]


def test_desktop_cancel_does_not_write(monkeypatch, tmp_path):
    pytest.importorskip("webview")
    desktop = runpy.run_path(str(Path(__file__).parents[1] / "desktop.pyw"))
    api = desktop["SetupApi"]()

    class Window:
        def create_file_dialog(self, *args, **kwargs):
            return None

    monkeypatch.setitem(api.save_file.__globals__, "SETUP_WINDOW", Window())
    result = api.save_file(str(tmp_path / "cancelled.zip"), "UEs=")
    assert result == {"ok": False, "cancelled": True}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("model_format", [None, "glb", "obj", "stl", "3mf"])
def test_slice_http_delivers_selected_format(export_http, model_format):
    options = "" if model_format is None else "&format=" + model_format
    export_http.request("GET", "/api/export_slices.zip?x0=0&z0=0&x1=1&z1=0&y0=10&y1=10" + options)
    response = export_http.getresponse()
    data = response.read()
    assert response.status == 200
    assert response.getheader("X-Model-Format") == (model_format or "glb")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == (model_format or "glb")
        assert archive.read(manifest["slices"][0]["body"])


@pytest.mark.parametrize("options", ["format=step", "format=3mf&colors=6", "scale=nan", "mode=other", "mode=single&index=999"])
def test_slice_http_rejects_invalid_options(export_http, options):
    export_http.request("GET", "/api/export_slices.zip?x0=0&z0=0&x1=1&z1=0&y0=10&y1=10&" + options)
    response = export_http.getresponse()
    assert response.status == 400
    assert json.loads(response.read())["error"]


def test_glb_http_embeds_colour_model(export_http):
    export_http.request("GET", "/api/export_voxel.glb?cx0=0&cz0=0&cx1=1&cz1=1"
                        "&x0=0&z0=0&x1=1&z1=0&y0=10&y1=10&scale=2")
    response = export_http.getresponse()
    data = response.read()
    assert response.status == 200
    assert response.getheader("Content-Type") == "model/gltf-binary"
    assert response.getheader("Content-Disposition").endswith('.glb"')
    assert struct.unpack_from("<4sII", data) == (b"glTF", 2, len(data))
    size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + size])
    assert document["images"][0]["mimeType"] == "image/png"
    assert response.getheader("X-Textures") == "2"


def test_slice_http_multiple_selection_keeps_only_requested_slices(export_http):
    export_http.request("GET", "/api/export_slices.zip?x0=0&z0=0&x1=9&z1=9&y0=10&y1=10"
                        "&axis=x&mode=selected&indices=9,0&format=glb")
    response = export_http.getresponse()
    data = response.read()
    assert response.status == 200 and response.getheader("X-Slices") == "2"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert [item["axis_index"] for item in manifest["slices"]] == [0, 9]
