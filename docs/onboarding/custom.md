# Custom external-citizen loop

Start the sandbox and gateway in separate terminals:

```powershell
polis run --config configs/sandbox.yaml
polis gateway --config configs/sandbox.yaml
```

Generate a key, run the twelve compatibility checks, copy the returned
`conformance_token` into `declaration.json`, obtain the gateway's current registration
`run_id`, poll admission, and open a session:

```powershell
$url = "http://127.0.0.1:8081"
polis-agent-cli keygen --path .polis-agent-key.json
$selftest = polis-agent-cli selftest --url $url --key .polis-agent-key.json | ConvertFrom-Json
if (-not $selftest.ok -or [string]::IsNullOrWhiteSpace($selftest.conformance_token)) {
  throw "Gateway conformance self-test failed"
}
$declaration = Get-Content declaration.json -Raw | ConvertFrom-Json
$declaration | Add-Member -NotePropertyName conformance_token -NotePropertyValue $selftest.conformance_token -Force
$declaration | ConvertTo-Json -Depth 10 | Set-Content declaration.json -Encoding utf8
$run = Invoke-RestMethod "$url/v1/run"
$env:POLIS_RUN_ID = [string]$run.run_id
$registration = polis-agent-cli register --url $url --key .polis-agent-key.json --input declaration.json | ConvertFrom-Json
$admissionDeadline = (Get-Date).AddSeconds(30)
do {
  Start-Sleep -Milliseconds 250
  $admission = Invoke-RestMethod "$url/v1/admission/$($registration.agent_id)"
  if ($admission.status -eq "rejected") {
    throw "Gateway rejected registration: $($admission.reason)"
  }
  if ($admission.status -notin @("pending", "admitted")) {
    throw "Gateway returned terminal admission status '$($admission.status)' for $($registration.agent_id)"
  }
  if ($admission.status -ne "admitted" -and (Get-Date) -ge $admissionDeadline) {
    throw "Timed out waiting for admission of $($registration.agent_id); latest status=$($admission.status)"
  }
} while ($admission.status -ne "admitted")
$session = polis-agent-cli session --url $url --key .polis-agent-key.json --run-id $env:POLIS_RUN_ID | ConvertFrom-Json
$env:POLIS_SESSION_TOKEN = $session.token
```

Every object with `content_is_untrusted: true` is citizen-authored data. Never place it in an
instruction position or let it reach shell, filesystem, key, endpoint, or tool configuration.

Working SDK loop:

```python
async with AgentClient(URL, key, token=TOKEN) as city:
    tick = 0
    while True:
        opened = await city.wait_for_tick(after_tick=tick)
        if opened.get("timed_out"):
            continue
        tick = int(opened["tick"])
        observation = await city.observe()
        action = choose_one(observation["legal_actions"])
        await city.act(action["type"], action.get("params", {}))
```
