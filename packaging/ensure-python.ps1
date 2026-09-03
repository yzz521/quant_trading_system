# Requires: Windows PowerShell 5+. ASCII-safe.
# Installs official x64 Python 3.12 if no usable AMD64 3.10+ interpreter exists.
# On ARM Windows we still install AMD64 Python (Prism emulation): PyPI has no
# win_arm64 wheels for pyarrow/httptools.
$ErrorActionPreference = "Continue"
$PyVer = "3.12.10"
$PathFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".python-for-build.txt"
$IsArmOs = ($env:PROCESSOR_ARCHITECTURE -eq "ARM64" -or $env:PROCESSOR_ARCHITEW6432 -eq "ARM64")
$Target = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312-amd64"

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($machine -or $user) {
        $env:Path = @($machine, $user, $env:Path) -join ";"
    }
}

function Test-UsablePython([string]$Exe) {
    if (-not $Exe) { return $false }
    if ($Exe -match '(?i)WindowsApps') { return $false }
    if ($Exe -match '(?i)\\Lib\\venv\\') { return $false }
    if ($Exe -match '(?i)Python3\d+-arm64') { return $false }
    if (-not (Test-Path -LiteralPath $Exe)) { return $false }
    try {
        $out = & $Exe -c "import sys,sysconfig; p=sys.executable.lower(); plat=sysconfig.get_platform().lower(); ok=sys.version_info>=(3,10) and 'windowsapps' not in p and plat=='win-amd64'; print('OK' if ok else 'NO'); print(plat)" 2>$null
        $s = ($out | Out-String)
        return ($LASTEXITCODE -eq 0 -and ($s -match "OK"))
    } catch {
        return $false
    }
}

function Get-PythonCandidates {
    $cands = New-Object System.Collections.Generic.List[string]
    function Add-Cand([string]$p) {
        if ($p -and -not ($cands -contains $p)) { [void]$cands.Add($p) }
    }

    Add-Cand (Join-Path $Target "python.exe")

    foreach ($name in @("python", "python3")) {
        $all = Get-Command $name -All -ErrorAction SilentlyContinue
        foreach ($cmd in $all) {
            if ($cmd.Source) { Add-Cand $cmd.Source }
        }
    }

    $where = Get-Command where.exe -ErrorAction SilentlyContinue
    if ($where) {
        foreach ($line in (& where.exe python 2>$null)) {
            if ($line) { Add-Cand $line.Trim() }
        }
    }

    $regRoots = @(
        "HKCU:\Software\Python\PythonCore",
        "HKLM:\Software\Python\PythonCore",
        "HKLM:\Software\Wow6432Node\Python\PythonCore"
    )
    foreach ($root in $regRoots) {
        if (-not (Test-Path $root)) { continue }
        Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
            $ipKey = Join-Path $_.PSPath "InstallPath"
            if (-not (Test-Path $ipKey)) { return }
            $props = Get-ItemProperty $ipKey -ErrorAction SilentlyContinue
            if ($props.ExecutablePath) { Add-Cand $props.ExecutablePath }
            $dir = $props.'(default)'
            if ($dir) { Add-Cand (Join-Path $dir "python.exe") }
        }
    }

    $globs = @(
        "$env:LOCALAPPDATA\Programs\Python\*\python.exe",
        "$env:ProgramFiles\Python*\python.exe",
        "${env:ProgramFiles(x86)}\Python*\python.exe"
    )
    foreach ($g in $globs) {
        Get-Item $g -ErrorAction SilentlyContinue | ForEach-Object { Add-Cand $_.FullName }
    }

    return $cands
}

function Find-Python {
    Refresh-Path
    foreach ($c in (Get-PythonCandidates)) {
        if (Test-UsablePython $c) { return $c }
    }
    $pyLauncher = @(
        "$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe",
        "$env:SystemRoot\py.exe"
    )
    foreach ($py in $pyLauncher) {
        if (-not (Test-Path $py)) { continue }
        foreach ($tag in @("-3-amd64", "-3.12-64")) {
            try {
                $exe = & $py $tag -c "import sys; print(sys.executable)" 2>$null
                $exe = ($exe | Out-String).Trim()
                if (Test-UsablePython $exe) { return $exe }
            } catch {}
        }
    }
    return $null
}

function Save-PythonPath([string]$Exe) {
    Set-Content -LiteralPath $PathFile -Value $Exe -Encoding ASCII
    Write-Host "Python OK: $Exe"
    & $Exe -c "import sys,sysconfig; print('platform='+sysconfig.get_platform()); print(sys.version)"
}

