<#
.SYNOPSIS
  Sets up this project on a new PC: database (restore the dump), Python environment, data files, BGE-M3 model, then checks.

.DESCRIPTION
  Run from anywhere; it works in the project root. Safe to run again: a step that is already done is skipped.

  What you need next to the project (storage\HANDOFF.md):
    storage\handoff\biosim_*.dump     the database dump (or -Dump <path>)
    biosim_data.zip                   the data bundle for the web UI (or -DataZip <path>); not needed for database-only use

  Examples
    .\scripts\setup_local.ps1                          # everything
    .\scripts\setup_local.ps1 -CheckOnly               # change nothing, only report what is missing
    .\scripts\setup_local.ps1 -SkipData -SkipModel     # database only
    .\scripts\setup_local.ps1 -Start                   # setup, then start the web UI
#>
[CmdletBinding()]
param(
    [string]$Dump,
    [string]$DataZip,
    [string]$Project = 'biosim',
    [int]$DbPort = 0,
    [switch]$SkipDatabase,
    [switch]$SkipPython,
    [switch]$SkipData,
    [switch]$SkipModel,
    [switch]$ForceRestore,
    [switch]$CheckOnly,
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $root
$compose = 'ingestion/db/compose.yaml'
$python = Join-Path $root '.venv\Scripts\python.exe'
$report = New-Object System.Collections.ArrayList

function Step($text) { Write-Host ''; Write-Host "== $text" -ForegroundColor Cyan }
function Done($label, $detail) { [void]$report.Add(@('OK', $label, $detail)); Write-Host "  [OK] $label  $detail" -ForegroundColor Green }
function Todo($label, $detail) { [void]$report.Add(@('!!', $label, $detail)); Write-Host "  [!!] $label  $detail" -ForegroundColor Yellow }
function Stop-Here($text) { Write-Host "  [중단] $text" -ForegroundColor Red; exit 1 }
function Has($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
# Windows PowerShell 5.1 can turn a native program's stderr text (docker progress) into a terminating error; the exit code decides.
function Native([scriptblock]$run) { $saved = $ErrorActionPreference; $ErrorActionPreference = 'Continue'; try { & $run } finally { $ErrorActionPreference = $saved } }

function Get-EnvValue($name) {
    $file = Join-Path $root '.env'
    if (-not (Test-Path -LiteralPath $file)) { return $null }
    foreach ($line in Get-Content -LiteralPath $file -Encoding UTF8) {
        if ($line -match "^\s*$name\s*=\s*(.*?)\s*$") { return $Matches[1].Trim('"').Trim("'") }
    }
    return $null
}

# ---- 0. .env ---------------------------------------------------------------------------------------------------
Step '0. 설정 파일 (.env)'
if (-not (Test-Path -LiteralPath (Join-Path $root '.env'))) {
    if ($CheckOnly) { Todo '.env' '없음. .env.example을 복사해 만들어야 합니다.' }
    else {
        Copy-Item -LiteralPath (Join-Path $root '.env.example') -Destination (Join-Path $root '.env')
        Done '.env' '.env.example에서 만들었습니다.'
    }
}
$key = Get-EnvValue 'OPENAI_API_KEY'
if ($key) { Done 'OPENAI_API_KEY' '입력돼 있습니다(웹 화면의 질문 답변에 필요).' }
else { Todo 'OPENAI_API_KEY' '.env에 OPENAI_API_KEY=... 를 넣으세요(웹 화면의 질문 답변에만 필요, DB만 쓰면 불필요).' }
if ($DbPort -le 0) {
    $fromEnv = Get-EnvValue 'BIOSIM_DB_PORT'
    $DbPort = if ($fromEnv) { [int]$fromEnv } else { 5432 }
}
$password = Get-EnvValue 'BIOSIM_DB_PASSWORD'
if (-not $password) { $password = 'biosim_local' }
$env:BIOSIM_DB_PORT = [string]$DbPort
$env:BIOSIM_DB_PASSWORD = $password
$env:BIOSIM_DATABASE_URL = "postgresql://biosim:$password@127.0.0.1:$DbPort/biosim"

# ---- 1. database -----------------------------------------------------------------------------------------------
$container = $null
$restored = $false
if ($SkipDatabase) {
    Step '1. 데이터베이스 (건너뜀)'
}
else {
    Step "1. 데이터베이스 (PostgreSQL + pgvector, 127.0.0.1:$DbPort)"
    if (-not (Has 'docker')) { Stop-Here 'docker 명령이 없습니다. Docker Desktop을 설치하고 실행하세요.' }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Here 'Docker가 실행 중이 아닙니다. Docker Desktop을 켜고 다시 실행하세요.' }

    $container = (& docker compose -p $Project --env-file .env -f $compose ps -q db 2>$null | Select-Object -First 1)
    if (-not $container -and -not $CheckOnly) {
        $busy = Get-NetTCPConnection -LocalPort $DbPort -State Listen -ErrorAction SilentlyContinue
        if ($busy) { Stop-Here "포트 $DbPort 가 이미 사용 중입니다. -DbPort 로 다른 포트를 지정하세요(예: -DbPort 5433)." }
        Write-Host '  DB 컨테이너를 만듭니다...'
        Native { docker compose -p $Project --env-file .env -f $compose up -d }
        if ($LASTEXITCODE -ne 0) { Stop-Here 'docker compose up 이 실패했습니다.' }
        $container = (& docker compose -p $Project --env-file .env -f $compose ps -q db | Select-Object -First 1)
    }
    elseif ($container -and -not $CheckOnly) {
        Native { docker compose -p $Project --env-file .env -f $compose up -d } | Out-Null
    }
    if (-not $container) {
        Todo 'DB 컨테이너' '없음. -CheckOnly 없이 실행하면 만듭니다.'
    }
    else {
        $health = ''
        for ($i = 0; $i -lt 60; $i++) {
            $health = (& docker inspect -f '{{.State.Health.Status}}' $container).Trim()
            if ($health -eq 'healthy') { break }
            Start-Sleep -Seconds 2
        }
        if ($health -ne 'healthy') { Stop-Here "DB가 준비되지 않았습니다(상태: $health). docker logs $container 를 확인하세요." }
        Done 'DB 컨테이너' "준비됨 ($($container.Substring(0,12)))"

        $countSql = "select case when to_regclass('rag.releases') is null then 0 else (xpath('/row/c/text()', query_to_xml('select count(*) c from rag.releases', false, true, '')))[1]::text::int end"
        $count = [int]((& docker exec $container psql -U biosim -d biosim -Atc $countSql).Trim())
        if ($count -gt 0 -and -not $ForceRestore) {
            Done 'DB 데이터' "이미 있습니다(rag.releases $count 개). 복원을 건너뜁니다."
            $restored = $true
        }
        elseif ($CheckOnly) {
            Todo 'DB 데이터' '비어 있습니다. 덤프를 복원해야 합니다.'
        }
        else {
            if (-not $Dump) {
                $found = Get-ChildItem -LiteralPath (Join-Path $root 'storage\handoff') -Filter '*.dump' -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime -Descending | Select-Object -First 1
                if ($found) { $Dump = $found.FullName }
            }
            if (-not $Dump -or -not (Test-Path -LiteralPath $Dump)) {
                Stop-Here '덤프 파일이 없습니다. storage\handoff\ 에 biosim_*.dump 를 두거나 -Dump <경로> 를 지정하세요.'
            }
            $shaFile = "$Dump.sha256"
            if (Test-Path -LiteralPath $shaFile) {
                $want = ((Get-Content -LiteralPath $shaFile -Raw).Trim() -split '\s+')[0].ToLower()
                $have = (Get-FileHash -LiteralPath $Dump -Algorithm SHA256).Hash.ToLower()
                if ($want -ne $have) { Stop-Here "덤프 해시가 다릅니다. 파일이 손상됐거나 다른 버전입니다. (기대 $want / 실제 $have)" }
                Done '덤프 해시' '일치'
            }
            else { Todo '덤프 해시' ".sha256 파일이 없어 확인하지 못했습니다: $Dump" }
            Write-Host '  복원 중입니다(인덱스를 다시 만드느라 수 분 걸립니다)...'
            Native { docker cp $Dump "${container}:/var/tmp/biosim.dump" }
            if ($LASTEXITCODE -ne 0) { Stop-Here '덤프를 컨테이너로 복사하지 못했습니다.' }
            if ($ForceRestore -and $count -gt 0) {
                Native { docker exec $container pg_restore -U biosim -d biosim --no-owner --clean --if-exists --exit-on-error /var/tmp/biosim.dump }
            }
            else {
                Native { docker exec $container pg_restore -U biosim -d biosim --no-owner --exit-on-error /var/tmp/biosim.dump }
            }
            $code = $LASTEXITCODE
            & docker exec $container rm -f /var/tmp/biosim.dump | Out-Null
            if ($code -ne 0) { Stop-Here 'pg_restore가 실패했습니다. 위의 오류 메시지를 확인하세요.' }
            $count = [int]((& docker exec $container psql -U biosim -d biosim -Atc $countSql).Trim())
            if ($count -lt 1) { Stop-Here '복원 후에도 rag.releases가 비어 있습니다.' }
            Done 'DB 복원' "완료(rag.releases $count 개)"
            $restored = $true
        }
    }
}

# ---- 2. python environment -------------------------------------------------------------------------------------
if ($SkipPython) {
    Step '2. Python 환경 (건너뜀)'
}
else {
    Step '2. Python 환경 (uv sync)'
    if (-not (Has 'uv')) { Stop-Here 'uv 가 없습니다. https://docs.astral.sh/uv/ 에서 설치하세요.' }
    if ((Test-Path -LiteralPath $python) -or $CheckOnly) {
        if (Test-Path -LiteralPath $python) { Done 'Python 환경' '.venv 가 있습니다.' }
        else { Todo 'Python 환경' '.venv 가 없습니다. uv sync 가 필요합니다.' }
    }
    if (-not $CheckOnly) {
        Native { uv sync }
        if ($LASTEXITCODE -ne 0) { Stop-Here 'uv sync 가 실패했습니다.' }
        Done 'Python 환경' 'uv sync 완료'
    }
}

# ---- 3. data bundle --------------------------------------------------------------------------------------------
$dataOk = $false
if ($SkipData) {
    Step '3. 데이터 묶음 (건너뜀)'
}
else {
    Step '3. 데이터 묶음 (원본 PDF, 전처리 결과: 웹 화면용)'
    if (-not (Test-Path -LiteralPath $python)) {
        Todo '데이터 점검' 'Python 환경이 없어 점검하지 못했습니다.'
    }
    else {
        & $python -X utf8 -m storage.serving_data --check *> $null
        if ($LASTEXITCODE -eq 0) { $dataOk = $true; Done '데이터' '필요한 파일이 모두 있습니다.' }
        elseif ($CheckOnly) { Todo '데이터' '빠진 파일이 있습니다. biosim_data.zip 을 프로젝트 루트에 풀어야 합니다.' }
        else {
            if (-not $DataZip) { $DataZip = Join-Path $root 'biosim_data.zip' }
            if (-not (Test-Path -LiteralPath $DataZip)) {
                Todo '데이터' "빠진 파일이 있고 묶음 파일이 없습니다. biosim_data.zip 을 루트에 두거나 -DataZip <경로> 를 지정하세요. (DB만 쓰면 무시해도 됩니다)"
            }
            else {
                Write-Host "  압축을 풉니다: $DataZip"
                if (Has 'tar') { & tar -xf $DataZip -C $root }
                else { Expand-Archive -LiteralPath $DataZip -DestinationPath $root -Force }
                if ($LASTEXITCODE -ne 0) { Stop-Here '압축을 풀지 못했습니다.' }
                & $python -X utf8 -m storage.serving_data --check
                if ($LASTEXITCODE -eq 0) { $dataOk = $true; Done '데이터' '풀었고, 필요한 파일이 모두 있습니다.' }
                else { Todo '데이터' '풀었지만 빠진 파일이 남아 있습니다(위 목록). 묶음 파일이 맞는지 확인하세요.' }
            }
        }
    }
}

# ---- 4. BGE-M3 model -------------------------------------------------------------------------------------------
if ($SkipModel) {
    Step '4. BGE-M3 모델 (건너뜀)'
}
else {
    Step '4. BGE-M3 모델 (질문 임베딩용, 서비스는 오프라인으로만 읽습니다)'
    $hub = if ($env:HF_HUB_CACHE) { $env:HF_HUB_CACHE } elseif ($env:HF_HOME) { Join-Path $env:HF_HOME 'hub' } else { Join-Path $HOME '.cache\huggingface\hub' }
    $snapshots = Join-Path $hub 'models--BAAI--bge-m3\snapshots'
    $present = $false
    if (Test-Path -LiteralPath $snapshots) {
        $present = [bool](Get-ChildItem -LiteralPath $snapshots -Recurse -Include 'pytorch_model.bin', 'model.safetensors' -ErrorAction SilentlyContinue | Select-Object -First 1)
    }
    if ($present) { Done 'BGE-M3' "있습니다($hub)" }
    elseif ($CheckOnly) { Todo 'BGE-M3' '없습니다(약 2.3GB를 내려받아야 합니다).' }
    elseif (-not (Test-Path -LiteralPath $python)) { Todo 'BGE-M3' 'Python 환경이 없어 내려받지 못했습니다.' }
    else {
        Write-Host '  내려받습니다(약 2.3GB, 시간이 걸립니다)...'
        Remove-Item Env:HF_HUB_OFFLINE -ErrorAction SilentlyContinue
        & $python -X utf8 -c "from sentence_transformers import SentenceTransformer as S; S('BAAI/bge-m3'); print('ok')"
        if ($LASTEXITCODE -eq 0) { Done 'BGE-M3' '내려받았습니다.' } else { Todo 'BGE-M3' '내려받기가 실패했습니다. 네트워크를 확인하세요.' }
    }
}

# ---- 5. consistency check --------------------------------------------------------------------------------------
Step '5. DB와 파일이 같은지 확인'
if ($container -and $restored -and $dataOk -and (Test-Path -LiteralPath $python)) {
    & $python -X utf8 -m storage.rag_loader verify --queries 20
    if ($LASTEXITCODE -eq 0) { Done '일치 검사' '청크·벡터가 파일과 같고 정확 검색이 numpy와 같습니다.' }
    else { Todo '일치 검사' '실패했습니다. 위 메시지를 확인하세요(덤프와 데이터 묶음의 날짜가 같은지).' }
}
else {
    Todo '일치 검사' '건너뜀(DB 복원과 데이터가 모두 있을 때만 실행합니다).'
}

# ---- summary ---------------------------------------------------------------------------------------------------
Write-Host ''
Write-Host '================ 요약 ================' -ForegroundColor Cyan
foreach ($row in $report) {
    $color = if ($row[0] -eq 'OK') { 'Green' } else { 'Yellow' }
    Write-Host ("[{0}] {1}  {2}" -f $row[0], $row[1], $row[2]) -ForegroundColor $color
}
$open = @($report | Where-Object { $_[0] -eq '!!' }).Count
Write-Host ''
if ($open -eq 0) {
    Write-Host '준비가 끝났습니다. 웹 화면 실행:' -ForegroundColor Green
}
else {
    Write-Host "확인할 항목이 $open 개 있습니다. 위 [!!] 를 처리한 뒤 다시 실행하세요(이미 끝난 단계는 건너뜁니다)." -ForegroundColor Yellow
    Write-Host '준비되면 웹 화면 실행:'
}
Write-Host '  $env:BIOSIM_SEARCH_BACKEND = ''postgres''    # 청크·벡터를 DB에서 읽기'
Write-Host '  .\scripts\start_ra_service.ps1               # http://127.0.0.1:8775/'
if ($DbPort -ne 5432) {
    Write-Host "  (DB 포트가 $DbPort 입니다. 서비스가 같은 DB를 보도록 .env 의 BIOSIM_DB_PORT 와 BIOSIM_DATABASE_URL 의 포트도 $DbPort 로 바꾸세요.)"
}

if ($Start) {
    if ($open -gt 0) { Stop-Here '확인할 항목이 남아 있어 서비스를 시작하지 않습니다.' }
    $env:BIOSIM_SEARCH_BACKEND = 'postgres'
    & (Join-Path $PSScriptRoot 'start_ra_service.ps1')
}
