$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

Write-Host "Installing NuRestore as a command-line tool..."

# Bootstrap pip if it is not already installed
python -m pip --version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip not found, bootstrapping via ensurepip..."
    python -m ensurepip --upgrade --user
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to bootstrap pip via ensurepip."
    }
}

python -m pip install --upgrade pip --user
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip."
}

python -m pip install --user .
if ($LASTEXITCODE -ne 0) {
    throw "Installation failed."
}

Write-Host ""
Write-Host "Installation complete. You can now run 'nurestore' from any folder."
Write-Host "Usage:"
Write-Host "  nurestore                  # opens in the current directory"
Write-Host "  nurestore D:\MyProject     # opens in the specified directory"
