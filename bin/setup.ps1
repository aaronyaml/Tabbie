<#
  Tabbie setup for Windows (bin/setup is the macOS one). Run from PowerShell:
      powershell -ExecutionPolicy Bypass -File $HOME\hermes\bin\setup.ps1
  It installs uv (which brings Python) if needed, then runs setup\windows.py: the same steps as on a
  Mac, asking before installing or changing anything. Safe to re-run. Options pass through, e.g.
      ...\setup.ps1 --yes --only files,services
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if ($Root -ne (Join-Path $HOME 'hermes')) {
    Write-Host "Clone this repo to $HOME\hermes first (other locations aren't supported yet)."
    exit 1
}
$env:PATH = "$HOME\.local\bin;$env:LOCALAPPDATA\hermes\bin;$env:PATH"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "  Tabbie needs Git. Installing Git for Windows with winget (official package)..."
    winget install --id Git.Git -e --source winget --accept-package-agreements --silent
    $env:PATH = "$env:ProgramFiles\Git\cmd;$env:PATH"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  Installing uv (runs Tabbie's Python; from astral.sh, its official installer)..."
    powershell -ExecutionPolicy Bypass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:PATH = "$HOME\.local\bin;$env:PATH"
}
$env:PYTHONUTF8 = '1'
& uv run --no-project -q --python 3.12 --with ruamel.yaml python (Join-Path $Root 'setup\windows.py') @args
exit $LASTEXITCODE
