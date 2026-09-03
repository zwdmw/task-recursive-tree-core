[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [ValidateRange(0, 30)]
    [int]$WaitSeconds = 8,

    [switch]$ListOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Test-SamePath {
    param(
        [string]$Left,
        [string]$Right
    )

    if (
        [string]::IsNullOrWhiteSpace($Left) -or
        [string]::IsNullOrWhiteSpace($Right)
    ) {
        return $false
    }
    try {
        return [StringComparer]::OrdinalIgnoreCase.Equals(
            (Get-NormalizedPath -Path $Left),
            (Get-NormalizedPath -Path $Right)
        )
    } catch {
        return $false
    }
}

$root = Get-NormalizedPath -Path (
    Resolve-Path -LiteralPath $ProjectRoot
).Path
$artifactsRoot = Join-Path $root '.artifacts'
$defaultPidFile = Join-Path $artifactsRoot '.task-tree-server.pid.json'
$serverScript = Join-Path $root (
    'src\task_recursive_tree\integrations\gemini_er2\server.py'
)
$serverModulePattern = (
    '(?i)(?:^|\s)-m\s+' +
    'task_recursive_tree\.integrations\.gemini_er2\.server' +
    '(?:\s|$)'
)

$processes = @(Get-CimInstance Win32_Process)
$processesById = @{}
$childrenByParent = @{}
foreach ($process in $processes) {
    $processId = [int]$process.ProcessId
    $parentId = [int]$process.ParentProcessId
    $processesById[$processId] = $process
    if (-not $childrenByParent.ContainsKey($parentId)) {
        $childrenByParent[$parentId] =
            [System.Collections.Generic.List[int]]::new()
    }
    $childrenByParent[$parentId].Add($processId)
}

$currentAncestors = [System.Collections.Generic.HashSet[int]]::new()
$cursor = [int]$PID
while ($cursor -gt 0 -and $processesById.ContainsKey($cursor)) {
    if (-not $currentAncestors.Add($cursor)) {
        break
    }
    $cursor = [int]$processesById[$cursor].ParentProcessId
}

function Test-ServerCommand {
    param([Parameter(Mandatory = $true)][object]$Process)

    $commandLine = [string]$Process.CommandLine
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return $false
    }
    return (
        $commandLine -match $serverModulePattern -or
        $commandLine.IndexOf(
            $serverScript,
            [StringComparison]::OrdinalIgnoreCase
        ) -ge 0
    )
}

function Get-CommandPidFile {
    param([Parameter(Mandatory = $true)][object]$Process)

    $commandLine = [string]$Process.CommandLine
    $match = [regex]::Match(
        $commandLine,
        '(?i)(?:^|\s)--pid-file(?:=|\s+)(?:"([^"]+)"|''([^'']+)''|(\S+))'
    )
    if (-not $match.Success) {
        return $null
    }
    $value = @(
        $match.Groups[1].Value,
        $match.Groups[2].Value,
        $match.Groups[3].Value
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $null
    }
    if (-not [IO.Path]::IsPathRooted($value)) {
        $value = Join-Path $root $value
    }
    try {
        return Get-NormalizedPath -Path $value
    } catch {
        return $null
    }
}

function Read-PidRecord {
    param([Parameter(Mandatory = $true)][string]$Path)

    try {
        $record = Get-Content -LiteralPath $Path -Raw |
            ConvertFrom-Json -ErrorAction Stop
        $recordRoot = [string]$record.project_root
        $recordScript = [string]$record.server_script
        if (
            -not (Test-SamePath -Left $recordRoot -Right $root) -or
            -not (Test-SamePath -Left $recordScript -Right $serverScript)
        ) {
            return $null
        }
        $recordId = 0
        $recordPort = 0
        if (
            -not [int]::TryParse(
                [string]$record.process_id,
                [ref]$recordId
            ) -or
            $recordId -le 0 -or
            -not [int]::TryParse([string]$record.port, [ref]$recordPort) -or
            $recordPort -lt 1 -or
            $recordPort -gt 65535
        ) {
            return $null
        }
        return [pscustomobject]@{
            process_id = $recordId
            project_root = $recordRoot
            server_script = $recordScript
            port = $recordPort
        }
    } catch {
        if (
            -not $ListOnly -and
            (Test-SamePath -Left $Path -Right $defaultPidFile)
        ) {
            Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        }
        return $null
    }
}

