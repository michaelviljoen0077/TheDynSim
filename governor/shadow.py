"""ShadowPool: sandboxed candidate evaluation in isolated spawn processes.

Each job forks the live world from a snapshot (with its live plugin set),
optionally installs one candidate, runs N headless ticks, and reports metrics.
Budgets are enforced Windows-native (Spike C, docs/spikes.md): a parent-side
psutil watchdog polls RSS + wall-clock at 250 ms and hard-kills on breach;
per-tick time is metered in-worker. A control job (candidate=None) runs the
same world untouched — fitness is always a delta vs "do nothing" (FR14/FR15).
"""

from __future__ import annotations

import multiprocessing as mp
import time
from dataclasses import asdict, dataclass, field
from queue import Empty

import psutil

SAMPLE_EVERY = 50
WATCHDOG_POLL_S = 0.25


@dataclass
class Budgets:
    wall_s: float = 180.0
    rss_mb: float = 1024.0
    tick_ms: float = 150.0
    # a single slow tick (GC pause, page fault, scheduler hiccup — especially with
    # 4 workers sharing the CPU) must not kill a run: disqualify only on SUSTAINED
    # breach — this many consecutive over-budget ticks, or this fraction overall
    tick_breach_consecutive: int = 10
    tick_breach_fraction: float = 0.05


@dataclass
class ShadowJob:
    snapshot_path: str
    candidate_source: str | None      # None = baseline control run (single-source form)
    ticks: int = 2000
    budgets: Budgets = field(default_factory=Budgets)
    label: str = "control"
    # changeset form: several plugin sources installed together in order. When
    # set (non-empty) it supersedes candidate_source. Kept as a trailing field so
    # positional ShadowJob(path, source, ...) construction stays valid.
    candidate_sources: list[str] | None = None


@dataclass
class ShadowResult:
    label: str
    ok: bool
    reason: str = ""
    wall_s: float = 0.0
    metrics: dict = field(default_factory=dict)


def _sandbox_bootstrap() -> None:
    """Deny network access in the worker (accidental-damage threat model)."""
    import socket

    def _deny(*_a, **_k):
        raise RuntimeError("sandbox: network access is disabled in shadow workers")

    socket.socket = _deny        # type: ignore[misc, assignment]
    socket.create_connection = _deny  # type: ignore[assignment]


def _shadow_worker(job: dict, queue: mp.Queue) -> None:
    """Worker entrypoint (module-level: Windows spawn needs it picklable by ref)."""
    _sandbox_bootstrap()
    try:
        from engine import load_snapshot
        from engine.plugin_host import PluginHost, PluginInstallError

        world = load_snapshot(job["snapshot_path"])
        host = PluginHost.rebind(world)
        # changeset: install every source in order (a control run has none)
        sources = job.get("candidate_sources") or (
            [job["candidate_source"]] if job["candidate_source"] is not None else [])
        for src in sources:
            try:
                host.install(src, run_setup=True)
            except PluginInstallError as e:
                queue.put({"label": job["label"], "ok": False,
                           "reason": f"install-failed: {e.reasons}", "metrics": {}})
                return

        pops0 = _populations(world)
        samples: list[dict] = []
        tick_times: list[float] = []
        tick_budget_s = job["budgets"]["tick_ms"] / 1000.0
        max_consecutive = int(job["budgets"].get("tick_breach_consecutive", 10))
        breach_fraction = float(job["budgets"].get("tick_breach_fraction", 0.05))
        max_total = max(20, int(job["ticks"] * breach_fraction))
        breaches = consecutive = 0
        t_start = time.perf_counter()
        for i in range(job["ticks"]):
            t0 = time.perf_counter()
            world.step()
            dt = time.perf_counter() - t0
            tick_times.append(dt)
            if dt > tick_budget_s:
                breaches += 1
                consecutive += 1
            else:
                consecutive = 0
            if consecutive >= max_consecutive or breaches > max_total:
                queue.put({"label": job["label"], "ok": False,
                           "reason": f"tick-budget: {breaches} ticks over {job['budgets']['tick_ms']} ms "
                                     f"({consecutive} consecutive) by tick {i}",
                           "metrics": {"samples": samples}})
                return
            if i % SAMPLE_EVERY == 0:
                samples.append({
                    "tick": world.tick,
                    "populations": _populations(world),
                    "flora_mean": round(float(world.flora.density.mean()), 5),
                })

        plugin_errors = {name: r.error_count for name, r in host.plugins.items() if r.error_count}
        quarantined = [name for name, r in host.plugins.items() if r.status == "quarantined"]
        pops1 = _populations(world)
        arr = sorted(tick_times)
        metrics = {
            "samples": samples,
            "initial_populations": pops0,
            "final_populations": pops1,
            "extinctions": sorted(s for s, n in pops0.items() if n > 0 and pops1.get(s, 0) == 0),
            "deaths": world.deaths,
            "plugin_errors": plugin_errors,
            "quarantined": quarantined,
            "flora_mean": round(float(world.flora.density.mean()), 5),
            "p95_tick_ms": round(arr[int(len(arr) * 0.95)] * 1000, 3) if arr else 0.0,
        }
        if quarantined:
            queue.put({"label": job["label"], "ok": False,
                       "reason": f"quarantined-in-shadow: {quarantined}", "metrics": metrics})
            return
        queue.put({
            "label": job["label"], "ok": True, "reason": "",
            "wall_s": round(time.perf_counter() - t_start, 2), "metrics": metrics,
        })
    except Exception as e:  # noqa: BLE001 — worker boundary: everything becomes a result
        queue.put({"label": job["label"], "ok": False,
                   "reason": f"worker-crash: {type(e).__name__}: {e}", "metrics": {}})


