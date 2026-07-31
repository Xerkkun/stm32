param(
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [string]$Gcc = 'C:\msys64\ucrt64\bin\gcc.exe',
    [string]$Ar = 'C:\msys64\ucrt64\bin\ar.exe'
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$scriptRoot = $PSScriptRoot
$output = [System.IO.Path]::GetFullPath($OutputDir)

if (Test-Path -LiteralPath $output) {
    throw "OutputDir must not already exist: $output"
}
if (-not (Test-Path -LiteralPath $Gcc -PathType Leaf)) {
    throw "GCC not found: $Gcc"
}
if (-not (Test-Path -LiteralPath $Ar -PathType Leaf)) {
    throw "ar not found: $Ar"
}

$required = @(
    (Join-Path $source 'include\Makefile.def'),
    (Join-Path $source 'mylib\tcode.c'),
    (Join-Path $source 'testu01\bbattery.c'),
    (Join-Path $scriptRoot 'testu01_file_batteries.c'),
    (Join-Path $scriptRoot 'testu01_ucrt64_config.h'),
    (Join-Path $scriptRoot 'testu01_ucrt64_gdefconf.h')
)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required file not found: $path"
    }
}

$includeDir = Join-Path $source 'include'
$buildDir = Join-Path $output 'build'
$binDir = Join-Path $output 'bin'
$libDir = Join-Path $output 'lib'
New-Item -ItemType Directory -Path $buildDir, $binDir, $libDir | Out-Null

$tcode = Join-Path $binDir 'tcode.exe'
& $Gcc -O2 -std=c11 -o $tcode (Join-Path $source 'mylib\tcode.c')
if ($LASTEXITCODE -ne 0) {
    throw "tcode compilation failed with exit code $LASTEXITCODE"
}

$definitionText = Get-Content -Raw -LiteralPath (
    Join-Path $source 'include\Makefile.def'
)
$groups = @(
    @{ Name = 'mylib'; Prefix = 'MYLIB' },
    @{ Name = 'probdist'; Prefix = 'PROBDIST' },
    @{ Name = 'testu01'; Prefix = 'TESTU01' }
)

foreach ($group in $groups) {
    $headerMatch = [regex]::Match(
        $definitionText,
        "(?m)^$($group.Prefix)HEADERS\s*=\s*(.+)$"
    )
    if (-not $headerMatch.Success) {
        throw "Cannot find $($group.Prefix)HEADERS in Makefile.def"
    }
    $headers = $headerMatch.Groups[1].Value -split '\s+'
    foreach ($header in $headers) {
        if (-not $header) {
            continue
        }
        $module = [System.IO.Path]::GetFileNameWithoutExtension($header)
        $tex = Join-Path $source "$($group.Name)\$module.tex"
        $destination = Join-Path $includeDir $header
        & $tcode $tex $destination
        if ($LASTEXITCODE -ne 0) {
            throw "Header generation failed for $header"
        }
    }
}

Copy-Item -LiteralPath (
    Join-Path $scriptRoot 'testu01_ucrt64_config.h'
) -Destination (Join-Path $includeDir 'config.h')
Copy-Item -LiteralPath (
    Join-Path $scriptRoot 'testu01_ucrt64_gdefconf.h'
) -Destination (Join-Path $includeDir 'gdefconf.h')

$includeFlags = @(
    "-I$includeDir",
    "-I$(Join-Path $source 'mylib')",
    "-I$(Join-Path $source 'probdist')",
    "-I$(Join-Path $source 'testu01')"
)
$libraryPaths = @{}

foreach ($group in $groups) {
    $sourceMatch = [regex]::Match(
        $definitionText,
        "(?m)^$($group.Prefix)SOURCES\s*=\s*(.+)$"
    )
    if (-not $sourceMatch.Success) {
        throw "Cannot find $($group.Prefix)SOURCES in Makefile.def"
    }
    $sourceNames = $sourceMatch.Groups[1].Value -split '\s+'
    $objectDir = Join-Path $buildDir $group.Name
    New-Item -ItemType Directory -Path $objectDir | Out-Null
    $objects = @()
    foreach ($sourceName in $sourceNames) {
        if (-not $sourceName) {
            continue
        }
        $sourcePath = Join-Path $source "$($group.Name)\$sourceName"
        $objectPath = Join-Path $objectDir (
            [System.IO.Path]::GetFileNameWithoutExtension($sourceName) + '.o'
        )
        & $Gcc -O2 -std=c11 -DHAVE_CONFIG_H @includeFlags `
            -c $sourcePath -o $objectPath
        if ($LASTEXITCODE -ne 0) {
            throw "Compilation failed for $sourcePath"
        }
        $objects += $objectPath
    }
    $libraryPath = Join-Path $libDir "lib$($group.Name).a"
    & $Ar rcs $libraryPath @objects
    if ($LASTEXITCODE -ne 0) {
        throw "Archive creation failed for $libraryPath"
    }
    $libraryPaths[$group.Name] = $libraryPath
}

$adapter = Join-Path $binDir 'testu01_file_batteries.exe'
& $Gcc -O2 -std=c11 -DHAVE_CONFIG_H @includeFlags `
    (Join-Path $scriptRoot 'testu01_file_batteries.c') `
    -o $adapter `
    $libraryPaths['testu01'] `
    $libraryPaths['probdist'] `
    $libraryPaths['mylib'] `
    -lm -lwsock32
if ($LASTEXITCODE -ne 0) {
    throw "Adapter link failed with exit code $LASTEXITCODE"
}

$commit = $null
$gitHead = Join-Path $source '.git\HEAD'
if (Test-Path -LiteralPath $gitHead -PathType Leaf) {
    $headText = (Get-Content -Raw -LiteralPath $gitHead).Trim()
    if ($headText.StartsWith('ref: ')) {
        $refPath = Join-Path $source (
            '.git\' + $headText.Substring(5).Replace('/', '\')
        )
        if (Test-Path -LiteralPath $refPath -PathType Leaf) {
            $commit = (Get-Content -Raw -LiteralPath $refPath).Trim()
        }
    } elseif ($headText -match '^[0-9a-fA-F]{40}$') {
        $commit = $headText.ToLowerInvariant()
    }
}
$provenance = [ordered]@{
    schema_version = 1
    testu01_version = '1.2.3'
    source_root = $source
    source_git_commit = $commit
    compiler = (& $Gcc --version | Select-Object -First 1)
    adapter = [ordered]@{
        path = $adapter
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $adapter).Hash.ToLowerInvariant()
        bytes = (Get-Item -LiteralPath $adapter).Length
    }
    wrapper_source_sha256 = (
        Get-FileHash -Algorithm SHA256 -LiteralPath (
            Join-Path $scriptRoot 'testu01_file_batteries.c'
        )
    ).Hash.ToLowerInvariant()
    input_policy = 'exact finite bit count; no repetition or recycling'
}
$provenancePath = Join-Path $output 'testu01_build_provenance.json'
$provenance | ConvertTo-Json -Depth 6 | Set-Content `
    -LiteralPath $provenancePath -Encoding utf8
Write-Output $provenancePath
