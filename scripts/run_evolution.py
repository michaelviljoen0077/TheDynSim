"""Run real evolution cycles against live Ollama — the whole pipeline, for real.

Usage: python scripts/run_evolution.py [cycles=1] [db=data/evolution_demo.db]
"""

import json
import logging
import sys
from pathlib import Path

from engine import WorldConfig
from governor.llm import OllamaProvider
from governor.notebook import Notebook
from governor.orchestrator import GovernorConfig, Orchestrator
from governor.shadow import Budgets
from server.runner import EngineRunner

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

PLUGINS = Path(__file__).resolve().parent.parent / "plugins_examples"


def main() -> None:
    n_cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    db_path = sys.argv[2] if len(sys.argv) > 2 else "data/evolution_demo.db"

    provider = OllamaProvider()
    if not provider.available():
        print("Ollama unreachable or model missing — aborting")
        sys.exit(1)

    settle = 800
    live_between = 3000  # let each decision actually play out before the next cycle
    sources = [(PLUGINS / n).read_text() for n in ("grazer.py", "predator.py", "birds.py")]
    runner = EngineRunner(
        WorldConfig(seed=1337, size=256, topology="cube",
                    initial_capacity=32768, field_step_every=6),
        plugin_sources=sources,
    )
    print(f"settling the base ecosystem ({settle} ticks)...")
    runner.world.run(settle)

    notebook = Notebook(db_path)
    notebook.reset_all()
    notebook.start_run(1337, runner.config.to_json(), notes="long evolution test")
    orch = Orchestrator(
        runner, notebook, provider,
        GovernorConfig(n_candidates=3, shadow_ticks=1200,
                       promotion_threshold=0.3,
                       budgets=Budgets(wall_s=150.0, rss_mb=1200.0, tick_ms=250.0)),
    )

    from engine.reporter import build_report

    def snapshot_pops():
        r = build_report(runner.world)
        return {n: p["total"] for n, p in r["populations"].items() if p["total"] or p["plugin"]}

    for k in range(n_cycles):
        print(f"\n{'=' * 70}\nCYCLE {k + 1}/{n_cycles}  (tick {runner.world.tick})  pops={snapshot_pops()}")
        decision = orch.run_cycle()
        cycle = notebook.cycles(limit=1)[0]
        print(f"decision: {decision}  tokens {cycle['tokens_in']}/{cycle['tokens_out']}")
        for cand in notebook.candidates_for(cycle["id"]):
            meta = cand["meta"]
            line = f"  [{cand['label']}] {cand['fate']} fit={cand['fitness']:.2f}"
            if cand["fate"] == "rejected_validation":
                line += f"  reasons={[v['code'] for v in cand['validation'].get('errors', [])]}"
            elif cand["fate"] == "rejected_shadow":
                line += f"  ({cand['shadow_metrics'].get('reason', '')[:70]})"
            print(line)
            print(f"      species={meta.get('species')}  hyp: {meta.get('hypothesis', '')[:110]}")
        # measure the PREVIOUS promotion's real outcome (governor does this next cycle)
        outc = notebook.db.execute(
            "SELECT plugin_name, verdict FROM outcomes ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if outc:
            print(f"  last-promotion outcome: {outc['plugin_name']} -> {outc['verdict']}")
        if runner.world.extinct:
            print(f"  EXTINCT so far: {[e['species'] for e in runner.world.extinct]}")
        print(f"  letting it live {live_between} ticks...")
        runner.world.run(live_between)

    print(f"\n{'=' * 70}\nFINAL @ tick {runner.world.tick}")
    report = build_report(runner.world)
    for name, p in report["populations"].items():
        print(f"  {name:16s} {p['total']:5d}  (plugin {p['plugin']}, status live)")
    print(f"shannon diversity: {report['shannon_diversity']}")
    print(f"extinct: {[e['species'] for e in runner.world.extinct]}")
    print(f"live plugins: {[m['name'] for m in runner.world.plugin_manifest if m['status'] == 'live']}")
    print(f"deaths: {json.dumps(report['deaths_by_cause'])}")


if __name__ == "__main__":
    main()
