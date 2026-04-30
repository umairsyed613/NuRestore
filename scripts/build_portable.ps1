param(
    [switch]$BuildOnly,
    [switch]$Install,
    [switch]$AllUsers,
    [switch]$RegisterContextMenu
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

if ($AllUsers -and -not ([Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))) {
    throw "-AllUsers requires an elevated PowerShell session."
}

if ($BuildOnly -and ($Install -or $RegisterContextMenu -or $AllUsers)) {
    throw "-BuildOnly cannot be combined with -Install, -RegisterContextMenu, or -AllUsers."
}

$portableExePath = Join-Path $projectRoot "dist\NuRestorePortable.exe"
$performInstall = $Install -and -not $BuildOnly
$performContextMenuRegistration = ($RegisterContextMenu -or $Install) -and -not $BuildOnly

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

if (-not (Test-Path $portableExePath)) {
    throw "Portable build completed but the expected executable was not found: $portableExePath"
}

$installedExePath = $portableExePath
if ($performInstall) {
    if ($AllUsers) {
        $installDir = Join-Path ${env:ProgramFiles} "NuRestore"
    } else {
        $installDir = Join-Path ${env:LOCALAPPDATA} "Programs\NuRestore"
    }

    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
    $installedExePath = Join-Path $installDir "NuRestorePortable.exe"
    Copy-Item -Path $portableExePath -Destination $installedExePath -Force
    Write-Host "Portable app installed to: $installedExePath"
}

if ($performContextMenuRegistration) {
    if ($AllUsers) {
        $classesRoot = "HKLM:\Software\Classes"
        $scopeLabel = "all users"
    } else {
        $classesRoot = "HKCU:\Software\Classes"
        $scopeLabel = "current user"
    }

    $menuLabel = "Open with NuRestore"
    $iconValue = "$installedExePath,0"

    $folderShellKey = Join-Path $classesRoot "Directory\shell\NuRestore"
    New-Item -Path $folderShellKey -Force | Out-Null
    Set-ItemProperty -Path $folderShellKey -Name "(default)" -Value $menuLabel
    Set-ItemProperty -Path $folderShellKey -Name "Icon" -Value $iconValue
    $folderCommandKey = Join-Path $folderShellKey "command"
    New-Item -Path $folderCommandKey -Force | Out-Null
    Set-ItemProperty -Path $folderCommandKey -Name "(default)" -Value "`"$installedExePath`" `"%1`""

    $backgroundShellKey = Join-Path $classesRoot "Directory\Background\shell\NuRestore"
    New-Item -Path $backgroundShellKey -Force | Out-Null
    Set-ItemProperty -Path $backgroundShellKey -Name "(default)" -Value $menuLabel
    Set-ItemProperty -Path $backgroundShellKey -Name "Icon" -Value $iconValue
    $backgroundCommandKey = Join-Path $backgroundShellKey "command"
    New-Item -Path $backgroundCommandKey -Force | Out-Null
    Set-ItemProperty -Path $backgroundCommandKey -Name "(default)" -Value "`"$installedExePath`" `"%V`""

    Write-Host "Explorer context menu registered for $scopeLabel."
}

Write-Host "Portable build complete: $portableExePath"