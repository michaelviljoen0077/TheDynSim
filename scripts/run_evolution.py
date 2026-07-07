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

    sources = [(PLUGINS / n).read_text() for n in ("grazer.py", "predator.py", "birds.py")]
    runner = EngineRunner(WorldConfig(seed=1337, size=192, initial_capacity=8192),
                          plugin_sources=sources)
    print("settling the base ecosystem (400 ticks)...")
    runner.world.run(400)

    notebook = Notebook(db_path)
    notebook.start_run(1337, runner.config.to_json(), notes="live evolution demo")
    orch = Orchestrator(
        runner, notebook, provider,
        GovernorConfig(n_candidates=2, shadow_ticks=900,
                       promotion_threshold=0.3,
                       budgets=Budgets(wall_s=120.0, rss_mb=1024.0, tick_ms=250.0)),
    )

    for k in range(n_cycles):
        print(f"\n=== evolution cycle {k + 1}/{n_cycles} (provider {provider.name}) ===")
        decision = orch.run_cycle()
        cycle = notebook.cycles(limit=1)[0]
        print(f"decision: {decision}   tokens in/out: {cycle['tokens_in']}/{cycle['tokens_out']}")
        for cand in notebook.candidates_for(cycle["id"]):
            meta = cand["meta"]
            print(f"\n  [{cand['label']}] fate={cand['fate']} fitness={cand['fitness']:.3f}")
            print(f"    hypothesis: {meta.get('hypothesis', '')[:160]}")
            if cand["fitness_breakdown"]:
                print(f"    breakdown: {json.dumps(cand['fitness_breakdown'].get('breakdown', {}))}")
            if cand["fate"] == "rejected_validation":
                print(f"    reasons: {[v['code'] for v in cand['validation'].get('errors', [])]}")
            if cand["fate"] == "rejected_shadow":
                print(f"    reason: {cand['shadow_metrics'].get('reason', '')}")
            if cand["fate"] == "promoted":
                print("    --- promoted plugin source ---")
                print("    " + "\n    ".join(cand["source"].splitlines()[:40]))
        if decision == "promoted":
            print("\nletting the promotion live for 300 ticks...")
            runner.world.run(300)

    print("\nfinal populations:")
    from engine.reporter import build_report
    report = build_report(runner.world)
    for name, p in report["populations"].items():
        print(f"  {name:14s} {p['total']:5d}  (plugin {p['plugin']})")
    print(f"shannon diversity: {report['shannon_diversity']}")
    print(f"deaths: {json.dumps(report['deaths_by_cause'])}")


if __name__ == "__main__":
    main()
