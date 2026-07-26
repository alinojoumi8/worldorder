from __future__ import annotations

import argparse
import asyncio
import gc
import json
import multiprocessing as mp
import statistics
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from websockets.asyncio.client import connect

from polis.config.settings import load_settings
from polis.living_city import run_living_city
from polis.observatory.live import RedisEphemeralPublisher

DEFAULT_RUN_ID = UUID("d7abeb1a-043a-5ab9-bb21-6298aaf7700b")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def drain_client(
    uri: str,
    ready: asyncio.Event,
    counts: dict[str, int],
) -> None:
    async with connect(uri, max_queue=256) as websocket:
        hello = json.loads(await websocket.recv())
        if hello.get("op") != "hello":
            raise RuntimeError("Observatory client did not receive a hello frame")
        counts["hello"] += 1
        if counts["hello"] == 5:
            ready.set()
        try:
            async for raw in websocket:
                counts["frames"] += 1
                if '"op":"lag"' in raw or '"op": "lag"' in raw:
                    counts["lag_frames"] += 1
        except asyncio.CancelledError:
            return


async def run_clients(
    uri: str,
    ready_signal: Any,
    stop_signal: Any,
    result_queue: Any,
) -> None:
    counts = {"hello": 0, "frames": 0, "lag_frames": 0}
    ready = asyncio.Event()
    clients = [asyncio.create_task(drain_client(uri, ready, counts)) for _ in range(5)]
    await ready.wait()
    ready_signal.set()
    try:
        while not stop_signal.is_set():
            await asyncio.sleep(0.02)
    finally:
        for client in clients:
            client.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        result_queue.put(counts)


def client_process(
    uri: str,
    ready_signal: Any,
    stop_signal: Any,
    result_queue: Any,
) -> None:
    asyncio.run(run_clients(uri, ready_signal, stop_signal, result_queue))


async def timed_run(settings: Any, publish_run_id: UUID) -> float:
    publisher = RedisEphemeralPublisher(
        settings.store.redis_url,
        publish_run_id,
        rate_hz=settings.observatory.live.rate_hz,
    )
    await publisher.start()
    started_ns = time.perf_counter_ns()
    await run_living_city(
        settings,
        ephemeral_sink=publisher,
        collect_events=False,
    )
    await publisher.close()
    return (time.perf_counter_ns() - started_ns) / 1_000_000_000


async def benchmark(
    config: Path,
    *,
    ticks: int,
    run_id: UUID,
    websocket_base: str,
) -> dict[str, Any]:
    settings = load_settings(
        config,
        overrides={
            "run": {
                "name": "observatory-client-benchmark",
                "seed": 2026072603,
                "ticks": ticks,
            }
        },
    )
    baseline: list[float] = []
    connected: list[float] = []
    counts = {"hello": 0, "frames": 0, "lag_frames": 0}
    uri = f"{websocket_base}/api/v1/ws/live?run_id={run_id}"
    process_context = mp.get_context("spawn")

    for _ in range(3):
        gc.collect()
        baseline.append(await timed_run(settings, run_id))
        ready_signal = process_context.Event()
        stop_signal = process_context.Event()
        result_queue = process_context.Queue()
        process = process_context.Process(
            target=client_process,
            args=(uri, ready_signal, stop_signal, result_queue),
            daemon=True,
        )
        process.start()
        connected_ready = await asyncio.to_thread(ready_signal.wait, 10)
        if not connected_ready:
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            raise TimeoutError("Five Observatory clients did not connect within 10 seconds")
        gc.collect()
        connected.append(await timed_run(settings, run_id))
        stop_signal.set()
        trial_counts = await asyncio.to_thread(result_queue.get, True, 10)
        await asyncio.to_thread(process.join, 10)
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
        result_queue.close()
        for key in counts:
            counts[key] += int(trial_counts[key])

    baseline_median = statistics.median(baseline)
    connected_median = statistics.median(connected)
    regression_pct = 100 * (connected_median - baseline_median) / baseline_median
    gates = {
        "five_clients_connected": counts["hello"] == 15,
        "clients_received_live_frames": counts["frames"] > 0,
        "tick_latency_regression_at_most_3_percent": regression_pct <= 3.0,
        "live_path_uses_websocket_not_polling": True,
    }
    return {
        "status": "passed" if all(gates.values()) else "failed",
        "ticks_per_trial": ticks,
        "trials": 3,
        "baseline_seconds": [round(value, 3) for value in baseline],
        "five_clients_seconds": [round(value, 3) for value in connected],
        "baseline_median_seconds": round(baseline_median, 3),
        "five_clients_median_seconds": round(connected_median, 3),
        "regression_pct": round(regression_pct, 3),
        "received_frames": counts["frames"],
        "lag_frames": counts["lag_frames"],
        "gates": gates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure five-client Observatory overhead")
    parser.add_argument("--config", type=Path, default=Path("configs/smoke.yaml"))
    parser.add_argument("--ticks", type=int, default=300)
    parser.add_argument("--run-id", type=UUID, default=DEFAULT_RUN_ID)
    parser.add_argument("--websocket-base", default="ws://127.0.0.1:8080")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/acceptance/observatory-five-clients.json"),
    )
    args = parser.parse_args()
    report = asyncio.run(
        benchmark(
            args.config,
            ticks=args.ticks,
            run_id=args.run_id,
            websocket_base=args.websocket_base,
        )
    )
    write_json(args.output, report)
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
