package io.sirius.bridge;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.FallingBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

import java.util.concurrent.CountDownLatch;

/**
 * The M3.5 v1.2 {@code dig} tool: an INTELLIGENT block-breaking primitive
 * that lives in the bridge so the brain sends intent ("dig this block") and
 * gets a verdict ({@code broken} / {@code timeout} / ...) instead of
 * hand-rolling lookAt + click-hold segments (the M3.5-T5a lesson: python-side
 * hold segments fight vanilla's progress-reset-on-release and burn minutes on
 * occluded trunks).
 *
 * <p><b>Lifecycle of one call</b> (all game access on the client main thread):
 * <ol>
 *   <li><b>Pre-checks</b> (start task): in-world; screen/permission gate
 *       (dig is {@link PermissionContracts.Action#INPUT} - the input_world
 *       tier, same class as {@code input.*}); eye-to-block-center distance
 *       &le; {@link DigContracts#REACH} (else {@code -32602} with a
 *       "walk closer first" teaching message); target not air
 *       ({@code already_air} idempotent success); the safe-to-break minimal
 *       set - any of the six neighbours holding a liquid
 *       ({@code blocked_liquid}, opening a flow is a world-changing surprise)
 *       or a {@link FallingBlock} directly above ({@code blocked_falling},
 *       sand/gravel/concrete powder would drop into the hole).</li>
 *   <li><b>Smooth aim</b>: a {@link TurnController} turn at
 *       {@link DigContracts#DIG_AIM_TURN_SPEED_DEG_S} (300 deg/s) to the
 *       block center - the natural-head-turn the T6 ruling asked for.</li>
 *   <li><b>Monitored hold</b> (action layer, see below): attack "press" ->
 *       per-tick ({@code ClientTickEvent.Post}) pump feeds
 *       {@link DigContracts.DigMonitor} - block-state-change main signal,
 *       occluder tolerance, destroy-stage telemetry early-stop, insta-mine
 *       short press, overall {@code timeout_ms} - and releases the moment
 *       the monitor finishes.</li>
     *   <li><b>Response</b>: {@code {result, block, elapsed_ms,
     *       broken_via_occluder?, reason?, drops?}} - the brain maps each
     *       result to a teaching phrase verbatim. M3.6: a {@code broken}
     *       verdict additionally waits {@link DigContracts#DROPS_WAIT_TICKS}
     *       ticks and reports the item entities that appeared within
     *       {@link DigContracts#DROPS_SCAN_RADIUS} of the dug block
     *       (pre-dig snapshot diffed) as {@code drops:[{item,count}]} -
     *       empirical drop knowledge instead of a hard-coded drop table
     *       (modded blocks included), which is also why the brain's pickup
     *       matching prefers this list over registry-id guessing.</li>
     * </ol>
 *
 * <p><b>Why the ACTION layer for the hold (not input.click's event layer):</b>
 * vanilla's continuous destroy is double-gated on OS focus:
 * {@code Minecraft.handleKeybinds} only continues mining while
 * {@code mouseHandler.isMouseGrabbed()}, and {@code MouseHandler.grabMouse()}
 * REFUSES the (re)grab whenever the window is not active - so after any
 * chat-screen open/close while unfocused (exactly the AI-plays-human-watches
 * workflow), an injected PRESS sets the key state but nothing mines
 * (real-machine-verified, M3.5 T6). The dig tool instead calls the exact
 * gameMode methods vanilla's held-button path itself runs -
 * {@code startDestroyBlock} / {@code continueDestroyBlock} /
 * {@code stopDestroyBlock} (+ hand swing) - the M2-D "look" precedent: an
 * action-layer primitive where no reliably-injectable event path exists.
 * This keeps working while the human alt-tabs (with
 * {@code keep_running_unfocused}, ticks continue).
 *
 * <p><b>Currency:</b> one call = one input rate-limit token (like
 * {@code input.click}) - the whole multi-second hold is one injection.
 * Every call writes an {@code INPUT} audit line.
 *
 * <p><b>Concurrency:</b> one active dig at a time (class-level slot, main
 * thread confined). A new dig supersedes the old one - its waiter is
 * released with {@code not_digging} immediately; the hold is stopped before
 * the new press, so the two never stack.
 */
