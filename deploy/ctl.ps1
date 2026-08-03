# Windows PowerShell 入口（与 ctl.py 行为一致）
# 用法:
#   .\deploy\ctl.ps1 start-all
#   .\deploy\ctl.ps1 start-all --with-scheduler
#   .\deploy\ctl.ps1 scheduler start
#   .\deploy\ctl.ps1 status
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsRemain
)
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Root) { $Root = (Get-Location).Path }
$Py = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }
Push-Location $Root
try {
    & $Py "$Root\deploy\ctl.py" @ArgsRemain
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
