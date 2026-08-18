# sirius-bridge

NeoForge mod running on the **real Minecraft client**, acting as the "eyes and hands" of the Sirius AI brain: screenshot capture, input injection, and event push. See `../docs_agent/sirius-technical.md` §8.2 for the full spec.

> Current status: **M2-D look + permission tiers implemented** - `look` /
> `lookAt` rotate the player's view absolutely, and a `permission` config
> tier gates every acting tool (`observe` / `input_gui` / `input_world` /
> `full`). Together with the M2-A input primitives (`input.*`), the M1-C
> perception tools (`screenshot`, `getStats`, `world.query`), the M2-B event
> push channel and the M2-C GUI state the bridge has "eyes", "hands", a
> passive attention channel, GUI comprehension and a gaze. `events.watch`
> lands in M3+.

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
- **Config**: `config/sirius_bridge.toml` with `port` (default 8765), `token`,
  and (M2-A) `input_enabled` / `rate_limit_per_sec` / `gui_click_evidence`
  (see below), plus (M2-A2) `keep_running_unfocused` and (M2-D)
  `permission`.
- **Audit log**: `logs/sirius_bridge.log` - one line per server start/stop,
  connect/disconnect, hello success/failure and every request (with the
  resulting error code); every `input.*` call additionally writes an
  `INPUT` line with a parameter summary and result (ok / rate_limited /
  input_disabled / no_screen).
- **Capabilities**: `capabilities/list` returns the 12 frozen capabilities
  (name/version/input_schema) assembled from the schema JSON files copied into
  the jar at build time from `../sirius-brain/schema` (single source of truth;
  see "Schema sync" below). Protocol version: `"1.0"`.

### Config file

```toml
port = 8765
token = "<64 hex chars>"
input_enabled = true          # master switch for input.* + look* (false -> error -32011)
rate_limit_per_sec = 20       # combined input.* calls/s (token bucket, 1..1000)
gui_click_evidence = true     # screenshot every GUI click -> logs/sirius_evidence/
keep_running_unfocused = true # disable vanilla pause-on-lost-focus at runtime (M2-A2)
permission = "full"           # M2-D tier: observe / input_gui / input_world / full
```

Missing keys are auto-added to an existing file on the next launch (the file
is rewritten with defaults + comments, unknown keys ignored); invalid values
fall back to the default plus a note in the log.

### Currently implemented frames

| Frame (brain -> mod) | Behaviour |
|---|---|
| `hello` (first frame only) | token check -> `hello_ack` or close 1008 |
| `request` `capabilities/list` | capability list + `protocol_version` |
| `request` `screenshot` | see "The perception tools" below |
| `request` `getStats` | see "The perception tools" below |
| `request` `world.query` | see "The perception tools" below |
| `request` `input.key` / `input.text` / `input.mouseMove` / `input.click` | see "The input tools" below |
| `request` `look` / `lookAt` | see "The look tools" below |
| `request` `getGuiState` | see "The GUI state tool" below |
| `request` `events.subscribe` | see "The event push channel" below |
| `request` any other method | `-32601` `not implemented: <method>` (until M3) |
| `task` (NEKO) | immediate `task_finished` `status=interrupted` `text="not implemented"`, `task_id` echoed verbatim (placeholder until M3) |
| invalid JSON | `-32700` parse error |
| non-object JSON / unknown frame type / malformed request | `-32600` invalid frame |

Mod -> brain frames: `response` (always carries `type/id/result/error`
exactly as the frozen schema prescribes) and `notification` (M2-B,
outbound-only - see below).

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

## The input tools (M2-A)

The four input primitives are the "hands" of the bridge. They replay the
**GLFW event callbacks** the real input pipeline uses - exactly what a human
at the keyboard/mouse produces - instead of calling game action methods:

| Tool | Event-layer path used |
|---|---|
| `input.key` | `KeyboardHandler.keyPress(window, key, scancode, PRESS, mods)` + scheduled `RELEASE` |
| `input.text` | `KeyboardHandler.charTyped(window, codepoint, 0)` per Unicode codepoint (true CJK support) |
| `input.mouseMove` | `MouseHandler.onMove(window, x, y)` |
| `input.click` | `MouseHandler.onPress(window, button, PRESS/RELEASE, 0)` |