$pidRecords = [System.Collections.Generic.List[object]]::new()
$pidPaths = [System.Collections.Generic.List[string]]::new()
$pidPathSet = [System.Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase
)
if (Test-Path -LiteralPath $artifactsRoot) {
    foreach (
        $file in Get-ChildItem -LiteralPath $artifactsRoot `
            -Filter '*.pid.json' -File -Recurse -ErrorAction SilentlyContinue
    ) {
        $null = $pidPathSet.Add(
            (Get-NormalizedPath -Path $file.FullName)
        )
    }
}
foreach ($process in $processes) {
    if (-not (Test-ServerCommand -Process $process)) {
        continue
    }
    $commandPidFile = Get-CommandPidFile -Process $process
    if (-not [string]::IsNullOrWhiteSpace($commandPidFile)) {
        $null = $pidPathSet.Add($commandPidFile)
    }
}
foreach ($pidPath in $pidPathSet) {
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
        continue
    }
    $record = Read-PidRecord -Path $pidPath
    if ($null -ne $record) {
        $pidRecords.Add($record)
        $pidPaths.Add($pidPath)
    }
}

$candidateIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($process in $processes) {
    $processId = [int]$process.ProcessId
    if (
        -not $currentAncestors.Contains($processId) -and
        (Test-ServerCommand -Process $process)
    ) {
        $null = $candidateIds.Add($processId)
    }
}
foreach ($record in $pidRecords) {
    $recordId = [int]$record.process_id
    if (
        $processesById.ContainsKey($recordId) -and
        -not $currentAncestors.Contains($recordId) -and
        (Test-ServerCommand -Process $processesById[$recordId])
    ) {
        $null = $candidateIds.Add($recordId)
    }
}

function Get-CandidatePorts {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    $ports = [System.Collections.Generic.List[int]]::new()
    $seenPorts = [System.Collections.Generic.HashSet[int]]::new()
    try {
        foreach (
            $connection in Get-NetTCPConnection `
                -OwningProcess $ProcessId `
                -State Listen `
                -ErrorAction Stop
        ) {
            $port = [int]$connection.LocalPort
            if (
                $port -ge 1 -and
                $port -le 65535 -and
                $seenPorts.Add($port)
            ) {
                $null = $ports.Add($port)
            }
        }
    } catch {
        # Explicit/default port probing below remains available.
    }

    foreach ($record in $pidRecords) {
        if ([int]$record.process_id -eq $ProcessId) {
            $port = [int]$record.port
            if (
                $port -ge 1 -and
                $port -le 65535 -and
                $seenPorts.Add($port)
            ) {
                $null = $ports.Add($port)
            }
        }
    }

    $commandLine = [string]$processesById[$ProcessId].CommandLine
    $portMatch = [regex]::Match(
        $commandLine,
        '(?i)(?:^|\s)--port(?:=|\s+)(\d{1,5})(?:\s|$)'
    )
    $attemptsMatch = [regex]::Match(
        $commandLine,
        '(?i)(?:^|\s)--port-attempts(?:=|\s+)(\d+)(?:\s|$)'
    )
    $preferredPort = if ($portMatch.Success) {
        [int]$portMatch.Groups[1].Value
    } else {
        8766
    }
    $attempts = if ($attemptsMatch.Success) {
        [Math]::Max(1, [int]$attemptsMatch.Groups[1].Value)
    } else {
        20
    }
    for ($offset = 0; $offset -lt $attempts; $offset++) {
        $port = $preferredPort + $offset
        if ($port -le 65535 -and $seenPorts.Add($port)) {
            $null = $ports.Add($port)
        }
    }
    return $ports.ToArray()
}