final class DigTools {

    private DigTools() {
    }

    /** Registers the tool; called once from the BridgeServer constructor. */
    static void registerAll(ToolRegistry tools, InputGuard guard) {
        tools.register("dig", (ctx, params) -> dig(ctx, params, guard));
    }

    // ------------------------------------------------------------------ the tool call

    /** What the start task decided (main-thread outcome; shell translates). */
    private record StartOutcome(String kind, String detail, String blockName, String reason,
                                boolean instaMine, ActiveDig startedDig) {
        static StartOutcome of(String kind) {
            return new StartOutcome(kind, null, null, null, false, null);
        }
    }

    private static final String STARTED = "started";
    private static final String NOT_IN_GAME = "not_in_game";
    private static final String DENIED = "denied";
    private static final String OUT_OF_REACH = "out_of_reach";
    private static final String ALREADY_AIR = "already_air";
    private static final String BLOCKED_LIQUID = DigContracts.RESULT_BLOCKED_LIQUID;
    private static final String BLOCKED_FALLING = DigContracts.RESULT_BLOCKED_FALLING;

    /** The tool handler: guard preamble -> main-thread start task -> latch wait -> response. */
    private static JsonObject dig(ToolContext ctx, JsonObject params, InputGuard guard) throws Exception {
        final DigContracts.DigParams p;
        try {
            p = DigContracts.digParams(params);
        } catch (ToolContracts.InvalidParams e) {
            return Json.errorResponse(ctx.id(), Json.INVALID_PARAMS, e.getMessage(), null);
        }
        String summary = p.x() + "," + p.y() + "," + p.z();
        if (!guard.enabled()) {
            ctx.audit("INPUT", "method=dig " + summary + " result=input_disabled");
            return Json.errorResponse(ctx.id(), Json.INPUT_DISABLED, InputContracts.inputDisabledMessage(), null);
        }
        if (PermissionContracts.deniedRegardlessOfScreen(guard.tier(), PermissionContracts.Action.INPUT)) {
            return permissionDenied(ctx, guard, "dig", summary, null);
        }
        if (!guard.tryAcquire()) {
            ctx.audit("INPUT", "method=dig " + summary + " result=rate_limited");
            return Json.errorResponse(ctx.id(), Json.RATE_LIMITED,
                    InputContracts.rateLimitedMessage(guard.limitPerSecond()), null);
        }

        final long startedMs = System.currentTimeMillis();
        final StartOutcome start = PerceptionTools.callOnMainThread(ctx, () -> startDig(p, guard));
        switch (start.kind()) {
            case NOT_IN_GAME:
                return Json.okResponse(ctx.id(), ToolContracts.notInGame());
            case DENIED:
                return permissionDenied(ctx, guard, "dig", summary, Boolean.valueOf(start.detail()));
            case OUT_OF_REACH:
                ctx.audit("INPUT", "method=dig " + summary + " result=out_of_reach dist=" + start.detail());
                return Json.errorResponse(ctx.id(), Json.INVALID_PARAMS,
                        "dig target (" + summary + ") is " + start.detail() + " blocks away - beyond the "
                                + DigContracts.REACH + "-block reach. Walk closer first (e.g. walkTo a spot "
                                + "~1.5 blocks beside the target), then dig again"
                                + (start.blockName() != null ? " (target block: " + start.blockName() + ")" : ""),
                        null);
            case ALREADY_AIR:
                return finish(ctx, "dig", summary,
                        DigContracts.digResult(DigContracts.RESULT_ALREADY_AIR, null,
                                System.currentTimeMillis() - startedMs, null, null),
                        null);
            case BLOCKED_LIQUID, BLOCKED_FALLING:
                return finish(ctx, "dig", summary,
                        DigContracts.digResult(start.kind(), start.blockName(),
                                System.currentTimeMillis() - startedMs, null, start.reason()),
                        start.reason());
            case STARTED:
            default:
                break;
        }

        // Monitor runs on the game's ticks (50 ms); latch bounded by the dig
        // timeout plus slack for one late frame. On a latch timeout (iconified
        // window -> no ticks) we answer the timeout verdict WITHOUT touching
        // game state from this thread - the monitor's own timeout check
        // releases the button on the next tick that does run.
        ActiveDig dig = start.startedDig();
        boolean completed = dig.done.await(p.timeoutMs() + 1500L, java.util.concurrent.TimeUnit.MILLISECONDS);
        String result = completed && dig.result != null ? dig.result : DigContracts.RESULT_TIMEOUT;
        long elapsedMs = completed ? dig.elapsedMs() : System.currentTimeMillis() - startedMs;
        boolean viaOccluder = completed && dig.viaOccluder;
        // M3.6 T3: empirical drops. Only a broken verdict pays the wait, and
        // only while the dig budget has a second to spare (breaking in the
        // last second of the window means the brain's own RPC timeout - 30 s
        // by default - could race the extra wait; the report is best-effort,
        // an old-jar-shaped response without drops beats a timed-out one).
        java.util.List<JsonObject> drops = null;
        if (completed && DigContracts.RESULT_BROKEN.equals(result)
                && elapsedMs < p.timeoutMs() - DigContracts.DROPS_WAIT_TICKS * 50L) {
            drops = dropsAfterBreak(ctx, dig);
        }
        JsonObject response = DigContracts.digResult(result, dig.blockId, elapsedMs, viaOccluder,
                completed ? dig.note : "monitor did not finish (window iconified? self-cleans next tick)",
                drops);
        ctx.audit("INPUT", "method=dig " + summary + " timeout_ms=" + p.timeoutMs()
                + " result=" + result + (viaOccluder ? " via_occluder=true" : "")
                + " elapsed_ms=" + elapsedMs
                + (drops != null ? " drops=" + drops.size() : ""));
        return Json.okResponse(ctx.id(), response);
    }

