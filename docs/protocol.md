# Wire Protocol v1 (Stories 1.5 / 1.6)

Hybrid binary + JSON WebSocket stream (FR20): hot entity/field data as binary frames,
cold metadata as JSON text. All binary is **little-endian**. Client decodes segments with
`buffer.slice(offset, offset + byteLength)` into typed arrays (slice avoids alignment issues).

## WebSocket `/ws`

### Server → client, on connect
1. **text** `{"t":"sync","protocol":1,"tick":int,"epoch":int,"size":int,"seaLevel":float,
   "heightScale":24,"species":[{"id":int,"name":str,"color":"#rrggbb","size":float,"plugin":str}]}`
   - `species` is re-sent as a fresh `sync` text message whenever the registry changes.
2. **binary** terrain frame (kind 1), once.

### Server → client, streaming (default 10 Hz)
- **binary** entity frame (kind 2) — every frame.
- **text** `{"t":"frame","tick":int,"epoch":int,"tps":float,"entities":int,
  "weather":{"temp":float,"precip":float,"windX":float,"windY":float},
  "clock":{"dayFrac":float,"seasonFrac":float,"seasonIndex":int}}` — every frame.
- **binary** field frame (kind 3) — every 5th frame, fields round-robin.

Server drops frames for slow clients (latest-wins), never buffers unboundedly.

### Binary frame layout
Common header: 4 × uint32 = 16 bytes: `[kind, tick, epoch, n]`.

**kind 1 — terrain** (`n` = grid side `S`): segments in order:
- `float32 height[S*S]` (row-major `[x][y]`, values 0..1)
- `uint8 water[S*S]` (1 = open water)

**kind 2 — entities** (`n` = alive count `N`): segments in order:
- `uint32 id[N]` — generational handle (row << 16 | gen); stable per entity lifetime
- `float32 x[N]`, `float32 y[N]`, `float32 z[N]` — z is offset within stratum band
- `float32 energy[N]`
- `uint16 species[N]`
- `uint8 stratum[N]` — 0 underground, 1 surface, 2 sky

**kind 3 — field** (`n` = grid side `S`): one extra `uint32 fieldId` after the header, then:
- `uint8 values[S*S]` normalized 0..255. fieldId: 0 = flora density (0..1),
  1 = temperature ((t + 20) / 60), 2 = soil moisture (0..1).

## REST `/api`
- `GET  /api/state` → `{"running":bool,"tick":int,"epoch":int,"tps":float,"entities":int,"targetTps":float}`
- `POST /api/control/start` · `/pause` · `/step` · `/reset`
- `POST /api/control/speed` body `{"tps": float}` (target tick rate, 1–240)

Static: server serves `web/dist` at `/` when present. Dev: Vite proxies `/api` and `/ws`
to `http://localhost:8000`.
