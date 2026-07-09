"""Report-driven strategy selection: cycles target the world's real weaknesses."""

from governor.orchestrator import _select_strategies


def test_selection_targets_overpopulation_and_low_diversity():
    report = {
        "populations": {"grazer": {"total": 800, "mean_energy": 20.0, "plugin": "grazer_herd"}},
        "shannon_diversity": 0.0,
        "flora": {"mean_density": 0.05},
        "deaths_by_cause": {"grazer": {"starvation": 100}},
        "plugins": [{"name": "grazer_herd", "status": "live"}],
    }
    picks = [s["id"] for s in _select_strategies(report, 3)]
    assert len(picks) == 3
    assert len(set(picks)) == 3               # distinct strategies
    assert "add_predator" in picks            # overpopulated + starving, no predation
    assert "balance_update" in picks          # overpopulated / starving


def test_selection_is_defensive_against_empty_report():
    picks = _select_strategies({}, 3)
    assert len(picks) == 3
    assert len({s["id"] for s in picks}) == 3  # still distinct, never crashes
