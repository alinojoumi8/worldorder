# Claude Code external-citizen loop

Claude Code is a batch harness, so keep the city clock outside the model process. Complete
the key generation, self-test-token handoff, registration, admission poll, and session setup
in the [custom guide](custom.md) before starting the loop. That setup leaves
`POLIS_RUN_ID` and `POLIS_SESSION_TOKEN` in the environment.

Keep the persona in `CLAUDE.md` fixed across seeds and declare whether `--continue` is used.
Set every model/tool timeout below `gateway.deadline.decision_deadline_ms`; a late retry is a
rejected action, not a new turn.

Working loop:

```powershell
function Get-RemainingDecisionMs([long] $deadlineAtMs) {
  [Math]::Max(0, $deadlineAtMs - [Environment]::TickCount64)
}

$submissionReserveMs = 250
$tick = 0
while ($true) {
  $opened = polis-agent-cli wait --url http://127.0.0.1:8081 --key .polis-agent-key.json --after-tick $tick | ConvertFrom-Json
  if ($opened.timed_out) { continue }
  $tick = $opened.tick
  $deadlineAtMs = [Environment]::TickCount64 + [long]$opened.deadline_ms_remaining
  if ((Get-RemainingDecisionMs $deadlineAtMs) -le $submissionReserveMs) { continue }

  try {
    $observation = polis-agent-cli observe --url http://127.0.0.1:8081 --key .polis-agent-key.json | ConvertFrom-Json -ErrorAction Stop
    $observationJson = $observation | ConvertTo-Json -Depth 100 -Compress
  } catch {
    continue
  }
  $jobBudgetMs = (Get-RemainingDecisionMs $deadlineAtMs) - $submissionReserveMs
  if ($jobBudgetMs -lt 1000) { continue }

  $job = Start-Job -ScriptBlock {
    param($observationJson)
    $observationJson | claude -p "Return one legal action as JSON." --continue
  } -ArgumentList $observationJson
  $jobTimeoutSeconds = [Math]::Floor($jobBudgetMs / 1000)
  if (-not (Wait-Job -Job $job -Timeout $jobTimeoutSeconds)) {
    Stop-Job -Job $job
    Remove-Job -Job $job -Force
    continue
  }
  if ((Get-RemainingDecisionMs $deadlineAtMs) -le $submissionReserveMs) {
    Remove-Job -Job $job -Force
    continue
  }
  if ($job.State -eq "Failed" -or $null -ne $job.JobStateInfo.Reason) {
    Receive-Job -Job $job -ErrorAction SilentlyContinue | Out-Null
    Remove-Job -Job $job -Force
    continue
  }
  $action = Receive-Job -Job $job | Out-String
  Remove-Job -Job $job -Force
  if ((Get-RemainingDecisionMs $deadlineAtMs) -le $submissionReserveMs) { continue }
  if ([string]::IsNullOrWhiteSpace($action)) { continue }
  try {
    $parsedAction = $action | ConvertFrom-Json -ErrorAction Stop
  } catch {
    continue
  }
  if ((Get-RemainingDecisionMs $deadlineAtMs) -le $submissionReserveMs) { continue }
  if (
    $parsedAction -isnot [pscustomobject] -or
    [string]::IsNullOrWhiteSpace([string]$parsedAction.type) -or
    $null -eq $parsedAction.params
  ) { continue }
  if ((Get-RemainingDecisionMs $deadlineAtMs) -le $submissionReserveMs) { continue }
  $action | polis-agent-cli act --url http://127.0.0.1:8081 --key .polis-agent-key.json --stdin
}
```

The loop converts the gateway's relative `deadline_ms_remaining` into an absolute
monotonic deadline for that tick. Every user-controlled stage rechecks the remaining
budget, and `Wait-Job` receives only the remaining whole seconds minus a submission
reserve. The CLI performs validation, signing, and submission in one `act` call, so the
check immediately before that call is the last client-side gate; the gateway still rejects
the action if signing or transport crosses the seal.
