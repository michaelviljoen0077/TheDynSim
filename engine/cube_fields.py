"""Per-face abiotic fields for the cube topology: six independent size x size
faces stacked as (6, size, size) arrays. The step math mirrors engine/fields.py
(kept byte-identical for flat/wrap) but vectorizes over the leading face axis.

Diffusion (blur / weather advection) is per-face — it wraps within a face rather
than flowing across face seams. Cross-face field diffusion is a later refinement;
the creatures already migrate across faces (Layer 2), which is the visible thing.
"""

from __future__ import annotations

import numpy as np

NF = 6

# Fixed sun direction for the cube's local day/night. Matches the renderer's sun
# position direction (SunLight.tsx cube branch) so the lit hemisphere is the warm
# / daytime hemisphere in the simulation too.
_SUN = np.array([1.0, 0.25, 0.35], dtype=np.float64)
_SUN /= np.linalg.norm(_SUN)


def _blur(a: np.ndarray, passes: int = 1) -> np.ndarray:
    """Edge-CLAMPED box blur over the last two axes (per-face). Clamping (not
    wrapping) matters on the cube: a wrap-blur leaks a face's opposite edge back
    in, which shows as a flora/terrain seam artifact at the face boundary."""
    out = a
    for _ in range(passes):
        p = np.pad(out, ((0, 0), (1, 1), (1, 1)), mode="edge")
        out = (p[:, 1:-1, 1:-1] + p[:, :-2, 1:-1] + p[:, 2:, 1:-1]
               + p[:, 1:-1, :-2] + p[:, 1:-1, 2:]) / 5.0
    return out


def _noise_stack(rng: np.random.Generator, size: int, octaves: int) -> np.ndarray:
    """Six independent seeded noise fields in [0,1], shape (6, size, size)."""
    acc = np.zeros((NF, size, size), dtype=np.float64)
    amp = 1.0
    for o in range(octaves):
        res = max(2, 2 ** (o + 2))
        coarse = rng.random((NF, res, res))
        reps = size // res + 1
        up = np.zeros((NF, size, size), dtype=np.float64)
        for f in range(NF):
            up[f] = np.kron(coarse[f], np.ones((reps, reps)))[:size, :size]
        acc += amp * _blur(up, passes=3)
        amp *= 0.5
    acc -= acc.min(axis=(-2, -1), keepdims=True)
    acc /= np.maximum(acc.max(axis=(-2, -1), keepdims=True), 1e-12)
    return acc.astype(np.float32)


def _cube_positions(size: int) -> np.ndarray:
    """3D cube-surface position of every face cell center, shape (6, size, size, 3).

    Uses the same face basis and cell-center sampling as engine/cube.py, so faces
    sharing an edge sample nearly-coincident 3D points there."""
    from engine.cube import FACES
    g = (np.arange(size) + 0.5) / size
    gu = g[:, None]              # varies along axis 0 (gx)
    gv = g[None, :]              # varies along axis 1 (gy)
    pos = np.zeros((NF, size, size, 3), dtype=np.float64)
    for f in range(NF):
        face = FACES[f]
        c = face.corner.astype(np.float64)
        r = face.r.astype(np.float64)
        u = face.u.astype(np.float64)
        pos[f] = (c[None, None, :]
                  + gu[..., None] * 2.0 * r[None, None, :]
                  + gv[..., None] * 2.0 * u[None, None, :])
    return pos


def _trilerp(lattice: np.ndarray, uvw: np.ndarray) -> np.ndarray:
    """Vectorized trilinear sample of a (res+1)^3 lattice at points uvw in [0,res]."""
    res = lattice.shape[0] - 1
    p = np.clip(uvw, 0.0, res - 1e-6)
    i = p.astype(np.int64)
    fr = p - i
    i0, j0, k0 = i[..., 0], i[..., 1], i[..., 2]
    i1, j1, k1 = i0 + 1, j0 + 1, k0 + 1
    fu, fv, fw = fr[..., 0], fr[..., 1], fr[..., 2]

    def L(a, b, c):  # noqa: E741 — lattice corner lookup
        return lattice[a, b, c]

    c00 = L(i0, j0, k0) * (1 - fu) + L(i1, j0, k0) * fu
    c01 = L(i0, j0, k1) * (1 - fu) + L(i1, j0, k1) * fu
    c10 = L(i0, j1, k0) * (1 - fu) + L(i1, j1, k0) * fu
    c11 = L(i0, j1, k1) * (1 - fu) + L(i1, j1, k1) * fu
    c0 = c00 * (1 - fv) + c10 * fv
    c1 = c01 * (1 - fv) + c11 * fv
    return c0 * (1 - fw) + c1 * fw


