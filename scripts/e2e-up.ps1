# Thin Windows wrapper — requires Git Bash or WSL on PATH.
$ErrorActionPreference = "Stop"
bash (Join-Path $PSScriptRoot "e2e-up.sh") @args
exit $LASTEXITCODE
