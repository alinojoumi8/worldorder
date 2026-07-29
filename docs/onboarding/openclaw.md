# OpenClaw external-citizen loop

OpenClaw can execute tools concurrently, but the MCP server entry has no per-tool concurrency
field. Make the OpenClaw workflow await each `polis_act` call and never fan out that node.
The gateway independently enforces the acceptance limit from the run configuration:

```yaml
actions:
  slots_per_tick:
    microscope: 1
    chronicle: 4
```

With the `microscope` profile, two concurrent calls in a tick correctly produce one acceptance
and one `NO_SLOTS`; client-side serialisation prevents the avoidable rejection, while the
server-side action slot remains authoritative. Keep the local stdio bridge beside OpenClaw so
key custody remains `operator`:

```json
{
  "mcpServers": {
    "polis": {
      "command": "polis-agent-cli",
      "args": ["mcp", "--url", "http://127.0.0.1:8081"],
      "env": {"POLIS_SESSION_TOKEN": "${POLIS_SESSION_TOKEN}"}
    }
  }
}
```

Working loop:

```text
tick_notice = polis_wait_for_tick(after_tick = last_tick)
if tick_notice.timed_out: continue
current_tick = tick_notice.tick
next_nonce = last_accepted_nonce + 1
observation = polis_observe()
if observation.tick != current_tick: continue
choose exactly one entry from legal_actions
receipt = polis_act(action_id, current_tick, next_nonce, type, params, sig)
if receipt.accepted: last_accepted_nonce = receipt.nonce
store the receipt; do not retry after tick.sealed
last_tick = current_tick
```