    /**
     * M3.6 T3: the empirical drop report for a broken block. Sleeps
     * {@link DigContracts#DROPS_WAIT_TICKS} ticks (the server spawns the item
     * entities on break; the client sees them a few packets later) and then
     * snapshots - diffed against the dig's pre-start snapshot - the item
     * entities within {@link DigContracts#DROPS_SCAN_RADIUS} of the dug
     * center. Sleeping here runs on the connection's handler thread: dig is
     * a synchronous RPC and the brain drives it serially, so a bounded 1 s
     * tail is the simplest correct pacing.
     *
     * @return the aggregated {@code [{item,count}]} list, or null when the
     *         wait was interrupted / world left (caller omits the member -
     *         brain falls back to registry-id matching, old-jar semantics).
     */
    private static java.util.List<JsonObject> dropsAfterBreak(ToolContext ctx, ActiveDig dig) {
        try {
            Thread.sleep(DigContracts.DROPS_WAIT_TICKS * 50L);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return null;
        }
        java.util.List<ToolContracts.EntityFact> facts;
        try {
            facts = PerceptionTools.callOnMainThread(ctx, () -> itemFactsNear(dig.target));
        } catch (Exception e) {
            // Best-effort report: any failure omits the member - the brain then
            // falls back to registry-id matching (old-jar semantics), so a flaky
            // observation can never fail an already-successful dig.
            return null;
        }
        if (facts == null) {
            return null;
        }
        return DigContracts.aggregateDrops(facts, dig.dropsSeenBefore,
                dig.target.getX() + 0.5, dig.target.getY() + 0.5, dig.target.getZ() + 0.5,
                DigContracts.DROPS_SCAN_RADIUS);
    }

