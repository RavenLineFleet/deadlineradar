# Registers the daily scheduled task for the rolling re-verification.
# Run once, by hand, ONLY AFTER AuditLab PASS (Orchestrator 2026-10-02: review before live schedule).
#   powershell -ExecutionPolicy Bypass -File scripts\reverify\install_tasks.ps1
# job.py resets its own worktree (C:\Users\Devin\AssetLab\dr_reverify) to origin/main every run.
$py  = (Get-Command python).Source
$job = "C:\Users\Devin\AssetLab\b3_saas\deadlineradar\scripts\reverify\job.py"
if (-not (Test-Path $job)) { throw "job.py not found at $job (merge the reverify branch first)" }
schtasks /Create /F /TN "DeadlineRadar\ReverifyDaily" /SC DAILY /ST 02:00 /TR "`"$py`" `"$job`" daily"
schtasks /Query /TN "DeadlineRadar\ReverifyDaily"