def _surface_noise_stack(rng: np.random.Generator, size: int, octaves: int) -> np.ndarray:
    """Coherent noise on the cube SURFACE: a single 3D fractal sampled at every
    face cell's 3D position. Adjacent faces share edge positions, so the field is
    continuous across seams by construction — no per-face cliffs. Shape (6,S,S)."""
    pos = _cube_positions(size)          # (6,S,S,3) in [-1,1]^3
    acc = np.zeros((NF, size, size), dtype=np.float64)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        res = 4 * (2 ** o)
        lattice = rng.random((res + 1, res + 1, res + 1))
        uvw = (pos + 1.0) * 0.5 * res     # map [-1,1] -> [0,res]
        acc += amp * _trilerp(lattice, uvw)
        total += amp
        amp *= 0.5
    acc /= total
    acc -= acc.min()
    acc /= max(float(acc.max()), 1e-12)
    return acc.astype(np.float32)


class CubeTerrain:
    FIELDS = ("height", "water_mask", "water_table", "fertility", "minerals", "aquifer")

    def __init__(self) -> None:
        for n in self.FIELDS:
            setattr(self, n, None)
        self._land_points: list[list[tuple[int, int]]] | None = None

    @classmethod
    def generate(cls, rng, size, octaves, sea_level_quantile) -> CubeTerrain:
        t = cls()
        # coherent 3D surface noise -> terrain is continuous across face seams
        t.height = _surface_noise_stack(rng, size, octaves)
        # one planet-wide sea level so oceans span faces (not a per-face quantile)
        sea = float(np.quantile(t.height, sea_level_quantile))
        t.water_mask = (t.height <= sea).astype(np.float32)
        t.water_table = np.clip(1.0 - (t.height - sea) * 2.0, 0.0, 1.0).astype(np.float32)
        t.water_table = _blur(t.water_table, passes=2).astype(np.float32)
        near_water = _blur(t.water_mask, passes=6)
        t.fertility = np.clip(
            0.45 * _noise_stack(rng, size, 3) + 0.55 * near_water * 1.5, 0.0, 1.0
        ).astype(np.float32)
        t.minerals = _noise_stack(rng, size, 4)
        t.aquifer = _blur(t.water_table, passes=4).astype(np.float32)
        return t

    def land_points(self, face: int) -> list[tuple[int, int]]:
        if self._land_points is None:
            self._land_points = [
                [(int(a), int(b)) for a, b in np.argwhere(self.water_mask[f] < 0.5).tolist()]
                for f in range(NF)
            ]
        return self._land_points[face]

    def water_points(self, face: int) -> list[tuple[int, int]]:
        if getattr(self, "_water_points", None) is None:
            self._water_points = [
                [(int(a), int(b)) for a, b in np.argwhere(self.water_mask[f] > 0.5).tolist()]
                for f in range(NF)
            ]
        return self._water_points[face]

    def to_arrays(self) -> dict:
        return {f"cterrain_{n}": getattr(self, n) for n in self.FIELDS}

    @classmethod
    def from_arrays(cls, arrays: dict) -> CubeTerrain:
        t = cls()
        for n in cls.FIELDS:
            setattr(t, n, arrays[f"cterrain_{n}"].copy())
        return t