(`keyPress` is public in 1.21.1; `charTyped`/`onPress`/`onMove` are private
and reached via reflection so the full vanilla + NeoForge dispatch - screen
key/mouse hooks, `KeyMapping.set/click`, view turning - runs verbatim.)
Every callback is submitted to the client main thread via
`Minecraft.execute`, the same marshalling vanilla's own GLFW callbacks do;
the response waits (latch, 10 s timeout) only for the FIRST event of a call.
Timed follow-ups (key RELEASE, click bursts) run on a scheduler thread and
are submitted to the main thread at their due time.

### `input.key({ code, duration_ms?, modifiers? })`

- `code`: logical key name (`"E"`, `"W"`, `"ENTER"`, `"SPACE"`,
  `"BACKSPACE"`, `"F3"`, `"LEFT_SHIFT"`, `"UP"`, `"NUMPAD5"`, ... - letters,
  digits, function keys, modifiers, navigation, numpad, punctuation; see
  `KeyCodes.java`) **or** a raw GLFW keycode integer (what the frozen schema
  declares). Unknown names -> `-32602`.
- `duration_ms`: hold time. Default 0 = tap, released after 50 ms; explicit
  values > 0 hold the key that long (movement: `input.key("W", 500)`).
  Cap 60000 -> `-32602` beyond.
- `modifiers`: array of `"SHIFT"`/`"CTRL"`/`"ALT"`/`"SUPER"` -> GLFW mod
  bits, forwarded to the key callback (reaches `Screen.keyPressed` mods
  argument and mod-GUI hotkeys). Note: `Screen.hasShiftDown()` reads the
  REAL OS keyboard state, so it does not see injected modifier bits - for
  shift-dependent GUI logic inject a real `"LEFT_SHIFT"` key press around
  the click instead.
- `"ESC"` (aliases `ESCAPE`, GLFW 256) follows the vanilla dispatch exactly:
  with a GUI open it reaches `Screen.keyPressed` -> `onClose()` (closes the
  screen), in-game (no screen) it opens the pause menu via
  `Minecraft.pauseGame(false)` - in singleplayer that pauses the world, so
  the brain should treat it as a "stop" action and press it again /
  click "Back to Game" to resume.
- Result: `{"injected":true,"key":"E","glfw_key":69,"modifiers":0,
  "duration_ms":50,"release_scheduled":true,"screen_open":false}`.

### `input.text({ string })`

Types text through the character callback (codepoint by codepoint, so
Chinese etc. works) into whatever Screen is focused - e.g. chat after
`input.key("T")`, or a search field. ENTER/BACKSPACE are separate keys: the
caller composes `input.text` + `input.key`. Max 512 codepoints. If no screen
is open nothing is delivered - the result says so instead of erroring:
`{"delivered":0,"codepoints":3,"screen_open":false}`.

### `input.mouseMove({ x, y })`

Moves the internal cursor to `x,y` in **window client-area pixels, origin
top-left** (the coordinate system of `MouseHandler`/GLFW cursor callbacks;
on the standard test client this matches screenshot pixels 1:1). Coordinates
clamp to the window. With a GUI open, widgets receive `mouseMoved` on the
next frame; with no GUI and the mouse grabbed, the delta **turns the view**
exactly like a human mouse move. Result includes the GUI-scaled position
(`gui_scaled`) the game derives with the same formula `MouseHandler.onPress`
uses - handy for GUI clicking math.

### `input.click({ button, count? })`

Clicks at the CURRENT cursor position (set it with `input.mouseMove`
first). `button`: 0 left / 1 right / 2 middle. `count` (1..8, default 1):
extra clicks repeat every 50 ms (press held 25 ms); count 2 = double-click.
With a GUI open the press goes through `Screen.mouseClicked`; without one it
is attack (left) / use (right) / pick (middle) via `KeyMapping`, and an
ungrabbed cursor re-grabs first - all authentic paths.

**GUI click evidence (spec 7.1)**: when a Screen is open and
`gui_click_evidence = true`, the framebuffer is captured right before the
press and saved as a small JPEG (longest edge 1024, quality 40) to
`logs/sirius_evidence/evidence_click_<yyyyMMdd_HHmmssSSS>.jpg`; the file
name and size come back in the result. Evidence failure never fails the
click.

Result: `{"clicked":true,"button":0,"count":1,"screen_open":true,
"screen":"InventoryScreen","evidence":{"file":"...jpg","bytes":53210}}`.

### Rate limiting, disable switch, errors

