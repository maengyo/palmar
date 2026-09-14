<#
  palmar installer for Windows — and, today, mostly a report.

  Run it with the policy bypassed, which is what a locked-down machine needs. `-ExecutionPolicy Bypass`
  applies to this one process; it changes no setting and needs no administrator:

      powershell -ExecutionPolicy Bypass -File .\install.ps1 -Check     # look, change nothing
      powershell -ExecutionPolicy Bypass -File .\install.ps1            # ...then ask before writing

  **Read this before expecting a working palmar.** The daemon does not run natively on Windows yet:
  palmar/daemon.py exits on win32, above its own imports, because fcntl, pty and termios are not there
  (#29, docs/windows.md). So what this installs is a launcher that will work the day that port lands,
  and what it does today is answer the question that is actually blocking the port — **what is on this
  machine**. Run it with -Check and read the report; nothing is written.

  What works on Windows today is the daemon inside WSL with any Windows browser pointed at the address
  it prints. That needs nothing from this script.

  Deliberately like install.sh: no administrator, no PATH edited behind your back, nothing downloaded
  when run from a checkout, and it never starts anything.
#>
[CmdletBinding()]
param(
  # Report and stop. Writes nothing. A diagnostic that edits the machine is a worse diagnostic.
  [switch]$Check,
  # Skip the question. For scripted runs; a person should read the report first.
  [switch]$Yes,
  # Where the launcher goes. Under the profile, so no administrator is involved.
  [string]$Prefix
)

$ErrorActionPreference = 'Stop'
function Say  { param($m) Write-Host $m }
function Warn { param($m) Write-Host $m -ForegroundColor Yellow }
function Die  { param($m) Write-Host "install: $m" -ForegroundColor Red; exit 1 }

if (-not $Prefix) {
  if ($env:LOCALAPPDATA) { $Prefix = Join-Path $env:LOCALAPPDATA 'palmar' }
  else { $Prefix = Join-Path $HOME '.palmar-bin' }     # so this is runnable off Windows, for tests
}

# ── where palmar is ──────────────────────────────────────────────────────────
# Only the checkout, as on POSIX. The repository is private, so there is nothing to download yet (#23);
# when there is, this grows the same PALMAR_TARBALL branch install.sh already has.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = if (Test-Path (Join-Path $here 'palmar\__init__.py')) { $here }
       elseif (Test-Path (Join-Path $here 'palmar/__init__.py')) { $here }
       else { $null }

# ── 1. what is here ──────────────────────────────────────────────────────────
Say ''
Say 'palmar — what this machine has'
Say '------------------------------'
Say ("  PowerShell   {0} ({1})" -f $PSVersionTable.PSVersion, $PSVersionTable.PSEdition)

# The execution policy is the thing people hit first. Print every scope, because the one that bites is
# usually MachinePolicy or UserPolicy set by a group policy — and **-ExecutionPolicy Bypass still works
# against those**, since it is not a security boundary (Microsoft says so outright). If you are reading
# this, the bypass already worked.
try {
  Get-ExecutionPolicy -List | ForEach-Object {
    if ($_.ExecutionPolicy -ne 'Undefined') { Say ("  policy       {0} = {1}" -f $_.Scope, $_.ExecutionPolicy) }
  }
} catch { Say '  policy       (could not read)' }

$onWindows = $true
if ($null -ne (Get-Variable -Name IsWindows -Scope Global -ErrorAction SilentlyContinue)) { $onWindows = $IsWindows }
Say ("  os           {0}" -f $(if ($onWindows) { 'Windows' } else { 'not Windows — this script is for Windows' }))

if ($onWindows) {
  $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
  if ($wsl) {
    $distros = & wsl.exe -l -q 2>$null
    $names = ($distros | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() }) -join ', '
    if ($names) { Say ("  wsl          {0}" -f $names) } else { Say '  wsl          present, no distribution installed' }
  } else { Say '  wsl          not installed' }
}

