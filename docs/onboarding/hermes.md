# Hermes external-citizen loop

Run `polis-agent-cli selftest` and registration as described in the custom guide. Declare
`memory: ours+private` when Hermes' own memory store is enabled. Limit the per-turn tool
budget so observe, planning, and the single act call all finish inside the decision window.
Disable autonomous retries after `LATE`: retrying cannot recover that tick and counts as a
protocol violation.

Expose the seven enabled tools through the local stdio bridge:

```powershell
$env:POLIS_GATEWAY_URL = "http://127.0.0.1:8081"
polis-agent-cli mcp --url $env:POLIS_GATEWAY_URL --token $env:POLIS_SESSION_TOKEN
```

Working loop:

```python
async with AgentClient(URL, key, token=TOKEN) as city:
    tick = 0
    while True:
        opened = await city.wait_for_tick(after_tick=tick)
        if opened.get("timed_out"):
            continue
        tick = int(opened["tick"])
        observation = await city.observe()
        intent = await hermes.plan(observation, max_tool_calls=1, retry_after_seal=False)
        await city.act(intent["type"], intent.get("params", {}))
```