All four tools share one token bucket: `rate_limit_per_sec` calls/s
(default 20), bucket capacity = 1 s of allowance. Exceeding it answers

- `-32010 rate_limited` (retry shortly),
- `-32011 input_disabled` when `input_enabled = false`,
- `-32012 permission_denied` when the `permission` tier vetoes the call
  (M2-D, see below),
- `-32602` for schema violations,
- `-32603` when the main thread does not drain within 10 s (iconified
  window) or a callback could not be reached.

Each call writes an `INPUT` audit line (method, parameter summary, result)
to `logs/sirius_bridge.log` in addition to the generic REQUEST line.

### Input coordinate cheat sheet

| Concept | Value |
|---|---|
| mouseMove origin | window top-left |
| mouseMove units | client-area pixels (= screenshot pixels on the test client) |
| GUI widget coords | `gui_scaled` returned by mouseMove (equals `MouseHandler`'s own conversion) |
| view turning | delta between consecutive mouseMove positions, applied next frame when no GUI + mouse grabbed |

### Unfocused-window behaviour (M2-A2)

The "AI plays, human watches" scenario requires the world to keep ticking
while the human focuses a terminal/browser. Two different mechanisms matter
(verified against the 1.21.1 decompiled sources):

**1. Pause on lost focus - disabled by the bridge.** Vanilla
`GameRenderer.render` opens the pause menu (`pauseGame(false)` -> PauseScreen)
500 ms after the window loses focus when `Options.pauseOnLostFocus` is true;
in singleplayer the open PauseScreen is what freezes ticks. With
`keep_running_unfocused = true` (the **default**), the bridge sets
`Options.pauseOnLostFocus = false` **once at startup** (title screen reached,
config loaded). In 1.21.1 the option is a plain `public boolean` field - not
an `OptionInstance` - so a field write is the entire runtime API; F3+P proves
the pattern (it flips the same field at runtime).

- **`options.txt` is never written by the bridge** (we skip
  `Options.save()`). Manual alternative: add `pauseOnLostFocus:false` to
  `options.txt` yourself - the bridge then has nothing left to change.
- Caveat: if the user saves options themselves later (any change in the
  vanilla settings screen, or F3+P), vanilla persists **all** fields,
  including our runtime `false`, into `options.txt`. That is vanilla
  behaviour, not a bridge write; `keep_running_unfocused = false` stops the
  bridge from touching the field (restore pausing via F3+P or the settings
  screen).
- This fixes *pausing* only - it does not make the unfocused window "active".

**2. What still works while unfocused** (injected callbacks are plain method
calls, not OS events; entry checks compare the window handle, never focus -
verified in the 1.21.1 sources):

| Path | Unfocused |
|---|---|
| `input.key` (KeyMapping: movement, inventory, chat, ...) | works - frames keep rendering |
| `input.text` (chat/search fields via a Screen) | works |
| `input.click` in a GUI (`Screen.mouseClicked`) | works |
| `input.click` in-world (attack/use/pick via KeyMapping) | works while the mouse is grabbed |
| `input.mouseMove` position tracking (GUI click math) | works (position always updates) |
| `input.mouseMove` -> **view rotation** | **does not work** |
| GUI hover/drag updates (`mouseMoved`/`mouseDragged`) | **do not work** |
| acquiring the mouse grab while un-grabbed (e.g. clicking through the title screen) | **does not work** (`MouseHandler.grabMouse` checks focus) |

The view-rotation limitation is double-gated in vanilla:
`MouseHandler.onMove` only accumulates deltas when `isWindowActive()`, and
`handleAccumulatedMovement()` (which calls `turnPlayer`) is entirely wrapped
in `if (isWindowActive())`. There is no vanilla switch for it - known
limitation, to be resolved in M4 pathing (either keep the game window
focused, which is the normal "human watches" setup, or add an action-layer
`player.turn()` look primitive, which has no event-callback entry anyway).

Iconified (minimized) remains the hard case from M1-C/M2-A: the render loop
stops draining `Minecraft.execute` tasks, so tools answer `-32603` after the
10 s latch timeout instead of hanging. Unfocused-but-visible is the supported
"human alt-tabs away" case.

## The look tools (M2-D)

`look` and `lookAt` set the player's view rotation absolutely - the gaze to
pair with the M2-A hands (aiming, pathing, "look at what I'm talking about").

- **Action layer, not event layer** (deliberate deviation from M2-A's
  injection principle): view rotation has no GLFW event-callback entry a
  human could produce for us - humans move the mouse, which becomes a
  *delta*. `input.mouseMove` turning requires the window focused AND the
  mouse grabbed (M2-A2 finding) and composes deltas; `look` instead writes
  the rotation directly with the exact statement sequence of vanilla's own
  `Entity.lookAt(Anchor, Vec3)` (1.21.1 sources): `setYRot`/`setXRot` (the
  NaN-guarded setters), `yRotO`/`xRotO` kept in sync (otherwise the next
  rendered frame smears from the old rotation), plus `setYHeadRot` so body
  and head do not decouple for a frame. A `LocalPlayer` auto-syncs rotation
  to the server next tick via `LocalPlayer.sendPosition` (PosRot packets) -
  no packet work on our side.
- **`look({yaw, pitch})`** - degrees, frozen-schema bounds `-180..180` /
  `-90..90` (violations `-32602`). Yaw 0 = +Z (south), -90 = +X (east),
  180/-180 = -Z (north); negative pitch is up - the F3 debug axes. Result:

```json
{"in_game": true, "looked": true, "yaw": -90.0, "pitch": 12.5,
 "previous": {"yaw": 37.0, "pitch": 0.0}}
```

- **`lookAt({x, y, z})`** - world position to face. The rotation is vanilla's
  `Entity.lookAt` math inverted-exactly: from the eye position
  (`getEyePosition()`, i.e. feet + eye height) to the target,
  `yaw = atan2(dz, dx) - 90°`, `pitch = -atan2(dy, horizontal)` (all wrapped
  into the schema bounds); `distance` is the Euclidean eye-to-target
  distance. Result:

```json
{"in_game": true, "looked": true, "target": {"x": 10.0, "y": 65.0, "z": -3.5},
 "yaw": 14.04, "pitch": -11.31, "distance": 12.08}
```

- Not in a world (title screen): `{"in_game": false, "looked": false}` - not
  an error, the getStats convention.
- Guard rails: `input_enabled = false` disables both (`-32011` - looking
  around IS acting); the permission tier treats them as world-level actions
  (`observe`/`input_gui` deny with `-32012`, see below). They are
  deliberately NOT charged against the input token bucket - a one-field
  write cannot flood the event pipeline, and M4 aiming will want high look
  rates without starving `input.*`.
- Known quirks: `lookAt` at the eye position itself is degenerate
  (`atan2(0,0)=0` -> yaw -90, pitch 0 - the same harmless answer vanilla's
  own `lookAt` gives); the rotation applies next rendered frame; while dead
  the rotation still writes but the server may ignore it.

## Permission tiers (M2-D)

`permission` in `config/sirius_bridge.toml` gates every ACTING tool
(`input.*`, `look`, `lookAt`); perception (`screenshot`/`getStats`/
`world.query`/`getGuiState`), `events.subscribe` and capabilities are always
allowed. Values (case-insensitive; anything else falls back to the default
plus a note):

| Tier | `input.*` | `look`/`lookAt` |
|---|---|---|
| `observe` | denied (`-32012`) | denied (`-32012`) |
| `input_gui` | allowed ONLY while a GUI screen is open | denied (`-32012`) |
| `input_world` | allowed ONLY while NO GUI is open | allowed |
| `full` (default) | allowed | allowed |

- **Default `full` preserves M2-A behaviour byte-identically** - tiers are
  strictly opt-in, and the key is auto-added to existing configs on the next
  launch.
- The GUI-open test uses the live `mc.screen` state read on the client main
  thread *inside* each tool's existing main-thread task (the screen can
  change any tick; a pre-flight check on the WebSocket thread would race).
  For `input.key`/`input.text`/`input.mouseMove` under `input_gui` a
  screen-less keypress would go to the KeyMapping/world - i.e. act outside
  the GUI - so it is denied; `input.click` already branched on
  `mc.screen != null` (GUI click vs attack/use).
