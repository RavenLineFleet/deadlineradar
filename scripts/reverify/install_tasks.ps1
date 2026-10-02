# Registers the daily scheduled task for the rolling re-verification.
# Run once, by hand, after AuditLab PASS (given 2026-10-02 15:00; Orchestrator GO 13:08).
#   powershell -ExecutionPolicy Bypass -File scripts\reverify\install_tasks.ps1
# The task runs job.py from the job's OWN worktree (C:\Users\Devin\AssetLab\dr_reverify), which job.py
# force-resets to origin/main at the start of every run, so the scheduled code is always the reviewed
# main version and AssetLab's working checkout is never touched.
$py   = (Get-Command python).Source
$repo = "C:\Users\Devin\AssetLab\b3_saas\deadlineradar"
$wt   = "C:\Users\Devin\AssetLab\dr_reverify"
if (-not (Test-Path "$wt\.git")) {
    git -C $repo fetch origin main
    git -C $repo worktree add --detach $wt origin/main
}
$job = "$wt\scripts\reverify\job.py"
if (-not (Test-Path $job)) { throw "job.py not found at $job (is reverify merged into main?)" }
schtasks /Create /F /TN "DeadlineRadar\ReverifyDaily" /SC DAILY /ST 02:00 /TR "`"$py`" `"$job`" daily"
schtasks /Query /TN "DeadlineRadar\ReverifyDaily" /V /FO LIST | Select-String "TaskName|Next Run Time|Task To Run|Status"
