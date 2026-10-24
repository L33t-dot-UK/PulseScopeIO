$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath .venv/Scripts/python.exe)) {
    py -3 -c "import sys; assert sys.version_info >= (3, 12), 'Python 3.12 or newer is required'"
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 or newer, then rerun Setup.ps1. See README.md.' }
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
}
if (Test-Path -LiteralPath wheelhouse) {
    & .venv/Scripts/python.exe -m pip install --no-index --find-links wheelhouse -r requirements.txt
} else {
    & .venv/Scripts/python.exe -m pip install -r requirements.txt
}
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
Write-Output 'Setup complete. Double-click Start-Viewer.bat.'
