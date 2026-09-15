<#
  palmar installer for Windows -- and, today, mostly a report.

  Run it with the policy bypassed, which is what a locked-down machine needs. `-ExecutionPolicy Bypass`
  applies to this one process; it changes no setting and needs no administrator:

      powershell -ExecutionPolicy Bypass -File .\install.ps1 -Check     # look, change nothing
      powershell -ExecutionPolicy Bypass -File .\install.ps1            # ...then ask before writing

  The daemon runs natively on Windows (ConPTY through ctypes, 2026-09-14). What is still rough is in
  docs/windows.md. Run with -Check to see what this machine has; nothing is written then.

  The one-liner, from any PowerShell:

      powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/maengyo/palmar/main/install.ps1 -OutFile install.ps1; .\install.ps1"

  Deliberately like install.sh: no administrator, the user PATH only and only after asking, nothing
  downloaded when run from a checkout, and it never starts anything.

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
# Run from a checkout, that checkout. Run any other way -- the one-liner (fetched to a file and run under
# -ExecutionPolicy Bypass, or `irm ... | iex`), where this
# script has no path of its own -- the tree is downloaded from GitHub into $Prefix\src and the launcher
# runs it from there. PALMAR_ZIP overrides the source: a URL, or a local .zip (tests). PALMAR_YES=1
# stands in for -Yes, which a piped script cannot be given.
if ($env:PALMAR_YES -eq '1') { $Yes = $true }
$here = $null
if ($MyInvocation.MyCommand.Path) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
$src = $null
if ($here -and (Test-Path (Join-Path $here 'palmar\__init__.py'))) { $src = $here }
$zip = if ($env:PALMAR_ZIP) { $env:PALMAR_ZIP } else { 'https://github.com/maengyo/palmar/archive/refs/heads/main.zip' }
$fetched = $false

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
  # **`--version`, and no -c.** Windows PowerShell 5.1 mangles arguments containing quotes on their
  # way to a native program -- PowerShell 7 fixed that, which is why a Mac could not see it. The
  # -c snippet this used to send arrived at python with its quotes eaten, died of a SyntaxError, and
  # came back here as "did not answer with a version" (user, 2026-09-14). `--version` needs no
  # quoting at all, so there is nothing left to mangle.
  try {
    $out = & $Exe @Pre '--version' 2>&1
  } catch { return $null }
  if (-not $out) { return $null }
  # "Python 3.13.1". Old versions printed it on stderr, hence 2>&1 rather than 2>$null.
  $m = [regex]::Match(($out | Out-String), 'Python\s+(\d+)\.(\d+)(\.(\d+))?')
  if (-not $m.Success) { return $null }
  $maj = [int]$m.Groups[1].Value
  $min = [int]$m.Groups[2].Value
  return [pscustomobject]@{ Exe = $Exe; Pre = $Pre; Version = "$maj.$min"; Major = $maj; Minor = $min }
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
        # A folder under C:\ or C:\ProgramData can be made by any account on the machine, and this
        # would run its python.exe and bake it into the launcher. Only one owned by an administrator,
        # SYSTEM or the person installing is a Python that was installed (review, 2026-09-15).
        $owner = ''
        try { $owner = (Get-Acl -LiteralPath $sub.FullName).Owner } catch { $owner = '' }
        $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        if ($owner -notin @('BUILTIN\Administrators', 'NT AUTHORITY\SYSTEM', 'NT SERVICE\TrustedInstaller', $me)) {
          Say ("  python       {0} -- skipped: owned by {1}, not an installer" -f $sub.FullName, $owner)
          continue
        }
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