    /**
     * Item-entity facts near the dug block (main thread). The same reading
     * path world.query(entities) uses (EntityFact + registry id), restricted
     * to ItemEntity - everything else is irrelevant to the drop report.
     */
    private static java.util.List<ToolContracts.EntityFact> itemFactsNear(BlockPos target) {
        Minecraft mc = Minecraft.getInstance();
        ClientLevel level = mc.level;
        if (level == null) {
            return null;
        }
        java.util.List<ToolContracts.EntityFact> facts = new java.util.ArrayList<>();
        for (Entity entity : level.entitiesForRendering()) {
            if (entity.isRemoved() || !(entity instanceof ItemEntity itemEntity)) {
                continue;
            }
            ItemStack stack = itemEntity.getItem();
            var itemId = BuiltInRegistries.ITEM.getKey(stack.getItem());
            facts.add(new ToolContracts.EntityFact(
                    entity.getStringUUID(),
                    entity.getName().getString(),
                    EntityType.getKey(entity.getType()).toString(),
                    entity.getX(), entity.getY(), entity.getZ(),
                    Float.NaN,
                    itemId != null ? itemId.toString() : "unknown",
                    stack.getCount()));
        }
        return facts;
    }

    private static JsonObject finish(ToolContext ctx, String method, String summary,
                                     JsonObject response, String reason) {
        ctx.audit("INPUT", "method=" + method + " " + summary + " result="
                + response.get("result").getAsString() + (reason != null ? " reason=" + reason : ""));
        return Json.okResponse(ctx.id(), response);
    }

    // ------------------------------------------------------------------ start (main thread)

    /**
     * The start task: pre-checks + safety + aim turn + installing the
     * monitor. MUST run on the client main thread (called via
     * {@code PerceptionTools.callOnMainThread}).
     */
    private static StartOutcome startDig(DigContracts.DigParams p, InputGuard guard) {
        Minecraft mc = Minecraft.getInstance();
        LocalPlayer player = mc.player;
        ClientLevel level = mc.level;
        if (player == null || level == null) {
            return StartOutcome.of(NOT_IN_GAME);
        }
        // Screen-dependent tiers decide HERE (the screen can change any tick)
        // - exactly the input.* discipline.
        boolean screenOpen = mc.screen != null;
        if (!PermissionContracts.allows(guard.tier(), screenOpen, PermissionContracts.Action.INPUT)) {
            return new StartOutcome(DENIED, String.valueOf(screenOpen), null, null, false, null);
        }

        BlockPos target = new BlockPos(p.x(), p.y(), p.z());
        BlockState state = level.getBlockState(target);
        String blockName = blockId(state);

        // Reach: eye to the block CENTER - the same point lookAt aims at.
        Vec3 eye = player.getEyePosition();
        Vec3 center = new Vec3(p.x() + 0.5, p.y() + 0.5, p.z() + 0.5);
        double dist = eye.distanceTo(center);
        if (dist > DigContracts.REACH) {
            return new StartOutcome(OUT_OF_REACH, String.format(java.util.Locale.ROOT, "%.1f", dist),
                    blockName, null, false, null);
        }
        if (state.isAir()) {
            return StartOutcome.of(ALREADY_AIR); // idempotent success, nothing to do
        }

        // Safe-to-break minimal set (ported from Numen-style rules):
        String blocked = liquidNeighbor(level, target);
        if (blocked != null) {
            return new StartOutcome(BLOCKED_LIQUID, null, blockName, blocked, false, null);
        }
        blocked = fallingAbove(level, target);
        if (blocked != null) {
            return new StartOutcome(BLOCKED_FALLING, null, blockName, blocked, false, null);
        }

        boolean instaMine = state.getDestroyProgress(player, level, target) >= 1.0F;
        // M3.6 T3: pre-dig snapshot of the item entities already near the
        // target - the drop report diffs against it so a neighbouring (foreign)
        // drop never rides along as "what my dig produced".
        java.util.Set<String> dropsSeenBefore = itemUuidsNear(level, target);
        // Smooth aim at the block center (300 deg/s); supersedes any active turn.
        double[] rotation = LookContracts.rotationTowards(
                eye.x, eye.y, eye.z, center.x, center.y, center.z);
        ActiveDig dig = installMonitor(target, blockName, p.timeoutMs(), instaMine, dropsSeenBefore);
        dig.turn = TurnController.begin(rotation[0], rotation[1], DigContracts.DIG_AIM_TURN_SPEED_DEG_S);
        return new StartOutcome(STARTED, null, blockName, null, instaMine, dig);
    }

