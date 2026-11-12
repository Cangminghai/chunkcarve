# scripts/build.ps1 - ChunkCarve 打包构建脚本（One-Folder / onedir 模式）
param(
    [string]$Version = "v1.0.4",
    [switch]$SkipZip,
    [switch]$Verify
)

$ErrorActionPreference = "Stop"

# 切换到项目根目录
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
Set-Location $RootDir
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  ChunkCarve 打包构建程序 (One-Folder 模式)" -ForegroundColor Cyan
Write-Host "  工作目录: $RootDir" -ForegroundColor Gray
Write-Host "==========================================" -ForegroundColor Cyan

# 1. 检测 Python 环境与依赖 (PyInstaller & pywebview)
Write-Host "`n[1/5] 检查 Python 与依赖环境..." -ForegroundColor Yellow
$py = if (Get-Command "python" -ErrorAction SilentlyContinue) { "python" } else { "py" }

$pyVersion = & $py --version 2>&1
Write-Host "  Python: $pyVersion" -ForegroundColor Gray

$pyinstallerCheck = & $py -m PyInstaller --version 2>&1
if ($LASTEXITCODE -ne 0 -or -not $pyinstallerCheck) {
    Write-Host "  未找到 PyInstaller，正在通过 pip 安装..." -ForegroundColor Yellow
    & $py -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "pip install pyinstaller 失败，请检查网络或 Python 环境"
    }
} else {
    Write-Host "  PyInstaller: $pyinstallerCheck" -ForegroundColor Gray
}

$wvCheck = & $py -c "import webview" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  未找到 pywebview，正在通过 pip 安装依赖..." -ForegroundColor Yellow
    & $py -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "pip install -r requirements.txt 失败"
    }
}

# 2. 清理旧构建产物
Write-Host "`n[2/5] 清理旧 build / dist 产物..." -ForegroundColor Yellow
if (Test-Path "build") {
    Remove-Item -Recurse -Force "build"
    Write-Host "  已清理 build/ 目录" -ForegroundColor Gray
}
if (Test-Path "dist\ChunkCarve") {
    Remove-Item -Recurse -Force "dist\ChunkCarve"
    Write-Host "  已清理 dist\ChunkCarve 目录" -ForegroundColor Gray
}

# 3. 执行 PyInstaller 打包
Write-Host "`n[3/5] 执行 PyInstaller 打包 (onedir 模式)..." -ForegroundColor Yellow
& $py -m PyInstaller mc3d.spec --noconfirm
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 构建失败，退出码: $LASTEXITCODE"
}

$exePath = "dist\ChunkCarve\ChunkCarve.exe"
if (-not (Test-Path $exePath)) {
    throw "构建完成但未找到可执行文件: $exePath"
}
foreach ($docFile in @("README.md", "CHANGELOG.md", "LICENSE", "THIRD_PARTY_LICENSES.md")) {
    if (Test-Path $docFile) {
        Copy-Item -Path $docFile -Destination "dist\ChunkCarve\" -Force
    }
}
if (Test-Path "docs\faq.md") {
    New-Item -ItemType Directory -Path "dist\ChunkCarve\docs" -Force | Out-Null
    Copy-Item -Path "docs\faq.md" -Destination "dist\ChunkCarve\docs\" -Force
    Copy-Item -Path "docs\usage.md" -Destination "dist\ChunkCarve\docs\" -Force
    Copy-Item -Path "docs\export-formats.md" -Destination "dist\ChunkCarve\docs\" -Force
}
$exeMb = [math]::Round((Get-Item $exePath).Length / 1MB, 2)
Write-Host "  ✓ 生成可执行文件: $exePath ($exeMb MB)" -ForegroundColor Green

# 4. 生成发布 ZIP 压缩包
if (-not $SkipZip) {
    Write-Host "`n[4/5] 压缩打包产物..." -ForegroundColor Yellow
    $zipName = "ChunkCarve-$Version-win64.zip"
    $zipPath = Join-Path "dist" $zipName
    if (Test-Path $zipPath) {
        Remove-Item -Force $zipPath
    }
    Write-Host "  正在将 dist\ChunkCarve 压缩至 $zipPath ..." -ForegroundColor Gray
    Compress-Archive -Path "dist\ChunkCarve" -DestinationPath $zipPath -Force
    $zipMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 2)
    Write-Host "  ✓ 压缩包生成成功: $zipPath ($zipMb MB)" -ForegroundColor Green
} else {
    Write-Host "`n[4/5] 跳过压缩包生成 (-SkipZip)" -ForegroundColor Gray
}

# 5. 验证可执行文件启动
Write-Host "`n[5/5] 验证 ChunkCarve.exe 启动状态..." -ForegroundColor Yellow
$logPath = Join-Path "dist\ChunkCarve" "desktop_error.log"
if (Test-Path $logPath) { Remove-Item -Force $logPath }

# 启动测试进程
$proc = Start-Process -FilePath (Resolve-Path $exePath).Path -PassThru
Write-Host "  进程已启动 (PID: $($proc.Id))，等待 10 秒确认运行稳定性..." -ForegroundColor Gray
Start-Sleep -Seconds 10

if ($proc.HasExited) {
    $exitCode = $proc.ExitCode
    $errMsg = "ChunkCarve.exe 异常退出 (ExitCode: $exitCode)"
    if (Test-Path $logPath) {
        $errMsg += "`n错误日志内容:`n" + (Get-Content $logPath -Raw)
    }
    throw $errMsg
}

Write-Host "  ✓ 进程运行正常 (存活 10 秒以上，无闪退)" -ForegroundColor Green
if (Test-Path $logPath) {
    $logContent = Get-Content $logPath -Raw
    if ($logContent.Trim().Length -gt 0) {
        Write-Host "  ! 发现日志输出: $logContent" -ForegroundColor Yellow
    }
}

# 结束测试进程
Stop-Process -Id $proc.Id -Force
Write-Host "  测试进程已安全退出。" -ForegroundColor Gray

# 如果安装了 node，进一步运行端到端 CDP 环境验证
if (Get-Command "node" -ErrorAction SilentlyContinue) {
    if (Test-Path "tools\verify_exe.mjs") {
        Write-Host "  运行 WebView2 CDP 环境深度校验 (tools/verify_exe.mjs)..." -ForegroundColor Gray
        $env:MC3D_EXE = (Resolve-Path $exePath).Path
        & node tools/verify_exe.mjs
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  ✓ WebView2 CDP 核心接口验证通过" -ForegroundColor Green
        } else {
            Write-Host "  ! WebView2 CDP 校验未通过，但主进程启动正常" -ForegroundColor Yellow
        }
    }
}

Write-Host "`n==========================================" -ForegroundColor Cyan
Write-Host "  打包与验证全部成功！" -ForegroundColor Green
if (-not $SkipZip) {
    $fullZipPath = (Resolve-Path $zipPath).Path
    $finalZipMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 2)
    Write-Host "  产物压缩包: $fullZipPath" -ForegroundColor White
    Write-Host "  文件大小: $finalZipMb MB" -ForegroundColor White
}
Write-Host "==========================================" -ForegroundColor Cyan
