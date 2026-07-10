"""Vectorized field systems: terrain (static), weather and flora (dynamic).

Everything is seeded from the world RNG at construction (fixed draw order) and
stepped with whole-array NumPy ops — the abiotic base layer is engine-native
(FR3); fauna and higher flora behaviour comes from plugins.
"""

from __future__ import annotations

import numpy as np


def _blur(a: np.ndarray, passes: int = 1) -> np.ndarray:
    """Cheap separable box blur with edge-clamped rolls (deterministic)."""
    out = a
    for _ in range(passes):
        out = (
            out
            + np.roll(out, 1, 0) + np.roll(out, -1, 0)
            + np.roll(out, 1, 1) + np.roll(out, -1, 1)
        ) / 5.0
    return out


def fractal_noise(rng: np.random.Generator, size: int, octaves: int) -> np.ndarray:
    """Seeded value noise in [0, 1]: coarse random grids upsampled + blurred, summed."""
    acc = np.zeros((size, size), dtype=np.float64)
    amp = 1.0
    for o in range(octaves):
        res = max(2, 2 ** (o + 2))
        coarse = rng.random((res, res))
        reps = size // res + 1
        up = np.kron(coarse, np.ones((reps, reps)))[:size, :size]
        acc += amp * _blur(up, passes=3)
        amp *= 0.5
    acc -= acc.min()
    acc /= max(acc.max(), 1e-12)
    return acc.astype(np.float32)


class Terrain:
    """Static after generation: heightmap, water, fertility, underground fields."""

    FIELDS = ("height", "water_mask", "water_table", "fertility", "minerals", "aquifer")

    def __init__(self) -> None:
        self.height: np.ndarray
        self.water_mask: np.ndarray
        self.water_table: np.ndarray
        self.fertility: np.ndarray
        self.minerals: np.ndarray
        self.aquifer: np.ndarray
        self._land_points: list[tuple[int, int]] | None = None

    @property
    def land_points(self) -> list[tuple[int, int]]:
        """Grid coords of non-water columns (derived, cached — not snapshotted)."""
        if self._land_points is None:
            self._land_points = [
                (int(a), int(b)) for a, b in np.argwhere(self.water_mask < 0.5).tolist()
            ]
        return self._land_points

    @property
    def water_points(self) -> list[tuple[int, int]]:
        """Grid coords of open-water columns (for aquatic spawns)."""
        if getattr(self, "_water_points", None) is None:
            self._water_points = [
                (int(a), int(b)) for a, b in np.argwhere(self.water_mask > 0.5).tolist()
            ]
        return self._water_points

    @classmethod
    def generate(cls, rng: np.random.Generator, size: int, octaves: int,
                 sea_level_quantile: float) -> Terrain:
        t = cls()
        t.height = fractal_noise(rng, size, octaves)
        sea = float(np.quantile(t.height, sea_level_quantile))
        t.water_mask = (t.height <= sea).astype(np.float32)
        # water table: high under water bodies, decays with height above sea level
        t.water_table = np.clip(1.0 - (t.height - sea) * 2.0, 0.0, 1.0).astype(np.float32)
        t.water_table = _blur(t.water_table, passes=2).astype(np.float32)
        near_water = _blur(t.water_mask, passes=6)
        t.fertility = np.clip(
            0.45 * fractal_noise(rng, size, 3) + 0.55 * near_water * 1.5, 0.0, 1.0
        ).astype(np.float32)
        t.minerals = fractal_noise(rng, size, 4)
        t.aquifer = _blur(t.water_table, passes=4).astype(np.float32)
        return t

    def to_arrays(self) -> dict[str, np.ndarray]:
        return {f"terrain_{n}": getattr(self, n) for n in self.FIELDS}

    @classmethod
    def from_arrays(cls, arrays: dict[str, np.ndarray]) -> Terrain:
        t = cls()
        for n in cls.FIELDS:
            setattr(t, n, arrays[f"terrain_{n}"].copy())
        return t


class Weather:
    """Temperature / humidity / soil moisture / precipitation + wind, evolving per tick."""

    FIELDS = ("temperature", "humidity", "soil_moisture", "precipitation")

    def __init__(self, size: int) -> None:
        self.size = size
        self.temperature = np.full((size, size), 15.0, dtype=np.float32)
        self.humidity = np.full((size, size), 0.3, dtype=np.float32)
        self.soil_moisture = np.full((size, size), 0.3, dtype=np.float32)
        self.precipitation = np.zeros((size, size), dtype=np.float32)
        self.wind = np.zeros(2, dtype=np.float32)
        lat = np.linspace(-1.0, 1.0, size, dtype=np.float32)
        self._lat_grad = np.tile(lat[:, None], (1, size)) * -6.0  # cooler toward +y "pole"

    def step(self, rng: np.random.Generator, terrain: Terrain,
             day_frac: float, season_frac: float) -> None:
        diurnal = float(np.sin(2 * np.pi * day_frac - np.pi / 2))          # -1 night .. +1 midday
        seasonal = float(np.sin(2 * np.pi * season_frac - np.pi / 2))      # -1 winter .. +1 summer
        # wind drifts slowly and deterministically (one rng draw per tick)
        self.wind += rng.normal(0.0, 0.02, 2).astype(np.float32)
        self.wind *= 0.995

        target = (
            16.0 + 10.0 * seasonal + 7.0 * diurnal
            + self._lat_grad
            - 10.0 * terrain.height
        )
        self.temperature += 0.05 * (target - self.temperature)

        # evaporation from open water + wet soil, stronger when warm
        warm = np.clip(self.temperature, 0.0, None) * 0.0004
        evap = warm * (terrain.water_mask + 0.4 * self.soil_moisture)
        self.humidity += evap
        # advect humidity along wind (integer shift of accumulated wind), then diffuse
        sx = int(round(float(self.wind[0])))
        sy = int(round(float(self.wind[1])))
        if sx or sy:
            self.humidity = np.roll(self.humidity, (sx, sy), (0, 1))
        self.humidity = _blur(self.humidity).astype(np.float32)

        # precipitation where humidity exceeds a temperature-dependent capacity
        capacity = 0.45 + np.clip(self.temperature, 0.0, None) * 0.004
        excess = np.clip(self.humidity - capacity, 0.0, None)
        self.precipitation = excess * 0.5
        self.humidity -= self.precipitation
        self.soil_moisture += self.precipitation
        # soil drains toward the aquifer and dries in heat
        self.soil_moisture *= 0.999
        self.soil_moisture += 0.001 * (terrain.aquifer - self.soil_moisture)
        np.clip(self.soil_moisture, 0.0, 1.0, out=self.soil_moisture)
        np.clip(self.humidity, 0.0, 2.0, out=self.humidity)

    def to_arrays(self) -> dict[str, np.ndarray]:
        d = {f"weather_{n}": getattr(self, n) for n in self.FIELDS}
        d["weather_wind"] = self.wind
        return d

    @classmethod
    def from_arrays(cls, arrays: dict[str, np.ndarray], size: int) -> Weather:
        w = cls(size)
        for n in cls.FIELDS:
            setattr(w, n, arrays[f"weather_{n}"].copy())
        w.wind = arrays["weather_wind"].copy()
        return w


