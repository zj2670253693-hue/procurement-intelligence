[CmdletBinding()]
param(
    [switch]$AuditOnly,
    [string]$ArtifactPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ArtifactsDirectory = Join-Path $PSScriptRoot "artifacts"
$RequiredFiles = @(
    "backend/output/raw_results.json",
    "backend/requirements.txt",
    "frontend-dist/index.html",
    "deploy/procurement-api.service",
    "deploy/nginx-procurement-app.conf",
    "deploy/mysql-low-memory.cnf",
    "deploy/procurement.env.example",
    "deploy/bootstrap-ubuntu.sh",
    "deploy/finalize-services.sh"
)

function Get-RelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$FullPath
    )

    $normalizedBase = [System.IO.Path]::GetFullPath($BasePath).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $normalizedFull = [System.IO.Path]::GetFullPath($FullPath)
    if (-not $normalizedFull.StartsWith($normalizedBase, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside the expected root: $FullPath"
    }
    return $normalizedFull.Substring($normalizedBase.Length).Replace('\', '/')
}

function Test-ForbiddenReleasePath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $path = $RelativePath.Replace('\', '/').TrimStart('/')
    $segments = $path.Split('/')
    $forbiddenDirectories = @("node_modules", "__pycache__", ".git", "mysql-data", "uploads")
    foreach ($segment in $segments) {
        if ($forbiddenDirectories -contains $segment) {
            return $true
        }
    }

    $leaf = $segments[-1]
    if ($leaf -eq ".env") {
        return $true
    }
    if ($leaf -match '^(id_rsa|id_ed25519)(\.pub)?$') {
        return $true
    }
    return $false
}

function Copy-FilteredTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string[]]$ExcludedTopLevel = @()
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Source directory does not exist: $Source"
    }

    Get-ChildItem -LiteralPath $Source -File -Recurse | ForEach-Object {
        $relative = Get-RelativePath -BasePath $Source -FullPath $_.FullName
        $topLevel = $relative.Split('/')[0]
        if (($ExcludedTopLevel -contains $topLevel) -or (Test-ForbiddenReleasePath -RelativePath $relative)) {
            return
        }

        $target = Join-Path $Destination $relative
        $targetDirectory = Split-Path -Parent $target
        New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $target -Force
    }
}

function Assert-ReleaseTree {
    param([Parameter(Mandatory = $true)][string]$Root)

    foreach ($required in $RequiredFiles) {
        $requiredPath = Join-Path $Root $required
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "Release audit failed; required file is missing: $required"
        }
    }

    $forbidden = @()
    Get-ChildItem -LiteralPath $Root -File -Recurse | ForEach-Object {
        $relative = Get-RelativePath -BasePath $Root -FullPath $_.FullName
        if (Test-ForbiddenReleasePath -RelativePath $relative) {
            $forbidden += $relative
        }
    }
    if ($forbidden.Count -gt 0) {
        throw "Release audit failed; forbidden paths found: $($forbidden -join ', ')"
    }
}

