package io.sirius.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

/**
 * Frozen-contract logic for the M3.5 v1.2 {@code dig} tool: parameter
 * validation, the pure per-tick monitor state machine and response assembly.
 * Pure Gson + JDK only - no Minecraft classes - so the whole decision logic
 * is covered by the in-process smoke test ({@code SmokeMain}) without a game.
 *
 * <p>The monitor encodes the "smart hold" discipline distilled from the
 * M3.5-T5a real-machine lessons (and Numen's ExecHarness telemetry rules):
 * <ul>
 *   <li><b>Main signal</b> - the target block's state changed (air or a
 *       different registry id) - is the ONLY broken/not-broken truth.</li>
 *   <li><b>Occluder tolerance (hysteresis)</b> - the crosshair hitting some
 *       other block NEVER fails the dig: we are chewing through an occluder
 *       (leaves in front of a trunk); it breaks, then the target follows.
 *       Jitter of the hit block is likewise ignored.</li>
 *   <li><b>Telemetry early-stop</b> - the destroy-stage signal (vanilla
 *       {@code getDestroyStage()}, crack 0-9 / -1 idle) staying -1 for 40
 *       consecutive pressing ticks means "not actually digging" (misaligned,
 *       out of sync) - release instead of pressing into nothing.</li>
 *   <li><b>Insta-mine</b> - a target whose per-tick destroy progress is
 *       already 1.0 (grass, some leaves) breaks the instant the press lands;
 *       hold 3 ticks then release so the block BEHIND it survives.</li>
 * </ul>
 */
public final class DigContracts {

    private DigContracts() {
    }

    // ------------------------------------------------------------------ params

    /** Default/{@code min}/{@code max} for {@code dig.timeout_ms} (schema v1.2). */
    public static final int DEFAULT_TIMEOUT_MS = 15_000;
    public static final int MIN_TIMEOUT_MS = 600;
    public static final int MAX_TIMEOUT_MS = 30_000;

    /** Dig reach: eye position to the target block CENTER (vanilla survival 4.5). */
    public static final double REACH = 4.5;

    /**
     * The fixed angular speed {@code dig} aims its target with (deg/s) -
     * "转头 300 deg/s" per the T6 ruling: human head saccade-ish, 90 deg in
     * 0.3 s, a full 180 deg in 0.6 s - fast enough not to eat the dig budget,
     * slow enough to look natural next to M2-D's instant snaps.
     */
    public static final double DIG_AIM_TURN_SPEED_DEG_S = 300.0;

    /**
     * M3.6 T3: after a {@code broken} verdict, wait this many ticks before
     * scanning for drop entities (50 ms/tick). The server spawns the item
     * entities the tick the block breaks; the client sees them only after the
     * spawn packet round trip (~1-4 ticks), so a few ticks of slack makes the
     * empirical drop report reliable without dragging the RPC out (20 ticks =
     * 1 s on top of a hold that already takes seconds; a {@code timeout}
     * verdict does NOT pay this cost).
     */
    public static final int DROPS_WAIT_TICKS = 20;

    /**
     * M3.6 T3: item entities within this radius of the dug block CENTER count
     * as its drops (matches the brain-side DROP_NEAR_DIG_DIST etiquette - a
     * matching drop 4+ blocks from the dig point may be someone else's).
     */
    public static final double DROPS_SCAN_RADIUS = 4.0;

    /** Validated {@code dig} params: integer block position + optional timeout_ms. */
    public record DigParams(int x, int y, int z, int timeoutMs) {
    }

    /**
     * Validates {@code dig} params per the frozen schema: REQUIRED integer
     * {@code x}/{@code y}/{@code z} (block coordinates; 3.0 accepted, 3.5
     * rejected) and optional integer {@code timeout_ms} 600..30000
     * (default 15000).
     */
    public static DigParams digParams(JsonObject params) throws ToolContracts.InvalidParams {
        return new DigParams(
                blockCoord(params, "x"),
                blockCoord(params, "y"),
                blockCoord(params, "z"),
                timeoutMs(params));
    }

