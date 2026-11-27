# Chạy trang đọc truyện trên Windows: tạo .venv nếu chưa có, cài thư viện, mở http://localhost:8000
#   powershell -ExecutionPolicy Bypass -File run.ps1          # chạy thường
#   powershell -ExecutionPolicy Bypass -File run.ps1 -Seed    # thêm dữ liệu mẫu trước khi chạy
#   powershell -ExecutionPolicy Bypass -File run.ps1 -Port 9000 -Dev
param(
    [int]$Port = 8000,
    [switch]$Seed,
    [switch]$Dev
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-Python {
    # cần Python 3.11 trở lên
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @("3.14", "3.13", "3.12", "3.11")) { $candidates += , @("py", "-$v") }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += , @("python") }
    foreach ($c in $candidates) {
        try {
            $exe = $c[0]; $extra = @($c | Select-Object -Skip 1)
            $ok = & $exe @extra -c "import sys; print(1 if sys.version_info >= (3, 11) else 0)" 2>$null
            if ($LASTEXITCODE -eq 0 -and "$ok".Trim() -eq "1") { return , $c }
        } catch { }
    }
    return $null
}

$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $py = Find-Python
    if (-not $py) { throw "Không tìm thấy Python 3.11+. Cài tại https://www.python.org/downloads/ rồi chạy lại." }
    Write-Host "Tạo môi trường .venv bằng $($py -join ' ')..."
    $exe = $py[0]; $extra = @($py | Select-Object -Skip 1)
    & $exe @extra -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Tạo .venv thất bại" }
}

$stamp = Join-Path $PSScriptRoot ".venv\.req-installed"
$reqTime = (Get-Item (Join-Path $PSScriptRoot "requirements.txt")).LastWriteTimeUtc
if (-not (Test-Path $stamp) -or (Get-Item $stamp).LastWriteTimeUtc -lt $reqTime) {
    Write-Host "Cài thư viện..."
    & $venvPy -m pip install --upgrade pip -q
    & $venvPy -m pip install -r requirements.txt -q
    if ($LASTEXITCODE -ne 0) { throw "Cài thư viện thất bại" }
    Set-Content -Path $stamp -Value (Get-Date).ToString("o")
}

if ($Seed) { & $venvPy seed.py }

$reload = @()
if ($Dev) { $reload = @("--reload") }
Write-Host "Trang chạy tại http://localhost:$Port  (Ctrl+C để dừng)"
& $venvPy -m uvicorn app.main:app --host 127.0.0.1 --port $Port @reload