function Assert-ZipArtifact {
    param([Parameter(Mandatory = $true)][string]$ZipPath)

    if (-not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) {
        throw "Release artifact does not exist: $ZipPath"
    }

    $archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $invalidEntries = @($archive.Entries | Where-Object {
            $name = $_.FullName
            $name.Contains('\') -or
                $name.StartsWith('/') -or
                $name -match '^[A-Za-z]:' -or
                @($name.Split('/') | Where-Object { $_ -eq '..' }).Count -gt 0
        })
        if ($invalidEntries.Count -gt 0) {
            throw "Release audit failed; ZIP contains non-portable or unsafe entry paths: $($invalidEntries[0].FullName)"
        }
    }
    finally {
        $archive.Dispose()
    }

    $auditDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("procurement-release-audit-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $auditDirectory | Out-Null
    try {
        Expand-Archive -LiteralPath $ZipPath -DestinationPath $auditDirectory -Force
        Assert-ReleaseTree -Root $auditDirectory
    }
    finally {
        if (Test-Path -LiteralPath $auditDirectory) {
            Remove-Item -LiteralPath $auditDirectory -Recurse -Force
        }
    }
}

function New-PortableZipArchive {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $stream = [System.IO.File]::Open($Destination, [System.IO.FileMode]::CreateNew)
    $archive = New-Object System.IO.Compression.ZipArchive(
        $stream,
        [System.IO.Compression.ZipArchiveMode]::Create,
        $false
    )
    try {
        Get-ChildItem -LiteralPath $Source -File -Recurse | Sort-Object FullName | ForEach-Object {
            $entryName = (Get-RelativePath -BasePath $Source -FullPath $_.FullName).Replace('\', '/')
            $entry = $archive.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
            $inputStream = [System.IO.File]::OpenRead($_.FullName)
            $outputStream = $entry.Open()
            try {
                $inputStream.CopyTo($outputStream)
            }
            finally {
                $outputStream.Dispose()
                $inputStream.Dispose()
            }
        }
    }
    finally {
        $archive.Dispose()
        $stream.Dispose()
    }
}

if ($AuditOnly) {
    if ([string]::IsNullOrWhiteSpace($ArtifactPath)) {
        $latest = Get-ChildItem -LiteralPath $ArtifactsDirectory -Filter "procurement-app-*.zip" -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($null -eq $latest) {
            throw "No release artifact was found. Build one before using -AuditOnly."
        }
        $ArtifactPath = $latest.FullName
    }
    else {
        $ArtifactPath = (Resolve-Path -LiteralPath $ArtifactPath).Path
    }

    Assert-ZipArtifact -ZipPath $ArtifactPath
    Write-Output "Release audit passed: $ArtifactPath"
    exit 0
}

$FrontendDirectory = Join-Path $ProjectRoot "frontend"
$BackendDirectory = Join-Path $ProjectRoot "task1_entity_extraction"
$FrontendDist = Join-Path $FrontendDirectory "dist"

Push-Location $FrontendDirectory
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) {
        throw "npm ci failed with exit code $LASTEXITCODE"
    }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) {
        throw "npm run build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $FrontendDist -PathType Container)) {
    throw "Frontend build did not create the expected dist directory."
}

New-Item -ItemType Directory -Path $ArtifactsDirectory -Force | Out-Null
$stageDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("procurement-release-stage-" + [guid]::NewGuid().ToString("N"))
$timestamp = Get-Date -Format "yyyyMMdd-HHmmssfff"
$zipPath = Join-Path $ArtifactsDirectory "procurement-app-$timestamp.zip"

New-Item -ItemType Directory -Path $stageDirectory | Out-Null
try {
    $stageBackend = Join-Path $stageDirectory "backend"
    $stageFrontend = Join-Path $stageDirectory "frontend-dist"
    $stageDeploy = Join-Path $stageDirectory "deploy"
    New-Item -ItemType Directory -Path $stageBackend, $stageFrontend, $stageDeploy -Force | Out-Null

    Get-ChildItem -LiteralPath $BackendDirectory -File | Where-Object {
        ($_.Extension -eq ".py") -or ($_.Name -in @("requirements.txt", ".env.example"))
    } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $stageBackend $_.Name) -Force
    }

    foreach ($directoryName in @("api", "data_loader", "database", "evaluation", "extractor", "models", "sample_data")) {
        Copy-FilteredTree -Source (Join-Path $BackendDirectory $directoryName) -Destination (Join-Path $stageBackend $directoryName)
    }

    $stageOutput = Join-Path $stageBackend "output"
    New-Item -ItemType Directory -Path $stageOutput -Force | Out-Null
    foreach ($outputName in @("__init__.py", "writer.py", "raw_results.json", "extraction_results.csv", "attachment_coverage_report.json", "dedouble_report.json", "evaluation_report.json")) {
        $outputPath = Join-Path (Join-Path $BackendDirectory "output") $outputName
        if (Test-Path -LiteralPath $outputPath -PathType Leaf) {
            Copy-Item -LiteralPath $outputPath -Destination (Join-Path $stageOutput $outputName) -Force
        }
    }

    Copy-FilteredTree -Source $FrontendDist -Destination $stageFrontend
    Copy-FilteredTree -Source $PSScriptRoot -Destination $stageDeploy -ExcludedTopLevel @("artifacts")

    Assert-ReleaseTree -Root $stageDirectory
    New-PortableZipArchive -Source $stageDirectory -Destination $zipPath
    Assert-ZipArtifact -ZipPath $zipPath
}
finally {
    if (Test-Path -LiteralPath $stageDirectory) {
        Remove-Item -LiteralPath $stageDirectory -Recurse -Force
    }
}

$artifact = Get-Item -LiteralPath $zipPath
$hash = Get-FileHash -LiteralPath $zipPath -Algorithm SHA256
Write-Output "Release artifact: $($artifact.FullName)"
Write-Output "Release size bytes: $($artifact.Length)"
Write-Output "Release SHA256: $($hash.Hash)"
