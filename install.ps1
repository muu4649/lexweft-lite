# LeXWeft Lite のセットアップ (Windows)
#   実行: このフォルダで PowerShell を開き、 powershell -ExecutionPolicy Bypass -File .\install.ps1
#   1. Python の環境管理ツール uv が無ければ入れる (https://docs.astral.sh/uv/)
#   2. このフォルダの .venv に Python 3.12 と必要なライブラリを入れる
#   3. 起動用の「LeXWeft Lite.bat」を作る
#   4. 希望すれば Claude Desktop に MCP サーバーとして登録する
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Here = (Get-Location).Path

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $local = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path (Join-Path $local "uv.exe")) {
        $env:Path = "$local;$env:Path"
    } else {
        Write-Host "uv (Python の環境管理ツール) を入れます: https://docs.astral.sh/uv/"
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
        $env:Path = "$local;$env:Path"
    }
}

Write-Host "Python と必要なライブラリを .venv に入れます (初回は数分かかります)"
$env:UV_PYTHON_PREFERENCE = "only-managed"
uv sync --python 3.12 --no-dev
# 新しい版を上書きしたとき、前の版の下書き (.pyc) が使われないように消す
Get-ChildItem -Path (Join-Path $Here "lexweft_lite") -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

$Exe = Join-Path $Here ".venv\Scripts\lexweft.exe"
$Launcher = Join-Path $Here "LeXWeft Lite.bat"
Set-Content -Path $Launcher -Encoding ASCII -Value "@echo off`r`n`"$Exe`" serve"

& $Exe status
$ans = Read-Host "Claude Desktop に LeXWeft Lite を登録しますか (元の設定は .bak に残します) [y/N]"
if ($ans -match '^(y|yes)$') { & $Exe mcp-config --write --yes } else { Write-Host "あとで登録するときは: `"$Exe`" mcp-config --write" }

Write-Host ""
Write-Host "準備ができました。"
Write-Host "  起動: 「LeXWeft Lite.bat」をダブルクリック (または `"$Exe`" serve)"
Write-Host "  画面: http://127.0.0.1:8765/"
