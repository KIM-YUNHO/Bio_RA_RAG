param(
    [ValidateRange(1,65535)][int]$Port = 8775,
    [ValidateSet('gpt-5.6-luna','gpt-4o-mini')][string]$Model = 'gpt-5.6-luna',
    # Release folder relative to the project root (a versions.json embedded_path).
    # Default: the version that versions.json marks as served_by_ra_service.
    [string]$Release = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $Release) {
    $registry = Get-Content -LiteralPath (Join-Path $projectRoot 'data\versions\versions.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $served = $registry.roles.served_by_ra_service
    $Release = ($registry.versions | Where-Object { $_.id -eq $served }).embedded_path
    if (-not $Release) { throw "versions.json has no embedded_path for served_by_ra_service '$served'" }
}
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$releasePath = Join-Path $projectRoot $Release
$runName = Split-Path -Leaf $releasePath
$guidelinePath = Join-Path $projectRoot 'data\guidelines\ra_guidelines_native_2026-10-12_v2'
$archivePath = Join-Path $projectRoot 'data\runs\ra_service_reports'
$logDirectory = Join-Path $projectRoot 'data\runs\ra_service_logs'
$summaryUrl = "http://127.0.0.1:$Port/api/summary"
Push-Location -LiteralPath $projectRoot
try { $expectedVersion = (& $pythonPath -X utf8 -c "from rag.ra_pipeline import VERSION; print(VERSION)").Trim() }
finally { Pop-Location }
foreach ($required in @($pythonPath,(Join-Path $releasePath 'release_manifest.json'),(Join-Path $guidelinePath 'manifest.json'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing required file: $required" }
}
$existingSummary = $null
try { $existingSummary = Invoke-RestMethod -Uri $summaryUrl -TimeoutSec 3 } catch { }
if ($existingSummary) {
    if ($existingSummary.pipeline_version -in @('ra-research-rag-4.8.3',$expectedVersion) -and
        $existingSummary.answer_model -eq $Model -and $existingSummary.run_name -eq $runName) {
        Write-Output "Already healthy: http://127.0.0.1:$Port/"
        return
    }
    throw 'A different service is running on this port. Inspect and stop that process before starting.'
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw 'This port is occupied. No existing process was stopped.'
}
New-Item -ItemType Directory -Path $archivePath,$logDirectory -Force | Out-Null
$stamp = (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$stdoutPath = Join-Path $logDirectory ($stamp+'.stdout.log')
$stderrPath = Join-Path $logDirectory ($stamp+'.stderr.log')
$arguments = @('-X','utf8','-u','-B','-m','rag_service','--mode','ra','--llm-model',$Model,
    '--run',$releasePath,'--guidelines',$guidelinePath,'--port',[string]$Port,
    '--research-rounds','1','--archive-dir',$archivePath,'--warmup',
    '--review-dir',(Join-Path $projectRoot 'data\runs\ra_review_records'))
$quotedArguments = ($arguments | ForEach-Object { '"' + $_.Replace('"','\"') + '"' }) -join ' '
$serviceProcess = Start-Process -FilePath $pythonPath -ArgumentList $quotedArguments -WorkingDirectory $projectRoot `
    -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
@{ pid=$serviceProcess.Id; port=$Port; model=$Model; stdout=$stdoutPath; stderr=$stderrPath; started_at=(Get-Date).ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDirectory ($stamp+'.process.json')) -Encoding utf8
Write-Output "Started process $($serviceProcess.Id). Logs: $stdoutPath"
for ($attempt=0; $attempt -lt 90; $attempt++) {
    $serviceProcess.Refresh()
    if ($serviceProcess.HasExited) { throw "Service exited. Inspect $stderrPath" }
    try {
        $summary = Invoke-RestMethod -Uri $summaryUrl -TimeoutSec 2
        if ($summary.pipeline_version -ne $expectedVersion -or $summary.answer_model -ne $Model) {
            throw 'Unexpected service version or model'
        }
        Write-Output "Healthy: http://127.0.0.1:$Port/"
        return
    } catch { }
    Start-Sleep -Seconds 2
}
throw "Startup health check timed out. Process was retained; inspect $stdoutPath and $stderrPath"
