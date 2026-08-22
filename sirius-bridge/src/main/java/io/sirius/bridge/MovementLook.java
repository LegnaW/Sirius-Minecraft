package io.sirius.bridge;

import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;

/**
 * M4.1 movement look ("moving should turn the head"): while the local player
 * is moving, turn the view toward the MOVEMENT DIRECTION at a fixed angular
 * speed - the head leads where the body actually goes, replacing the
 * fixed-camera strafing the user observed.
 *
 * <p><b>Why this exists (root-cause evidence):</b> during the M4 acceptance
 * 130-block Baritone walk the bridge audit log shows ZERO look/lookAt/turn
 * writes - nothing of ours touched the rotation, yet the camera never
 * followed the movement. Baritone (this build) drives WASD combinations
 * without rotating the view, so the body strafes sideways under a frozen
 * camera. The bridge is the right layer for the fix: rotation writes here
 * ({@link LookTools#applyRotation}) are focus-independent and already
 * server-synced.
 *
 * <p><b>Yield rules (no fight with anything that steers the view on
 * purpose):</b>
 * <ul>
 *   <li>An active {@link TurnController} turn (explicit smooth
 *       {@code lookAt}, the dig aim phase) always wins - movement look does
 *       not write a single tick while one is in flight.</li>
 *   <li>Below the speed threshold (standing, tiny nudges, pure vertical
 *       motion) - no write.</li>
 *   <li>Within the yaw deadzone of the movement heading - no write (so when
 *       Baritone DOES rotate, its target and ours coincide and the controller
 *       stays silent instead of fighting).</li>
 * </ul>
 *
 * <p>Only YAW is written - pitch stays wherever the last explicit look left
 * it (minimal intervention; the complaint was the frozen heading, and dig /
 * lookAt aiming must not be disturbed while walking).
 *
 * <p><b>Threading:</b> main thread only ({@code SiriusBridge}'s
 * {@code ClientTickEvent.Post} listener, right after
 * {@link TurnController#onClientTick()}); the decision math is pure
 * ({@link #nextYaw}) and smoke-tested without a game.
 */
final class MovementLook {

    private MovementLook() {
    }

    /** Fixed turn speed while moving (deg/s) - matches the dig aim default. */
    static final double SPEED_DEG_PER_SEC = 300.0;

    /**
     * Minimum horizontal speed to count as "moving" (blocks per tick).
     * Walking is ~0.21 b/t at 20 tps; 0.05 (=1 m/s) filters idle drift,
     * knockback wobble and pure vertical motion (falling, swimming up).
     */
    static final double MIN_SPEED = 0.05;

    /** Within this many degrees of the movement heading: no write (deadzone). */
    static final double DEADZONE_DEG = 2.0;

    /** ms per client tick - the step math assumes the vanilla 20 tps clock. */
    private static final double TICK_SECONDS = 0.050;

    /**
     * Per-tick advance hook; called from {@code SiriusBridge.onClientTick}.
     * No-op when disabled (config {@code movement_look = false}), when no
     * player exists, or when the yield rules say stay silent.
     */
    static void onClientTick(boolean enabled) {
        if (!enabled) {
            return;
        }
        LocalPlayer player = Minecraft.getInstance().player;
        if (player == null) {
            return;
        }
        if (TurnController.activeTurn() != null) {
            return; // an explicit turn owns the view this tick
        }
        var velocity = player.getDeltaMovement();
        Double yaw = nextYaw(player.getYRot(), velocity.x, velocity.z, SPEED_DEG_PER_SEC);
        if (yaw != null) {
            LookTools.applyYaw(player, yaw);
        }
    }

    /**
     * Pure decision: the yaw to write this tick, or {@code null} when the
     * controller must stay silent. Target heading is derived from the
     * horizontal velocity vector exactly like {@link LookContracts#rotationTowards}
     * (yaw 0 = +Z / south, atan2(dz, dx) - 90), the step is a fixed
     * {@code speedDegPerSec * 50ms} along the shortest signed difference.
     */
    static Double nextYaw(double currentYaw, double vx, double vz, double speedDegPerSec) {
        double horizontal = Math.sqrt(vx * vx + vz * vz);
        if (horizontal < MIN_SPEED) {
            return null;
        }
        double targetYaw = LookContracts.wrapDegrees(Math.toDegrees(Math.atan2(vz, vx)) - 90.0);
        double delta = LookContracts.yawDelta(currentYaw, targetYaw);
        if (Math.abs(delta) <= DEADZONE_DEG) {
            return null;
        }
        double step = speedDegPerSec * TICK_SECONDS;
        return LookContracts.approach(currentYaw, delta, step);
    }
}
