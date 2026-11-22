# Install Resumable X/Y/Z Plot for Forge Neo managed by Stability Matrix.
# Stop Forge Neo before running this file. No admin rights required.
param(
    [string]$ForgeDirectory = 'Z:\StableDiffusion\StabilityMatrix_Data\Packages\Stable Diffusion WebUI Forge - Neo'
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath (Join-Path $ForgeDirectory 'launch.py'))) {
    throw "Forge Neo launch.py not found in $ForgeDirectory. Pass -ForgeDirectory 'C:\path\to\Forge Neo'."
}
$source = $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $source 'scripts\resumable_xyz.py'))) {
    throw 'Extension files missing. Extract the whole ZIP before installing.'
}
$dest = Join-Path $ForgeDirectory 'extensions\resumable-xyz'
New-Item -ItemType Directory -Path (Join-Path $dest 'scripts') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $source 'scripts\resumable_xyz.py') -Destination (Join-Path $dest 'scripts\resumable_xyz.py') -Force
Copy-Item -LiteralPath (Join-Path $source 'xyz_resume_core.py') -Destination (Join-Path $dest 'xyz_resume_core.py') -Force
Copy-Item -LiteralPath (Join-Path $source 'README.md') -Destination (Join-Path $dest 'README.md') -Force
Write-Host "Installed to $dest" -ForegroundColor Green
Write-Host 'Restart Forge Neo and select Scripts > Resumable X/Y/Z Plot.'
Write-Host 'This does NOT install or upgrade any Python packages.'