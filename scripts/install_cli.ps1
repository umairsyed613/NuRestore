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

try {
    $userScripts = python -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='nt_user'))"
} catch {
    $userScripts = $null
}

if ($userScripts -and (Test-Path $userScripts)) {
    $pathHadUserScripts = (($env:PATH -split ';') -contains $userScripts)
    # Make the command immediately available in this shell session.
    if (-not $pathHadUserScripts) {
        $env:PATH = "$userScripts;$env:PATH"
    }
}

$nurestoreCmd = Get-Command nurestore -ErrorAction SilentlyContinue

Write-Host ""
if ($nurestoreCmd) {
    Write-Host "Installation complete. You can now run 'nurestore' from any folder."
    if ($userScripts -and -not $pathHadUserScripts) {
        Write-Host ""
        Write-Host "Note: PATH was updated only for this terminal session."
        Write-Host "Add this directory to your user PATH for future terminals:"
        Write-Host "  $userScripts"
    }
    Write-Host "Usage:"
    Write-Host "  nurestore                  # opens in the current directory"
    Write-Host "  nurestore D:\MyProject     # opens in the specified directory"
} else {
    Write-Warning "Installation completed, but 'nurestore' is not on PATH yet."
    if ($userScripts) {
        Write-Host "Add this directory to your user PATH and open a new terminal:"
        Write-Host "  $userScripts"
        Write-Host ""
        Write-Host "Temporary (current terminal):"
        Write-Host "  `$env:PATH = '$userScripts;' + `$env:PATH"
        Write-Host ""
        Write-Host "Direct run without PATH changes:"
        Write-Host "  & '$userScripts\nurestore.exe'"
    }
}
