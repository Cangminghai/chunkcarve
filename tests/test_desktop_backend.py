"""A healthy old API must not capture windows opened by a newer desktop build."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import runpy
import threading
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

from mcmap import config, server


@pytest.fixture
def desktop():
    pytest.importorskip('webview')
    return runpy.run_path(str(Path(__file__).parents[1] / 'desktop.pyw'))


@contextmanager
def service(capabilities=None, handler=None):
    class LegacyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/api/info':
                payload, status = {'name': 'old world'}, 200
            elif self.path == '/api/capabilities' and capabilities is not None:
                payload, status = capabilities, 200
            else:
                payload, status = {'error': 'not found'}, 404
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler or LegacyHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_info_200_without_current_api_cannot_be_reused(desktop):
    with service() as port:
        with urlopen(f'http://127.0.0.1:{port}/api/info') as response:
            assert response.status == 200
        assert not desktop['server_alive'](port)


def test_exact_current_backend_can_be_reused(desktop):
    with service(server.capabilities()) as port:
        assert desktop['server_alive'](port)


@pytest.mark.parametrize('change', [
    {'build_id': 'older-build'},
    {'api_revision': 0},
    {'app_dir': str(Path(config.APPDIR) / 'another-installation')},
    {'features': []},
])
def test_mixed_build_or_configuration_directory_is_rejected(desktop, change):
    with service({**server.capabilities(), **change}) as port:
        assert not desktop['server_alive'](port)


def test_capabilities_endpoint_does_not_depend_on_a_loaded_world():
    with service(handler=server.Handler) as port:
        with urlopen(f'http://127.0.0.1:{port}/api/capabilities') as response:
            actual = json.load(response)
    assert actual == server.capabilities()
    assert actual['service'] == 'chunkcarve' and actual['features'] == ['selection', 'preview']
    assert len(actual['build_id']) == 64


def test_desktop_starts_current_backend_on_another_port_when_default_is_old(desktop, monkeypatch):
    import webview

    class Event:
        def __iadd__(self, callback):
            return self

    urls, started, backends = [], [], []

    def current_main(port, open_browser=False):
        started.append(port)
        httpd = ThreadingHTTPServer(('127.0.0.1', port), server.Handler)
        backends.append(httpd)
        httpd.serve_forever()

    def window(title, **kwargs):
        assert '正在启动 ChunkCarve' in kwargs['html']
        assert not started, 'the visible window must be created before backend startup'
        return SimpleNamespace(load_url=urls.append,
                               events=SimpleNamespace(before_show=Event(), shown=Event()))

    monkeypatch.setattr(config, 'resolve', lambda: {})
    monkeypatch.setattr(server, 'configure', lambda *args: {})
    monkeypatch.setattr(server, 'main', current_main)
    monkeypatch.setattr(webview, 'create_window', window)
    monkeypatch.setattr(webview, 'start', lambda func, args, **kwargs: func(*args))
    desktop['main'].__globals__['_ensure_custom_window_icon'] = lambda *args: None
    try:
        with service() as old_port:
            monkeypatch.setattr(server, 'DEFAULT_PORT', old_port)
            desktop['main']()
            assert len(started) == 1 and started[0] != old_port
            assert urls == [f'http://127.0.0.1:{started[0]}/']
            assert desktop['server_alive'](started[0])
            with urlopen(f'http://127.0.0.1:{old_port}/api/info') as response:
                assert json.load(response)['name'] == 'old world'
    finally:
        for httpd in backends:
            httpd.shutdown()
            httpd.server_close()


@pytest.mark.parametrize('kind', ['folder', 'world', 'mods', 'jar'])
def test_picker_uses_owning_window_dialog(desktop, kind):
    import webview

    calls = []
    selected = 'C:/Minecraft/version.jar' if kind == 'jar' else 'C:/Minecraft'

    def pick(dialog_type, **options):
        calls.append((dialog_type, options))
        return [selected]

    desktop['SetupApi'].pick.__globals__['SETUP_WINDOW'] = SimpleNamespace(create_file_dialog=pick)
    assert desktop['SetupApi']().pick(kind) == selected
    assert calls[0][0] == (webview.FileDialog.OPEN if kind == 'jar' else webview.FileDialog.FOLDER)
    assert calls[0][1]['allow_multiple'] is False
    if kind == 'jar':
        assert '*.jar' in calls[0][1]['file_types'][0]


def test_picker_cancel_and_failure_are_distinct(desktop):
    globals_ = desktop['SetupApi'].pick.__globals__
    globals_['SETUP_WINDOW'] = SimpleNamespace(create_file_dialog=lambda *a, **k: None)
    assert desktop['SetupApi']().pick('folder') == ''

    def fail(*args, **kwargs):
        raise RuntimeError('picker unavailable')

    globals_['SETUP_WINDOW'] = SimpleNamespace(create_file_dialog=fail)
    globals_['log'] = lambda text: None
    with pytest.raises(RuntimeError, match='picker unavailable'):
        desktop['SetupApi']().pick('folder')


def test_backend_startup_error_is_visible_in_window(desktop, monkeypatch):
    messages = []
    monkeypatch.setattr(config, 'resolve', lambda: (_ for _ in ()).throw(ValueError('missing <world>')))
    desktop['_start_backend'].__globals__['log'] = lambda text: None
    desktop['_start_backend'](SimpleNamespace(load_html=messages.append))
    assert '启动失败' in messages[0]
    assert 'missing &lt;world&gt;' in messages[0]
