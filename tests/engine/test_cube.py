"""Cube geometry: the edge-folding must be exactly right or the sim silently
corrupts. These are the ground-truth round-trip tests."""

import numpy as np
import pytest

from engine.cube import FACES, N_FACES, CubeGeometry, _face_of, _to_cube

S = 64
G = CubeGeometry(S)


def walk(face, x, y, dx, dy, steps):
    for _ in range(steps):
        face, x, y = G.step(face, x, y, dx, dy)
    return face, x, y


def test_faces_have_outward_normals_and_orthonormal_basis():
    for i, f in enumerate(FACES):
        assert abs(np.dot(f.r, f.u)) == 0, f"face {i} basis not orthogonal"
        # corner + r-edge + u-edge points are all cube vertices (|coord|==1)
        assert np.all(np.abs(f.corner) == 1)
        assert _face_of(f.normal.astype(float) * 0.999) == i  # normal identifies the face


def test_no_crossing_is_identity():
    f, x, y = G.step(0, 30.0, 30.0, 2.0, -3.0)
    assert f == 0 and x == 32.0 and y == 27.0


def test_equator_walk_returns_to_start():
    """Going east around the 4-face band (4*size) returns to the same place."""
    face, x, y = 0, 0.5, 20.0
    face, x, y = walk(face, x, y, 1.0, 0.0, 4 * S)
    assert face == 0
    assert x == pytest.approx(0.5, abs=1e-3)
    assert y == pytest.approx(20.0, abs=1e-3)


def test_equator_crosses_all_band_faces_in_order():
    face, x, y = 0, S - 1.0, 20.0
    seen = [0]
    for _ in range(4):
        face, x, y = walk(face, x, y, 1.0, 0.0, S)
        seen.append(face)
    # visits the band faces and returns to 0
    assert seen[-1] == 0
    assert set(seen[:4]) == {0, 1, 2, 3}


def test_walk_is_3d_continuous_no_teleports():
    """The real correctness invariant: along any walk, each local step moves the
    entity a proportional distance in 3D — folding across edges never teleports.
    Corners (8 points, 3 faces meeting) allow a slightly larger but still bounded
    nudge. A rotation bug in the fold table would show up here as a big jump."""
    def near_corner(x, y):
        m = 3.0
        return (x < m or x > S - m) and (y < m or y > S - m)

    rng = np.random.default_rng(3)
    cell = 2.0 / S  # 3D size of one grid cell
    face, x, y = 0, 32.0, 32.0
    for _ in range(8000):
        dx, dy = float(rng.uniform(-3, 3)), float(rng.uniform(-3, 3))
        p0 = np.array(G.to_3d(face, x, y))
        corner = near_corner(x, y)
        face, x, y = G.step(face, x, y, dx, dy)
        p1 = np.array(G.to_3d(face, x, y))
        moved = float(np.linalg.norm(p1 - p0))
        step_mag = (dx * dx + dy * dy) ** 0.5 * cell
        budget = (step_mag + 3 * cell) if not corner else (step_mag + 12 * cell)
        assert moved <= budget, f"teleport: moved {moved:.3f} 3D for a {step_mag:.3f} step"


def test_pole_crossing_reaches_top_and_bottom():
    # north off a band face reaches the top; south reaches the bottom
    assert G.step(0, 30.0, S - 0.3, 0.0, 1.0)[0] == 4
    assert G.step(0, 30.0, 0.3, 0.0, -1.0)[0] == 5
    assert G.step(2, 30.0, S - 0.3, 0.0, 1.0)[0] == 4
    assert G.step(2, 30.0, 0.3, 0.0, -1.0)[0] == 5


def test_crossing_north_edge_lands_on_top():
    face, x, y = G.step(0, 30.0, S - 0.5, 0.0, 1.0)
    assert face == 4  # top


def test_positions_always_in_bounds():
    rng = np.random.default_rng(0)
    face, x, y = 0, 30.0, 30.0
    for _ in range(20000):
        dx, dy = rng.uniform(-4, 4), rng.uniform(-4, 4)
        face, x, y = G.step(face, x, y, dx, dy)
        assert 0 <= face < N_FACES
        assert 0.0 <= x < S and 0.0 <= y < S, f"out of bounds: {face},{x},{y}"


def test_continuity_across_edge_small_step():
    """A tiny step across an edge lands a tiny distance away in 3D (no teleport)."""
    face0, x0, y0 = 0, S - 0.2, 30.0
    before = np.array(G.to_3d(face0, x0, y0))
    face1, x1, y1 = G.step(face0, x0, y0, 0.4, 0.0)  # step across the +x edge
    after = np.array(G.to_3d(face1, x1, y1))
    assert face1 != face0
    assert np.linalg.norm(after - before) < 0.1  # continuous, not a jump


def test_to_3d_on_cube_surface():
    for face in range(N_FACES):
        c = np.array(G.to_3d(face, 32.0, 32.0))
        assert max(abs(c)) == pytest.approx(1.0, abs=1e-6)  # on the unit cube


def test_spherify_maps_to_unit_sphere():
    for face in range(N_FACES):
        c = np.array(G.to_3d(face, 10.0, 50.0, spherify=1.0))
        assert np.linalg.norm(c) == pytest.approx(1.0, abs=1e-6)


def test_to_cube_corner_mapping():
    # local (0,0) sits at the face corner; (near-1,near-1) at the opposite vertex
    for face in range(N_FACES):
        assert np.allclose(_to_cube(face, 0.0, 0.0), FACES[face].corner)
