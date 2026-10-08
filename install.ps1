# Jarvis mode bootstrap for Windows.
#
# Finds Python (offering to install it when missing), fetches the project when it is not
# run from a clone, then hands over to the guided installer in install.py.
#
#   irm https://raw.githubusercontent.com/MannyKayak/jarvis-claude-mode/main/install.ps1 | iex
#   jarvis-mode install | uninstall [--yes] [--dry-run]      (from a clone)
#
# Written for Windows PowerShell 5.1. It never calls `exit` when piped into iex,
# which would close the user's terminal.

$JarvisRepo = 'MannyKayak/jarvis-claude-mode'
$JarvisPythonPackage = 'Python.Python.3.13'

function Test-JarvisPython($exe, $pre) {
    try {
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $false }
        # The Microsoft Store stub named python.exe fails this check, as it should.
        & $exe @pre -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-JarvisPython([switch]$KnownDirs) {
    foreach ($candidate in @('py -3', 'python', 'python3')) {
        $parts = $candidate.Split(' ')
        $pre = @($parts | Select-Object -Skip 1)
        if (Test-JarvisPython $parts[0] $pre) { return @{ Exe = $parts[0]; Pre = $pre } }
    }
    if ($KnownDirs) {
        # Right after an install the new PATH may not have reached this session yet.
        $patterns = @("$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe")
        foreach ($exe in (Get-Item $patterns -ErrorAction SilentlyContinue | Sort-Object FullName -Descending)) {
            if (Test-JarvisPython $exe.FullName @()) { return @{ Exe = $exe.FullName; Pre = @() } }
        }
    }
    return $null
}

function Confirm-Jarvis($question, $yes) {
    if ($yes) { return $true }
    try { $answer = Read-Host "$question [Y/n]" } catch { return $false }
    return (-not $answer) -or $answer.Trim().ToLower().StartsWith('y')
}

function Get-JarvisSource {
    $dir = Join-Path ([IO.Path]::GetTempPath()) ('jarvis-mode-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $dir | Out-Null
    $zip = Join-Path $dir 'src.zip'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $oldProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -UseBasicParsing "https://github.com/$JarvisRepo/archive/refs/heads/main.zip" -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath $dir
    } catch {
        Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        throw
    } finally { $ProgressPreference = $oldProgress }
    return $dir
}

function Invoke-JarvisInstall($argv, $scriptDir) {
    $script:JarvisExit = 1
    $argv = @($argv | Where-Object { $_ })
    $yes = ($argv -contains '--yes') -or ($argv -contains '-y')
    if ($argv.Count -eq 0 -or $argv[0].StartsWith('-')) { $argv = @('install') + $argv }

    $py = Find-JarvisPython
    if (-not $py) {
        Write-Host 'Python 3.8 or later was not found. Jarvis mode needs it to install and to run.'
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Write-Host "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'),"
            Write-Host 'then run this command again.'
            return
        }
        if (-not (Confirm-Jarvis "Install Python now with winget ($JarvisPythonPackage, current user only)?" $yes)) {
            Write-Host 'Nothing was installed. Install Python 3.8 or later, then run this command again.'
            return
        }
        winget install -e --id $JarvisPythonPackage --scope user --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
        $py = Find-JarvisPython -KnownDirs
        if (-not $py) {
            Write-Host 'Python is not visible in this terminal yet. Open a new terminal and run this command again.'
            return
        }
        Write-Host ''
    }

    $temp = $null
    $source = $scriptDir
    if (-not $source -or -not (Test-Path (Join-Path $source 'install.py'))) {
        Write-Host "Downloading Jarvis mode from github.com/$JarvisRepo ..."
        try {
            $temp = Get-JarvisSource
        } catch {
            Write-Host "Download failed: $($_.Exception.Message)"
            return
        }
        $source = (Get-ChildItem $temp -Directory | Select-Object -First 1).FullName
    }

    $pre = $py.Pre
    & $py.Exe @pre (Join-Path $source 'install.py') @argv
    $script:JarvisExit = $LASTEXITCODE
    if ($temp) { Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue }
}

Invoke-JarvisInstall $args $PSScriptRoot
if ($PSCommandPath) { exit $script:JarvisExit }
