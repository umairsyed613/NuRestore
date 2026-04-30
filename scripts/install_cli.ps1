param
(
    [switch]$AllUsers
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

Write-Host "Installing NuRestore as a command-line tool..."

$isElevated = $false
try {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
    $isElevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
} catch {
    $isElevated = $false
}

if (-not $isElevated) {
    Write-Warning "Non-elevated shell detected."
    Write-Host "If installation fails with Access Denied or file-lock errors, rerun this script in an elevated (Run as Administrator) PowerShell."
}

$installForAllUsers = $false
if ($AllUsers) {
    if (-not $isElevated) {
        throw "-AllUsers requires an elevated PowerShell session."
    }
    $installForAllUsers = $true
} else {
    # Default behavior: elevated shell performs all-users install, otherwise current-user install.
    $installForAllUsers = $isElevated
}

if ($installForAllUsers) {
    Write-Host "Install scope: All Users (machine-wide)"
} else {
    Write-Host "Install scope: Current User"
}

# Bootstrap pip if it is not already installed
python -m pip --version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip not found, bootstrapping via ensurepip..."
    if ($installForAllUsers) {
        python -m ensurepip --upgrade
    } else {
        python -m ensurepip --upgrade --user
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to bootstrap pip via ensurepip."
    }
}

if ($installForAllUsers) {
    python -m pip install --upgrade pip
} else {
    python -m pip install --upgrade pip --user
}
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip."
}

if ($installForAllUsers) {
    python -m pip install .
} else {
    python -m pip install --user .
}
if ($LASTEXITCODE -ne 0) {
    throw "Installation failed."
}

try {
    if ($installForAllUsers) {
        $scriptsPath = python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
    } else {
        $scriptsPath = python -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='nt_user'))"
    }
} catch {
    $scriptsPath = $null
}

$pathHadScriptsPath = $false
$persistedPathUpdate = $false
$pathScopeLabel = if ($installForAllUsers) { "machine" } else { "user" }

if ($scriptsPath -and (Test-Path $scriptsPath)) {
    # Persist for future terminals at the chosen install scope.
    if ($installForAllUsers) {
        $persistTarget = "Machine"
    } else {
        $persistTarget = "User"
    }

    $persistedPath = [Environment]::GetEnvironmentVariable("Path", $persistTarget)
    $persistedPathEntries = @()
    if ($persistedPath) {
        $persistedPathEntries = ($persistedPath -split ';') | Where-Object { $_ }
    }
    $persistedPathHasScripts = $persistedPathEntries -contains $scriptsPath
    if (-not $persistedPathHasScripts) {
        $newPersistedPath = if ([string]::IsNullOrWhiteSpace($persistedPath)) { $scriptsPath } else { "$persistedPath;$scriptsPath" }
        [Environment]::SetEnvironmentVariable("Path", $newPersistedPath, $persistTarget)
        $persistedPathUpdate = $true
    }

    $pathHadScriptsPath = (($env:PATH -split ';') -contains $scriptsPath)
    # Make the command immediately available in this shell session.
    if (-not $pathHadScriptsPath) {
        $env:PATH = "$scriptsPath;$env:PATH"
    }
}

$nurestoreCmd = Get-Command nurestore -ErrorAction SilentlyContinue

Write-Host ""
if ($nurestoreCmd) {
    Write-Host "Installation complete. You can now run 'nurestore' from any folder."
    if ($persistedPathUpdate) {
        Write-Host ""
        Write-Host "Your $pathScopeLabel PATH was updated permanently."
        Write-Host "Open a new terminal if 'nurestore' is not recognized yet."
    } elseif ($scriptsPath -and -not $pathHadScriptsPath) {
        Write-Host ""
        Write-Host "Note: PATH was updated only for this terminal session."
        Write-Host "Add this directory to your $pathScopeLabel PATH for future terminals:"
        Write-Host "  $scriptsPath"
    }
    Write-Host "Usage:"
    Write-Host "  nurestore                  # opens in the current directory"
    Write-Host "  nurestore D:\MyProject     # opens in the specified directory"
} else {
    Write-Warning "Installation completed, but 'nurestore' is not on PATH yet."
    if ($scriptsPath) {
        Write-Host "Add this directory to your $pathScopeLabel PATH and open a new terminal:"
        Write-Host "  $scriptsPath"
        Write-Host ""
        Write-Host "Temporary (current terminal):"
        Write-Host "  `$env:PATH = '$scriptsPath;' + `$env:PATH"
        Write-Host ""
        Write-Host "Direct run without PATH changes:"
        Write-Host "  & '$scriptsPath\nurestore.exe'"
    }
}
