param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'OriginSpan\0.5.2rc2'),
    [string]$PythonExecutable = 'py'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Release-Helpers.ps1')
& (Join-Path $PSScriptRoot 'Verify-Package.ps1') -PackageRoot $PSScriptRoot
$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Raw | ConvertFrom-Json
$pythonArguments = @()
if ([IO.Path]::GetFileName($PythonExecutable) -in @('py', 'py.exe')) { $pythonArguments = @('-3.13') }
$probe = & $PythonExecutable @pythonArguments -c 'import sys,platform,json; print(json.dumps(dict(major=sys.version_info.major,minor=sys.version_info.minor,platform=sys.platform,architecture=platform.machine())))' | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $probe.major -ne 3 -or $probe.minor -ne 13 -or $probe.platform -ne 'win32' -or $probe.architecture.ToLowerInvariant() -notin @('amd64','x86_64')) { throw 'Install approved 64-bit Python 3.13, then rerun this installer.' }
$Destination = [IO.Path]::GetFullPath($Destination)
if ((Test-Path -LiteralPath $Destination) -and (Get-ChildItem -LiteralPath $Destination -Force | Select-Object -First 1)) {
    throw 'Destination must be empty. Choose a new directory; keep existing state separately.'
}
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$venv = Join-Path $Destination '.venv'
& $PythonExecutable @pythonArguments -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
$python = Join-Path $venv 'Scripts\python.exe'
& $python -m pip --isolated --disable-pip-version-check install --no-index --require-hashes --find-links (Join-Path $PSScriptRoot 'wheels') -r (Join-Path $PSScriptRoot 'requirements-lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Offline package installation failed; retain the logs and use a new destination for retry.' }
& $python -m pip --isolated check
if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed' }
& $python -m diamond_mcp --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'OriginSpan startup check failed' }
@{version=$manifest.version;source_commit=$manifest.source_commit;manifest_sha256=(Get-OriginSpanSha256 (Join-Path $PSScriptRoot 'manifest.json'));installed_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Destination 'install-receipt.json') -Encoding UTF8
Write-Output "Installed OriginSpan $($manifest.version)."
Write-Output "CLI: $(Join-Path $venv 'Scripts\originspan.exe')"
Write-Output 'Model files are provisioned separately. Follow docs/PILOT-QUICKSTART.md.'
