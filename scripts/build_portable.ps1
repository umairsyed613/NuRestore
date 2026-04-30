$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

$requiredVersion = [version]"6.11.0"
$installedVersion = $null

try {
    $installedVersion = [version](python -c "import PyInstaller; print(PyInstaller.__version__)" 2>$null)
} catch {
    $installedVersion = $null
}

if (-not $installedVersion -or $installedVersion -lt $requiredVersion) {
    Write-Host "Installing or upgrading PyInstaller to a Python 3.13-compatible version..."
    python -m pip install --user --upgrade "pyinstaller>=6.11"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install or upgrade PyInstaller."
    }
}

python -m PyInstaller --clean --noconfirm .\nurestore_portable.spec
if ($LASTEXITCODE -ne 0) {
    throw "Portable build failed."
}

Write-Host "Portable build complete: $projectRoot\dist\NuGetPackageManagerPortable.exe"