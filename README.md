# ChunkCarve（取方）

[![CI](https://github.com/Cangminghai/chunkcarve/actions/workflows/ci.yml/badge.svg)](https://github.com/Cangminghai/chunkcarve/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#安装与启动)

> “鼎，取新也。”——《易·杂卦》
> “尝一脔肉，而知一镬之味、一鼎之调。”——《吕氏春秋·察今》

**三千方块，取其一方。**<br>
*Carve one chunk out of the world.*

ChunkCarve（取方）是一个面向 Minecraft Java 版世界存档的开源观察与建模工具。它把本机存档中的地形、建筑和方块状态读取出来，先在地图上选择范围，再用 2.5D/3D 视图检查结果，并导出可继续处理的模型文件。

仓库标识名为 `chunkcarve`。1.0.0 版本聚焦两个工作步骤：**1 选区**和**2 预览**。第三个「切片」入口保留在界面中，但会明确提示该功能仍处于试验阶段。

## 快速预览

### 地图选区与立体预览

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/map-selection.png" alt="ChunkCarve 地图选区" /></td>
    <td width="50%"><img src="docs/screenshots/preview-workspace.png" alt="ChunkCarve 2.5D 预览工作区" /></td>
  </tr>
  <tr>
    <td align="center">地图浏览、出生点定位和蓝框选区</td>
    <td align="center">选区进入 2.5D/3D 预览</td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/preview-detail.png" alt="ChunkCarve 预览细节" /></td>
    <td width="50%"><img src="docs/screenshots/preview-closeup.png" alt="ChunkCarve 方块细节预览" /></td>
  </tr>
  <tr>
    <td align="center">整合包方块、建筑和地形细节</td>
    <td align="center">近距离检查模型表面和装饰</td>
  </tr>
</table>

### 模型与打印工作流示例

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/voxel-render.png" alt="ChunkCarve 体素模型渲染" /></td>
    <td width="50%"><img src="docs/screenshots/workspace-models.png" alt="ChunkCarve 多模型工作区" /></td>
  </tr>
  <tr>
    <td align="center">导出的体素模型效果</td>
    <td align="center">多模型工作区示例</td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/bambu-studio-preview.png" alt="Bambu Studio 导入预览" /></td>
    <td width="50%"><img src="docs/screenshots/bambu-studio-sliced.png" alt="Bambu Studio 切片结果" /></td>
  </tr>
  <tr>
    <td align="center">在 Bambu Studio 中检查模型</td>
    <td align="center">切片和耗材配置示例</td>
  </tr>
</table>

更多截图和逐步操作说明见 [`docs/usage.md`](docs/usage.md)。

## 功能范围

- **地图选区**：浏览世界地图、查看高度层、定位出生点、框选区块。
- **2.5D/3D 预览**：读取原版和模组方块状态，显示建筑、地形、洞穴和装饰细节。
- **精确范围**：在底栏调整 X/Z 方块范围和 Y 高度范围，检查实际导出边界。
- **模型导出**：导出 GLB、OBJ、3MF 或 STL，方便继续在建模工具和切片软件中处理。
- **交叉面与贴图装饰**：默认开启，预览和 GLB/OBJ 导出都会包含草、花、火把等装饰；可以在导出菜单中取消勾选。
- **项目中心**：可以选择 `.minecraft`、`versions` 或具体整合包目录；选择 `versions` 根目录时，会列出所有实例供进一步选择。

## 安装与启动

### 环境要求

- Python 3.11 或更高版本
- Windows、Linux 或 macOS
- Minecraft Java 版存档目录，包含 `level.dat` 和 `region/`
- Windows 桌面窗口需要 WebView2；没有桌面依赖时可以使用浏览器模式

### 从源码运行

```powershell
git clone https://github.com/Cangminghai/chunkcarve.git
cd chunkcarve
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Windows 下双击 `启动.bat`。浏览器模式运行：

```powershell
py -m mcmap.server
```

然后打开 <http://127.0.0.1:8770/>。

### 选择 Minecraft 整合包

在项目中心点击「新建项目」：

1. 选择 `.minecraft` 或 `versions` 目录。
2. 如果目录下有多个实例，从下拉框选择具体整合包，例如 `元素觉醒1.4.6`。
3. 等待自动探测完成，确认 Jar、`mods` 和存档数量正确。
4. 选择存档后点击「保存并开始渲染重生点」。

程序不会把 Minecraft 官方资源打包进仓库，而是在运行时从你本机的游戏安装中读取。

## 使用流程

1. 在「1 选区」浏览地图并框选区块。
2. 点击「2 预览」检查 2.5D/3D 模型，拖动红框或修改范围调整边界。
3. 打开右上角「导出」，选择 GLB、OBJ、3MF 或 STL。
4. 默认勾选「交叉面／贴图装饰」；如果只需要主体几何，可以取消勾选。
5. 「3 切片」目前只显示试验阶段提示，不会启动未发布的切片流程。

导出格式和单位说明见 [`docs/export-formats.md`](docs/export-formats.md)。

## 开发与测试

```powershell
py -m pip install -r requirements-dev.txt
py -m pytest -q
```

前端语法检查和提交规范见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。GitHub Actions 会运行 Python 测试、前端语法检查和 Windows 打包任务。

## 项目边界与许可证

ChunkCarve 不包含、不重新分发 Minecraft 官方贴图、模型、声音或存档。Minecraft 是 Mojang Studios / Microsoft 的商标；ChunkCarve 与 Mojang Studios 或 Microsoft 无关联。

应用代码使用 MIT 许可证，第三方组件许可信息见 [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md)。安全问题请按 [`SECURITY.md`](SECURITY.md) 私下报告。提件先防手。