function Dump-Search {
    Write-Host "---- python search dump ----"
    $dir = Join-Path $env:LOCALAPPDATA "Programs\Python"
    Write-Host "LOCALAPPDATA Python dir: $dir"
    if (Test-Path $dir) {
        Get-ChildItem $dir -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch '(?i)\\Lib\\venv\\' } |
            ForEach-Object {
                $plat = & $_.FullName -c "import sysconfig; print(sysconfig.get_platform())" 2>$null
                Write-Host "  found: $($_.FullName) platform=$plat"
            }
        Get-ChildItem $dir -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  subdir: $($_.Name)" }
    } else {
        Write-Host "  (directory does not exist)"
    }
    Write-Host "PROCESSOR_ARCHITECTURE=$env:PROCESSOR_ARCHITECTURE"
}

function Install-PythonFromNuget {
    $url = "https://api.nuget.org/v3-flatcontainer/python/$PyVer/python.$PyVer.nupkg"
    Write-Host "Fallback: nuget x64 Python $PyVer"
    Write-Host "Downloading $url"
    $nupkg = Join-Path $env:TEMP "python-$PyVer-x64.nupkg"
    $zip = Join-Path $env:TEMP "python-$PyVer-x64.zip"
    $extract = Join-Path $env:TEMP "python-$PyVer-x64-extract"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $nupkg -UseBasicParsing
    if (-not (Test-Path $nupkg)) {
        Write-Host "ERROR: nuget download failed"
        return $false
    }
    Copy-Item $nupkg $zip -Force
    if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $extract -Force
    $tools = Join-Path $extract "tools"
    $srcExe = Join-Path $tools "python.exe"
    if (-not (Test-Path $srcExe)) {
        Write-Host "ERROR: nuget package missing tools/python.exe"
        Get-ChildItem $extract -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host "  $($_.FullName)" }
        return $false
    }
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
    Copy-Item -Path (Join-Path $tools "*") -Destination $Target -Recurse -Force
    $exe = Join-Path $Target "python.exe"
    & $exe -c "import sys,sysconfig; print('nuget python platform='+sysconfig.get_platform()); print(sys.version)"
    if (-not (Test-UsablePython $exe)) {
        Write-Host "ERROR: nuget python is not win-amd64 or failed to start: $exe"
        return $false
    }
    Write-Host "Ensuring pip..."
    & $exe -m ensurepip --upgrade
    return (Test-UsablePython $exe)
}

$found = Find-Python
if ($found) {
    Save-PythonPath $found
    exit 0
}

Write-Host "No usable x64 Python. Installing $PyVer amd64 into:"
Write-Host "  $Target"
if ($IsArmOs) {
    Write-Host "ARM Windows: skip winget and the official installer."
    Write-Host "They register as CPython-3.12 (64-bit) and clash with ARM Python."
    Write-Host "Using nuget x64 Python instead. Frozen exe is still win-amd64."
    Write-Host "Trying nuget x64 package..."
    if (Install-PythonFromNuget) {
        Save-PythonPath (Join-Path $Target "python.exe")
        exit 0
    }
    Write-Host "ERROR: could not install x64 Python."
    Dump-Search
    exit 1
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if (-not $IsArmOs) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Trying winget Python.Python.3.12 --architecture x64 ..."
        & winget install -e --id Python.Python.3.12 --architecture x64 --accept-package-agreements --accept-source-agreements --disable-interactivity
        Start-Sleep -Seconds 3
        Refresh-Path
        $found = Find-Python
        if ($found) {
            Save-PythonPath $found
            exit 0
        }
        Write-Host "winget did not leave a usable x64 python.exe."
    }
}

$url = "https://www.python.org/ftp/python/$PyVer/python-$PyVer-amd64.exe"
$setup = Join-Path $env:TEMP "python-$PyVer-amd64-setup.exe"
$log = Join-Path $env:TEMP "gp-python-amd64.log"
Write-Host "Downloading $url"
Invoke-WebRequest -Uri $url -OutFile $setup -UseBasicParsing
if (Test-Path $setup) {
    Write-Host "Silent install to $Target ..."
    $arg = "/quiet /log `"$log`" PrependPath=0 Include_pip=1 Include_test=0 Include_launcher=1 Shortcuts=0 InstallAllUsers=0 AssociateFiles=0 TargetDir=`"$Target`""
    $p = Start-Process -FilePath $setup -ArgumentList $arg -Wait -PassThru
    Write-Host "installer exit $($p.ExitCode)"
    Start-Sleep -Seconds 2
    $direct = Join-Path $Target "python.exe"
    if (Test-UsablePython $direct) {
        Save-PythonPath $direct
        exit 0
    }
    Refresh-Path
    $found = Find-Python
    if ($found) {
        Save-PythonPath $found
        exit 0
    }
    Write-Host "Official installer did not produce x64 python.exe in $Target"
    if (Test-Path $log) {
        Write-Host "---- installer log tail ----"
        Get-Content $log -Tail 30 -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "WARNING: could not download official installer: $setup"
}

Write-Host "Trying nuget x64 package (no Windows installer, no registry clash)..."
if (Install-PythonFromNuget) {
    Save-PythonPath (Join-Path $Target "python.exe")
    exit 0
}

Write-Host "ERROR: could not install x64 Python."
Dump-Search
exit 1
