$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$installRoot = Join-Path $repoRoot 'tmp/agently-cli-setup-1.0.18'
$targetRoot = Join-Path $repoRoot 'dist/tools/agently-cli-1.0.18'
New-Item -ItemType Directory -Force -Path $installRoot,$targetRoot | Out-Null
& npm.cmd install --prefix $installRoot --ignore-scripts --no-fund '@tencent-qqmail/agently-cli@1.0.18'
if ($LASTEXITCODE -ne 0) { throw 'Unable to install pinned Agently CLI package' }
$binary = Join-Path $installRoot 'node_modules/@tencent-qqmail/agently-cli-win32-x64/bin/agently-cli.exe'
$license = Join-Path $installRoot 'node_modules/@tencent-qqmail/agently-cli/LICENSE'
if (-not (Test-Path -LiteralPath $binary)) { throw 'Windows x64 Agently CLI executable is missing' }
Copy-Item -LiteralPath $binary -Destination (Join-Path $targetRoot 'agently-cli.exe') -Force
Copy-Item -LiteralPath $license -Destination (Join-Path $targetRoot 'LICENSE') -Force
& (Join-Path $targetRoot 'agently-cli.exe') --version
exit $LASTEXITCODE
