"""Minecraft 存档观察器 —— 桌面版

双击「桌面版.bat」或用 pythonw 运行：
- 若路径配置（config.json / 自动探测）已能找到存档 → 直接打开观察器窗口
- 否则显示首次运行向导：选存档目录 / 版本 jar / 模组目录 → 写入 config.json → 打开观察器
关闭窗口即退出（进程内启动的服务随进程结束）。
"""
import os
import sys

# 在 Windows 上程序最早期设置 AppUserModelID，使任务栏图标也生效（摆脱 Python 默认图标）
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ChunkCarve.App")
    except Exception:
        pass
import threading
import json
import time
import traceback
import urllib.request

ROOT = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    import webview
except Exception:
    pass

FROZEN = bool(getattr(sys, "frozen", False))
# 打包后：资源在解包目录（ROOT 已由 mcmap 内部处理），可写目录在 exe 旁
APPDIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else ROOT
LOG = os.path.join(APPDIR, "desktop_error.log")


def log(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


def server_alive(port: int) -> bool:
    from mcmap import server
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/capabilities", timeout=1) as r:
            current = json.load(r)
        expected = server.capabilities()
        return (all(current.get(key) == expected[key]
                    for key in ('service', 'api_revision', 'build_id'))
                and os.path.normcase(os.path.realpath(current.get('app_dir', '')))
                == os.path.normcase(expected['app_dir'])
                and set(expected['features']).issubset(current.get('features', [])))
    except Exception:
        return False


SETUP_WINDOW = None   # 原生窗口引用（不要放 api 实例里）

STARTUP_HTML = """<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">
<style>body{margin:0;background:#f6f7f9;color:#0f172a;font:16px/1.7 "Segoe UI","Microsoft YaHei",sans-serif;
min-height:100vh;display:grid;place-items:center}.message{max-width:600px;padding:32px;text-align:center}
p{color:#475569;font-size:14px;white-space:pre-wrap;overflow-wrap:anywhere}</style>
<div class="message"><h2>正在启动 ChunkCarve…</h2><p>正在读取本机配置并启动服务，请稍候。</p></div></html>"""

SETUP_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>首次运行设置</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; background:#0b0d10; color:#e8eaed; font:13px/1.6 "Segoe UI","Microsoft YaHei",sans-serif; }
  .wrap { max-width: 720px; margin: 0 auto; padding: 34px 28px; }
  h1 { font-size: 19px; margin: 0 0 6px; }
  p.sub { color:#8b93a1; margin: 0 0 26px; }
  .row { margin-bottom: 18px; }
  .row label { display:block; font-size:12px; color:#aab1bd; margin-bottom:6px; }
  .line { display:flex; gap:8px; }
  input { flex:1; background:#101216; border:1px solid #262b33; color:#e8eaed;
          border-radius:8px; padding:9px 11px; font:inherit; font-size:12px; outline:none; }
  input:focus { border-color:#4a7fe0; }
  button { background:#1a1e25; border:1px solid #2a3038; color:#d5dae2; border-radius:8px;
           padding:0 14px; font:inherit; font-size:12px; cursor:pointer; white-space:nowrap; }
  button:hover { background:#232833; }
  button.primary { background:#3b82f6; border-color:#3b82f6; color:#fff; padding:10px 26px; }
  button.primary:hover { background:#5b95f8; }
  .hint { color:#6f7683; font-size:11.5px; margin-top:4px; }
  .err { color:#ff6b6b; min-height:18px; margin-top:14px; }
  .foot { margin-top: 22px; display:flex; align-items:center; gap:14px; }
</style></head><body><div class="wrap">
  <h1>Minecraft 存档观察器 · 首次运行设置</h1>
  <p class="sub">选择要观察的存档和游戏资源位置（会保存到 exe 同目录的 config.json，之后可随时修改）。</p>

  <div class="row"><label>存档根目录（包含 level.dat，兼容旧版与 26.1+）</label>
    <div class="line"><input id="world" placeholder="例如 D:\\minecraft\\saves\\我的世界" value="__WORLD__">
      <button onclick="pick('world')">浏览…</button></div>
    <div class="hint">也可以直接把 .minecraft/saves 下的存档文件夹拖进来</div>
  </div>

  <div class="row"><label>原版版本 jar（渲染真实贴图需要，取自你本机的游戏文件）</label>
    <div class="line"><input id="jar" placeholder="例如 D:\\minecraft\\versions\\1.20.1\\1.20.1.jar" value="__JAR__">
      <button onclick="pick('jar')">浏览…</button></div>
  </div>

  <div class="row"><label>模组目录（可选，整合包的 mods 文件夹）</label>
    <div class="line"><input id="mods" placeholder="可选，例如 D:\\minecraft\\mods" value="__MODS__">
      <button onclick="pick('mods')">浏览…</button></div>
  </div>

  <div class="foot"><button class="primary" onclick="go(this)">开始</button>
    <span class="hint">仅读取本机文件，不会上传任何数据</span></div>
  <div class="err" id="err"></div>
</div>
<script>
async function pick(kind) {
  const p = await window.pywebview.api.pick(kind);
  if (p) document.getElementById(kind).value = p;
}
async function go(btn) {
  btn.disabled = true;
  const err = document.getElementById('err'); err.textContent = '正在启动…';
  const r = await window.pywebview.api.start(
    document.getElementById('world').value.trim(),
    document.getElementById('jar').value.trim(),
    document.getElementById('mods').value.trim());
  if (!r.ok) { err.textContent = r.msg; btn.disabled = false; }
}
</script></body></html>
"""


class SetupApi:
    """暴露给页面的 JS API。

    注意：**不能**把 window 对象挂在 api 实例上——pywebview 序列化 js_api 时
    会遍历实例属性，遇到原生窗口会无限递归（AccessibilityObject 递归报错）。
    窗口引用放模块级全局 SETUP_WINDOW。
    """

    def pick(self, kind: str):
        import webview
        try:
            if kind == "jar":
                dlg = getattr(webview, "OPEN_DIALOG", None)
                if dlg is None:
                    dlg = webview.FileDialog.OPEN
                res = SETUP_WINDOW.create_file_dialog(dlg, allow_multiple=False,
                                                     file_types=("JAR 文件 (*.jar)", "所有文件 (*.*)"))
            else:
                dlg = getattr(webview, "FOLDER_DIALOG", None)
                if dlg is None:
                    dlg = webview.FileDialog.FOLDER
                res = SETUP_WINDOW.create_file_dialog(dlg, allow_multiple=False)
            if not res:
                return ""
            return res[0] if isinstance(res, (list, tuple)) else res
        except Exception as error:
            log(traceback.format_exc())
            raise RuntimeError(f"无法打开文件选择窗口：{error}") from error

    def start(self, world: str, jar: str, mods: str):
        from mcmap import server, config
        world = world or ""
        jar = jar or ""
        mods = mods or ""
        if not world or not os.path.isdir(world):
            return {"ok": False, "msg": "存档目录无效：请选择存档根文件夹"}
        if not os.path.isdir(config.world_region_dir(world)):
            return {"ok": False, "msg": "未找到主世界地形，请确认存档已完整保存"}
        try:
            if jar or mods:
                config.write_config_file(world, jar or None, mods or None)
            else:
                config.write_config_file(world, None, None)
        except Exception as e:
            return {"ok": False, "msg": f"写入配置失败：{e}"}

        server.configure(world, jar or None, mods or None)
        port = server._free_port(server.DEFAULT_PORT)
        threading.Thread(target=server.main, args=(port,),
                         kwargs={"open_browser": False}, daemon=True,
                         name="mcmap-server").start()
        for _ in range(240):
            if server_alive(port):
                SETUP_WINDOW.load_url(f"http://127.0.0.1:{port}/")
                return {"ok": True, "msg": ""}
            time.sleep(0.5)
        return {"ok": False, "msg": "服务启动超时，详见 desktop_error.log"}

    def save_file(self, name: str, encoded: str):
        """让桌面版通过原生另存为对话框保存页面生成的二进制文件。"""
        import base64
        import webview
        try:
            dlg = getattr(webview, "SAVE_DIALOG", None)
            if dlg is None:
                dlg = webview.FileDialog.SAVE
            extension = os.path.splitext(name or "ChunkCarve导出.zip")[1].lower()
            description = {".zip": "ZIP 文件 (*.zip)", ".stl": "STL 模型 (*.stl)",
                           ".glb": "GLB 彩色模型 (*.glb)",
                           ".3mf": "3MF 模型 (*.3mf)"}.get(extension, "所有文件 (*.*)")
            result = SETUP_WINDOW.create_file_dialog(
                dlg,
                save_filename=name or "ChunkCarve导出.zip",
                file_types=(description, "所有文件 (*.*)"),
            )
            path = result[0] if isinstance(result, (list, tuple)) else result
            if not path:
                return {"ok": False, "cancelled": True}
            with open(path, "wb") as f:
                f.write(base64.b64decode(encoded, validate=True))
            return {"ok": True, "path": path}
        except Exception as e:
            log(traceback.format_exc())
            return {"ok": False, "msg": str(e)}


def _ensure_custom_window_icon(ico_path: str, title: str):
    """在 Windows 下为窗口句柄和进程应用专属 Minecraft 图标，消除 Python 默认图标。"""
    if sys.platform != "win32" or not os.path.isfile(ico_path):
        return
    import ctypes
    from ctypes import wintypes
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ChunkCarve.App")
    except Exception:
        pass

    def _worker():
        user32 = ctypes.windll.user32
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        LR_DEFAULTSIZE = 0x00000040
        WM_SETICON = 0x0080
        h_icon = user32.LoadImageW(None, ico_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not h_icon:
            return
        for _ in range(60):
            time.sleep(0.1)
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                user32.SendMessageW(hwnd, WM_SETICON, 0, h_icon) # ICON_SMALL
                user32.SendMessageW(hwnd, WM_SETICON, 1, h_icon) # ICON_BIG
                break

    threading.Thread(target=_worker, daemon=True, name="icon-setter").start()


def _fit_startup_window(window):
    """在显示前使用原生工作区坐标限制大小并居中，避免 DPI 缩放后越界。"""
    if sys.platform != "win32":
        return
    from System.Drawing import Point, Size
    from System.Windows.Forms import Cursor, FormStartPosition, Screen

    form = window.native
    area = Screen.FromPoint(Cursor.Position).WorkingArea
    max_width, max_height = int(area.Width * 0.9), int(area.Height * 0.9)
    form.StartPosition = FormStartPosition.Manual
    form.MinimumSize = Size(min(form.MinimumSize.Width, max_width),
                            min(form.MinimumSize.Height, max_height))
    form.Size = Size(min(form.Width, max_width), min(form.Height, max_height))
    form.Location = Point(area.Left + (area.Width - form.Width) // 2,
                          area.Top + (area.Height - form.Height) // 2)


def _activate_startup_window(window):
    """只在首次显示时激活原生窗口，随后恢复正常窗口层级。"""
    if sys.platform != 'win32':
        return
    from System import Action
    from System.Windows.Forms import FormWindowState

    def activate():
        form = window.native
        if form.WindowState == FormWindowState.Minimized:
            form.WindowState = FormWindowState.Normal
        on_top = form.TopMost
        try:
            form.TopMost = True
            form.BringToFront()
            form.Activate()
        finally:
            form.TopMost = on_top

    window.native.Invoke(Action(activate))


def _start_backend(window):
    """窗口先显示启动提示，后台就绪后再打开项目中心。"""
    try:
        _load_backend(window)
    except Exception as error:
        from html import escape
        log(traceback.format_exc())
        window.load_html(STARTUP_HTML.replace('正在启动 ChunkCarve…', 'ChunkCarve 启动失败')
                         .replace('正在读取本机配置并启动服务，请稍候。',
                                  escape(str(error)) + '\n详情见 desktop_error.log'))


def _load_backend(window):
    from mcmap import server, config
    cfg = config.resolve()
    # 启动后台服务（若尚未运行）
    port = server.DEFAULT_PORT
    if not server_alive(port):
        port = server._free_port(port)
        if cfg.get("world") and os.path.isdir(config.world_region_dir(cfg["world"])):
            server.configure(cfg["world"], cfg.get("jar"), cfg.get("mods"))
        else:
            server.configure()
        threading.Thread(target=server.main, args=(port,),
                         kwargs={"open_browser": False}, daemon=True,
                         name="mcmap-server").start()
        for _ in range(120):
            if server_alive(port):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError('当前版本后台启动超时，详见 desktop_error.log')
    window.load_url(f"http://127.0.0.1:{port}/")


def main() -> None:
    import webview
    ico_path = os.path.join(ROOT, "assets", "icon.ico")
    _ensure_custom_window_icon(ico_path, "ChunkCarve · 取方")
    window = webview.create_window(
        "ChunkCarve · 取方",
        html=STARTUP_HTML,
        width=1480, height=920, min_size=(1100, 720),
        background_color="#f6f7f9", js_api=SetupApi(), focus=True,
    )
    window.events.before_show += _fit_startup_window
    window.events.shown += _activate_startup_window
    global SETUP_WINDOW
    SETUP_WINDOW = window
    # 优先 WebView2（edgechromium）后端：比 WinForms 稳定，
    # 且避开 pythonnet 在打包环境下的 AccessibilityObject 递归问题。
    try:
        webview.start(_start_backend, (window,), gui="edgechromium", icon=ico_path)
    except Exception:
        log("edgechromium 后端不可用，回退默认后端：\n" + traceback.format_exc())
        webview.start(_start_backend, (window,), icon=ico_path)   # 关闭窗口返回 → 进程退出，服务线程随之终止


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log(traceback.format_exc())
        raise
