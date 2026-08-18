# sirius-bridge

NeoForge mod running on the **real Minecraft client**, acting as the "eyes and hands" of the Sirius AI brain: screenshot capture, input injection, and event push. See `../docs_agent/sirius-technical.md` §8.2 for the full spec.

> Current status: **M1-C perception tools implemented** - `screenshot`, `getStats`
> and `world.query` answer real requests from the live client (M1-B server +
> handshake + dispatch verified against the real `sirius-brain` Python client).
> Input injection (`input.*`), `look*`/`getGuiState` and event push land in M2+.

## Versions

| Component | Version |
|---|---|
| Minecraft | 1.21.1 |
| NeoForge | 21.1.248 (1.21.1 line = 21.1.x) |
| ModDevGradle | 2.0.141 |
| Gradle wrapper | 9.2.0 |
| Java | 21 (JDK 21 required) |
| Java-WebSocket | 1.5.7 (bundled via NeoForge jar-in-jar) |

> 2026-08-18: NeoForge dependency aligned from 21.1.233 to **21.1.248** to match the
> HMCL test client instance `1.21.1-Sirius` (`.minecraft/versions/1.21.1-Sirius`),
> whose NeoForge is 21.1.248.

## The bridge server (M1-B)

When the client reaches the title screen, the mod starts a WebSocket server:

- **Address**: `ws://127.0.0.1:8765` - bound to loopback only, never reachable
  from the network. Port is configurable (see below).
- **Token handshake**: the first frame on every connection must be
  `{"type":"hello","token":"...","protocol_version":"1.0"}`. On a matching token
  the server replies `{"type":"hello_ack","ok":true,"protocol_version":"1.0"}`.
  A wrong token, any other frame first, or 10 s of silence closes the
  connection (close code 1008). Token comparison is constant-time.
- **Token location**: `config/sirius_bridge.toml` (relative to the game
  directory). On first launch a random 64-hex-char token is generated and
  written there; it is also printed to `logs/sirius_bridge.log` (one `START`
  line per launch). Rotate it by deleting the line (or setting `token = ""`)
  and restarting the game.
- **Config**: `config/sirius_bridge.toml` with `port` (default 8765) and `token`.
- **Audit log**: `logs/sirius_bridge.log` - one line per server start/stop,
  connect/disconnect, hello success/failure and every request (with the
  resulting error code).
- **Capabilities**: `capabilities/list` returns the 12 frozen capabilities
  (name/version/input_schema) assembled from the schema JSON files copied into
  the jar at build time from `../sirius-brain/schema` (single source of truth;
  see "Schema sync" below). Protocol version: `"1.0"`.

### Config file

```toml
port = 8765
token = "<64 hex chars>"
```

### Currently implemented frames

| Frame (brain -> mod) | Behaviour |
|---|---|
| `hello` (first frame only) | token check -> `hello_ack` or close 1008 |
| `request` `capabilities/list` | capability list + `protocol_version` |
| `request` `screenshot` | see "The perception tools" below |
| `request` `getStats` | see "The perception tools" below |
| `request` `world.query` | see "The perception tools" below |
| `request` any other method | `-32601` `not implemented: <method>` (until M2) |
| `task` (NEKO) | immediate `task_finished` `status=interrupted` `text="not implemented"`, `task_id` echoed verbatim (placeholder until M2) |
| invalid JSON | `-32700` parse error |
| non-object JSON / unknown frame type / malformed request | `-32600` invalid frame |

Response frames always carry `type/id/result/error` exactly as the frozen
schema (`sirius-brain/schema/frames/`) prescribes.

## The perception tools (M1-C)

All three tools read game state on the client main (render) thread via
`ToolContext.onMainThread`; the calling WebSocket thread blocks for the result
(bounded, see below). Parameter violations of the frozen schemas answer
`-32602` with a human-readable message.

### `screenshot({ tier: "full"|"crop", bbox?, quality? })`

Reads the **main framebuffer** - which in 1.21.1 contains the world, the hand
**and the HUD/open GUI** (they are rendered into `Minecraft.getMainRenderTarget()`
every frame), so the capture is literally what the player sees, GUI included;
it also works on the title screen.

- `tier:"full"` captures everything; `tier:"crop"` additionally requires
  `bbox: [x, y, w, h]` (pixels, origin top-left; floats round, coordinates
  clamp to the image, non-intersecting boxes -> `-32602`).
- `quality`: JPEG quality 0-100, default 80.
- The pixel download happens on the render thread (one frame, ~10-30 ms);
  cropping, JPEG encoding and base64 run on the WebSocket thread so the game
  is not stalled by encoding.
- Size guard: if the base64 text exceeds ~2 MB, quality drops in steps of 10
  down to 40; if it is still over, the image scales to a 1024 px longest edge
  and the ladder repeats. Result:

```json
{"image_b64":"...","format":"jpeg","width":1920,"height":1080,
 "taken_at":1724000000000,"quality":80,"downscaled":false}
```

(`quality` echoes the quality actually used; `downscaled:true` marks the
1024 px fallback. The ~100 KB streaming pipeline from spec 8.2 belongs to the
future event-push, not to this on-demand RPC.)
- Failure mode: while the window is iconified the render loop stops draining
  the task queue, so the tool gives up after 10 s with `-32603` instead of
  hanging the connection.

### `getStats()`

Main-thread snapshot of the local player:

```json
{"in_game":true,"health":18.5,"food":17,"saturation":4.2,"air":300,
 "xp_level":27,"xp_progress":0.6,"position":{"x":1.5,"y":64.0,"z":-12.25},
 "dimension":"minecraft:overworld","game_mode":"survival",
 "effects":[{"id":"minecraft:speed","duration":1200,"amplifier":1}],"alive":true}
```

