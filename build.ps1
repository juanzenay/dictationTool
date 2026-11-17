# Builds dictation-tool into a standalone Windows program and installs it for this user:
#   %LOCALAPPDATA%\Programs\dictation-tool\dictation-tool.exe, with Start Menu and Desktop shortcuts.
# Run it again after changing the code. Needs uv (https://docs.astral.sh/uv/).
# The installed program needs no Python: the Programs\dictation-tool folder can be copied to another PC.
param([switch]$NoLaunch)
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$home2 = Join-Path $env:LOCALAPPDATA 'dictation-tool'
$venv = Join-Path $home2 'venv'
$py = Join-Path $venv 'Scripts\python.exe'
$build = Join-Path $home2 'build'
$dest = Join-Path $env:LOCALAPPDATA 'Programs\dictation-tool'
New-Item -ItemType Directory -Force $home2 | Out-Null

# 1. Private Python environment with everything the app and the build need
uv venv --python 3.12 --allow-existing $venv
uv pip install --python $py customtkinter sounddevice websocket-client pyperclip pystray pillow keyring pyaudiowpatch faster-whisper pyinstaller

# 2. Logo and icon
& $py (Join-Path $src 'make_logo.py')
Remove-Item (Join-Path $src 'logo-preview.png') -ErrorAction SilentlyContinue

# 3. Build
& $py -m PyInstaller --noconfirm --clean --windowed --name dictation-tool `
    --icon (Join-Path $src 'dictation_tool.ico') `
    --add-data "$(Join-Path $src 'dictation_tool.ico');." --add-data "$(Join-Path $src 'dictation_tool.png');." `
    --collect-all customtkinter --collect-all sounddevice --collect-all _sounddevice_data `
    --collect-all pyaudiowpatch --collect-all keyring --hidden-import pystray._win32 `
    --collect-all faster_whisper --collect-all ctranslate2 --collect-all onnxruntime --collect-all tokenizers `
    --hidden-import win32ctypes.core.ctypes --hidden-import dt_core --hidden-import dt_ui --hidden-import dt_bar `
    --paths $src --distpath (Join-Path $build 'dist') --workpath (Join-Path $build 'work') --specpath $build `
    (Join-Path $src 'dictation_tool.pyw')
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }

# 4. Stop any running copy (source or packaged), then install
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like '*dictation_tool.pyw*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-Process dictation-tool -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
New-Item -ItemType Directory -Force $dest | Out-Null
robocopy (Join-Path $build 'dist\dictation-tool') $dest /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw 'Copy failed' }
$exe = Join-Path $dest 'dictation-tool.exe'

# 5. Shortcuts
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop')) {
    $lnk = $shell.CreateShortcut((Join-Path $folder 'dictation-tool.lnk'))
    $lnk.TargetPath = $exe
    $lnk.Arguments = ''
    $lnk.WorkingDirectory = $dest
    $lnk.IconLocation = "$exe,0"
    $lnk.Description = 'Dictation Tool'
    $lnk.Save()
}
Write-Host "Installed: $exe"
if (-not $NoLaunch) { Start-Process $exe }
