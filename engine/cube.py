"""Cube topology geometry — the six-face coordinate system for a folded world.

A cube-sphere: six square faces (each a `size`x`size` grid, same as the flat
world) folded into a closed surface. An entity has (face, x, y) with x,y in
[0, size). Moving off a face's edge folds onto the neighbouring face with the
correct rotation — derived from face basis vectors, not hand-coded rotation
rules, so there are no per-edge transcription bugs.

Faces (by outward normal), each defined by a `corner` (the cube vertex where
local (0,0) sits) and unit axis directions `r` (local +x) and `u` (local +y),
in cube coordinates [-1, 1]^3:

    0 +Z front   1 +X right   2 -Z back   3 -X left     <- equatorial band
    4 +Y top     5 -Y bottom                            <- poles

The band 0->1->2->3 wraps east; +y on any band face borders the top, -y the
bottom. `step()` (called only for the rare entity that crosses an edge in a
tick) folds the overshoot about the exited edge using that edge's direction as
the neighbour face's normal. `to_3d()` maps a surface point to 3D for rendering.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# axis unit vectors
_X = np.array([1, 0, 0], dtype=np.int64)
_Y = np.array([0, 1, 0], dtype=np.int64)
_Z = np.array([0, 0, 1], dtype=np.int64)

FACE_FRONT, FACE_RIGHT, FACE_BACK, FACE_LEFT, FACE_TOP, FACE_BOTTOM = range(6)
N_FACES = 6


@dataclass(frozen=True)
class Face:
    corner: np.ndarray  # cube vertex at local (0,0)
    r: np.ndarray       # local +x axis (unit)
    u: np.ndarray       # local +y axis (unit)

    @property
    def normal(self) -> np.ndarray:
        return np.cross(self.r, self.u)


# corner is the vertex at local (0,0); r/u are the two edge directions.
FACES: tuple[Face, ...] = (
    Face(np.array([-1, -1, 1]), _X, _Y),    # 0 front  (+Z)
    Face(np.array([1, -1, 1]), -_Z, _Y),    # 1 right  (+X)
    Face(np.array([1, -1, -1]), -_X, _Y),   # 2 back   (-Z)
    Face(np.array([-1, -1, -1]), _Z, _Y),   # 3 left   (-X)
    Face(np.array([-1, 1, 1]), _X, -_Z),    # 4 top    (+Y)
    Face(np.array([-1, -1, -1]), _X, _Z),   # 5 bottom (-Y)
)


def _to_cube(face: int, fu: float, fv: float) -> np.ndarray:
    """Face-local normalized coords (fu, fv in [0,1]) -> 3D point on the cube."""
    f = FACES[face]
    return f.corner.astype(np.float64) + fu * 2.0 * f.r + fv * 2.0 * f.u


def _from_cube(face: int, c: np.ndarray) -> tuple[float, float]:
    """3D cube point on `face` -> face-local normalized (fu, fv)."""
    f = FACES[face]
    rel = c - f.corner
    return float(np.dot(rel, f.r)) / 2.0, float(np.dot(rel, f.u)) / 2.0


def _face_of(c: np.ndarray) -> int:
    """Which face a 3D point (on/near the cube surface) belongs to: max |axis|."""
    a = np.abs(c)
    axis = int(np.argmax(a))
    positive = c[axis] > 0
    return {
        (0, True): FACE_RIGHT, (0, False): FACE_LEFT,     # X
        (1, True): FACE_TOP, (1, False): FACE_BOTTOM,     # Y
        (2, True): FACE_FRONT, (2, False): FACE_BACK,     # Z
    }[(axis, positive)]


class CubeGeometry:
    def __init__(self, size: int) -> None:
        self.size = size

    # -- movement --------------------------------------------------------------

    def step(self, face: int, x: float, y: float,
             dx: float, dy: float) -> tuple[int, float, float]:
        """Advance (face,x,y) by local (dx,dy), folding across at most one edge.

        Returns (new_face, new_x, new_y) with new_x,new_y in [0, size). Moves are
        small (<= a few cells), so an entity crosses at most one edge per call;
        a corner (two edges at once) is resolved by folding the dominant axis
        first, which lands on a valid adjacent face.
        """
        s = self.size
        nx, ny = x + dx, y + dy
        if 0.0 <= nx < s and 0.0 <= ny < s:
            return face, nx, ny
        # fold: extend into the exited edge and refold onto the neighbour.
        # normalized coords may be <0 or >=1 on one (or both) axes.
        fu, fv = nx / s, ny / s
        # pick the axis with the larger overshoot to fold first
        over_u = max(0.0, -fu, fu - 1.0 + 1e-9)
        over_v = max(0.0, -fv, fv - 1.0 + 1e-9)
        if over_u >= over_v:
            new_face, nfu, nfv = self._fold_axis(face, fu, fv, axis="u")
        else:
            new_face, nfu, nfv = self._fold_axis(face, fu, fv, axis="v")
        # clamp any residual tiny overshoot on the other axis (corner case)
        nfu = min(max(nfu, 0.0), 1.0 - 1e-9)
        nfv = min(max(nfv, 0.0), 1.0 - 1e-9)
        return new_face, nfu * s, nfv * s

    def _fold_axis(self, face: int, fu: float, fv: float,
                   axis: str) -> tuple[int, float, float]:
        f = FACES[face]
        if axis == "u":
            exited = f.r if fu >= 1.0 else -f.r
            over = (fu - 1.0) if fu >= 1.0 else (-fu)      # >= 0
            edge_fu = 1.0 if fu >= 1.0 else 0.0
            edge3d = _to_cube(face, edge_fu, min(max(fv, 0.0), 1.0))
        else:
            exited = f.u if fv >= 1.0 else -f.u
            over = (fv - 1.0) if fv >= 1.0 else (-fv)
            edge_fv = 1.0 if fv >= 1.0 else 0.0
            edge3d = _to_cube(face, min(max(fu, 0.0), 1.0), edge_fv)
        # the neighbour face's outward normal is the direction we exited
        neighbour = _face_of(exited.astype(np.float64))
        # fold the overshoot down the origin face's inward normal onto the neighbour
        folded = edge3d + over * 2.0 * (-f.normal.astype(np.float64))
        nfu, nfv = _from_cube(neighbour, folded)
        return neighbour, nfu, nfv

    # -- rendering / distance --------------------------------------------------

    def to_3d(self, face: int, x: float, y: float, radius: float = 1.0,
              spherify: float = 0.0) -> tuple[float, float, float]:
        """Surface point -> 3D. spherify in [0,1] morphs the cube toward a sphere."""
        s = self.size
        c = _to_cube(face, (x + 0.5) / s, (y + 0.5) / s)
        if spherify > 0.0:
            sph = _cube_to_sphere(c)
            c = (1.0 - spherify) * c + spherify * sph
        return float(c[0] * radius), float(c[1] * radius), float(c[2] * radius)


def _cube_to_sphere(c: np.ndarray) -> np.ndarray:
    """Map a point on the [-1,1]^3 cube surface onto the unit sphere (equal-ish area)."""
    x, y, z = c
    x2, y2, z2 = x * x, y * y, z * z
    return np.array([
        x * np.sqrt(1 - y2 / 2 - z2 / 2 + y2 * z2 / 3),
        y * np.sqrt(1 - z2 / 2 - x2 / 2 + z2 * x2 / 3),
        z * np.sqrt(1 - x2 / 2 - y2 / 2 + x2 * y2 / 3),
    ])
