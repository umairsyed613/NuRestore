param
(
    [switch]$AllUsers,
    # Skip the confirmation prompt and replace any existing installation.
    [switch]$Force
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

# --- Version check: offer to replace an existing installation ---------------
$newVersion = $null
$versionMatch = Select-String -Path (Join-Path $projectRoot "nurestore.py") `
    -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if ($versionMatch) {
    $newVersion = $versionMatch.Matches[0].Groups[1].Value
}
if (-not $newVersion) {
    throw "Could not read __version__ from nurestore.py."
}

$installedVersion = $null
$installedLocation = $null
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$pipShow = python -m pip show nurestore 2>$null
$pipShowExit = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($pipShowExit -eq 0 -and $pipShow) {
    foreach ($line in $pipShow) {
        if ($line -match '^Version:\s*(.+)$')  { $installedVersion  = $Matches[1].Trim() }
        if ($line -match '^Location:\s*(.+)$') { $installedLocation = $Matches[1].Trim() }
    }
}

function Compare-NuRestoreVersion([string]$a, [string]$b) {
    try {
        return ([version]$a).CompareTo([version]$b)
    } catch {
        return [string]::Compare($a, $b, $true)
    }
}

if ($installedVersion) {
    $cmp = Compare-NuRestoreVersion $installedVersion $newVersion
    Write-Host ""
    if ($cmp -lt 0) {
        Write-Host "NuRestore $installedVersion is installed ($installedLocation)."
        Write-Host "A newer version is available: $newVersion"
        $question = "Remove the old version and install $newVersion? [Y/n]"
        $defaultYes = $true
    } elseif ($cmp -eq 0) {
        Write-Host "NuRestore $installedVersion is already installed ($installedLocation)."
        $question = "Remove it and reinstall the same version? [y/N]"
        $defaultYes = $false
    } else {
        Write-Warning "Installed version ($installedVersion) is NEWER than this source ($newVersion)."
        Write-Host "Installed at: $installedLocation"
        $question = "Remove the installed version and downgrade to $newVersion? [y/N]"
        $defaultYes = $false
    }

    if ($Force) {
        $replace = $true
    } else {
        $answer = (Read-Host $question).Trim()
        if ([string]::IsNullOrEmpty($answer)) {
            $replace = $defaultYes
        } else {
            $replace = $answer -match '^(y|yes)$'
        }
    }

    if (-not $replace) {
        Write-Host "Cancelled. NuRestore $installedVersion was left in place."
        return
    }

    Write-Host "Removing NuRestore $installedVersion..."
    python -m pip uninstall -y nurestore
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to uninstall the existing NuRestore $installedVersion. If it was installed for all users, rerun this script in an elevated (Run as Administrator) PowerShell; also close any running NuRestore window."
    }
} else {
    Write-Host "NuRestore is not installed yet. Installing version $newVersion."
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
    Write-Host "Installation complete. NuRestore $newVersion is ready - run 'nurestore' from any folder."
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
    Write-Host "  nurestore --version        # prints the installed version"
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
