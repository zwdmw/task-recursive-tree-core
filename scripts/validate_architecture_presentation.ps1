param([string]$PresentationPath)

$ErrorActionPreference = 'Stop'

function Resolve-DeckPath {
    param([string]$InputPath)

    $repoRoot = [IO.Path]::GetFullPath(
        (Join-Path $PSScriptRoot '..')
    )
    if ([string]::IsNullOrWhiteSpace($InputPath)) {
        $candidate = Get-ChildItem -LiteralPath (
            Join-Path $repoRoot 'docs'
        ) -Filter '*.pptx' -File |
            Where-Object { $_.BaseName -like 'Task-Recursive-Tree-Agent-*' } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($null -eq $candidate) {
            throw 'No Task Recursive Tree presentation was found.'
        }
        return $candidate.FullName
    }

    $candidatePath = if ([IO.Path]::IsPathRooted($InputPath)) {
        $InputPath
    } else {
        Join-Path (Get-Location).Path $InputPath
    }
    $fullPath = [IO.Path]::GetFullPath($candidatePath)
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        throw "Presentation not found: $fullPath"
    }
    return $fullPath
}

$resolvedPresentationPath = Resolve-DeckPath $PresentationPath
$validationRoot = Join-Path (
    Split-Path -Parent $resolvedPresentationPath
) (
    'presentation\validation-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
)
$zipPath = Join-Path $validationRoot 'deck.zip'
$unpackedPath = Join-Path $validationRoot 'unpacked'

New-Item -ItemType Directory -Path $validationRoot | Out-Null
Copy-Item -LiteralPath $resolvedPresentationPath -Destination $zipPath
Expand-Archive -LiteralPath $zipPath -DestinationPath $unpackedPath

$slides = @(
    Get-ChildItem -LiteralPath (
        Join-Path $unpackedPath 'ppt\slides'
    ) -Filter 'slide*.xml'
)
$notes = @(
    Get-ChildItem -LiteralPath (
        Join-Path $unpackedPath 'ppt\notesSlides'
    ) -Filter 'notesSlide*.xml' -ErrorAction SilentlyContinue
)
$relationships = @(
    Get-ChildItem -LiteralPath (
        Join-Path $unpackedPath 'ppt\slides\_rels'
    ) -Filter '*.rels'
)

$invalidXml = [Collections.Generic.List[string]]::new()
$duplicateIds = [Collections.Generic.List[string]]::new()
$negativeExtents = [Collections.Generic.List[string]]::new()
foreach ($file in Get-ChildItem -LiteralPath $unpackedPath -Recurse -Filter '*.xml') {
    try {
        [xml](Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8) |
            Out-Null
    }
    catch {
        $invalidXml.Add($file.FullName)
    }
}

foreach ($file in $slides) {
    $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
    $ids = [regex]::Matches(
        $content,
        'cNvPr id="([0-9]+)"'
    ) | ForEach-Object { $_.Groups[1].Value }
    foreach ($group in ($ids | Group-Object | Where-Object Count -gt 1)) {
        $duplicateIds.Add(
            "$($file.Name): $($group.Name) x $($group.Count)"
        )
    }
    if ($content -match '<a:ext cx="-' -or $content -match '<a:ext[^>]*cy="-[0-9]+') {
        $negativeExtents.Add($file.Name)
    }
}

$result = [pscustomobject]@{
    Presentation = $resolvedPresentationPath
    FileBytes = (
        Get-Item -LiteralPath $resolvedPresentationPath
    ).Length
    Slides = $slides.Count
    SlideRelationships = $relationships.Count
    Notes = $notes.Count
    InvalidXml = $invalidXml.Count
    DuplicateIds = $duplicateIds.Count
    NegativeExtents = $negativeExtents.Count
    HasContentTypes = Test-Path -LiteralPath (
        Join-Path $unpackedPath '[Content_Types].xml'
    )
    HasPresentation = Test-Path -LiteralPath (
        Join-Path $unpackedPath 'ppt\presentation.xml'
    )
    ValidationDirectory = $validationRoot
}

$result | ConvertTo-Json -Depth 3
if (
    $invalidXml.Count -gt 0 -or
    $duplicateIds.Count -gt 0 -or
    $negativeExtents.Count -gt 0
) {
    $invalidXml
    $duplicateIds
    $negativeExtents
    exit 1
}