- `observe` denies pre-flight (no rate-limit token burned, no main-thread
  round trip); screen-dependent verdicts deny from inside the task. Denials
  return error `-32012` (not `-32603`) and write an
  `INPUT_DENIED reason=permission tier=...` audit line to
  `logs/sirius_bridge.log`.

## The event push channel (M2-B)

The mod -> brain direction of the protocol: once a client subscribes, the
bridge pushes `notification` frames the moment something happens in the
game - the brain no longer has to poll.

```json
{"type":"notification","event":"chat",
 "data":{"message":"<Steve> hi","system":false,"sender":"<uuid>","level":"INFO"},
 "timestamp":1755470400.123,"seq":0}
```

- **`timestamp`** is epoch **seconds** as a float (matches Python
  `time.time()`; the Python `NotificationFrame` model requires a float).
- **`seq`** is per-connection, starts at 0, increments by exactly 1 per
  delivered frame (the brain warns on non-monotonic seq - our side never
  skips or repeats for a live connection).
- **`level`** rides inside `data` by convention (the frozen frame model has
  no level field); the three levels `CRITICAL`/`WARNING`/`INFO` follow the
  frozen `EventLevel` enum.

### `events.subscribe({ types, min_level })`

- `types`: REQUIRED array of event name strings (schema violations ->
  `-32602`); `"*"` or an **empty array** = all events.
