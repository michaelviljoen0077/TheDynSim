"""Energy conservation: consumption (attack / eat_flora) distributes only the
resource that actually exists, so N consumers on one resource can never mint
energy from tick-start estimates."""

from engine import World, WorldConfig
from engine.entities import SURFACE
from engine.world_api import WorldAPI


def test_many_predators_on_one_prey_cannot_mint_energy():
    w = World(WorldConfig(seed=1, size=64, initial_capacity=1024))
    pred = WorldAPI(w, "pack", 0, ["wolf"])
    pred.register_species("wolf")
    prey_api = WorldAPI(w, "herd", 1, ["deer"])
    prey_api.register_species("deer")
    wid = w.registry.by_name["wolf"].id
    did = w.registry.by_name["deer"].id
    # one prey with 10 energy, 100 predators each trying to drain 60
    prey = w.store.spawn(did, 30.0, 30.0, 0.0, SURFACE, 10.0)
    for _ in range(100):
        w.store.spawn(wid, 30.0, 30.0, 0.0, SURFACE, 0.0)
    w.spatial.rebuild(w.store)
    pred.on_tick_begin()
    wolves = w.store.alive_indices(wid)
    prey_e0 = float(w.store.energy[prey >> 16])
    for h in w.store.handles_of(wolves):
        pred.attack(h, prey, 60.0, efficiency=0.8)
    w.commands.apply(w.store, float(w.config.size))
    prey_row = prey >> 16
    prey_lost = prey_e0 - float(w.store.energy[prey_row])
    gained = float(w.store.energy[wolves].sum())
    assert float(w.store.energy[prey_row]) == 0.0            # prey fully drained, not negative
    assert prey_lost <= prey_e0 + 1e-6                        # never lost more than it had
    # predators collectively gained at most prey_lost * efficiency (0.8), NOT 100x
    assert gained <= prey_lost * 0.8 + 1e-4
    assert abs(gained - prey_lost * 0.8) < 1e-3               # exactly the conserved share
