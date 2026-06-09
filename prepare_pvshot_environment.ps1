param(
  [string]$CaseDir
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $ScriptDir "pvshot_config.json"

function Test-AsciiPath {
  param([string]$PathText)
  return ($PathText -cmatch "^[\x00-\x7F]+$")
}

function Test-OpenFoamCase {
  param([string]$PathText)
  return (
    (Test-Path -LiteralPath (Join-Path $PathText "constant\polyMesh")) -and
    (Test-Path -LiteralPath (Join-Path $PathText "system\controlDict"))
  )
}

function Get-SafeName {
  param([string]$Name)
  $safe = $Name -replace "[^A-Za-z0-9_.-]", ""
  if ([string]::IsNullOrWhiteSpace($safe)) {
    $safe = "case"
  }
  return $safe
}

function Test-WritableDirectory {
  param([string]$PathText)
  try {
    New-Item -ItemType Directory -Path $PathText -Force | Out-Null
    $probe = Join-Path $PathText ".pvshot_write_test"
    Set-Content -LiteralPath $probe -Value "ok" -Encoding ASCII
    Remove-Item -LiteralPath $probe -Force
    return $true
  } catch {
    return $false
  }
}

function Get-WorkRoot {
  $candidates = @()
  if (Test-Path -LiteralPath "D:\") { $candidates += "D:\pvshot_work" }
  if (Test-Path -LiteralPath "C:\") { $candidates += "C:\pvshot_work" }

  foreach ($candidate in $candidates) {
    if (Test-WritableDirectory $candidate) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }

  throw "No writable work root found. Tried: $($candidates -join ', ')"
}

function Add-PvPythonCandidate {
  param(
    [System.Collections.Generic.List[string]]$Candidates,
    [string]$PathText
  )

  if ([string]::IsNullOrWhiteSpace($PathText)) {
    return
  }

  $clean = $PathText.Trim('"')
  if (Test-Path -LiteralPath $clean -PathType Container) {
    $maybe = Join-Path $clean "bin\pvpython.exe"
    if (Test-Path -LiteralPath $maybe -PathType Leaf) {
      $Candidates.Add((Resolve-Path -LiteralPath $maybe).Path)
    }
    return
  }

  if (Test-Path -LiteralPath $clean -PathType Leaf) {
    $item = Get-Item -LiteralPath $clean
    if ($item.Name -ieq "pvpython.exe") {
      $Candidates.Add($item.FullName)
    } elseif ($item.Name -ieq "paraview.exe") {
      $maybe = Join-Path $item.DirectoryName "pvpython.exe"
      if (Test-Path -LiteralPath $maybe -PathType Leaf) {
        $Candidates.Add((Resolve-Path -LiteralPath $maybe).Path)
      }
    }
  }
}

function Find-PvPython {
  $searched = New-Object System.Collections.Generic.List[string]
  $candidates = New-Object System.Collections.Generic.List[string]

  $cmd = Get-Command "pvpython.exe" -ErrorAction SilentlyContinue
  if ($cmd) {
    foreach ($entry in $cmd) {
      Add-PvPythonCandidate $candidates $entry.Source
    }
  }
  $searched.Add("PATH")

  $registryRoots = @(
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*"
  )
  foreach ($root in $registryRoots) {
    $searched.Add($root)
    Get-ItemProperty $root -ErrorAction SilentlyContinue |
      Where-Object { $_.DisplayName -like "*ParaView*" } |
      ForEach-Object {
        Add-PvPythonCandidate $candidates $_.InstallLocation
        Add-PvPythonCandidate $candidates $_.DisplayIcon
      }
  }

  $commonRoots = @(
    "C:\Program Files",
    "C:\Program Files (x86)",
    "D:\",
    "E:\"
  )
  foreach ($root in $commonRoots) {
    $searched.Add($root)
    if (Test-Path -LiteralPath $root) {
      Get-ChildItem -LiteralPath $root -Directory -Filter "ParaView*" -ErrorAction SilentlyContinue |
        ForEach-Object {
          Add-PvPythonCandidate $candidates $_.FullName
          $direct = Join-Path $_.FullName "bin\pvpython.exe"
          Add-PvPythonCandidate $candidates $direct
        }
    }
  }

  $found = $candidates | Select-Object -Unique | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
  if (-not $found) {
    throw "Cannot find pvpython.exe. Searched: $($searched -join '; ')"
  }

  return $found
}

function Get-ParaViewVersion {
  param([string]$PvPythonPath)

  $fromPath = [regex]::Match($PvPythonPath, "ParaView\s*([0-9]+(?:\.[0-9]+){1,3})")
  if ($fromPath.Success) {
    return $fromPath.Groups[1].Value
  }

  try {
    $info = (Get-Item -LiteralPath $PvPythonPath).VersionInfo
    if (-not [string]::IsNullOrWhiteSpace($info.ProductVersion)) {
      return $info.ProductVersion
    }
  } catch {
  }

  return "unknown"
}

function Find-OpenFoamCase {
  param([string]$RequestedCaseDir)

  if (-not [string]::IsNullOrWhiteSpace($RequestedCaseDir)) {
    $resolved = (Resolve-Path -LiteralPath $RequestedCaseDir).Path
    if (-not (Test-OpenFoamCase $resolved)) {
      throw "The requested CaseDir is not an OpenFOAM case: $resolved"
    }
    return $resolved
  }

  if (Test-OpenFoamCase $ScriptDir) {
    return (Resolve-Path -LiteralPath $ScriptDir).Path
  }

  $queue = New-Object System.Collections.Generic.Queue[object]
  $queue.Enqueue([pscustomobject]@{ Path = $ScriptDir; Depth = 0 })
  $matches = New-Object System.Collections.Generic.List[string]

  while ($queue.Count -gt 0) {
    $item = $queue.Dequeue()
    if ($item.Depth -ge 3) {
      continue
    }

    Get-ChildItem -LiteralPath $item.Path -Directory -ErrorAction SilentlyContinue |
      ForEach-Object {
        if (Test-OpenFoamCase $_.FullName) {
          $matches.Add($_.FullName)
        } else {
          $queue.Enqueue([pscustomobject]@{ Path = $_.FullName; Depth = $item.Depth + 1 })
        }
      }
  }

  $case = $matches | Sort-Object | Select-Object -First 1
  if (-not $case) {
    throw "Cannot find an OpenFOAM case. Pass -CaseDir or place a case under this folder."
  }

  return (Resolve-Path -LiteralPath $case).Path
}

function Copy-CaseIfNeeded {
  param(
    [string]$OriginalCaseDir,
    [string]$WorkRoot
  )

  $pathWasNonAscii = -not (Test-AsciiPath $OriginalCaseDir)
  $safeName = Get-SafeName (Split-Path -Leaf $OriginalCaseDir)

  if (-not $pathWasNonAscii) {
    return [pscustomobject]@{
      CaseDir = $OriginalCaseDir
      SafeName = $safeName
      PathWasNonAscii = $false
      CaseWasCopied = $false
    }
  }

  $target = Join-Path (Join-Path $WorkRoot "cases") $safeName
  New-Item -ItemType Directory -Path $target -Force | Out-Null

  & robocopy $OriginalCaseDir $target /E /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -gt 7) {
    throw "robocopy failed with exit code $LASTEXITCODE while copying case to $target"
  }

  return [pscustomobject]@{
    CaseDir = (Resolve-Path -LiteralPath $target).Path
    SafeName = $safeName
    PathWasNonAscii = $true
    CaseWasCopied = $true
  }
}

function Ensure-FoamFile {
  param(
    [string]$CaseDir,
    [string]$SafeName
  )

  $existing = Get-ChildItem -LiteralPath $CaseDir -File -Filter "*.foam" -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($existing) {
    return [pscustomobject]@{
      CaseFile = $existing.FullName
      FoamFileWasCreated = $false
    }
  }

  $foamFile = Join-Path $CaseDir "$SafeName.foam"
  Set-Content -LiteralPath $foamFile -Value "" -Encoding ASCII
  return [pscustomobject]@{
    CaseFile = (Resolve-Path -LiteralPath $foamFile).Path
    FoamFileWasCreated = $true
  }
}

$pvpython = Find-PvPython
$version = Get-ParaViewVersion $pvpython
$originalCase = Find-OpenFoamCase $CaseDir
$workRoot = Get-WorkRoot
$preparedCase = Copy-CaseIfNeeded $originalCase $workRoot
$foam = Ensure-FoamFile $preparedCase.CaseDir $preparedCase.SafeName
$outputDir = Join-Path (Join-Path $workRoot "output") $preparedCase.SafeName
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

$existingConfig = $null
if (Test-Path -LiteralPath $ConfigPath) {
  try {
    $existingConfig = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
  } catch {
    $existingConfig = $null
  }
}

$config = [ordered]@{
  pvpython_path = $pvpython
  paraview_version = $version
  original_case_dir = $originalCase
  case_dir = $preparedCase.CaseDir
  case_file = $foam.CaseFile
  output_dir = (Resolve-Path -LiteralPath $outputDir).Path
  path_was_non_ascii = [bool]$preparedCase.PathWasNonAscii
  case_was_copied = [bool]$preparedCase.CaseWasCopied
  foam_file_was_created = [bool]$foam.FoamFileWasCreated
}

if ($existingConfig) {
  foreach ($key in @(
      "screenshot_settings",
      "pvsm_state_file",
      "batch_enabled",
      "batch_case_root",
      "output_root",
      "batch_cases"
    )) {
    if ($existingConfig.PSObject.Properties.Name -contains $key) {
      $config[$key] = $existingConfig.$key
    }
  }
}

$json = $config | ConvertTo-Json -Depth 4
Set-Content -LiteralPath $ConfigPath -Value $json -Encoding UTF8

Write-Host "Prepared pvshot environment:"
Write-Host "  Config: $ConfigPath"
Write-Host "  pvpython: $pvpython"
Write-Host "  case: $($preparedCase.CaseDir)"
Write-Host "  output: $($config.output_dir)"
