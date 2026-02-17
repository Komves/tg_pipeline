# ============================================
# WATCHDOG: TG INGESTION (Windows local)
# Uses short paths for config, but kills by suffix match to avoid кириллица/8.3 mismatch
# ============================================

$RAW_DIR    = "C:\Users\E6DA~1\tg_pipeline\tg_pipeline\data\tg\raw\MIX"
$STATE_FILE = "C:\Users\E6DA~1\tg_pipeline\tg_pipeline\out\logs\tg_state.json"

$PYTHON_EXE = "C:\Users\E6DA~1\tg_pipeline\tg_pipeline\.venv\Scripts\python.exe"
$INGEST_PY  = "C:\Users\E6DA~1\tg_pipeline\tg_pipeline\tg_ingest.py"

$LOG_FILE   = "C:\Users\E6DA~1\tg_pipeline\tg_pipeline\out\logs\watchdog_ingest.log"

$STALE_MINUTES   = 10
$CHECK_EVERY_SEC = 20

New-Item -ItemType Directory -Force -Path "C:\Users\E6DA~1\tg_pipeline\tg_pipeline\out\logs" | Out-Null

function Log([string]$msg) {
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg" | Tee-Object -FilePath $LOG_FILE -Append
}

Log "WATCHDOG STARTED (stale>=${STALE_MINUTES}min, check=${CHECK_EVERY_SEC}s)"
Log "RAW_DIR=$RAW_DIR"
Log "STATE_FILE=$STATE_FILE"
Log "INGEST=$PYTHON_EXE $INGEST_PY"

function Kill-Ingest {
    # Kill ONLY python that is this project's venv python.
    # Do NOT rely on shortpath equality; match by stable suffix.
    $needle = "\tg_pipeline\tg_pipeline\.venv\Scripts\python.exe"

    $procs = Get-Process python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.ToLower().EndsWith($needle.ToLower()) }

    if (-not $procs) {
        Log "KILL: no matching python process (Path endswith $needle)"
        return
    }

    foreach ($p in $procs) {
        Log "KILL python PID=$($p.Id) PATH=$($p.Path)"
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    }
}

function Start-Ingest {
    Log "START ingestion"
    Start-Process -FilePath $PYTHON_EXE -ArgumentList "`"$INGEST_PY`"" -WorkingDirectory "C:\Users\E6DA~1\tg_pipeline\tg_pipeline"
}

while ($true) {
    try {
        $now = Get-Date

        Get-ChildItem $RAW_DIR -Recurse -File -Filter "*.part" -ErrorAction SilentlyContinue |
            ForEach-Object {
                $ageMin = ($now - $_.LastWriteTime).TotalMinutes
                if ($ageMin -ge $STALE_MINUTES) {
                    Log "STALE .part age=$([math]::Round($ageMin,1))min => $($_.FullName)"

                    Kill-Ingest
                    Start-Sleep -Seconds 2

                    if (Test-Path $_.FullName) {
                        Log "DELETE .part => $($_.FullName)"
                        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
                    }

                    Start-Sleep -Seconds 1
                    Start-Ingest
                    break
                }
            }
    }
    catch {
        Log "ERROR: $_"
    }

    Start-Sleep -Seconds $CHECK_EVERY_SEC
}

