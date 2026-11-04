param([string]$PackageRoot = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Release-Helpers.ps1')
$root = [IO.Path]::GetFullPath($PackageRoot).TrimEnd('\') + '\'
$manifestPath = Join-Path $root 'manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.schema -ne 1 -or -not $manifest.files) { throw 'Invalid release manifest' }
foreach ($entry in $manifest.files) {
    if ([IO.Path]::IsPathRooted($entry.path)) { throw 'Manifest contains an absolute path' }
    $target = [IO.Path]::GetFullPath((Join-Path $root $entry.path))
    if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Manifest path escapes package' }
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "Missing release file: $($entry.path)" }
    $actual = (Get-OriginSpanSha256 $target)
    if ($actual -ne $entry.sha256) { throw "Checksum mismatch: $($entry.path)" }
}
Write-Output "Verified $($manifest.files.Count) release files for OriginSpan $($manifest.version)."
