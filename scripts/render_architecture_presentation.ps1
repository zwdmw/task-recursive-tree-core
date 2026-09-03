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
$docsDir = Split-Path -Parent $resolvedPresentationPath
$baseName = [IO.Path]::GetFileNameWithoutExtension(
    $resolvedPresentationPath
)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$renderDir = Join-Path $docsDir "presentation\rendered-$stamp"
$pdfPath = Join-Path $docsDir "$baseName.pdf"

New-Item -ItemType Directory -Path $renderDir | Out-Null

$powerPoint = $null
$presentation = $null
try {
    $powerPoint = New-Object -ComObject PowerPoint.Application
    $presentation = $powerPoint.Presentations.Open(
        $resolvedPresentationPath,
        -1,
        0,
        0
    )
    $presentation.SaveAs($pdfPath, 32)
    $presentation.Export($renderDir, 'PNG', 1920, 1080)

    [pscustomobject]@{
        Presentation = $resolvedPresentationPath
        Slides = $presentation.Slides.Count
        Pdf = $pdfPath
        PdfBytes = (Get-Item -LiteralPath $pdfPath).Length
        RenderDirectory = $renderDir
        PngCount = @(
            Get-ChildItem -LiteralPath $renderDir -Filter '*.PNG'
        ).Count
    } | ConvertTo-Json -Depth 3
}
finally {
    if ($null -ne $presentation) {
        $presentation.Close()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject(
            $presentation
        )
    }
    if ($null -ne $powerPoint) {
        $powerPoint.Quit()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject(
            $powerPoint
        )
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
