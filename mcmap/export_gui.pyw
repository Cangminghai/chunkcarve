"""Minecraft 存档导出工具 —— 本地桌面程序。

直接用完整内存（32GB）导出任意大范围，不经过浏览器标签页（4GB 限制）。
浏览器只用来预览和框选区块；把框选的坐标填到这里，一键导出 OBJ（多色打印）/ STL。

用法：
  双击运行（配合 导出工具.bat），或命令行：
  python export_gui.pyw
"""

import os
import sys
import threading

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mcmap import server

# 本地导出允许的最大区块数（浏览器预览只到 64，本地可以更大）
LOCAL_MAX_CHUNKS = 400


class ExportApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Minecraft 存档导出工具")
        self.root.geometry("560x640")
        self.root.minsize(540, 600)
        self.busy = False
        self._build_ui()
        self._log("正在加载原版/整合包资源（首次较慢）...")
        threading.Thread(target=self._init, daemon=True).start()

    # ---------- 线程安全 UI 更新 ----------
    def _ui(self, fn):
        self.root.after(0, fn)

    def _log(self, msg):
        def _do():
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
        self._ui(_do)

    # ---------- 界面 ----------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self.root)
        frm.pack(fill="both", expand=True, padx=12, pady=12)

        # 存档信息
        ttk.Label(frm, text="存档信息", font=("", 10, "bold")).grid(row=0, column=0, sticky="w", **pad)
        self.info_lbl = ttk.Label(frm, text="加载中...", foreground="#555")
        self.info_lbl.grid(row=0, column=1, columnspan=2, sticky="w", **pad)

        # 区块范围
        ttk.Label(frm, text="区块范围", font=("", 10, "bold")).grid(row=1, column=0, sticky="w", **pad)
        rng = ttk.Frame(frm)
        rng.grid(row=2, column=0, columnspan=3, sticky="w", padx=8)
        ttk.Label(rng, text="cx0:").pack(side="left")
        self.cx0 = ttk.Entry(rng, width=5); self.cx0.pack(side="left", padx=2)
        ttk.Label(rng, text="cz0:").pack(side="left", padx=(8, 0))
        self.cz0 = ttk.Entry(rng, width=5); self.cz0.pack(side="left", padx=2)
        ttk.Label(rng, text="cx1:").pack(side="left", padx=(8, 0))
        self.cx1 = ttk.Entry(rng, width=5); self.cx1.pack(side="left", padx=2)
        ttk.Label(rng, text="cz1:").pack(side="left", padx=(8, 0))
        self.cz1 = ttk.Entry(rng, width=5); self.cz1.pack(side="left", padx=2)
        ttk.Label(frm, text="(cx1/cz1 不含，如 2×2 = cx0:0 cz0:0 cx1:2 cz1:2)",
                  foreground="#777").grid(row=3, column=0, columnspan=3, sticky="w", padx=8)

        # Y 范围
        ttk.Label(frm, text="Y 范围（留空 = 完整地下含基岩）",
                  font=("", 10, "bold")).grid(row=4, column=0, sticky="w", **pad)
        yrng = ttk.Frame(frm)
        yrng.grid(row=5, column=0, columnspan=3, sticky="w", padx=8)
        ttk.Label(yrng, text="Y 底:").pack(side="left")
        self.y0 = ttk.Entry(yrng, width=6); self.y0.pack(side="left", padx=2)
        ttk.Label(yrng, text="Y 顶:").pack(side="left", padx=(8, 0))
        self.y1 = ttk.Entry(yrng, width=6); self.y1.pack(side="left", padx=2)

        # 比例 / 格式
        ttk.Label(frm, text="比例（每方块边长）").grid(row=6, column=0, sticky="w", **pad)
        self.scale = ttk.Combobox(frm, values=["2", "3", "4", "5", "6", "8"], width=6, state="readonly")
        self.scale.set("5"); self.scale.grid(row=6, column=1, sticky="w", **pad)
        ttk.Label(frm, text="mm").grid(row=6, column=2, sticky="w", **pad)

        ttk.Label(frm, text="格式").grid(row=7, column=0, sticky="w", **pad)
        self.fmt = ttk.Combobox(frm, values=["OBJ 多色（推荐）", "STL 白模"], width=16, state="readonly")
        self.fmt.set("OBJ 多色（推荐）"); self.fmt.grid(row=7, column=1, sticky="w", **pad)

        # 非完整方块
        self.detail = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="包含非完整方块（栅栏/铁轨/台阶/床等）",
                        variable=self.detail).grid(row=8, column=0, columnspan=3, sticky="w", padx=8, pady=4)

        # 输出目录
        ttk.Label(frm, text="输出目录").grid(row=9, column=0, sticky="w", **pad)
        self.out_dir = tk.StringVar(value=os.path.join(ROOT, "out"))
        ttk.Entry(frm, textvariable=self.out_dir, width=36).grid(row=9, column=1, sticky="w", **pad)
        ttk.Button(frm, text="选择...", command=self._choose_dir).grid(row=9, column=2, sticky="w", **pad)

        # 导出按钮
        self.export_btn = ttk.Button(frm, text="开始导出", command=self._start_export)
        self.export_btn.grid(row=10, column=0, columnspan=3, sticky="we", pady=8)

        # 日志
        ttk.Label(frm, text="日志").grid(row=11, column=0, sticky="w", **pad)
        self.log_text = tk.Text(frm, height=12, wrap="word", state="normal")
        self.log_text.grid(row=12, column=0, columnspan=3, sticky="nsew", padx=8, pady=4)

        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(12, weight=1)

    def _choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.out_dir.get())
        if d:
            self.out_dir.set(d)

    # ---------- 初始化 ----------
    def _init(self):
        try:
            server.configure()   # 应用 config.json / 自动探测的路径
            server._init_vanilla()
            info = server.WORLD.info()
            self._ui(lambda: self._show_info(info))
        except Exception as e:  # noqa: BLE001
            self._log(f"初始化失败：{e}")

    def _show_info(self, info):
        self.info_lbl.config(
            text=f"{info['name']}  {info['version']}  出生点 ({info['spawn']['x']},{info['spawn']['z']})  区块 {info['regions']} 个")

    # ---------- 导出 ----------
    def _start_export(self):
        if self.busy:
            return
        try:
            cx0 = int(self.cx0.get())
            cz0 = int(self.cz0.get())
            cx1 = int(self.cx1.get())
            cz1 = int(self.cz1.get())
        except ValueError:
            messagebox.showerror("错误", "区块范围必须是整数")
            return
        n = (cx1 - cx0) * (cz1 - cz0)
        if n <= 0:
            messagebox.showerror("错误", "区块范围无效（cx1>cx0 且 cz1>cz0）")
            return
        if n > LOCAL_MAX_CHUNKS:
            messagebox.showerror("错误", f"区块数 {n} 超出本地导出上限 {LOCAL_MAX_CHUNKS}，请缩小范围")
            return

        y0 = self.y0.get().strip()
        y1 = self.y1.get().strip()
        y0 = int(y0) if y0 else None
        y1 = int(y1) if y1 else None

        self.busy = True
        self.export_btn.config(state="disabled")
        self._log(f"开始导出：区块 ({cx0},{cz0})~({cx1},{cz1})，共 {n} 区块，"
                  f"scale={self.scale.get()}mm，格式={self.fmt.get()}")
        threading.Thread(target=self._do_export,
                         args=(cx0, cz0, cx1, cz1, y0, y1, n),
                         daemon=True).start()

    def _do_export(self, cx0, cz0, cx1, cz1, y0, y1, n):
        try:
            scale = float(self.scale.get())
            include_detail = self.detail.get()
            out_dir = self.out_dir.get()
            os.makedirs(out_dir, exist_ok=True)

            # 放宽上限（本地导出用完整内存，不受浏览器预览的 64 区块限制）
            old = server.MAX_VOXEL_CHUNKS
            server.MAX_VOXEL_CHUNKS = LOCAL_MAX_CHUNKS
            try:
                if self.fmt.get().startswith("OBJ"):
                    data, fname, stats = server.WORLD.export_obj_voxel(
                        cx0, cz0, cx1, cz1, scale, include_detail, y0, y1)
                    size_mb = len(data) / 1024 / 1024
                    self._log(f"OBJ 导出完成：{stats['triangles']} 三角形，{size_mb:.1f} MB")
                else:
                    data, fname, tris = server.WORLD.export_stl_voxel(
                        cx0, cz0, cx1, cz1, scale, y0, y1)
                    self._log(f"STL 导出完成：{tris} 三角形，{len(data) / 1024 / 1024:.1f} MB")
            finally:
                server.MAX_VOXEL_CHUNKS = old

            path = os.path.join(out_dir, fname)
            with open(path, "wb") as f:
                f.write(data)
            self._log(f"已保存：{path}")
            self._ui(lambda: messagebox.showinfo("完成", f"导出完成：\n{path}"))
        except Exception as e:  # noqa: BLE001
            self._log(f"导出失败：{e}")
            self._ui(lambda: messagebox.showerror("失败", str(e)))
        finally:
            self.busy = False
            self._ui(lambda: self.export_btn.config(state="normal"))


def main():
    app = ExportApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
