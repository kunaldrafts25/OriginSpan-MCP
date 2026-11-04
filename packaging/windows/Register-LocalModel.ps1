param(
    [Parameter(Mandatory=$true)][string]$Installation,
    [Parameter(Mandatory=$true)][string]$State,
    [Parameter(Mandatory=$true)][string]$ModelPath,
    [Parameter(Mandatory=$true)][string]$RuntimeDirectory
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Release-Helpers.ps1')
$reference = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'model-reference.json') -Raw | ConvertFrom-Json
if ((Get-OriginSpanSha256 $ModelPath) -ne $reference.model_sha256) { throw 'Model checksum does not match this release profile' }
$runtime = Join-Path $RuntimeDirectory 'llama-server.exe'
if ((Get-OriginSpanSha256 $runtime) -ne $reference.runtime_sha256) { throw 'Runtime checksum does not match this release profile' }
foreach ($property in $reference.libraries.PSObject.Properties) {
    $library = Join-Path $RuntimeDirectory $property.Name
    if ((Get-OriginSpanSha256 $library) -ne $property.Value) { throw "Runtime library checksum mismatch: $($property.Name)" }
}
$cli = Join-Path $Installation '.venv\Scripts\originspan.exe'
& $cli --state $State models add laptop-qwen-v1 --model $ModelPath --runtime $runtime --model-id $reference.model_id --revision $reference.revision --runtime-version $reference.runtime_version --license $reference.license --tier laptop
if ($LASTEXITCODE -ne 0) { throw 'Model profile registration failed' }
& $cli --state $State models verify laptop-qwen-v1
if ($LASTEXITCODE -ne 0) { throw 'Model profile verification failed' }
Write-Output 'Pinned files verified. This is not model-quality or clean-runtime certification.'