- `min_level`: `"CRITICAL"|"WARNING"|"INFO"` or null (default INFO = no
  level filtering).
- Response: `{"subscribed":true,"types":[...],"min_level":"INFO",
  "note":"unsubscribed clients receive no pushes"}`.
- **A client that never subscribes receives nothing** - pushes are strictly
  opt-in per connection. Re-subscribing replaces the previous filter (the
  seq counter continues, it is per-connection, not per-subscription).
- Each successful subscribe writes a `SUBSCRIBE` audit line.

### Event catalogue

| Event | Level | Fired when | `data` |
|---|---|---|---|
| `chat` | INFO | any chat/system line arrives | `message`, `system` (bool), `sender` (uuid; player lines only) |
| `gui_open` | WARNING | a screen is about to open (`ScreenEvent.Opening`) | `screen` (class simple name, e.g. `InventoryScreen`) |
| `gui_close` | WARNING | a screen is replaced or closed (`ScreenEvent.Closing` - fires on screen-to-screen switches too) | `screen` |
| `death` | CRITICAL | player becomes dead-or-dying (edge-triggered) | `health`, `air`, `on_fire` |
| `fire` | CRITICAL | player catches fire | `health` |
| `health_low` | CRITICAL | health <= 6.0 | `health`, `threshold` |
| `drown` | CRITICAL | underwater AND air < 300 | `air` |
| `screenshot` | INFO | the screenshot stream (below) | `image_b64`, `format`, `width`, `height`, `quality` |

Danger states are sampled every 20 ticks (~1 s) and are **edge-triggered**
(one event per false->true transition) with a 5 s per-type cooldown to
suppress flapping (fire flickering on/off, health oscillating around the
threshold). While dead, the weaker states (fire/health_low/drown) stay
silent - `death` is the signal. Leaving a world resets the edges.

### The screenshot stream