class Flora:
    """Base grass-equivalent density field: grows with moisture/light/warmth, spreads, dies back."""

    def __init__(self, size: int) -> None:
        self.density = np.zeros((size, size), dtype=np.float32)

    @classmethod
    def generate(cls, rng: np.random.Generator, size: int, terrain: Terrain) -> Flora:
        f = cls(size)
        seedbed = fractal_noise(rng, size, 3) * terrain.fertility
        f.density = np.clip(seedbed * 1.4 * (1.0 - terrain.water_mask), 0.0, 1.0).astype(np.float32)
        return f

    def step(self, terrain: Terrain, weather: Weather, season_frac: float,
             dt: int = 1) -> None:
        light = 0.6 + 0.4 * float(np.sin(2 * np.pi * season_frac - np.pi / 2))
        temp_factor = np.exp(-((weather.temperature - 18.0) / 14.0) ** 2)
        moisture = np.clip(weather.soil_moisture + 0.3 * terrain.water_table, 0.0, 1.0)
        # dt (ticks since last field update) scales growth so regrowth is
        # throttle-independent; dt=1 on flat/wrap keeps this byte-identical
        growth = 0.06 * dt * light * temp_factor * moisture * terrain.fertility
        self.density += growth * self.density * (1.0 - self.density)
        # density-INDEPENDENT reseeding so an overgrazed cell actually recovers
        # (logistic growth alone stalls near zero and starves the herd)
        self.density += 0.0015 * dt * terrain.fertility * (1.0 - self.density)
        # spread into fertile neighbours
        spread = _blur(self.density) - self.density
        self.density += 0.08 * dt * np.clip(spread, 0.0, None) * terrain.fertility
        # cold dieback + nothing grows on open water
        self.density = np.where(weather.temperature < 0.0, self.density * 0.995, self.density)
        self.density *= 1.0 - terrain.water_mask
        # subsistence floor on ALL land so even poor range feeds a grazer at a trickle
        self.density = np.maximum(
            self.density, (0.02 + 0.02 * terrain.fertility) * (1.0 - terrain.water_mask)
        )
        np.clip(self.density, 0.0, 1.0, out=self.density)

    def to_arrays(self) -> dict[str, np.ndarray]:
        return {"flora_density": self.density}

    @classmethod
    def from_arrays(cls, arrays: dict[str, np.ndarray], size: int) -> Flora:
        f = cls(size)
        f.density = arrays["flora_density"].copy()
        return f


class Plankton:
    """Aquatic food field — the water-borne mirror of flora. Blooms on open water
    (never on land), giving fish a food source and making the ocean a real niche."""

    def __init__(self, size: int) -> None:
        self.density = np.zeros((size, size), dtype=np.float32)

    @classmethod
    def generate(cls, rng: np.random.Generator, size: int, terrain: Terrain) -> Plankton:
        p = cls(size)
        seedbed = fractal_noise(rng, size, 3)
        p.density = np.clip(seedbed * 0.9 * terrain.water_mask, 0.0, 1.0).astype(np.float32)
        return p

    def step(self, terrain: Terrain, weather: Weather, season_frac: float,
             dt: int = 1) -> None:
        water = terrain.water_mask
        light = 0.6 + 0.4 * float(np.sin(2 * np.pi * season_frac - np.pi / 2))
        temp_factor = np.exp(-((weather.temperature - 16.0) / 16.0) ** 2)
        growth = 0.05 * dt * light * temp_factor
        self.density += growth * self.density * (1.0 - self.density)
        spread = _blur(self.density) - self.density
        self.density += 0.06 * dt * np.clip(spread, 0.0, None)
        self.density *= water
        self.density = np.maximum(self.density, 0.02 * water)
        np.clip(self.density, 0.0, 1.0, out=self.density)

    def to_arrays(self) -> dict[str, np.ndarray]:
        return {"plankton_density": self.density}

    @classmethod
    def from_arrays(cls, arrays: dict[str, np.ndarray], size: int) -> Plankton:
        p = cls(size)
        if "plankton_density" in arrays:       # back-compat: pre-plankton snapshots
            p.density = arrays["plankton_density"].copy()
        return p