class CubeWeather:
    FIELDS = ("temperature", "humidity", "soil_moisture", "precipitation")

    def __init__(self, size: int) -> None:
        self.size = size
        self.temperature = np.full((NF, size, size), 15.0, dtype=np.float32)
        self.humidity = np.full((NF, size, size), 0.3, dtype=np.float32)
        self.soil_moisture = np.full((NF, size, size), 0.3, dtype=np.float32)
        self.precipitation = np.zeros((NF, size, size), dtype=np.float32)
        self.wind = np.zeros((NF, 2), dtype=np.float32)
        # per-cell outward surface normals (unit), derived from geometry — used to
        # compute LOCAL day/night: a cell is in daylight when its (rotated) normal
        # faces the sun. Constant for a given size; recomputed on construction, not
        # snapshotted.
        pos = _cube_positions(size)                       # (6,S,S,3) on the cube
        nrm = pos / np.linalg.norm(pos, axis=-1, keepdims=True)
        self._nx = nrm[..., 0].astype(np.float32)
        self._ny = nrm[..., 1].astype(np.float32)
        self._nz = nrm[..., 2].astype(np.float32)
        # true latitude cooling: coldest at the poles (|normal.y| -> 1)
        self._lat_grad = (-11.0 * self._ny ** 2).astype(np.float32)

    def step(self, rng, terrain: CubeTerrain, day_frac: float, season_frac: float) -> None:
        seasonal = float(np.sin(2 * np.pi * season_frac - np.pi / 2))
        # LOCAL day/night: spin the planet by the day phase and see which cells
        # face the (fixed) sun. This is what makes the clock coherent on a globe —
        # noon where the sun is overhead, midnight on the far side, per longitude.
        theta = 2 * np.pi * day_frac
        cos_t, sin_t = np.cos(theta), np.sin(theta)
        rx = self._nx * cos_t + self._nz * sin_t
        rz = -self._nx * sin_t + self._nz * cos_t
        local_sun = rx * _SUN[0] + self._ny * _SUN[1] + rz * _SUN[2]   # (6,S,S) in [-1,1]
        self.wind += rng.normal(0.0, 0.02, (NF, 2)).astype(np.float32)
        self.wind *= 0.995
        target = (16.0 + 10.0 * seasonal + 9.0 * local_sun + self._lat_grad
                  - 10.0 * terrain.height)
        self.temperature += 0.05 * (target - self.temperature)
        warm = np.clip(self.temperature, 0.0, None) * 0.0004
        evap = warm * (terrain.water_mask + 0.4 * self.soil_moisture)
        self.humidity += evap
        # per-face advection: shift each face's humidity by its own integer wind
        for f in range(NF):
            sx = int(round(float(self.wind[f, 0])))
            sy = int(round(float(self.wind[f, 1])))
            if sx or sy:
                self.humidity[f] = np.roll(self.humidity[f], (sx, sy), (0, 1))
        self.humidity = _blur(self.humidity).astype(np.float32)
        capacity = 0.45 + np.clip(self.temperature, 0.0, None) * 0.004
        excess = np.clip(self.humidity - capacity, 0.0, None)
        self.precipitation = excess * 0.5
        self.humidity -= self.precipitation
        self.soil_moisture += self.precipitation
        self.soil_moisture *= 0.999
        self.soil_moisture += 0.001 * (terrain.aquifer - self.soil_moisture)
        np.clip(self.soil_moisture, 0.0, 1.0, out=self.soil_moisture)
        np.clip(self.humidity, 0.0, 2.0, out=self.humidity)

    def local_sun(self, face: int, ix: int, iy: int, day_frac: float) -> float:
        """Local solar illumination at one cell in [-1,1] (same model as step)."""
        s = self.size
        ix = min(max(ix, 0), s - 1)
        iy = min(max(iy, 0), s - 1)
        theta = 2 * np.pi * day_frac
        cos_t, sin_t = np.cos(theta), np.sin(theta)
        nx = float(self._nx[face, ix, iy])
        ny = float(self._ny[face, ix, iy])
        nz = float(self._nz[face, ix, iy])
        rx = nx * cos_t + nz * sin_t
        rz = -nx * sin_t + nz * cos_t
        return float(rx * _SUN[0] + ny * _SUN[1] + rz * _SUN[2])

    def to_arrays(self) -> dict:
        d = {f"cweather_{n}": getattr(self, n) for n in self.FIELDS}
        d["cweather_wind"] = self.wind
        return d

    @classmethod
    def from_arrays(cls, arrays: dict, size: int) -> CubeWeather:
        w = cls(size)
        for n in cls.FIELDS:
            setattr(w, n, arrays[f"cweather_{n}"].copy())
        w.wind = arrays["cweather_wind"].copy()
        return w


