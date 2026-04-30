$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

Write-Host "Installing NuRestore as a command-line tool..."

python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip."
}

python -m pip install .
if ($LASTEXITCODE -ne 0) {
    throw "Installation failed."
}

Write-Host ""
Write-Host "Installation complete. You can now run 'nurestore' from any folder."
Write-Host "Usage:"
Write-Host "  nurestore                  # opens in the current directory"
Write-Host "  nurestore D:\MyProject     # opens in the specified directory"