function Get-OptionalPropertyValue {
    param(
        [AllowNull()]
        [object]$InputObject,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if ($null -eq $InputObject) {
        return $null
    }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Get-OwnedHealth {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    foreach ($port in Get-CandidatePorts -ProcessId $ProcessId) {
        try {
            $health = Invoke-RestMethod `
                -Method Get `
                -Uri "http://127.0.0.1:$port/api/health" `
                -TimeoutSec 1
        } catch {
            continue
        }
        $healthServer = Get-OptionalPropertyValue `
            -InputObject $health `
            -Name 'server'
        $healthProcessValue = Get-OptionalPropertyValue `
            -InputObject $health `
            -Name 'process_id'
        $healthRoot = [string](Get-OptionalPropertyValue `
            -InputObject $health `
            -Name 'project_root')
        $healthProcessId = 0
        if (
            $healthServer -ne 'task-recursive-tree' -or
            -not [int]::TryParse(
                [string]$healthProcessValue,
                [ref]$healthProcessId
            ) -or
            $healthProcessId -ne $ProcessId -or
            -not (Test-SamePath -Left $healthRoot -Right $root)
        ) {
            continue
        }
        return [pscustomobject]@{
            ProcessId = $ProcessId
            Port = $port
            Health = $health
        }
    }
    return $null
}

$owned = [System.Collections.Generic.List[object]]::new()
foreach ($candidateId in ($candidateIds | Sort-Object)) {
    $health = Get-OwnedHealth -ProcessId $candidateId
    if ($null -ne $health) {
        $owned.Add($health)
        continue
    }

    $trustedRecord = $pidRecords | Where-Object {
        [int]$_.process_id -eq $candidateId
    } | Select-Object -First 1
    if ($null -ne $trustedRecord) {
        $owned.Add([pscustomobject]@{
            ProcessId = $candidateId
            Port = [int]$trustedRecord.port
            Health = $null
        })
    }
}

if ($ListOnly) {
    $owned | ForEach-Object { Write-Output $_.ProcessId }
    exit 0
}

if ($owned.Count -eq 0) {
    Write-Host 'No previous task-tree server instance found.'
    exit 0
}

foreach ($entry in $owned) {
    if ($null -eq $entry.Health) {
        continue
    }
    try {
        Invoke-RestMethod `
            -Method Post `
            -Uri "http://127.0.0.1:$($entry.Port)/api/shutdown" `
            -ContentType 'application/json' `
            -Body '{}' `
            -TimeoutSec 2 |
            Out-Null
    } catch {
        # A verified process is force-stopped below if graceful shutdown fails.
    }
}

$deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
do {
    $remaining = @(
        $owned |
            Where-Object {
                $null -ne (
                    Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
                )
            }
    )
    if ($remaining.Count -eq 0) {
        break
    }
    Start-Sleep -Milliseconds 100
} while ([DateTime]::UtcNow -lt $deadline)

$seen = [System.Collections.Generic.HashSet[int]]::new()
$stopOrder = [System.Collections.Generic.List[int]]::new()
function Add-ProcessTree {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)

    if (
        $currentAncestors.Contains($RootProcessId) -or
        -not $seen.Add($RootProcessId)
    ) {
        return
    }
    if ($childrenByParent.ContainsKey($RootProcessId)) {
        foreach ($childId in $childrenByParent[$RootProcessId]) {
            Add-ProcessTree -RootProcessId $childId
        }
    }
    $stopOrder.Add($RootProcessId)
}

foreach ($entry in $remaining) {
    Add-ProcessTree -RootProcessId $entry.ProcessId
}
foreach ($processId in $stopOrder) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}

$forceDeadline = [DateTime]::UtcNow.AddSeconds(3)
do {
    $remainingIds = @(
        $owned.ProcessId |
            Where-Object {
                $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue)
            }
    )
    if ($remainingIds.Count -eq 0) {
        break
    }
    Start-Sleep -Milliseconds 100
} while ([DateTime]::UtcNow -lt $forceDeadline)

if ($remainingIds.Count -gt 0) {
    Write-Error (
        'Failed to stop previous task-tree server process(es): ' +
        ($remainingIds -join ', ')
    )
    exit 1
}

for ($index = 0; $index -lt $pidRecords.Count; $index++) {
    $record = $pidRecords[$index]
    if ($owned.ProcessId -contains [int]$record.process_id) {
        Remove-Item `
            -LiteralPath $pidPaths[$index] `
            -Force `
            -ErrorAction SilentlyContinue
    }
}
Write-Host (
    'Stopped previous task-tree server process(es): ' +
    (($owned.ProcessId | Sort-Object -Unique) -join ', ')
)
exit 0