    /** uuids of the item entities within the drop scan radius of (x,y,z). */
    private static java.util.Set<String> itemUuidsNear(ClientLevel level, BlockPos target) {
        double cx = target.getX() + 0.5, cy = target.getY() + 0.5, cz = target.getZ() + 0.5;
        double maxDistSq = DigContracts.DROPS_SCAN_RADIUS * DigContracts.DROPS_SCAN_RADIUS;
        java.util.Set<String> seen = new java.util.HashSet<>();
        for (Entity entity : level.entitiesForRendering()) {
            if (entity.isRemoved() || !(entity instanceof ItemEntity)) {
                continue;
            }
            double dx = entity.getX() - cx, dy = entity.getY() - cy, dz = entity.getZ() - cz;
            if (dx * dx + dy * dy + dz * dz <= maxDistSq) {
                seen.add(entity.getStringUUID());
            }
        }
        return seen;
    }

    /** First liquid-holding neighbour of the target, or null ("water at (x,y,z)"). */
    private static String liquidNeighbor(ClientLevel level, BlockPos target) {
        for (Direction direction : Direction.values()) {
            BlockPos neighbor = target.relative(direction);
            var fluid = level.getFluidState(neighbor);
            if (!fluid.isEmpty()) {
                var fluidId = BuiltInRegistries.FLUID.getKey(fluid.getType());
                return "liquid " + (fluidId != null ? fluidId : "unknown") + " at neighbor "
                        + neighbor.toShortString()
                        + " would flow in (break it from behind/a side, or dam the flow first)";
            }
        }
        return null;
    }

    /** The falling block directly above the target, or null ("sand above"). */
    private static String fallingAbove(ClientLevel level, BlockPos target) {
        BlockPos above = target.above();
        BlockState up = level.getBlockState(above);
        if (!up.isAir() && up.getBlock() instanceof FallingBlock) {
            return blockId(up) + " directly above would fall into the hole"
                    + " (break the falling block first, or approach from the side)";
        }
        return null;
    }

    private static String blockId(BlockState state) {
        var id = BuiltInRegistries.BLOCK.getKey(state.getBlock());
        return id != null ? id.toString() : "unknown";
    }

    // ------------------------------------------------------------------ monitor + tick pump

    /** The single active dig; main-thread confined. */
    private static final class ActiveDig {
        final DigContracts.DigMonitor monitor;
        final BlockPos target;
        final String blockId;
        final long startedMs;
        /** M3.6: item-entity uuids near the target BEFORE the dig (drop diff base). */
        final java.util.Set<String> dropsSeenBefore;
        final CountDownLatch done = new CountDownLatch(1);
        /** The aim turn currently owned by this dig (re-issued on drift; null before the first). */
        TurnController.Turn turn;
        volatile String result;
        volatile boolean viaOccluder;
        volatile String note;

