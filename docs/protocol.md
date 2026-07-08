# Wire Protocol v2 (Stories 1.5 / 1.6 / cube)

Hybrid binary + JSON WebSocket stream (FR20): hot entity/field data as binary frames,
cold metadata as JSON text. All binary is **little-endian**. Client decodes segments with
`buffer.slice(offset, offset + byteLength)` into typed arrays (slice avoids alignment issues).

## WebSocket `/ws`

### Server → client, on connect
1. **text** `{"t":"sync","protocol":2,"tick":int,"epoch":int,"size":int,
   "topology":"flat"|"wrap"|"cube","faces":int (1 or 6),"seaLevel":float,
   "heightScale":24,"species":[{"id":int,"name":str,"color":"#rrggbb","size":float,"plugin":str}]}`
   - `species` is re-sent as a fresh `sync` text message whenever the registry changes.
2. **binary** terrain frame (kind 1) per face — one for flat/wrap, six for a cube.

### Server → client, streaming (default 10 Hz)
- **binary** entity frame (kind 2) — every frame.
- **text** `{"t":"frame","tick":int,"epoch":int,"tps":float,"entities":int,
  "weather":{"temp":float,"precip":float,"windX":float,"windY":float},
  "clock":{"dayFrac":float,"seasonFrac":float,"seasonIndex":int}}` — every frame.
- **binary** field frame (kind 3) — every 5th frame, fields round-robin.

Server drops frames for slow clients (latest-wins), never buffers unboundedly.

### Binary frame layout
Common header: 4 × uint32 = 16 bytes: `[kind, tick, epoch, n]`.

**kind 1 — terrain** (`n` = grid side `S`): `uint32 face` (0..5), then:
- `float32 height[S*S]` (row-major `[x][y]`, values 0..1)
- `uint8 water[S*S]` (1 = open water)

**kind 2 — entities** (`n` = alive count `N`): segments in order:
- `uint32 id[N]` — generational handle (row << 16 | gen); stable per entity lifetime
- `float32 x[N]`, `float32 y[N]`, `float32 z[N]` — z is offset within stratum band
- `float32 energy[N]`
- `uint16 species[N]`
- `uint8 stratum[N]` — 0 underground, 1 surface, 2 sky
- `uint8 face[N]` — cube face 0..5 (always 0 on flat/wrap)

**kind 3 — field** (`n` = grid side `S`): `uint32 fieldId`, `uint32 face`, then:
- `uint8 values[S*S]` normalized 0..255. fieldId: 0 = flora density (0..1),
  1 = temperature ((t + 20) / 60), 2 = soil moisture (0..1).

### Cube face geometry (matches `engine/cube.py`)
Six faces, each an `S`×`S` grid. A face-local point maps to 3D on the `[-1,1]³` cube via
`corner + (x+0.5)/S · 2·r + (y+0.5)/S · 2·u`, where per face `(corner, r, u)` are:
- 0 front: `corner (-1,-1, 1)`, `r (1,0,0)`, `u (0,1,0)`
- 1 right: `corner ( 1,-1, 1)`, `r (0,0,-1)`, `u (0,1,0)`
- 2 back:  `corner ( 1,-1,-1)`, `r (-1,0,0)`, `u (0,1,0)`
- 3 left:  `corner (-1,-1,-1)`, `r (0,0,1)`, `u (0,1,0)`
- 4 top:   `corner (-1, 1, 1)`, `r (1,0,0)`, `u (0,0,-1)`
- 5 bottom:`corner (-1,-1,-1)`, `r (1,0,0)`, `u (0,0,1)`

Spherify (morph cube→ball): `x·√(1−y²/2−z²/2+y²z²/3)` and cyclic for y,z.

## REST `/api`
- `GET  /api/state` → `{"running":bool,"tick":int,"epoch":int,"tps":float,"entities":int,"targetTps":float}`
- `POST /api/control/start` · `/pause` · `/step` · `/reset`
- `POST /api/control/speed` body `{"tps": float}` (target tick rate, 1–240)

Static: server serves `web/dist` at `/` when present. Dev: Vite proxies `/api` and `/ws`
to `http://localhost:8000`.
