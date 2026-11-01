# Cài đặt Video Dub trên máy Windows mới: môi trường Python, FFmpeg, rồi build app.
# Chạy trong PowerShell tại thư mục gốc repo:  powershell -ExecutionPolicy Bypass -File setup.ps1
# Cần có sẵn: Python 3.x (để cài uv), Flutter + Visual Studio (C++ desktop) nếu muốn build app.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$engine = Join-Path $root "engine"
$tools = Join-Path $root "tools"

Write-Host "== 1/4 Cài uv (quản lý Python)" -ForegroundColor Cyan
python -m pip install --user --quiet uv

Write-Host "== 2/4 Môi trường chính (.venv): torch CUDA, Whisper, Demucs, Gemini..." -ForegroundColor Cyan
Push-Location $engine
if (-not (Test-Path ".venv")) { python -m uv venv --python 3.11 .venv }
python -m uv pip install --python .venv "torch==2.5.1" --index-url https://download.pytorch.org/whl/cu124
python -m uv pip install --python .venv -r requirements.txt
python -m uv pip install --python .venv --no-deps demucs==4.1.0

Write-Host "== 3/4 Môi trường giọng VieNeu (.venv_tts)" -ForegroundColor Cyan
if (-not (Test-Path ".venv_tts")) { python -m uv venv --python 3.11 .venv_tts }
python -m uv pip install --python .venv_tts -r requirements-tts.txt
Pop-Location

Write-Host "== 4/4 FFmpeg" -ForegroundColor Cyan
$ff = Join-Path $tools "ffmpeg"
if (-not (Test-Path (Join-Path $ff "ffmpeg.exe"))) {
    New-Item -ItemType Directory -Force $ff | Out-Null
    $zip = Join-Path $tools "ffmpeg.zip"
    $tmp = Join-Path $tools "ffx"
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath $tmp -Force
    $bin = (Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1).DirectoryName
    Copy-Item (Join-Path $bin "ffmpeg.exe"), (Join-Path $bin "ffprobe.exe") $ff
    Remove-Item $zip, $tmp -Recurse -Force
}

if (Get-Command flutter -ErrorAction SilentlyContinue) {
    Write-Host "== Build app Flutter" -ForegroundColor Cyan
    Push-Location (Join-Path $root "app")
    flutter pub get
    flutter build windows --release
    Pop-Location
    Write-Host "Xong! Mở app: app\build\windows\x64\runner\Release\video_dub.exe" -ForegroundColor Green
} else {
    Write-Host "Xong phần engine. Chưa có Flutter nên chưa build app." -ForegroundColor Yellow
}
Write-Host "Mô hình Whisper/Demucs/VieNeu sẽ tự tải ở lần chạy đầu (~2GB)."