Not in a world (title screen / disconnecting) answers `{"in_game": false}` -
not an error. `game_mode` comes from the client player info with an
ability-based fallback; `effects` lists active potion effects with registry
ids (`Holder.getRegisteredName()`).

### `world.query({ type: "blocks"|"entities", range })`

`range` is a block radius (default 16, hard cap 64 -> `-32602` when over).
Not in a world -> `{"in_game": false}`.

- `type:"blocks"`: cubic scan around the player's block position; every
  **non-air** block is returned as `{x, y, z, block: "minecraft:stone"}`
  (unloaded chunks read as air and drop out). Capped at **512** entries -
  beyond that `truncated:true` and enumeration stops:

```json
{"blocks":[{"x":10,"y":64,"z":-3,"block":"minecraft:stone"}, ...],
 "count":512,"truncated":true}
```

- `type:"entities"`: everything from `ClientLevel.entitiesForRendering()`
  within `range` (3D distance, player included) as
  `{uuid, name, type, position:{x,y,z}, health?}`. `health` is attached only
  when the client knows it (`LivingEntity` with health > 0); client-side mob
  health is often unsynced, treat it as best-effort. Capped at 128 entries:

```json
{"entities":[{"uuid":"...","name":"Zombie","type":"minecraft:zombie",
              "position":{"x":5.0,"y":64.0,"z":0.5},"health":12.0}, ...],
 "count":3}
```

Known cost note: a `range:64` block scan over open air touches ~2.1M block
positions once (a few hundred ms on the main thread); dense scans hit the
512 cap long before that.

### Threading model

WebSocket callbacks run on Java-WebSocket's own threads. Parsing, validation
and dispatching happen there; any access to game state must be scheduled onto
the client main (render) thread - `ToolContext.onMainThread(Runnable)` wraps
`Minecraft.getInstance().execute(...)`. The M1-C tools block the WS thread on
a latch until the main thread ran their task (10 s timeout: the render loop
stops draining the queue while the window is iconified, so an unbounded wait
would hang the connection). Heavy post-processing (JPEG encode/base64) runs
on the WS thread; the render thread only pays for the framebuffer download.
Writing frames back to the socket is thread-safe from any thread. Handlers
are registered once at server start-up (`ToolRegistry`), so adding tools
never touches the dispatcher.

### Schema sync (single source of truth)

`gradlew build` runs the `syncToolSchemas` task, which copies
`../sirius-brain/schema/index.json` + `tools/*.json` into the jar under
`schema/`. At runtime `capabilities/list` assembles its response from those
resources - the mod never re-declares tool names or schemas by hand, and the
sirius-brain repository is only ever read, never written.

## Build

```bash
gradlew build
```

The built jar lands in `build/libs/` with `Java-WebSocket-1.5.7.jar` embedded
under `META-INF/jarjar/` (loaded by NeoForge in production). Dev runs
(`gradlew runClient`) get the library via the `clientAdditionalRuntimeClasspath`
configuration instead.

`build` also runs the **smoke test** (`gradlew smokeTest`, wired into
`check`): an in-process `main()` that exercises the pure halves of the
perception tools - parameter validation, bbox cropping, the JPEG budget
ladder, response assembly, block scan and entity filtering - with no game
launched (45 checks; a 4K incompressible-noise image verifies the degrade to
a 1024 px fallback).

## Deploy to test client

```bash
deploy.cmd
```

Runs `gradlew build` (which includes the schema sync from sirius-brain), then
copies `build/libs/sirius_bridge-*.jar` (excluding `-sources`/`-javadoc`) into
`..\.minecraft\versions\1.21.1-Sirius\mods\`, removing any older
`sirius_bridge-*.jar` there first, and prints the deployed jar name. Safe to
re-run (idempotent). The Gradle invocation inside includes local proxy flags
(`localhost:9674`) - remove them if the machine has direct internet access.

## Development

- `gradlew runClient` - launch a dev Minecraft client with the mod loaded
- `gradlew runServer` - launch a dev server (mod is client-only, does nothing there)
- `gradlew runData` - data generation

## Project layout

```
src/main/java/io/sirius/bridge/
    SiriusBridge.java     mod entry: lifecycle wiring (start on first tick, stop on shutdown)
    BridgeServer.java     WebSocket server: hello/token handshake, frame dispatch, audit
    BridgeConfig.java     config/sirius_bridge.toml (port + token, generates token on first run)
    AuditLog.java         logs/sirius_bridge.log (one line per security/protocol event)
    Capabilities.java     capability list assembled from the frozen schema resources
    ToolRegistry.java     method -> handler registry (add tools here, dispatcher untouched)
    ToolContext.java      per-call context: main-thread marshalling + thread-safe send
    PerceptionTools.java  M1-C shells: framebuffer grab, player/level reads (main thread)
    ToolContracts.java    pure: param validation + response assembly + world scan logic
    ImageOps.java         pure: crop / JPEG encode / size-budget ladder / base64
    Json.java             wire-frame builders + JSON-RPC style error codes
src/test/java/io/sirius/bridge/SmokeMain.java   in-process smoke test (gradlew smokeTest)
src/main/templates/META-INF/      neoforge.mods.toml template (properties expanded at build time)
build.gradle                      jarJar dependency + schema sync + smokeTest task
src/main/resources/assets/sirius_bridge/   Asset placeholder
```

## Next steps (per §8.2 of the technical spec)

- M2: input.* primitives (mouse/keyboard injection), look/lookAt, getGuiState
  + event subscription push (and the ~100 KB screenshot streaming pipeline)
- Permission tiers (`observe`/`input_world`/`input_gui`) and input rate
  limiting (~20/s) are deliberately left for M2 (M1 implements localhost
  binding, token handshake and the audit log).