# -- 3. what is still rough ---------------------------------------------------
Say  'The daemon runs natively on Windows (ConPTY through ctypes). What is still rough there'
Say  '  is written in docs/windows.md: the shell prompt does not yet tell palmar where it cd-ed'
Say  '  (#30), and a pane opens under your profile only (#31).'
Say  ''
if ($found -and $src) {
  Say  'The ConPTY layer''s own check, if something looks wrong on this machine:'
  Say  ("    {0} {1}dev\conpty-check.py" -f $found.Exe, $(if ($src -eq $here) { '' } else { "$src\" }))
  Say  ''
}

if ($Check) { Say 'Nothing was written (-Check).'; exit 0 }
if (-not $found) {
  # -- 2b. no Python: get one ----------------------------------------------------
  # winget ships with Windows 10/11, and python.org's package installs per user, no admin (asked for
  # 2026-09-15). A locked-down machine may refuse it; then the sentence says what to do by hand.
  $wg = Get-Command winget.exe -ErrorAction SilentlyContinue
  if (-not $wg) { Die "no Python 3.9 or newer, and no winget to fetch one. Install it from python.org (tick 'Add python.exe to PATH'), or use WSL." }
  if (-not $Yes) {
    $a = Read-Host 'No Python 3.9 or newer here. Install Python 3.13 from python.org with winget (per user, no admin)? [y/N]'
    if ($a -notmatch '^[yY]') { Die "no Python 3.9 or newer. Install one from python.org (tick 'Add python.exe to PATH'), or use WSL." }
  }
  Say 'installing Python 3.13 with winget ...'
  & $wg.Source install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity
  if ($LASTEXITCODE -eq 0) { $script:found = Find-Python; $found = $script:found }
  if (-not $found) { Die "winget ran, but no Python 3.9 or newer answers yet. Open a new PowerShell and run this again." }
}
if (-not $src) {
  # Not in a checkout: fetch the tree and keep it. The old tree, if any, is replaced whole.
  $srcRoot = Join-Path $Prefix 'src'
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ('palmar-' + [IO.Path]::GetRandomFileName())
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  $zipFile = Join-Path $tmp 'palmar.zip'
  if (Test-Path -LiteralPath $zip) {
    Copy-Item -LiteralPath $zip -Destination $zipFile
  } else {
    Say ("downloading palmar from {0}" -f $zip)
    try { Invoke-WebRequest -UseBasicParsing -Uri $zip -OutFile $zipFile }
    catch { Die ("download failed -- is the repository public, and is the network up? ({0})" -f $_.Exception.Message) }
  }
  try { Expand-Archive -LiteralPath $zipFile -DestinationPath (Join-Path $tmp 'x') -Force }
  catch { Die ("could not unpack the archive ({0})" -f $_.Exception.Message) }
  $init = Get-ChildItem -Path (Join-Path $tmp 'x') -Recurse -Depth 3 -Filter '__init__.py' |
          Where-Object { $_.Directory.Name -eq 'palmar' } | Select-Object -First 1
  if (-not $init) { Die 'the archive did not contain palmar/' }
  $tree = $init.Directory.Parent.FullName
  if (Test-Path -LiteralPath $srcRoot) { Remove-Item -LiteralPath $srcRoot -Recurse -Force }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $srcRoot) | Out-Null
  Move-Item -LiteralPath $tree -Destination $srcRoot
  Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  $src = $srcRoot
  $fetched = $true
  Say ("installed the tree at {0}" -f $src)
}

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
  'rem The port is not finished, and the refusal in daemon.py is what says so. It is lifted here'
  'rem rather than left for you to type every time: you read the warning above before this was'
  'rem written, and nothing else on the machine gets it. Remove this line and `palmar` refuses again.'
  'set "PALMAR_WINDOWS_ANYWAY=1"'
  'rem By script path, not python -m: -m puts the current directory first on sys.path (PYTHONSAFEPATH'
  'rem only stops that on 3.11+), and a cloned repository holding a palmar\ or a json.py would run.'
  'rem No PYTHONPATH either: an empty element in it is the current directory too (review, 2026-09-15).'
  'set "PYTHONSAFEPATH=1"'
  ('"{0}" {1}"{2}\launch.py" %*' -f $found.Exe, $preArgs, $src)
) | Set-Content -Path $cmd -Encoding ASCII

Say ''
Say ("installed a launcher at {0}" -f $cmd)
if ($env:PATH -and ($env:PATH -split ';' | Where-Object { $_ -eq $bin })) {
  Say 'run:  palmar'
} else {
  # **The user environment, read back from the registry -- not %PATH%.** `setx PATH "%PATH%;..."`
  # is the line everyone copies and it is a trap: %PATH% at that moment is user *and* machine PATH
  # joined, so it writes the whole machine PATH into the user's, permanently, and 1024 characters in
  # it silently truncates. Reading the user value alone and appending to that changes one entry.
  $mine = [Environment]::GetEnvironmentVariable('PATH', 'User')
  $already = $mine -and (($mine -split ';') -contains $bin)
  if ($already) {
    Say 'it is on your PATH already -- open a new terminal and run:  palmar'
  } else {
    Say ''
    Say ('It is not on your PATH yet. Adding {0} to your **user** PATH' -f $bin)
    Say '  changes nothing else, needs no administrator, and applies to new terminals.'
    $ok = $Yes
    if (-not $ok) {
      $a = Read-Host 'Add it? [y/N]'
      $ok = ($a -match '^(y|yes)$')
    }
    if ($ok) {
      $next = if ($mine) { $mine.TrimEnd(';') + ';' + $bin } else { $bin }
      try {
        [Environment]::SetEnvironmentVariable('PATH', $next, 'User')
        # **And this session too.** Run as `irm ... | iex` this script is in the person's own
        # console, so `palmar` works the moment it returns -- not only in the next window.
        $env:PATH = $env:PATH.TrimEnd(';') + ';' + $bin
        Say 'added. Run:  palmar'
      } catch {
        Say ('could not write it -- add it by hand: {0}' -f $bin)
      }
    } else {
      Say ('not added. Run it by its full path: {0}' -f $cmd)
    }
  }
}
Say ''
Say 'The launcher sets PALMAR_WINDOWS_ANYWAY for you, so `palmar` runs rather than refusing.'
Say 'What is still missing there is at the top of this output, and in docs/windows.md.'
