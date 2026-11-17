[CmdletBinding(DefaultParameterSetName = 'Start')]
param(
    [Parameter(ParameterSetName = 'Start')]
    [ValidateRange(0, 100000)][int]$Limit = 0,
    [Parameter(Mandatory = $true, ParameterSetName = 'Monitor')]
    [ValidatePattern('^[a-f0-9]{32}$')][string]$RunId,
    [ValidateNotNullOrEmpty()][string]$ApiUrl = 'http://127.0.0.1:8101',
    [ValidateRange(1, 60)][int]$PollSeconds = 10
)
$ErrorActionPreference = 'Stop'
$baseUri = $null
if (-not [Uri]::TryCreate($ApiUrl, [UriKind]::Absolute, [ref]$baseUri) -or
    $baseUri.Scheme -notin @('http', 'https') -or $baseUri.Query -or $baseUri.Fragment) {
    throw 'ApiUrl must be an HTTP(S) backend URL without a query string or fragment.'
}
$ApiUrl = $ApiUrl.TrimEnd('/')
$runsUrl = "$ApiUrl/api/v1/map/runs"

if ($PSCmdlet.ParameterSetName -eq 'Start') {
    try {
        $ready = Invoke-RestMethod -Uri "$ApiUrl/api/v1/map/ready" -TimeoutSec 30
    } catch {
        throw "MAP is unavailable at $ApiUrl. Start it with .\Start-Map.ps1 or .\Start-Docker.ps1 and check source readiness. $($_.Exception.Message)"
    }
    if ($ready.source_ready -ne $true) {
        throw 'MAP source is not ready. Run Setup-Map.ps1 and check the backend source configuration.'
    }
    $selection = if ($Limit -eq 0) { 'all eligible source records' } else { "up to $Limit eligible source records" }
    Write-Host "Starting MAP for $selection, with live extraction and summaries."
    $body = @{ limit = $Limit; source_mode = 'historical'; summary_mode = 'live' } | ConvertTo-Json -Compress
    try {
        $run = Invoke-RestMethod -Method Post -Uri $runsUrl -ContentType 'application/json' -Body $body -TimeoutSec 30
    } catch {
        $detail = if ($_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { $_.Exception.Message }
        throw "The start request did not return successfully: $detail. Check $runsUrl before retrying; a timed-out request may already have started a job."
    }
    $RunId = [string]$run.run_id
    if ($RunId -notmatch '^[a-f0-9]{32}$') {
        throw "MAP did not return a valid run ID. Check $runsUrl before retrying."
    }
}

$statusUrl = "$runsUrl/$RunId"
Write-Host "Run ID: $RunId"
Write-Host "Status: $statusUrl"
Write-Host "Stopping this monitor does not cancel the backend job. Resume with: .\Run-Pipeline.ps1 -RunId $RunId -ApiUrl $ApiUrl"
$elapsed = [Diagnostics.Stopwatch]::StartNew()
$lastProgress = ''
$readFailures = 0
try {
    while ($true) {
        try {
            $state = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 30
            $readFailures = 0
        } catch {
            $readFailures++
            if ($readFailures -ge 3) {
                throw "Could not read run status after three attempts. The backend job may still be running. Resume with: .\Run-Pipeline.ps1 -RunId $RunId -ApiUrl $ApiUrl"
            }
            Write-Warning "Status temporarily unavailable. Retrying the same run ($readFailures/3)."
            Start-Sleep -Seconds $PollSeconds
            continue
        }
        $progress = "$($state.status): $($state.stage)"
        if ($progress -ne $lastProgress) {
            Write-Host "[$($elapsed.Elapsed.ToString('hh\:mm\:ss'))] $progress"
            $lastProgress = $progress
        }
        Write-Progress -Activity "MAP pipeline $RunId" -Status "$progress | elapsed $($elapsed.Elapsed.ToString('hh\:mm\:ss'))"
        switch ($state.status) {
            'completed' {
                Write-Host "MAP pipeline completed. Processed records: $($state.records)"
                Write-Host "Results: $statusUrl/results"
                Write-Host "Dataset: $statusUrl/dataset"
                Write-Host "CSV: $statusUrl/download?kind=draft"
                Write-Host "Saved on the backend under CAPA_DATA_DIR/map-runs/$RunId."
                return
            }
            'failed' { throw "MAP run $RunId failed at '$($state.stage)'. $($state.message) Check the run's worker.log on the backend." }
            'cancelled' { throw "MAP run $RunId was cancelled." }
            'queued' { }
            'running' { }
            default { throw "MAP returned an unexpected status '$($state.status)' for run $RunId. Inspect $statusUrl." }
        }
        Start-Sleep -Seconds $PollSeconds
    }
} finally {
    Write-Progress -Activity "MAP pipeline $RunId" -Completed
}
