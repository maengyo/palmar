<#
  palmar installer for Windows -- and, today, mostly a report.

  Run it with the policy bypassed, which is what a locked-down machine needs. `-ExecutionPolicy Bypass`
  applies to this one process; it changes no setting and needs no administrator:

      powershell -ExecutionPolicy Bypass -File .\install.ps1 -Check     # look, change nothing
      powershell -ExecutionPolicy Bypass -File .\install.ps1            # ...then ask before writing

  **Read this before expecting a working palmar.** The daemon does not run natively on Windows yet:
  palmar/daemon.py exits on win32, above its own imports, because fcntl, pty and termios are not there
  (#29, docs/windows.md). So what this installs is a launcher that will work the day that port lands,
  and what it does today is answer the question that is actually blocking the port -- **what is on this
  machine**. Run it with -Check and read the report; nothing is written.

  What works on Windows today is the daemon inside WSL with any Windows browser pointed at the address
  it prints. That needs nothing from this script.

  Deliberately like install.sh: no administrator, no PATH edited behind your back, nothing downloaded
  when run from a checkout, and it never starts anything.

  **ASCII only, on purpose.** Windows PowerShell 5.1 -- which is what Windows has built in, and what
  this has to run on -- reads a BOM-less file in the system code page, not UTF-8. One em dash in a
  string was enough to break parsing at line 54 and take the whole script with it, while the same
  file ran clean under PowerShell 7 on a Mac (2026-09-14). A test keeps it ASCII; a BOM would also
  work, but not needing one is simpler than remembering one.
#>
[CmdletBinding()]
param(
  # Report and stop. Writes nothing. A diagnostic that edits the machine is a worse diagnostic.
  [switch]$Check,
  # Skip the question. For scripted runs; a person should read the report first.
  [switch]$Yes,
  # Where the launcher goes. Under the profile, so no administrator is involved.
  [string]$Prefix,
  # A python.exe to use, when the search misses one you know is there. Say where it went wrong.
  [string]$Python
)

# **Not 'Stop'.** This is a diagnostic, and a diagnostic that dies on its first surprise reports
# nothing. Worse, on Windows PowerShell 5.1 a *native* command writing a single line to stderr is
# turned into a terminating NativeCommandError under 'Stop' -- so `wsl.exe -l -q` on a machine with
# no distribution installed took the whole report down with it (user, 2026-09-14). Each step is
# guarded on its own instead, the same shape dev/conpty-check.py uses.
$ErrorActionPreference = 'Continue'
$script:Trouble = @()

function Say  { param($m) Write-Host $m }
function Warn { param($m) Write-Host $m -ForegroundColor Yellow }
function Die  { param($m) Write-Host "install: $m" -ForegroundColor Red; exit 1 }

function Step {
  # One line when a step falls over, with the line number -- **because this report is often read
  # aloud off a machine nothing can be copied from.** A PowerShell stack trace cannot be.
  param([string]$What, [scriptblock]$Do)
  try { & $Do }
  catch {
    $line = $_.InvocationInfo.ScriptLineNumber
    $msg = ($_.Exception.Message -split "`n")[0]
    Say ("  ! {0} -- line {1}: {2}" -f $What, $line, $msg)
    $script:Trouble += $What
  }
}

if (-not $Prefix) {
  if ($env:LOCALAPPDATA) { $Prefix = Join-Path $env:LOCALAPPDATA 'palmar' }
  else { $Prefix = Join-Path $HOME '.palmar-bin' }     # so this is runnable off Windows, for tests
}

# -- where palmar is ----------------------------------------------------------
# Only the checkout, as on POSIX. The repository is private, so there is nothing to download yet (#23);
# when there is, this grows the same PALMAR_TARBALL branch install.sh already has.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = if (Test-Path (Join-Path $here 'palmar\__init__.py')) { $here }
       elseif (Test-Path (Join-Path $here 'palmar/__init__.py')) { $here }
       else { $null }

# -- 1. what is here ----------------------------------------------------------
Say ''
Say 'palmar -- what this machine has'
Say '------------------------------'
Say ("  PowerShell   {0} ({1})" -f $PSVersionTable.PSVersion, $PSVersionTable.PSEdition)

# The execution policy is the thing people hit first. Print every scope, because the one that bites is
# usually MachinePolicy or UserPolicy set by a group policy -- and **-ExecutionPolicy Bypass still works
# against those**, since it is not a security boundary (Microsoft says so outright). If you are reading
# this, the bypass already worked.
Step 'reading the execution policy' {
  Get-ExecutionPolicy -List | ForEach-Object {
    if ($_.ExecutionPolicy -ne 'Undefined') { Say ("  policy       {0} = {1}" -f $_.Scope, $_.ExecutionPolicy) }
  }
}

$onWindows = $true
if ($null -ne (Get-Variable -Name IsWindows -Scope Global -ErrorAction SilentlyContinue)) { $onWindows = $IsWindows }
Say ("  os           {0}" -f $(if ($onWindows) { 'Windows' } else { 'not Windows -- this script is for Windows' }))

if ($onWindows) {
  Step 'asking wsl what it has' {
    $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if (-not $wsl) { Say '  wsl          not installed'; return }
    $distros = & wsl.exe -l -q 2>$null
    $names = ($distros | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() }) -join ', '
    if ($names) { Say ("  wsl          {0}" -f $names) } else { Say '  wsl          present, no distribution installed' }
  }
}