# ── 2. a Python new enough ───────────────────────────────────────────────────
# **The Store stub is the trap.** Windows ships a zero-length `python.exe` in WindowsApps that opens the
# Microsoft Store instead of running anything, and Get-Command finds it first. Run each candidate and
# believe the version it prints, rather than the fact that a file exists.
function Find-Python {
  $cands = @()
  $py = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($py) { $cands += ,@($py.Source, @('-3')) }
  foreach ($n in 'python3.exe','python.exe','python3','python') {
    foreach ($c in @(Get-Command $n -All -ErrorAction SilentlyContinue)) { $cands += ,@($c.Source, @()) }
  }
  foreach ($c in $cands) {
    $exe, $pre = $c
    if ($exe -like '*\WindowsApps\*' -and (Get-Item $exe).Length -eq 0) {
      Say ("  python       {0} — the Microsoft Store stub, skipped" -f $exe); continue
    }
    try {
      $out = & $exe @pre '-c' 'import sys;print("%d.%d"%sys.version_info[:2])' 2>$null
    } catch { continue }
    if ($LASTEXITCODE -ne 0 -or -not $out) { continue }
    $parts = ("$out".Trim() -split '\.')
    if ($parts.Count -lt 2) { continue }
    if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 9)) {
      return [pscustomobject]@{ Exe = $exe; Pre = $pre; Version = "$out".Trim() }
    }
    Say ("  python       {0} is {1} — too old, 3.9 is the floor" -f $exe, "$out".Trim())
  }
  return $null
}

$found = Find-Python
if ($found) { Say ("  python       {0}  ({1})" -f $found.Exe, $found.Version) }
else        { Say '  python       none 3.9 or newer' }
Say ("  checkout     {0}" -f $(if ($src) { $src } else { 'not run from one' }))
Say ''

# ── 3. the part that is not ready ────────────────────────────────────────────
Warn 'The daemon does not run natively on Windows yet (#29).'
Say  '  palmar/daemon.py exits on win32 because fcntl, pty and termios are not there, so the'
Say  '  launcher below will print that sentence until the port lands (docs/windows.md).'
Say  '  What works today: run the daemon inside WSL and open the address it prints in any'
Say  '  Windows browser. That needs nothing from this script.'
Say  ''
if ($found -and $src) {
  Say  'What this machine can do for the port, right now:'
  Say  ("    {0} {1}dev\conpty-check.py" -f $found.Exe, $(if ($src -eq $here) { '' } else { "$src\" }))
  Say  '  That is the ConPTY layer''s own check — the one thing #29 has been waiting for a machine to run.'
  Say  ''
}

if ($Check) { Say 'Nothing was written (-Check).'; exit 0 }
if (-not $found) { Die "no Python 3.9 or newer. Install one from python.org, or use WSL." }
if (-not $src)   { Die "run this from inside a palmar checkout. (A one-line download needs #23.)" }

# ── 4. write the launcher ────────────────────────────────────────────────────
$bin = Join-Path $Prefix 'bin'
$cmd = Join-Path $bin 'palmar.cmd'
Say 'This will write, and nothing else:'
Say ("    {0}" -f $cmd)
Say ("    a launcher running {0} against {1}" -f $found.Exe, $src)
if (-not $Yes) {
  $a = Read-Host 'Go ahead? [y/N]'
  if ($a -notmatch '^(y|yes)$') { Say 'Nothing was written.'; exit 0 }
}

New-Item -ItemType Directory -Force -Path $bin | Out-Null
# A launcher, not a copy — the same promise install.sh makes. `git pull` in the checkout updates palmar
# with no reinstall, which is how the daemon already expects to live (⑦=b).
$preArgs = if ($found.Pre.Count) { ($found.Pre -join ' ') + ' ' } else { '' }
@(
  '@echo off'
  'rem Generated by palmar''s install.ps1. Runs the tree at the path below.'
  ('set "PYTHONPATH={0};%PYTHONPATH%"' -f $src)
  ('"{0}" {1}-m palmar %*' -f $found.Exe, $preArgs)
) | Set-Content -Path $cmd -Encoding ASCII

Say ''
Say ("installed a launcher at {0}" -f $cmd)
if ($env:PATH -and ($env:PATH -split ';' | Where-Object { $_ -eq $bin })) {
  Say 'run:  palmar'
} else {
  Say 'it is not on your PATH. Either run it by its full path, or add it for your user only:'
  Say ('    setx PATH "%PATH%;{0}"' -f $bin)
  Say '  (setx writes the user environment, needs no administrator, and applies to new terminals.)'
}
Say ''
Say 'Remember: until #29, running it prints the WSL sentence. That is expected.'