        ActiveDig(DigContracts.DigMonitor monitor, BlockPos target, String blockId, long startedMs,
                  java.util.Set<String> dropsSeenBefore) {
            this.monitor = monitor;
            this.target = target;
            this.blockId = blockId;
            this.startedMs = startedMs;
            this.dropsSeenBefore = dropsSeenBefore;
        }

        long elapsedMs() {
            return System.currentTimeMillis() - startedMs;
        }

        void complete(String result, boolean viaOccluder, String note) {
            this.result = result;
            this.viaOccluder = viaOccluder;
            this.note = note;
            done.countDown();
        }
    }

    private static ActiveDig active;

    /**
     * Installs a new dig (returning the handle the caller waits on),
     * superseding (and stopping) any active one. Main thread only.
     */
    private static ActiveDig installMonitor(BlockPos target, String blockId, int timeoutMs,
                                            boolean instaMine, java.util.Set<String> dropsSeenBefore) {
        if (active != null) {
            release(Minecraft.getInstance());
            active.complete(DigContracts.RESULT_NOT_DIGGING, active.monitor.sawOccluder(), "superseded by another dig");
            active = null;
        }
        ActiveDig dig = new ActiveDig(new DigContracts.DigMonitor(timeoutMs, instaMine), target, blockId,
                System.currentTimeMillis(), dropsSeenBefore);
        active = dig;
        return dig;
    }

    /**
     * Forces the active dig to end now (hold stopped, waiter released). Main
     * thread only; returns the finished dig (or null when none was active).
     */
    private static ActiveDig abortActive(String note) {
        ActiveDig dig = active;
        if (dig == null) {
            return null;
        }
        release(Minecraft.getInstance());
        dig.complete(DigContracts.RESULT_NOT_DIGGING, dig.monitor.sawOccluder(), note);
        active = null;
        return dig;
    }

    /**
     * The per-tick pump (from {@code SiriusBridge.onClientTick}): gathers the
     * main-thread observation and acts on the monitor's verdict. Main thread
     * only; no-op when idle.
     */
    static void onClientTick() {
        ActiveDig dig = active;
        if (dig == null) {
            return;
        }
        Minecraft mc = Minecraft.getInstance();
        LocalPlayer player = mc.player;
        ClientLevel level = mc.level;
        if (player == null || level == null) {
            abortActive("left the world mid-dig");
            return;
        }
        if (mc.screen != null) {
            // A GUI opened mid-dig (chat, pause, container): the held attack
            // press must NOT leak into the screen - abort honestly.
            abortActive("screen opened mid-dig");
            return;
        }

        BlockState state = level.getBlockState(dig.target);
        boolean targetGone = state.isAir() || !dig.blockId.equals(blockId(state));
        double[] want = aimRotation(player, dig.target);
        boolean aimed = Math.abs(LookContracts.yawDelta(player.getYRot(), want[0])) < 2.0
                && Math.abs(player.getXRot() - want[1]) < 2.0;
        if (!aimed && !targetGone) {
            // Re-aim on drift: the initial turn may have CONVERGED and finished,
            // then something else rotated the view afterwards (Baritone still
            // finishing its approach was the real-machine case) - without a
            // live turn the dig would sit in AIMING forever. Issue a fresh turn
            // whenever the current one is finished or aims at a stale rotation.
            TurnController.Turn turn = dig.turn;
            if (turn == null || turn.isFinished() || !turn.matchesTarget(want[0], want[1], 1.0)) {
                dig.turn = TurnController.begin(want[0], want[1], DigContracts.DIG_AIM_TURN_SPEED_DEG_S);
            }
        }
        boolean hitOnOccluder = mc.hitResult instanceof BlockHitResult hit
                && !hit.getBlockPos().equals(dig.target);
        boolean stageActive = mc.gameMode != null && mc.gameMode.getDestroyStage() >= 0;

        DigContracts.Verdict verdict = dig.monitor.tick(new DigContracts.TickView(
                aimed, targetGone, hitOnOccluder, stageActive, dig.elapsedMs()));
        switch (verdict.action()) {
            case WAIT -> {
                // hold stays as-is (not yet pressing while aiming / verifying an insta press)
            }
            case PRESS -> pressStart(mc, dig);
            case HOLD -> holdContinue(mc, dig); // per-tick destroy advance (vanilla continueAttack's statement)
            case UNPRESS, FINISH -> {
                release(mc);
                if (verdict.action() == DigContracts.Action.FINISH) {
                    dig.complete(verdict.result(), verdict.viaOccluder(), null);
                    active = null;
                }
            }
        }
    }