# -- 2. a Python new enough ---------------------------------------------------
# **The Store stub is the trap.** Windows ships a zero-length `python.exe` in WindowsApps that opens the
# Microsoft Store instead of running anything, and Get-Command finds it first. Run each candidate and
# believe the version it prints, rather than the fact that a file exists.
function Try-Python {
  # **Run it and believe what it prints.** A file existing proves nothing on Windows: the alias in
  # WindowsApps is zero bytes whether Python is installed or not, so length cannot tell a working
  # Store install from the stub that opens the Store. The version it prints can.
  param([string]$Exe, [string[]]$Pre = @())
  if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) { return $null }
  try {
    $out = & $Exe @Pre '-c' 'import sys;print("%d.%d" % sys.version_info[:2])' 2>$null
  } catch { return $null }
  if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
  $v = ("$out".Trim() -split "`n")[-1].Trim()
  $parts = $v -split '\.'
  if ($parts.Count -lt 2) { return $null }
  try { $maj = [int]$parts[0]; $min = [int]$parts[1] } catch { return $null }
  return [pscustomobject]@{ Exe = $Exe; Pre = $Pre; Version = $v; Major = $maj; Minor = $min }
}

function Find-Python {
  # Four places, because PATH alone is not where Python is. **The installer's "Add python.exe to
  # PATH" box is unticked by default**, so a perfectly good Python is routinely invisible to
  # Get-Command -- which is exactly what happened the first time this ran (2026-09-14, user).
  $looked = New-Object System.Collections.ArrayList
  $cands = New-Object System.Collections.ArrayList
  function Add-Cand { param($e, $pre = @(), $why) 
    if ($e) { [void]$cands.Add(@($e, $pre, $why)) } }

  # 1) PATH -- the py launcher first, since it knows about every installed version.
  foreach ($c in @(Get-Command py.exe -All -ErrorAction SilentlyContinue)) {
    if ($c.CommandType -eq 'Application') { Add-Cand $c.Source @('-3') 'PATH (py launcher)' }
  }
  foreach ($n in 'python3.exe','python.exe','python3','python') {
    foreach ($c in @(Get-Command $n -All -ErrorAction SilentlyContinue)) {
      if ($c.CommandType -eq 'Application') { Add-Cand $c.Source @() 'PATH' }
    }
  }
  [void]$looked.Add('PATH')

  if ($onWindows) {
    # 2) The registry, which is where the installer actually records itself -- the authority, and
    # unaffected by the PATH box.
    foreach ($root in 'HKCU:\SOFTWARE\Python','HKLM:\SOFTWARE\Python','HKLM:\SOFTWARE\WOW6432Node\Python') {
      foreach ($company in 'PythonCore','ContinuumAnalytics') {
        $base = Join-Path $root $company
        if (-not (Test-Path $base)) { continue }
        foreach ($k in @(Get-ChildItem $base -ErrorAction SilentlyContinue)) {
          try {
            $ip = (Get-ItemProperty (Join-Path $k.PSPath 'InstallPath') -ErrorAction Stop)
            $dir = $ip.'(default)'
            if (-not $dir) { $dir = $ip.ExecutablePath }
            if ($dir) {
              $exe = if ($dir -like '*.exe') { $dir } else { Join-Path $dir 'python.exe' }
              Add-Cand $exe @() ("registry " + $company + " " + $k.PSChildName)
            }
          } catch { }
        }
      }
    }
    [void]$looked.Add('registry (PythonCore, ContinuumAnalytics)')

    # 3) Where the installers put it when nobody changed the path.
    $dirs = @()
    foreach ($b in @($env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:USERPROFILE, 'C:\', 'C:\ProgramData')) {
      if (-not $b) { continue }
      $dirs += (Join-Path $b 'Programs\Python')
      $dirs += $b
    }
    foreach ($d in $dirs) {
      if (-not (Test-Path -LiteralPath $d)) { continue }
      foreach ($sub in @(Get-ChildItem -LiteralPath $d -Directory -ErrorAction SilentlyContinue |
                         Where-Object { $_.Name -match '^(Python3|Python 3|anaconda3|miniconda3|miniforge3)' })) {
        Add-Cand (Join-Path $sub.FullName 'python.exe') @() ("folder " + $sub.FullName)
      }
    }
    [void]$looked.Add('the usual install folders')

    # 4) The launcher lives in the Windows directory even when PATH has been emptied.
    Add-Cand (Join-Path $env:WINDIR 'py.exe') @('-3') 'C:\Windows\py.exe'
    [void]$looked.Add('C:\Windows\py.exe')
  }

  $seen = @{}
  $tooOld = @()
  foreach ($c in $cands) {
    $exe, $pre, $why = $c
    $key = "$exe|$($pre -join ' ')"
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true
    $store = ($exe -like '*\WindowsApps\*')
    $r = Try-Python -Exe $exe -Pre $pre
    if (-not $r) {
      if ($store) { Say ("  python       {0} -- the WindowsApps alias answered nothing (Store stub)" -f $exe) }
      continue
    }
    if ($r.Major -gt 3 -or ($r.Major -eq 3 -and $r.Minor -ge 9)) {
      Say ("  python       {0}  ({1})  via {2}" -f $r.Exe, $r.Version, $why)
      return $r
    }
    $tooOld += ("{0} is {1}" -f $exe, $r.Version)
  }
  foreach ($t in $tooOld) { Say ("  python       {0} -- too old, 3.9 is the floor" -f $t) }
  Say '  python       none 3.9 or newer'
  Say ("  looked in    {0}" -f ($looked -join ' * '))
  Say '  ! if you know where it is, pass it: -Python "C:\path\to\python.exe"'
  return $null
}

$found = $null
Step 'looking for Python' {
  if ($Python) {
    $script:found = Try-Python -Exe $Python
    if ($script:found) { Say ("  python       {0}  ({1})  via -Python" -f $script:found.Exe, $script:found.Version) }
    else               { Say ("  python       -Python {0} did not answer with a version" -f $Python) }
  } else {
    $script:found = Find-Python
  }
}
$found = $script:found
Say ("  checkout     {0}" -f $(if ($src) { $src } else { 'not run from one' }))
if ($script:Trouble.Count) {
  Say ''
  Warn ("  {0} step(s) had trouble: {1}" -f $script:Trouble.Count, ($script:Trouble -join '; '))
  Say  '  The rest of the report above is still good. Read the ! line out and it can be fixed.'
}
Say ''

# -- 3. the part that is not ready --------------------------------------------
Warn 'The daemon does not run natively on Windows yet (#29).'
Say  '  palmar/daemon.py exits on win32 because fcntl, pty and termios are not there, so the'
Say  '  launcher below will print that sentence until the port lands (docs/windows.md).'
Say  '  What works today: run the daemon inside WSL and open the address it prints in any'
Say  '  Windows browser. That needs nothing from this script.'
Say  ''
if ($found -and $src) {
  Say  'What this machine can do for the port, right now:'
  Say  ("    {0} {1}dev\conpty-check.py" -f $found.Exe, $(if ($src -eq $here) { '' } else { "$src\" }))
  Say  '  That is the ConPTY layer''s own check -- the one thing #29 has been waiting for a machine to run.'
  Say  ''
}

if ($Check) { Say 'Nothing was written (-Check).'; exit 0 }
if (-not $found) { Die "no Python 3.9 or newer. Install one from python.org, or use WSL." }
if (-not $src)   { Die "run this from inside a palmar checkout. (A one-line download needs #23.)" }

# -- 4. write the launcher ----------------------------------------------------
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
# A launcher, not a copy -- the same promise install.sh makes. `git pull` in the checkout updates palmar
# with no reinstall, which is how the daemon already expects to live ((7)=b).
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
