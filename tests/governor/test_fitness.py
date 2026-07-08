"""Story 3.5: fitness is a pure, unit-testable function over metrics."""

from governor.fitness import FitnessScore, score_candidate, shannon, source_novelty


def metrics(final_pops, samples=None, extinctions=(), deaths=None, errors=None):
    return {
        "final_populations": final_pops,
        "samples": samples or [],
        "extinctions": list(extinctions),
        "deaths": deaths or {},
        "plugin_errors": errors or {},
    }


def flat_samples(pops, n=6, flora=0.1):
    return [{"tick": i * 50, "populations": dict(pops), "flora_mean": flora} for i in range(n)]


CONTROL = metrics(
    {"grazer": 200, "wolf": 10},
    samples=flat_samples({"grazer": 200, "wolf": 10}),
    deaths={"grazer": {"predation": 50, "starvation": 50}},
)


def test_added_species_raises_diversity_score():
    cand = metrics(
        {"grazer": 200, "wolf": 10, "beetle": 60},
        samples=flat_samples({"grazer": 200, "wolf": 10, "beetle": 60}),
        deaths={"grazer": {"predation": 50, "starvation": 50}},
    )
    score = score_candidate(cand, CONTROL, candidate_species=["beetle"])
    assert score.breakdown["diversity"] > 0
    assert score.total > 0


def test_own_extinction_is_heavily_penalized():
    cand = metrics(
        {"grazer": 200, "wolf": 10, "beetle": 0},
        samples=flat_samples({"grazer": 200, "wolf": 10, "beetle": 0}),
        extinctions=(),
    )
    score = score_candidate(cand, CONTROL, candidate_species=["beetle"])
    assert score.breakdown["own_survival"] == -2.0


def test_caused_extinction_penalty():
    cand = metrics({"wolf": 30}, extinctions=["grazer"],
                   samples=flat_samples({"wolf": 30}))
    score = score_candidate(cand, CONTROL, candidate_species=["x"])
    assert score.breakdown["extinctions"] < 0
    assert score.total < 0


def test_volatility_hurts_stability():
    wild = [{"tick": i * 50, "populations": {"grazer": 200 + (i % 2) * 300, "wolf": 10},
             "flora_mean": 0.1} for i in range(6)]
    cand = metrics({"grazer": 200, "wolf": 10}, samples=wild)
    score = score_candidate(cand, CONTROL)
    assert score.breakdown["stability"] < 0


def test_plugin_errors_penalized():
    cand = metrics({"grazer": 200, "wolf": 10},
                   samples=flat_samples({"grazer": 200, "wolf": 10}), errors={"x": 4})
    score = score_candidate(cand, CONTROL)
    assert score.breakdown["errors"] == -2.0


def test_novelty_of_copy_is_zero():
    src = "def setup(world):\n    world.spawn('a', 1, 1)\n"
    assert source_novelty(src, [src]) < 0.05
    assert source_novelty(src, ["def on_tick(world):\n    x = 42\n"]) > 0.5


def test_shannon():
    assert shannon({"a": 100}) == 0.0
    assert shannon({"a": 50, "b": 50}) > shannon({"a": 90, "b": 10})


def test_identical_to_control_scores_near_zero():
    score = score_candidate(CONTROL, CONTROL)
    assert isinstance(score, FitnessScore)
    assert abs(score.breakdown["diversity"]) < 1e-9
    assert abs(score.breakdown["stability"]) < 1e-9


def test_prey_crash_penalises_exterminators():
    ctrl = metrics({"grazer": 200, "wolf": 10},
                   samples=flat_samples({"grazer": 200, "wolf": 10}))
    # a new predator that crashes grazers to 20% of control (collapse in progress)
    cand = metrics({"grazer": 40, "wolf": 10, "hawk": 15},
                   samples=flat_samples({"grazer": 40, "wolf": 10, "hawk": 15}))
    score = score_candidate(cand, ctrl, candidate_species=["hawk"])
    assert score.breakdown.get("prey_crash", 0.0) < 0
    # a benign addition that leaves prey intact gets no crash penalty
    benign = metrics({"grazer": 195, "wolf": 10, "beetle": 40},
                     samples=flat_samples({"grazer": 195, "wolf": 10, "beetle": 40}))
    assert score_candidate(benign, ctrl, candidate_species=["beetle"]).breakdown.get("prey_crash", 0.0) == 0
