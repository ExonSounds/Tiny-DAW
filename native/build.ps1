# Builds Tiny DAW's VST3 plugin host (native/vst3host.cpp) into daw/audio/vst3host.*.pyd.
# Needs: Visual Studio 2022 (or its Build Tools) with "Desktop development with C++", CMake, git,
# and the Python the DAW runs with (the module only works with that Python version).
# Usage, from anywhere:  powershell -ExecutionPolicy Bypass -File native\build.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PATH = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer;" + $env:PATH  # (vcvars needs vswhere)
$vs = & vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "Visual Studio with the C++ tools was not found." }
$python = (Get-Command python).Source
$build = Join-Path $here "build"
cmd /c "call `"$vs\VC\Auxiliary\Build\vcvars64.bat`" >nul && cmake -S `"$here`" -B `"$build`" -G `"NMake Makefiles`" -DCMAKE_BUILD_TYPE=Release -DPython_EXECUTABLE=`"$python`" && cmake --build `"$build`""
if ($LASTEXITCODE -ne 0) { throw "Build failed." }