def _populations(world) -> dict[str, int]:
    return {
        sp.name: int(world.store.alive_indices(sp.id).size)
        for sp in world.registry.by_id
    }


def _drain(queue: mp.Queue, timeout: float = 0.0) -> dict | None:
    """Non-blocking (or briefly blocking) read of the worker's single result.

    The queue must be drained *while the worker is alive*: a payload larger than
    the OS pipe buffer blocks the worker's feeder thread until the parent reads
    it, so the worker would never exit and would be spuriously wall-budget-killed.
    """
    try:
        return queue.get(timeout=timeout) if timeout > 0 else queue.get_nowait()
    except Empty:
        return None


def run_shadow_batch(jobs: list[ShadowJob], max_parallel: int = 4) -> list[ShadowResult]:
    """Run jobs in parallel spawn processes, each under its own watchdog."""
    results: dict[str, ShadowResult] = {}
    pending = list(jobs)
    active: list[tuple[ShadowJob, mp.Process, mp.Queue, float]] = []
    ctx = mp.get_context("spawn")

    while pending or active:
        while pending and len(active) < max_parallel:
            job = pending.pop(0)
            queue: mp.Queue = ctx.Queue()
            proc = ctx.Process(target=_shadow_worker, args=(asdict(job), queue), daemon=True)
            proc.start()
            active.append((job, proc, queue, time.perf_counter()))

        time.sleep(WATCHDOG_POLL_S)
        still_active = []
        for job, proc, queue, t0 in active:
            elapsed = time.perf_counter() - t0
            verdict: ShadowResult | None = None

            # Read the result first, while the worker is still alive — otherwise a
            # large metrics payload deadlocks the worker's feeder thread against
            # the OS pipe buffer and it gets spuriously wall-budget-killed.
            payload = _drain(queue)
            if payload is not None:
                verdict = ShadowResult(payload["label"], payload["ok"], payload["reason"],
                                       payload.get("wall_s", elapsed),
                                       payload.get("metrics", {}))
            else:
                rss_mb = 0.0
                if proc.is_alive():
                    try:
                        rss_mb = psutil.Process(proc.pid).memory_info().rss / (1024 * 1024)
                    except psutil.NoSuchProcess:
                        pass
                if proc.is_alive() and rss_mb > job.budgets.rss_mb:
                    proc.kill()
                    verdict = ShadowResult(job.label, False,
                                           f"rss-budget: {rss_mb:.0f} MB > {job.budgets.rss_mb} MB",
                                           elapsed)
                elif proc.is_alive() and elapsed > job.budgets.wall_s:
                    proc.kill()
                    verdict = ShadowResult(job.label, False,
                                           f"wall-budget: {elapsed:.1f} s > {job.budgets.wall_s} s",
                                           elapsed)
                elif not proc.is_alive():
                    # Exited without us seeing a result — give any in-flight
                    # payload a brief chance to arrive before declaring it dead.
                    payload = _drain(queue, timeout=0.2)
                    if payload is None:
                        verdict = ShadowResult(job.label, False,
                                               f"worker-died: exit code {proc.exitcode}", elapsed)
                    else:
                        verdict = ShadowResult(payload["label"], payload["ok"], payload["reason"],
                                               payload.get("wall_s", elapsed),
                                               payload.get("metrics", {}))
            if verdict is None:
                still_active.append((job, proc, queue, t0))
            else:
                proc.join(timeout=2.0)
                if proc.is_alive():
                    proc.kill()
                results[job.label] = verdict
        active = still_active

    return [results[j.label] for j in jobs]
