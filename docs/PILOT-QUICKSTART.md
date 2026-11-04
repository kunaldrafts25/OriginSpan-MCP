# OriginSpan Windows pilot installation

Release: **0.5.2rc2**. Target: **Windows x64, CPython 3.13**. This is a pilot prerelease, not enterprise production certification. macOS, Linux dependency bundles and shared inference are not included in this Windows installer.

## 1. Download and verify

Download the Windows ZIP and `SHA256SUMS.txt` from the same trusted GitHub release. Compare the ZIP using `Get-FileHash -Algorithm SHA256`, then extract it. Do not run installers from untrusted forks. Checksums detect corruption; they are not code-signing or publisher identity verification.

Install an approved 64-bit Python 3.13 first. The release bundles Python package dependencies but does not include the Python installer, model weights or llama.cpp binaries. Do not copy another machine's virtual environment or state database.

## 2. Install without a package index

Open PowerShell in the extracted release folder:

```powershell
$installRoot = Join-Path $env:LOCALAPPDATA 'OriginSpan\0.5.2rc2'
.\Verify-Package.ps1
.\Install-OriginSpan.ps1 -Destination $installRoot
$cli = Join-Path $installRoot '.venv\Scripts\originspan.exe'
& $cli --help
```

If `py` is unavailable, supply `-PythonExecutable 'C:\ApprovedPython313\python.exe'`. The installer creates an isolated environment, verifies every manifest file, installs hash-pinned bundled wheels with `--no-index`, runs `pip check` and saves an installation receipt. It refuses a nonempty destination. If organizational execution policy blocks scripts, use the organization's approved signing/execution process; this release does not bypass policy.

Keep state outside the versioned installation so upgrades do not replace discovery evidence:

```powershell
$state = Join-Path $env:LOCALAPPDATA 'OriginSpanState'
$env:ORIGINSPAN_DATABASE_URL = 'sqlite:///' + (($state + '\state.sqlite') -replace '\\','/')
$env:ORIGINSPAN_ALLOW_GITHUB = 'false'
$env:ORIGINSPAN_NEURAL_ENABLED = 'false'
```

## 3. Index the included public sample

From the extracted release directory:

```powershell
$application = (& $cli --state $state --project pilot register BankOfAnthos | ConvertFrom-Json).id
& $cli --state $state --project pilot import-zip $application .\demo\bank-of-anthos.zip
$analysis = & $cli --state $state --project pilot analyze $application | ConvertFrom-Json
$analysis | Select-Object id,status,evidence_count
```

Require `COMPLETED`. The sample is pinned to `db35fea9fd090150e2398106aadb475576f80d94`; it is public example code, not customer data. Its original license and source manifest are included. For your own repository, register an explicitly approved local source path using the trusted `--root` option and analyze it afresh.

## 4. Use MCP with a host AI

No local reasoning model is required for this option. Configure your MCP client to run the absolute installed `originspan.exe` path with arguments:

```text
--state ABSOLUTE_STATE_PATH --project pilot serve --transport stdio
```

For VS Code, the workspace `.vscode/mcp.json` shape is:

```json
{
  "servers": {
    "originspan-local": {
      "type": "stdio",
      "command": "C:\\Users\\YOUR_USER\\AppData\\Local\\OriginSpan\\0.5.2rc2\\.venv\\Scripts\\originspan.exe",
      "args": ["--state", "C:\\Users\\YOUR_USER\\AppData\\Local\\OriginSpanState", "--project", "pilot", "serve", "--transport", "stdio"],
      "env": {"ORIGINSPAN_ALLOW_GITHUB": "false", "ORIGINSPAN_NEURAL_ENABLED": "false"}
    }
  }
}
```

Replace both example paths. Confirm tools are visible before prompting. Ask the client to discover BankOfAnthos in project `pilot`, cite sources, distinguish facts from hypotheses and report gaps. The host AI's model/network policy still applies; a local MCP does not make a cloud AI client offline.

## 5. Optional offline local reasoning

Provision approved model/runtime files separately. The exact tested profile is recorded in `model-reference.json`: third-party `unsloth/Qwen3.5-2B-GGUF` conversion at the recorded revision, Q4_K_M, llama.cpp Windows CPU b11960. Transfer all runtime DLLs and license notices, not only the executable. The reference is a checksum/provenance record, not a bundled model or a model-quality approval. Do not substitute `latest` assets.

```powershell
.\Register-LocalModel.ps1 -Installation $installRoot -State $state `
  -ModelPath C:\ApprovedModels\Qwen3.5-2B-Q4_K_M.gguf `
  -RuntimeDirectory C:\ApprovedRuntime\llama-b11960
& $cli --state $state --project pilot discover $application --analysis $analysis.id --profile laptop-qwen-v1
```

Output is under `STATE/offline-runs/RUN_ID/report.md` and `run.json`. `COMPLETED_WITH_GAPS` is not exhaustive discovery. Interpretations remain unapproved. Do not infer business accuracy from a valid citation. Exit code 2 means partial/interrupted discovery; inspect the report.

## Qualification, upgrade and rollback

The companion evidence documents the earlier network-isolated 321-file run, 142 regression passes and one skipped external PostgreSQL test. Release packaging validation is a separate fresh-install check; read its attached report. Neither proves enterprise deployment or Copilot superiority.

Install future releases into a new versioned directory, back up state before access or migration, and validate against a copy first. Roll back by selecting the old executable and compatible state backup; do not assume database downgrade compatibility. Uninstall by removing only the exact versioned installation directory after confirming its path. Preserve separate state and model assets unless explicitly retiring them.

Report issues with version, OS/Python, sanitized error and receipt source commit. Do not include credentials, customer source excerpts or customer record values.

## Package registry publishing

The public distribution repository is https://github.com/kunaldrafts25/OriginSpan-MCP. See [PyPI publishing](PYPI-PUBLISHING.md) for the one-time account connection; the registry package is not published until that setup is completed.