    private static int blockCoord(JsonObject params, String member) throws ToolContracts.InvalidParams {
        JsonElement e = params.get(member);
        if (e == null || e.isJsonNull() || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) {
            throw new ToolContracts.InvalidParams("dig requires integer block coordinate " + member);
        }
        double v = e.getAsDouble();
        if (!Double.isFinite(v) || v != Math.floor(v)) {
            throw new ToolContracts.InvalidParams("dig " + member + " must be an integer block coordinate, got: " + v);
        }
        if (v < -30_000_000.0 || v > 30_000_000.0) {
            throw new ToolContracts.InvalidParams("dig " + member + " is outside the world border: " + (long) v);
        }
        return (int) v;
    }

    private static int timeoutMs(JsonObject params) throws ToolContracts.InvalidParams {
        JsonElement e = params.get("timeout_ms");
        if (e == null || e.isJsonNull()) {
            return DEFAULT_TIMEOUT_MS;
        }
        if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) {
            throw new ToolContracts.InvalidParams("dig timeout_ms must be an integer "
                    + MIN_TIMEOUT_MS + ".." + MAX_TIMEOUT_MS);
        }
        double v = e.getAsDouble();
        if (!Double.isFinite(v) || v != Math.floor(v)) {
            throw new ToolContracts.InvalidParams("dig timeout_ms must be an integer "
                    + MIN_TIMEOUT_MS + ".." + MAX_TIMEOUT_MS + ", got: " + v);
        }
        if (v < MIN_TIMEOUT_MS || v > MAX_TIMEOUT_MS) {
            throw new ToolContracts.InvalidParams("dig timeout_ms must be within "
                    + MIN_TIMEOUT_MS + ".." + MAX_TIMEOUT_MS + ", got: " + (long) v);
        }
        return (int) v;
    }

    // ------------------------------------------------------------------ results

    /** The {@code result} vocabulary; the brain maps each to a teaching phrase. */
    public static final String RESULT_BROKEN = "broken";
    public static final String RESULT_ALREADY_AIR = "already_air";
    public static final String RESULT_TIMEOUT = "timeout";
    public static final String RESULT_NOT_DIGGING = "not_digging";
    public static final String RESULT_BLOCKED_LIQUID = "blocked_liquid";
    public static final String RESULT_BLOCKED_FALLING = "blocked_falling";

    /**
     * Assembles the {@code dig} response:
     * {@code {"in_game":true,"result":...,"block":...,"elapsed_ms":...}} plus
     * {@code "broken_via_occluder":true} (target broke after the crosshair
     * had been chewing a DIFFERENT block - leaves in front of a trunk) and
     * {@code "reason"} for the {@code blocked_*} refusals. {@code block} is
     * JSON null for {@code already_air}. M3.6: {@code drops} (non-null only)
     * attaches the empirically observed drop list to a {@code broken} verdict.
     */
    public static JsonObject digResult(String result, String block, long elapsedMs,
                                        Boolean viaOccluder, String reason) {
        return digResult(result, block, elapsedMs, viaOccluder, reason, null);
    }

    /** Full assembly incl. the M3.6 {@code drops} array (null = omit the member). */
    public static JsonObject digResult(String result, String block, long elapsedMs,
                                        Boolean viaOccluder, String reason,
                                        java.util.List<JsonObject> drops) {
        JsonObject result_ = new JsonObject();
        result_.addProperty("in_game", true);
        result_.addProperty("result", result);
        if (block != null) {
            result_.addProperty("block", block);
        } else {
            result_.add("block", com.google.gson.JsonNull.INSTANCE); // JSON null for already_air
        }
        result_.addProperty("elapsed_ms", elapsedMs);
        if (viaOccluder != null && viaOccluder) {
            result_.addProperty("broken_via_occluder", true);
        }
        if (reason != null) {
            result_.addProperty("reason", reason);
        }
        if (drops != null) {
            JsonArray drops_ = new JsonArray();
            drops.forEach(drops_::add);
            result_.add("drops", drops_);
        }
        return result_;
    }

    // ------------------------------------------------------------------ empirical drops (M3.6 T3)

    /**
     * Aggregates the dig's empirical drop report: item facts that (a) are
     * item entities (registry id present), (b) sit within {@code radius} of
     * the dug block center, and (c) were NOT already there when the dig
     * started (uuid snapshot diff - a neighbouring player's drop must not
     * ride along on our report). Counts sum per registry id, order = first
     * appearance. Pure so the smoke test covers it without a game.
     */
    public static java.util.List<JsonObject> aggregateDrops(java.util.List<ToolContracts.EntityFact> facts,
                                                            java.util.Set<String> seenBefore,
                                                            double cx, double cy, double cz,
                                                            double radius) {
        double maxDistSq = radius * radius;
        java.util.LinkedHashMap<String, Integer> sums = new java.util.LinkedHashMap<>();
        for (ToolContracts.EntityFact fact : facts) {
            if (fact.item() == null || seenBefore.contains(fact.uuid())) {
                continue;
            }
            double dx = fact.x() - cx;
            double dy = fact.y() - cy;
            double dz = fact.z() - cz;
            if (dx * dx + dy * dy + dz * dz > maxDistSq) {
                continue;
            }
            sums.merge(fact.item(), Math.max(1, fact.count()), Integer::sum);
        }
        java.util.List<JsonObject> drops = new java.util.ArrayList<>();
        for (java.util.Map.Entry<String, Integer> entry : sums.entrySet()) {
            JsonObject drop = new JsonObject();
            drop.addProperty("item", entry.getKey());
            drop.addProperty("count", entry.getValue());
            drops.add(drop);
        }
        return drops;
    }

    // ------------------------------------------------------------------ monitor state machine

    /** Telemetry early-stop: destroy stage -1 for this many pressing ticks -> not_digging. */
    public static final int NOT_DIGGING_TICKS = 40;

    /**
     * Aim stall guard: if the view never converges on the target within this
     * many aiming ticks (5 s - something else owns the rotation, or the math
     * disagrees), fail fast with {@code not_digging} instead of burning the
     * whole timeout in WAIT (real-machine lesson: an aim that never lands
     * surfaced as a bare timeout, which teaches nothing).
     */
    public static final int AIM_STALL_TICKS = 100;

    /** Insta-mine: hold the press this many ticks, then release (protects the block behind). */
    public static final int INSTA_PRESS_TICKS = 3;

    /** Insta-mine: after releasing, wait this many ticks for the break before re-pressing. */
    public static final int INSTA_VERIFY_TICKS = 10;

    /** Insta-mine: at most this many short presses before giving up (not_digging). */
    public static final int INSTA_MAX_PRESSES = 3;

    /** What the shell should do with the mouse button this tick. */
    public enum Action {
        /** Aiming not done yet; button stays up, keep waiting. */
        WAIT,
        /** Press the attack button now (transition aiming -> breaking). */
        PRESS,
        /** Button is already down and the dig continues. */
        HOLD,
        /** Release the button but KEEP monitoring (insta-mine verification). */
        UNPRESS,
        /** Terminal: release the button and finish with {@code result}. */
        FINISH
    }

    /** One tick's monitor verdict: an action plus the terminal result (null unless FINISH). */
    public record Verdict(Action action, String result, boolean viaOccluder) {
        static Verdict of(Action action) {
            return new Verdict(action, null, false);
        }
    }

    /** The per-tick observation the shell extracts on the main thread. */
    public record TickView(boolean aimed, boolean targetGone, boolean hitOnOccluder,
                           boolean stageActive, long elapsedMs) {
    }

    /**
     * The pure dig monitor. Feed it one {@link TickView} per client tick
     * (main thread) and act on the returned {@link Verdict}; when it answers
     * {@link Action#FINISH}, the result string (one of
     * broken/timeout/not_digging) plus {@code viaOccluder()} complete the
     * tool response.
     *
     * <p>State machine: AIMING (button up, the smooth turn converges) ->
     * BREAKING (button down) [-> INSTA_VERIFY (button up, watching an
     * insta-mine press land) -> BREAKING ...] -> terminal.
     */
    public static final class DigMonitor {

        private enum Phase { AIMING, BREAKING, INSTA_VERIFY }

        private final int timeoutMs;
        private final boolean instaMine;
        private Phase phase = Phase.AIMING;
        private int notDiggingStreak;
        private int aimingTicks;
        private int instaPressTicks;
        private int instaVerifyTicks;
        private int instaPresses;
        private boolean sawOccluder;

        /** @param instaMine target destroy progress >= 1.0 per tick (breaks on press) */
        public DigMonitor(int timeoutMs, boolean instaMine) {
            this.timeoutMs = timeoutMs;
            this.instaMine = instaMine;
        }

        public boolean sawOccluder() {
            return sawOccluder;
        }

        /** One tick; pure except for its own counters - fully smoke-testable. */
        public Verdict tick(TickView view) {
            if (view.targetGone()) {
                // The main signal outranks everything - even mid-aim (someone
                // else broke it / piston moved it away): idempotent success.
                return new Verdict(Action.FINISH, RESULT_BROKEN, sawOccluder);
            }
            if (view.elapsedMs() >= timeoutMs) {
                return new Verdict(Action.FINISH, RESULT_TIMEOUT, sawOccluder);
            }
            switch (phase) {
                case AIMING:
                    if (!view.aimed()) {
                        if (++aimingTicks >= AIM_STALL_TICKS) {
                            return new Verdict(Action.FINISH, RESULT_NOT_DIGGING, sawOccluder);
                        }
                        return Verdict.of(Action.WAIT);
                    }
                    phase = Phase.BREAKING;
                    return Verdict.of(Action.PRESS);
                case BREAKING:
                    if (view.hitOnOccluder()) {
                        // Crosshair on a DIFFERENT solid block while pressing:
                        // we are chewing an occluder - NEVER a failure (Numen
                        // hysteresis lesson); it breaks, then the target
                        // follows. (Not tracked during AIMING: the sweeping
                        // crosshair legitimately crosses other blocks.)
                        sawOccluder = true;
                    }
                    if (!view.stageActive()) {
                        notDiggingStreak++;
                    } else {
                        notDiggingStreak = 0;
                    }
                    if (notDiggingStreak >= NOT_DIGGING_TICKS) {
                        return new Verdict(Action.FINISH, RESULT_NOT_DIGGING, sawOccluder);
                    }
                    if (instaMine) {
                        if (++instaPressTicks >= INSTA_PRESS_TICKS) {
                            phase = Phase.INSTA_VERIFY;
                            instaVerifyTicks = 0;
                            return Verdict.of(Action.UNPRESS);
                        }
                        return Verdict.of(Action.HOLD);
                    }
                    return Verdict.of(Action.HOLD);
                case INSTA_VERIFY:
                    if (view.hitOnOccluder()) {
                        sawOccluder = true;
                    }
                    if (++instaVerifyTicks >= INSTA_VERIFY_TICKS) {
                        if (instaPresses + 1 >= INSTA_MAX_PRESSES) {
                            return new Verdict(Action.FINISH, RESULT_NOT_DIGGING, sawOccluder);
                        }
                        instaPresses++;
                        phase = Phase.BREAKING;
                        instaPressTicks = 0;
                        return Verdict.of(Action.PRESS);
                    }
                    return Verdict.of(Action.WAIT);
                default:
                    return new Verdict(Action.FINISH, RESULT_NOT_DIGGING, sawOccluder);
            }
        }
    }
}
