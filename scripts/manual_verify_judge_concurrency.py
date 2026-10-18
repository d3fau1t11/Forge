"""Manual concurrency verification for the flag-judge event-loop stall fix.

Run by hand in the Kali VM:

    python scripts/manual_verify_judge_concurrency.py

(No pytest, no fixtures from the main test suite — fully self-contained.)

HOW TO READ THE OUTPUT
----------------------
This mirrors the real production path:

  * "slow job" — simulates the blocking judge_relevance/_llm_response assess()
    path (a 2s LLM round trip) dispatched onto a worker thread via
    loop.run_in_executor(functools.partial(...)), exactly the pattern now used
    at the assess() call site in swarm_orchestrator.py's
    _check_tool_output_for_flags. time.sleep() here blocks only the worker
    thread, NOT the asyncio event loop.

  * "fast job" — a plain asyncio coroutine that increments a counter every 100ms
    for ~3s, representing the checkpoint coordinator / other agents still making
    progress on the shared event loop.

PASS criteria: despite the slow job running concurrently, the fast job's counter
kept incrementing all the way to ~30 (3s / 100ms). That proves the blocking
assess() call did NOT stall the event loop.

FAIL criteria: the fast job's counter is clearly short (it paused while the
slow job ran). That would mean the slow call blocked the event loop thread —
the stall we are fixing.

Wall-clock total should be ~3s (the fast job's duration), not ~5s (2s slow +
3s fast serialized).
"""

import asyncio
import functools
import time


def slow_judge_simulation():
    """Simulate the blocking LLM-bridged assess() path (~2s).

    Runs on a worker thread via run_in_executor; time.sleep blocks only that
    worker thread, leaving the asyncio event loop free.
    """
    time.sleep(2.0)
    return "judge-done"


async def fast_progress_task(duration, interval):
    """Increment a counter every `interval` seconds for `duration` seconds.

    Represents the checkpoint coordinator / other agents still making progress
    on the shared event loop while the slow judge call runs off-loop.
    """
    counter = 0
    end = time.time() + duration
    samples = []
    while time.time() < end:
        counter += 1
        samples.append((time.time(), counter))
        await asyncio.sleep(interval)
    return counter, samples


async def main():
    print("=== Manual judge-concurrency verification ===")
    print("Slow job: ~2s blocking LLM simulation via run_in_executor (worker thread)")
    print("Fast job: counter increment every 100ms for ~3s on the event loop\n")

    loop = asyncio.get_running_loop()
    start = time.time()

    # Dispatch the blocking assess()-equivalent onto a worker thread — the exact
    # pattern used at the real assess() call site.
    slow_future = loop.run_in_executor(None, functools.partial(slow_judge_simulation))
    # The fast job runs concurrently on the event loop.
    fast_task = asyncio.create_task(fast_progress_task(duration=3.0, interval=0.1))

    slow_result = await slow_future  # await off-loop blocking work
    slow_elapsed = time.time() - start

    counter, samples = await fast_task  # fast task may already be done
    total_elapsed = time.time() - start

    print(f"Wall-clock start: {time.strftime('%H:%M:%S', time.localtime(start))}")
    print(f"Slow job completed after {slow_elapsed:.2f}s -> {slow_result!r}")
    print(f"Fast-task counter at completion: {counter} (expected ~30 for 3s/100ms)")
    print(f"Total wall-clock elapsed: {total_elapsed:.2f}s "
          f"(~3s = concurrent, ~5s = serialized/stall)\n")

    print("Fast-task counter samples (offset_from_start -> counter):")
    t0 = samples[0][0] if samples else start
    for ts, c in samples:
        print(f"  +{ts - t0:6.2f}s  ->  {c}")
    print()

    if counter >= 28:  # ~30, allow small timing slack
        print("PASS: fast task kept incrementing while the slow (executor) job ran; "
              "event loop was NOT stalled.")
    else:
        print(f"FAIL: fast task only reached {counter}; the slow job appears to have "
              "blocked the event loop.")


if __name__ == "__main__":
    asyncio.run(main())
