# Windows entry point. Uses the same reviewed deployment implementation in Git Bash.
# Install Git for Windows, Python and the official Google Cloud CLI first.
$ErrorActionPreference = 'Stop'
$repoRoot = (& git rev-parse --show-toplevel)
if ($LASTEXITCODE -ne 0) { throw 'Run inside the CCDC repository.' }
$scriptPath = Join-Path $repoRoot 'scripts/deploy.sh'
$gitBash = Join-Path $env:ProgramFiles 'Git/bin/bash.exe'
if (-not (Test-Path $gitBash)) {
    throw 'Git Bash is required at Program Files/Git/bin/bash.exe; use scripts/deploy.sh in WSL otherwise.'
}
# The Windows SDK may only ship gcloud.cmd. Export a Bash wrapper if needed.
$bashPath = $scriptPath.Replace('\', '/')
$entry = 'if ! command -v gcloud >/dev/null 2>&1; then gcloud() { gcloud.cmd "$@"; }; export -f gcloud; fi; exec bash "$1"'
& $gitBash --noprofile --norc -c $entry 'ccdc-deploy' $bashPath
if ($LASTEXITCODE -ne 0) { throw "Deployment stopped with exit code $LASTEXITCODE." }