A push-based visual feed for subscribers of the `screenshot` event, budgeted
so a frame never breaks the ~100KB wire ceiling (spec 8.2 pipeline,
parameters adopted from N.E.K.O's production service):

- **Sampling**: the framebuffer is read ~1 Hz (every 20 ticks), and only
  while at least one subscriber matches the `screenshot` event - an idle
  bridge costs nothing.
- **Throttle**: minimum 6 s between pushes. Frames arriving inside a closed
  window collapse into a single **latest-wins pending slot**; exactly one
  delayed flush is armed for the window boundary. The newest frame is
  therefore never lost, only delayed to the boundary (an immediate push
  cancels an armed flush so a stale frame can never surface after a fresh
  one).
- **Budget ladder**: longest edge 1024 + JPEG q80 first; on overrun,
  quality descends `[80, 65, 50, 40, 30]` (never above the configured
  quality) crossed with edge halving `[1024, 512, 256]`. The first
  combination whose **base64 length** fits 100KB wins; if nothing fits, the
  smallest attempt ships anyway - a frame is never dropped for size.
- **Ring buffer**: the last 3 encoded frames are kept (spec); only the
  latest is consumed (reconnect replay is future work).
- Payload `data` mirrors the `screenshot` RPC response fields
  (`image_b64`/`format`/`width`/`height`/`quality`, plus the injected
  `level`).

### Threading

Event sources fire on the client main/render thread (NeoForge chat/screen
events, the tick sampler); the screenshot encode and the delayed boundary
flush run on a daemon thread (`sirius-bridge-events`). Delivery
(`EventPusher.push`) is fully thread-safe: sessions live in a concurrent
map, subscriptions are volatile, `seq` is an atomic per-connection counter,
and socket writes are safe from any thread. The framebuffer read reuses
`PerceptionTools.grabScreen()` (render thread, pixel download only) - the
render thread never pays for JPEG encoding.

### Honest drop policy

A push to a connection that died between sampling and sending counts as
dropped: one `EVENT_DROP` audit line plus a counter, never a silent loss
and never an exception into the game. Shutdown reports the totals
(`EVENTS_STOP pushed=.. dropped=..`). Stream frames that fail to encode are
logged and skipped - the next 1 Hz sample replaces them.

## The GUI state tool (M2-C)

`getGuiState()` takes **no parameters** (any member in `params` answers
`-32602`) and returns a structural snapshot of the currently open `Screen`
- the alternative to OCR-ing screenshots: exact widget geometry, text-field
contents and container-slot item ids, in one request.

- **No screen open**: `{"screen_open": false}` (not an error).
- **Standard response**:

```json
{"screen_open": true, "in_game": true, "screen_class": "InventoryScreen",
 "widgets": [{"type": "Button", "x": 210, "y": 170, "width": 40, "height": 20,
              "visible": true, "active": true, "message": "Reset Demo"},
             {"type": "EditBox", "x": 5, "y": 6, "width": 100, "height": 16,
              "visible": true, "active": true, "text": "hello"}],
 "slots": [{"index": 0, "container_slot": 0, "x": 154, "y": 28, "role": "result",
            "item": "minecraft:oak_log", "count": 12},
           {"index": 9, "container_slot": 9, "x": 30, "y": 40, "role": "player",
            "item": null, "count": 0}],
 "truncated": false}
```

- `widgets` comes from a walk of BOTH widget registries a Screen keeps: the
  `children()` tree (addWidget / addRenderableWidget) and the public
  `renderables` list (the only home of `addRenderableOnly` widgets such as
  render-only labels) - identity-deduplicated, depth-bounded at 12, capped
  at **512** nodes - beyond that `truncated:true` and enumeration stops, the
  world.query discipline. Each node carries the class simple name as `type`
  (anonymous subclasses - vanilla's ChatScreen input is one - fall back to
  the first named superclass, so it reports `EditBox`), geometry,
  `visible`/`active` flags, `message` (`getMessage().getString()` -
  **omitted when empty**) and `text` for text fields (`EditBox.getValue()` -
  omitted for other widgets). Note the vanilla `InventoryScreen` really has
  only ~1 widget (the recipe-book toggle) - its structure lives in `slots`.
- `slots` is present only for container screens
  (`AbstractContainerScreen`): every `menu.slots` entry with screen position
  `getGuiLeft()+slot.x / getGuiTop()+slot.y` (slots are 16x16 cells rendered
  outside the children() widget tree). `index` is the menu list position
  (`Slot.index`), `container_slot` the slot's index inside its container
  (`Slot.getContainerSlot()`). `item` is the registry name string or JSON
  `null` (empty slot / access failure), `count` 0 when empty. There is
  deliberately no slot cap - vanilla containers stay under ~50 slots and
  slots are flat, not recursive.
- **Role semantics** (generic detection, adopted from Numen GuiOps):
  `crafting` (slot container is a `CraftingContainer`), `result`
  (`ResultSlot`), `hotbar`/`player` (the player's own inventory - **container
  slot** 0-8 = hotbar; 9-35 = player), `armor` (container slots 36-39),
  `offhand` (40), `container` (anything else - chest/furnace/modded). The
  boundary uses `Slot.getContainerSlot()`, never `Slot.index` -
  `AbstractContainerMenu.addSlot` overwrites `index` with the menu position,
  which put the ARMOR slots (menu positions 5-8) in "hotbar" in the first
  real-machine run. Armor/offhand are separate roles because lumping them
  into "player" made the first acceptance replay drag crafted planks onto
  the helmet slot when filtering for "empty player slots" - storage filters
  should use player/hotbar only. The vanilla E-key inventory therefore
  reports 1 result + 4 crafting + 9 hotbar + 27 player + 4 armor + 1
  offhand = **46 slots**.
- **`in_game`** mirrors the getStats convention: a screen without a world
  (title/options screens) still reports its widgets, plus `in_game: false`
  for context.
- **Fallback tier**: if widget traversal throws (modded screens can be
  creative), the response degrades instead of erroring -
  `{"screen_open": true, "fallback": true, "screen_class": ..., "rects":
  [...whatever geometry was collected before the failure...], "note":
  "<failure>"}`; a broken slot likewise degrades alone to `item: null` plus a
  per-slot `note`. getGuiState never surfaces `-32603` for screen content.
- **Coordinate basis**: all x/y are **GUI-scaled** - the same space as the
  `gui_scaled` object `input.mouseMove` *returns*, NOT the window pixels
  mouseMove *takes*. To click a slot at gui (gx, gy): derive the scale from
  any mouseMove response (`delivered px / gui_scaled` of one probe), convert
  `gx * screenWidth / guiScaledWidth`, then mouseMove + click. On the
  standard test client the scale is a small integer (GUI scale 2-4).
- Known limits: modded screens with fully custom rendering may yield few or
  zero widgets (they draw directly); widgets that are plain fields rendered
  manually - e.g. container-screen title labels drawn with `Font` in
  `render()` - live in neither `children()` nor `renderables` and are
  unreachable; slots are reliable for vanilla-style containers; `message`
  only carries plain text (no styling metadata); the response is a snapshot -
  the screen may change between call and click.

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
perception, input, event-push, GUI state and look code - parameter
validation, bbox cropping, both JPEG budget ladders, response assembly,
block scan and entity filtering, the key-name table, the rate-limiter token
bucket, evidence file naming, the config parser (incl.
`keep_running_unfocused` and `permission` defaults/round-trips),
subscription matching, notification frame assembly, the stream throttle
state machine (injected clock), the streaming ladder, plus widget/slot node
assembly, the 512-node cap, slot role classification, all three getGuiState
response shapes, the look rotation math (hand-computed cases against the
vanilla formula) and the permission-tier decision matrix - with no game
launched (241 checks; a 4K incompressible-noise image verifies the 2MB RPC
degrade, an 800x600 one the 100KB stream ladder).

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
    InputTools.java       M2-A shells: event-callback injection, GUI click evidence (main thread)
    EventPusher.java      M2-B shell: notification emit choke point (chat/gui/danger/screenshot stream)
    GuiTools.java         M2-C shell: screen widget tree + container slot reads (main thread)
    LookTools.java        M2-D shells: absolute view rotation via the vanilla lookAt statement sequence
    InputGuard.java       shared guard rails: input_enabled + rate limit + evidence flag + permission tier
    ToolContracts.java    pure: param validation + response assembly + world scan logic
    InputContracts.java   pure: input param validation + result assembly + evidence naming
    GuiContracts.java     pure: GUI state node/slot assembly, cap + truncation, response shapes
    EventsContracts.java  pure: event levels, subscription matching, notification shape, stream throttle
    LookContracts.java    pure: look/lookAt validation, vanilla rotation math, response shapes
    PermissionContracts.java  pure: M2-D permission tier matrix (tier x screen-state x action)
    KeyCodes.java         pure: logical key names -> GLFW keycodes (1.21.1 InputConstants values)
    TokenBucket.java      pure: input rate limiter (clock injectable for the smoke test)
    ImageOps.java         pure: crop / JPEG encode / size-budget ladder / base64
    Json.java             wire-frame builders + JSON-RPC style error codes
src/test/java/io/sirius/bridge/SmokeMain.java   in-process smoke test (gradlew smokeTest)
src/main/templates/META-INF/      neoforge.mods.toml template (properties expanded at build time)
build.gradle                      jarJar dependency + schema sync + smokeTest task
src/main/resources/assets/sirius_bridge/   Asset placeholder
```

## Next steps (per §8.2 of the technical spec)

- M3+: `events.watch` (stat/condition watches with hysteresis + cooldown -
  deliberately NOT implemented in M2-B).
- Natural M2-B extensions: more event sources just call
  `EventPusher.push` (one method, thread-safe); a reconnect replay could
  consume the stream ring buffer; the danger catalogue (hunger, weather,
  attacked) grows in `sampleDanger`.
- Security model so far: M1 loopback binding + token handshake, M2-A the
  input rate limiter/master switch/per-input audit, M2-B the strictly
  opt-in push channel, M2-D the permission tiers (`observe` is the
  read-only mode). A future token-scoped tier could reuse
  `PermissionContracts` per connection instead of per config.
