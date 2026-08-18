package io.sirius.bridge;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.phys.Vec3;

/**
 * The M2-D look tools: {@code look({yaw, pitch})} and
 * {@code lookAt({x, y, z})} - absolute view rotation for the local player.
 * This class is the thin Minecraft-coupled shell; validation, the rotation
 * math and response shapes live in the pure {@link LookContracts} (covered
 * by the smoke test without a game).
 *
 * <p><b>Why the action layer and not the event layer (unlike M2-A):</b> view
 * rotation has no GLFW event-callback entry a human could produce "for" us -
 * humans move the mouse, which becomes a delta. Turning by delta
 * ({@code input.mouseMove}) requires the window to be focused AND the mouse
 * grabbed (M2-A2 finding), and composes deltas instead of setting an
 * absolute direction. {@code look} instead writes the rotation directly
 * ({@code setYRot}/{@code setXRot} + the {@code yRotO}/{@code xRotO}
 * interpolation fields, plus {@code setYHeadRot} - the exact statement
 * sequence of vanilla's own {@code Entity.lookAt(Anchor, Vec3)}, 1.21.1
 * sources). A LocalPlayer auto-syncs its rotation to the server next tick
 * via {@code LocalPlayer.sendPosition} (PosRot packets) - no packet work on
 * our side.
 *
 * <p><b>Guard rails:</b> the same {@link InputGuard} preamble as input.* -
 * {@code input_enabled = false} answers -32011 (looking around IS acting),
 * and the M2-D permission tier treats look as a world-level action (denied
 * under {@code observe} and {@code input_gui} with -32012). Look is
 * deliberately NOT charged against the input token bucket: it is a
 * one-field write that cannot flood the event pipeline, and M4 pathing will
 * legitimately aim at high rates without starving input.* calls.
 *
 * <p><b>Threading:</b} game reads/writes go through the shared
 * {@code PerceptionTools.callOnMainThread} latch (10 s timeout, -32603 when
 * the window is iconified); assembly and response building run on the
 * WebSocket thread.
 */
final class LookTools {

    private LookTools() {
    }

    /** Registers the two tools; called once from the BridgeServer constructor. */
    static void registerAll(ToolRegistry tools, InputGuard guard) {
        tools.register("look", (ctx, params) -> look(ctx, params, guard));
        tools.register("lookAt", (ctx, params) -> lookAt(ctx, params, guard));
    }

    // ------------------------------------------------------------------ look

    private static JsonObject look(ToolContext ctx, JsonObject params, InputGuard guard) throws Exception {
        final LookContracts.LookParams p;
        try {
            p = LookContracts.lookParams(params);
        } catch (ToolContracts.InvalidParams e) {
            return invalidParams(ctx, e);
        }
        if (!guard.enabled()) {
            ctx.audit("INPUT", summary("look", p.yaw() + "," + p.pitch()) + " result=input_disabled");
            return Json.errorResponse(ctx.id(), Json.INPUT_DISABLED, InputContracts.inputDisabledMessage(), null);
        }
        // Look is world-level: under observe/input_gui the tier alone decides the denial.
        if (PermissionContracts.deniedRegardlessOfScreen(guard.tier(), PermissionContracts.Action.LOOK)) {
            return permissionDenied(ctx, guard, "look", p.yaw() + "," + p.pitch(), false);
        }

        JsonObject result = PerceptionTools.callOnMainThread(ctx, () -> {
            LocalPlayer player = Minecraft.getInstance().player;
            if (player == null) {
                return LookContracts.notInGameLook(); // title screen etc.: not an error
            }
            float previousYaw = player.getYRot();
            float previousPitch = player.getXRot();
            applyRotation(player, p.yaw(), p.pitch());
            return LookContracts.lookResult(previousYaw, previousPitch, player.getYRot(), player.getXRot());
        });
        return Json.okResponse(ctx.id(), result);
    }

    // ------------------------------------------------------------------ lookAt

    private static JsonObject lookAt(ToolContext ctx, JsonObject params, InputGuard guard) throws Exception {
        final LookContracts.LookAtParams p;
        try {
            p = LookContracts.lookAtParams(params);
        } catch (ToolContracts.InvalidParams e) {
            return invalidParams(ctx, e);
        }
        if (!guard.enabled()) {
            ctx.audit("INPUT", summary("lookAt", p.x() + "," + p.y() + "," + p.z()) + " result=input_disabled");
            return Json.errorResponse(ctx.id(), Json.INPUT_DISABLED, InputContracts.inputDisabledMessage(), null);
        }
        if (PermissionContracts.deniedRegardlessOfScreen(guard.tier(), PermissionContracts.Action.LOOK)) {
            return permissionDenied(ctx, guard, "lookAt", p.x() + "," + p.y() + "," + p.z(), false);
        }

        JsonObject result = PerceptionTools.callOnMainThread(ctx, () -> {
            LocalPlayer player = Minecraft.getInstance().player;
            if (player == null) {
                return LookContracts.notInGameLook();
            }
            Vec3 eye = player.getEyePosition(); // (x, y + eyeHeight, z) - the EYES anchor
            double[] rotation = LookContracts.rotationTowards(
                    eye.x, eye.y, eye.z, p.x(), p.y(), p.z());
            applyRotation(player, rotation[0], rotation[1]);
            return LookContracts.lookAtResult(p.x(), p.y(), p.z(),
                    player.getYRot(), player.getXRot(), rotation[2]);
        });
        return Json.okResponse(ctx.id(), result);
    }

    // ------------------------------------------------------------------ helpers

    /**
     * Applies an absolute rotation the way vanilla {@code Entity.lookAt}
     * does: the setters (NaN-guarded), the interpolation fields kept in sync
     * (otherwise the next rendered frame smears from the old rotation) and
     * the head rotation so the body/head do not decouple for a frame. MUST
     * run on the client main thread.
     */
    private static void applyRotation(LocalPlayer player, double yaw, double pitch) {
        player.setYRot((float) yaw);
        player.setXRot((float) pitch);
        player.yRotO = player.getYRot();
        player.xRotO = player.getXRot();
        player.setYHeadRot(player.getYRot());
    }

    /** Maps a schema violation to the standard {@code -32602} response. */
    private static JsonObject invalidParams(ToolContext ctx, ToolContracts.InvalidParams e) {
        return Json.errorResponse(ctx.id(), Json.INVALID_PARAMS, e.getMessage(), null);
    }

    /** The {@code -32012} denial + INPUT_DENIED audit line (mirrors the input.* preamble). */
    private static JsonObject permissionDenied(ToolContext ctx, InputGuard guard,
                                               String method, String detail, boolean screenOpen) {
        ctx.audit("INPUT_DENIED", "reason=permission tier=" + guard.tier().configName()
                + " " + summary(method, detail) + " screen=" + screenOpen);
        return Json.errorResponse(ctx.id(), Json.PERMISSION_DENIED,
                PermissionContracts.deniedMessage(guard.tier(), screenOpen, PermissionContracts.Action.LOOK), null);
    }

    private static String summary(String method, String detail) {
        return "method=" + method + " " + detail;
    }
}