class CubeFlora:
    def __init__(self, size: int) -> None:
        self.size = size
        self.density = np.zeros((NF, size, size), dtype=np.float32)

    @classmethod
    def generate(cls, rng, size, terrain: CubeTerrain) -> CubeFlora:
        f = cls(size)
        # coherent surface noise (seamless across faces) * fertility, so the
        # initial flora pattern has no per-face seam either. Generous starting
        # cover so the world begins lush and greens the land immediately.
        seedbed = _surface_noise_stack(rng, size, 3) * terrain.fertility
        f.density = np.clip(seedbed * 1.4 * (1.0 - terrain.water_mask), 0.0, 1.0).astype(np.float32)
        return f

    def step(self, terrain: CubeTerrain, weather: CubeWeather, season_frac: float,
             dt: int = 1) -> None:
        light = 0.6 + 0.4 * float(np.sin(2 * np.pi * season_frac - np.pi / 2))
        temp_factor = np.exp(-((weather.temperature - 18.0) / 14.0) ** 2)
        moisture = np.clip(weather.soil_moisture + 0.3 * terrain.water_table, 0.0, 1.0)
        # growth & spread scale with dt (ticks since last update) so the amortized
        # regrowth rate is independent of field_step_every — throttle stays purely
        # a perf lever and never unbalances grazing
        growth = 0.06 * dt * light * temp_factor * moisture * terrain.fertility
        self.density += growth * self.density * (1.0 - self.density)
        # density-INDEPENDENT reseeding so an overgrazed cell actually recovers:
        # logistic growth alone stalls near zero (rate scales with the density that
        # was just eaten away), which left grazed land barren and starved the herd.
        self.density += 0.0015 * dt * terrain.fertility * (1.0 - self.density)
        spread = _blur(self.density) - self.density
        self.density += 0.08 * dt * np.clip(spread, 0.0, None) * terrain.fertility
        self.density = np.where(weather.temperature < 0.0, self.density * 0.995, self.density)
        self.density *= 1.0 - terrain.water_mask
        # subsistence floor on ALL land (not scaled to near-zero on poor ground), so
        # even low-fertility range feeds a grazer at a trickle instead of starving it.
        self.density = np.maximum(
            self.density, (0.02 + 0.02 * terrain.fertility) * (1.0 - terrain.water_mask)
        )
        np.clip(self.density, 0.0, 1.0, out=self.density)

    def to_arrays(self) -> dict:
        return {"cflora_density": self.density}

    @classmethod
    def from_arrays(cls, arrays: dict, size: int) -> CubeFlora:
        f = cls(size)
        f.density = arrays["cflora_density"].copy()
        return f


class CubePlankton:
    """Aquatic food field — the water-borne mirror of flora. Blooms on open water
    (never on land), so fish have a food source and the ocean is a real niche."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.density = np.zeros((NF, size, size), dtype=np.float32)

    @classmethod
    def generate(cls, rng, size, terrain: CubeTerrain) -> CubePlankton:
        p = cls(size)
        seedbed = _surface_noise_stack(rng, size, 3)
        p.density = np.clip(seedbed * 0.9 * terrain.water_mask, 0.0, 1.0).astype(np.float32)
        return p

    def step(self, terrain: CubeTerrain, weather: CubeWeather, season_frac: float,
             dt: int = 1) -> None:
        water = terrain.water_mask
        light = 0.6 + 0.4 * float(np.sin(2 * np.pi * season_frac - np.pi / 2))
        temp_factor = np.exp(-((weather.temperature - 16.0) / 16.0) ** 2)   # mild warm bloom
        growth = 0.05 * dt * light * temp_factor
        self.density += growth * self.density * (1.0 - self.density)
        spread = _blur(self.density) - self.density
        self.density += 0.06 * dt * np.clip(spread, 0.0, None)
        self.density *= water                              # only on open water
        self.density = np.maximum(self.density, 0.02 * water)   # trace re-seed for recovery
        np.clip(self.density, 0.0, 1.0, out=self.density)

    def to_arrays(self) -> dict:
        return {"cplankton_density": self.density}

    @classmethod
    def from_arrays(cls, arrays: dict, size: int) -> CubePlankton:
        p = cls(size)
        if "cplankton_density" in arrays:      # back-compat: pre-plankton snapshots
            p.density = arrays["cplankton_density"].copy()
        return p
