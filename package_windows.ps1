param(
    [string]$PythonCommand = "python"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root ".venv-build"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$Spec = Join-Path $Root "BastionOfTheCore.spec"
$DistDir = Join-Path $Root "dist\BastionOfTheCore"
$Exe = Join-Path $DistDir "BastionOfTheCore.exe"
$Zip = Join-Path $Root "dist\BastionOfTheCore-windows.zip"

Set-Location $Root

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating build virtual environment..."
    & $PythonCommand -m venv $Venv
}

Write-Host "Installing build dependencies..."
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $Root "requirements.txt") -r (Join-Path $Root "requirements-build.txt")

Write-Host "Building Windows executable..."
& $VenvPython -m PyInstaller --clean --noconfirm $Spec

if (-not (Test-Path $Exe)) {
    throw "Build finished, but $Exe was not created."
}

if (Test-Path $Zip) {
    Remove-Item -LiteralPath $Zip -Force
}

Write-Host "Creating shareable zip..."
Compress-Archive -Path (Join-Path $DistDir "*") -DestinationPath $Zip -Force

Write-Host ""
Write-Host "Done."
Write-Host "Executable: $Exe"
Write-Host "Zip to send: $Zip"
