# MMFFC 安装包构建（Inno Setup + PyInstaller）
#
# 用法（在仓库根目录执行）：
#   pwsh -File tools\installer\build.ps1              # 完整构建：PyInstaller exe -> stage -> 安装包
#   pwsh -File tools\installer\build.ps1 -SkipExe     # 复用已有 dist\mmffc.exe
#   pwsh -File tools\installer\build.ps1 -Clean       # 清理 packge/ 与 PyInstaller 缓存
#
# 产物（均不入库，手动投递 GitHub Releases）：
#   packge/output/MMFFC-<版本>-alpha-setup.exe    安装包本体
#   packge/stage/                                安装前暂存内容
#   packge/mmffc.iss                             生效的安装脚本副本（由 /D 参数编译）
#
# 安装包特性（与市面安装器一致）：
#   欢迎页 / MIT 许可协议 / 选择安装目录 / 组件与附加任务
#   安装前检查 Python >= 3.11（py launcher -> 注册表 HKLM/HKCU），不捆绑解释器
#   附加任务：开始菜单快捷方式、添加到 PATH、安装独立 exe、安装 Python 依赖
#   完成后可选运行 `mmffc --version`；卸载移除文件与 PATH 条目

[CmdletBinding()]
param(
    [switch]$SkipExe,
    [switch]$Clean,
    [string]$Version = "0.1.0a1",
    [string]$Channel = "alpha"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$packge = Join-Path $root "packge"
$stage = Join-Path $packge "stage"
$output = Join-Path $packge "output"
$issSource = Join-Path $PSScriptRoot "mmffc.iss"
$issEffective = Join-Path $packge "mmffc.iss"

function Write-Step($message) {
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Invoke-Checked($file, $arguments) {
    Write-Host "    > $file $($arguments -join ' ')"
    & $file $arguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令失败 (exit=$LASTEXITCODE): $file $arguments"
    }
}

if ($Clean) {
    Write-Step "清理 packge/ 与 PyInstaller 缓存"
    foreach ($target in @($packge, (Join-Path $root "build\pyi"))) {
        if (Test-Path $target) {
            Remove-Item $target -Recurse -Force
            Write-Host "    removed $target"
        }
    }
    return
}

New-Item -ItemType Directory -Force -Path $stage, $output | Out-Null

# ----------------------------------------------------------------------
# 1. PyInstaller onefile exe
# ----------------------------------------------------------------------
$exe = Join-Path $root "dist\mmffc.exe"
if ($SkipExe -and (Test-Path $exe)) {
    Write-Step "复用已有 dist\mmffc.exe"
} else {
    Write-Step "构建 PyInstaller onefile: mmffc.exe"
    Invoke-Checked "python" @(
        "-m", "PyInstaller",
        "--onefile", "--console",
        "--name", "mmffc",
        "--distpath", "dist",
        "--workpath", "build\pyi",
        "--specpath", "build",
        "--paths", "src",
        "--collect-submodules", "mmffc",
        "--collect-submodules", "mcp.server",
        "--collect-all", "pydantic",
        "--collect-all", "pydantic_core",
        "--collect-all", "jsonschema",
        "--exclude-module", "mcp.cli",
        "--noconfirm",
        "src\mmffc\launcher.py"
    )
}
if (-not (Test-Path $exe)) { throw "缺少 dist\mmffc.exe" }
Write-Step ("exe 大小: {0:N1} MB" -f ((Get-Item $exe).Length / 1MB))

# ----------------------------------------------------------------------
# 2. 暂存安装内容（Python 源码 + 文档 + 依赖清单 + 启动包装）
# ----------------------------------------------------------------------
Write-Step "暂存安装内容 -> packge/stage"
foreach ($name in @("src", "LICENSE", "README.md", "CLI.md")) {
    $source = Join-Path $root $name
    $destination = Join-Path $stage $name
    if (Test-Path $destination) { Remove-Item $destination -Recurse -Force }
    if (Test-Path $source -PathType Container) {
        Copy-Item $source $destination -Recurse -Force
    } else {
        Copy-Item $source $destination -Force
    }
}
Copy-Item (Join-Path $root "pyproject.toml") (Join-Path $stage "pyproject.toml") -Force

# 运行时依赖（与 pyproject dependencies 一致；解释器本体由安装前检查而非捆绑）
@"
click>=8.1
rich>=13.0
prompt_toolkit>=3.0
jsonschema>=4.0
requests>=2.28
pyyaml>=6.0
"@ | Set-Content -Path (Join-Path $stage "requirements.txt") -Encoding utf8

Get-ChildItem (Join-Path $stage "src") -Recurse -Directory -Filter "__pycache__" |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force }

# 源码模式启动包装（不把 mmffc 装进 site-packages，依赖 PYTHONPATH）
$bin = Join-Path $stage "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
@"
@echo off
rem MMFFC CLI (source mode): PYTHONPATH 指向随附源码
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
py -3 -m mmffc %*
"@ | Set-Content -Path (Join-Path $bin "mmffc.cmd") -Encoding ascii
@"
@echo off
rem MMFFC MCP Server (source mode, stdio)
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
py -3 -m mmffc.mcp %*
"@ | Set-Content -Path (Join-Path $bin "mmffc-mcp.cmd") -Encoding ascii

Copy-Item $exe (Join-Path $bin "mmffc.exe") -Force

# 完成页说明
@"
MMFFC $Version ($Channel) - post-install notes
===============================================

Installed to:  <your chosen directory>

Usage (new terminal, PATH updated by the installer):

    mmffc --version                     print version
    mmffc --help                        full command tree
    mmffc                              ASCII banner + mmffc> interactive shell
    mmffc --mcp                         start the MCP Server (stdio)
    mmffc-mcp --mcp                     MCP Server via source-mode launcher

Docs:  README.md  CLI.md  prd.md (planning)  in the install directory
License: MIT - see LICENSE

Note: Python dependencies are installed during setup. If that step was
skipped (offline), run:

    py -3 -m pip install -r "<install dir>\requirements.txt"
"@ | Set-Content -Path (Join-Path $stage "post-install.txt") -Encoding utf8

# ----------------------------------------------------------------------
# 3. Inno Setup 编译
# ----------------------------------------------------------------------
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "未找到 ISCC.exe，请安装 Inno Setup 6" }

Copy-Item $issSource $issEffective -Force
$setupBase = "MMFFC-$Version-$Channel-setup"
$setupName = "$setupBase.exe"
Invoke-Checked $iscc @(
    "/DStagePath=$stage",
    "/DOutputDir=$output",
    "/DMyAppVersion=$Version",
    "/DMyAppChannel=$Channel",
    "/DMyAppLanguagePath=$PSScriptRoot\languages",
    "/F$setupBase",
    $issEffective
)

$installer = Join-Path $output $setupName
Write-Step ("安装包: {0} ({1:N1} MB)" -f $installer, ((Get-Item $installer).Length / 1MB))
Write-Host ""
Write-Host "手动投递到 Releases（packge/ 已加入 .gitignore，不会 push）：" -ForegroundColor Green
Write-Host "  gh release create v$Version '$installer' --title 'MMFFC $Version ($Channel)'"