    // ------------------------------------------------------------------ action-layer hold

    /**
     * The block currently under the attack hold: the live crosshair hit when
     * it is a solid non-target block (the occluder being chewed), else the
     * dig target itself. Direction is the face the eye looks at it from
     * (vanilla uses the BlockHitResult's face; the nearest-axis direction of
     * eye->center is the same answer without needing a live raycast).
     */
    private static BlockHitResult currentHit(Minecraft mc, ActiveDig dig) {
        if (mc.hitResult instanceof BlockHitResult hit
                && !mc.level.getBlockState(hit.getBlockPos()).isAir()) {
            return hit;
        }
        LocalPlayer player = mc.player;
        Vec3 eye = player.getEyePosition();
        BlockPos target = dig.target;
        Vec3 center = new Vec3(target.getX() + 0.5, target.getY() + 0.5, target.getZ() + 0.5);
        Direction face = Direction.getNearest(
                center.x - eye.x, center.y - eye.y, center.z - eye.z).getOpposite();
        return new BlockHitResult(center, face, target, false);
    }

    /** Starts the held destroy (vanilla startAttack's gameMode statement) + hand swing. */
    private static void pressStart(Minecraft mc, ActiveDig dig) {
        if (mc.gameMode == null) {
            return;
        }
        BlockHitResult hit = currentHit(mc, dig);
        mc.gameMode.startDestroyBlock(hit.getBlockPos(), hit.getDirection());
        mc.player.swing(InteractionHand.MAIN_HAND);
    }

    /** Advances the held destroy one tick (vanilla continueAttack's gameMode statement). */
    private static void holdContinue(Minecraft mc, ActiveDig dig) {
        if (mc.gameMode == null) {
            return;
        }
        BlockHitResult hit = currentHit(mc, dig);
        if (mc.gameMode.continueDestroyBlock(hit.getBlockPos(), hit.getDirection())) {
            mc.player.swing(InteractionHand.MAIN_HAND);
        }
    }

    /** Stops the held destroy (progress resets server-side, like vanilla). */
    private static void release(Minecraft mc) {
        if (mc.gameMode != null) {
            mc.gameMode.stopDestroyBlock();
        }
    }

    /** The eye->block-center rotation the dig aims with ({yaw, pitch}). */
    private static double[] aimRotation(LocalPlayer player, BlockPos target) {
        Vec3 eye = player.getEyePosition();
        return LookContracts.rotationTowards(
                eye.x, eye.y, eye.z, target.getX() + 0.5, target.getY() + 0.5, target.getZ() + 0.5);
    }

    // ------------------------------------------------------------------ helpers

    /** The {@code -32012} denial + INPUT_DENIED audit line (mirrors the input.* preamble). */
    private static JsonObject permissionDenied(ToolContext ctx, InputGuard guard,
                                               String method, String detail, Boolean screenOpen) {
        ctx.audit("INPUT_DENIED", "reason=permission tier=" + guard.tier().configName()
                + " method=" + method + " " + detail
                + (screenOpen != null ? " screen=" + screenOpen : ""));
        boolean screen = screenOpen != null && screenOpen;
        return Json.errorResponse(ctx.id(), Json.PERMISSION_DENIED,
                PermissionContracts.deniedMessage(guard.tier(), screen, PermissionContracts.Action.INPUT), null);
    }
}
