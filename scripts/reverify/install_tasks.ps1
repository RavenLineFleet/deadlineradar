# Registers the two scheduled tasks for the monthly re-verification.
# Run once, by hand, ONLY AFTER AuditLab PASS (Orchestrator 2026-10-02: review before live schedule).
#   powershell -ExecutionPolicy Bypass -File scripts\reverify\install_tasks.ps1
# schtasks.exe is used because PowerShell 5.1's New-ScheduledTaskTrigger has no monthly trigger.
# The job script lives in the main checkout's copy until the branch is merged; job.py itself always
# resets its own worktree (C:\Users\Devin\AssetLab\dr_reverify) to origin/main before running.
$py  = (Get-Command python).Source
$job = "C:\Users\Devin\AssetLab\b3_saas\deadlineradar\scripts\reverify\job.py"
if (-not (Test-Path $job)) { throw "job.py not found at $job (merge the reverify branch first)" }
schtasks /Create /F /TN "DeadlineRadar\ReverifyMonthly" /SC MONTHLY /D 1 /ST 02:00 /TR "`"$py`" `"$job`" monthly"
schtasks /Create /F /TN "DeadlineRadar\ReverifyRetry"   /SC DAILY       /ST 02:30 /TR "`"$py`" `"$job`" retry"
schtasks /Query /TN "DeadlineRadar\ReverifyMonthly"
schtasks /Query /TN "DeadlineRadar\ReverifyRetry"
