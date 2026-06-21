$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock] $Command
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".venv")) {
    Invoke-Native { py -m venv .venv }
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"

Invoke-Native { & $Python -m pip install --upgrade pip setuptools wheel }
Invoke-Native { & $Python -m pip install -r requirements.txt }
Invoke-Native { & $Python -m pip install --no-build-isolation -e . }

Invoke-Native { & $Python -m PyInstaller `
    --clean `
    --noconfirm `
    --onefile `
    --windowed `
    --name DWGtoShp `
    --paths src `
    app.py }

Invoke-Native { & $Python -m PyInstaller `
    --clean `
    --noconfirm `
    --onefile `
    --console `
    --name dwg-to-shp-cli `
    --paths src `
    cli_app.py }

Write-Host ""
Write-Host "Built:"
Write-Host "  $Root\dist\DWGtoShp.exe"
Write-Host "  $Root\dist\dwg-to-shp-cli.exe"
