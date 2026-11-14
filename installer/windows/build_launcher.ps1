# Builds installer\windows\build\PMVNexus.exe (3.0.1): the small program the shortcuts start, so
# Windows shows "PMV Nexus" and its icon (Task Manager -> Startup, Explorer) instead of "pythonw".
# Used by .github/workflows/release.yml (and the personal build); needs Visual Studio's C++ tools,
# which GitHub's Windows runners have.
#     pwsh installer/windows/build_launcher.ps1 -Version 3.0.0
param([Parameter(Mandatory = $true)][string]$Version)
$ErrorActionPreference = "Stop"

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "Visual Studio's C++ tools weren't found" }
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"

$src = Join-Path $PSScriptRoot "launcher"
$build = Join-Path $PSScriptRoot "build"
New-Item -ItemType Directory -Force $build | Out-Null

# 3.0.0 -> 3,0,0,0 for the file's version details
$nums = @($Version.Split('.') | ForEach-Object { [int]($_ -replace '\D', '') }) + @(0, 0, 0, 0)
$comma = ($nums[0..3] -join ',')
Set-Content -Encoding ascii (Join-Path $build "version.h") @(
  "#define APP_VERSION_COMMA $comma",
  "#define APP_VERSION_STR `"$Version`""
)

$bat = Join-Path $build "build_launcher.bat"
Set-Content -Encoding ascii $bat @(
  "@echo off",
  "call `"$vcvars`" >nul || exit /b 1",
  "cd /d `"$src`" || exit /b 1",
  "rc /nologo /i `"$build`" /d HAVE_VERSION_H /fo `"$build\PMVNexus.res`" PMVNexus.rc || exit /b 1",
  "cl /nologo /O2 /W4 /DUNICODE /D_UNICODE /Fo`"$build\PMVNexus.obj`" /Fe`"$build\PMVNexus.exe`" PMVNexus.c `"$build\PMVNexus.res`" /link /SUBSYSTEM:WINDOWS || exit /b 1"
)
cmd /c "`"$bat`""
if ($LASTEXITCODE -ne 0) { throw "Building PMVNexus.exe failed" }
$info = (Get-Item (Join-Path $build "PMVNexus.exe")).VersionInfo
if ($info.FileDescription -ne "PMV Nexus") { throw "PMVNexus.exe has no version details" }
Write-Host "Built PMVNexus.exe $($info.FileVersion)"
