package io.sirius.bridge;

import com.google.gson.JsonObject;
import com.mojang.blaze3d.platform.InputConstants;
import com.mojang.blaze3d.platform.Window;
import net.minecraft.client.KeyboardHandler;
import net.minecraft.client.Minecraft;
import net.minecraft.client.MouseHandler;
import net.neoforged.fml.loading.FMLPaths;
import org.lwjgl.glfw.GLFW;

import java.awt.image.BufferedImage;
import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

/**
 * The M2-A input tools: {@code input.key}, {@code input.text},
 * {@code input.mouseMove}, {@code input.click} - the "hands" of the bridge.
 *
 * <p><b>Injection principle (spec 8.2, risk-frontloaded):</b> every action
 * replays the exact GLFW event-callback entry points the real input pipeline
 * uses - {@code KeyboardHandler.keyPress} (public),
 * {@code KeyboardHandler.charTyped} / {@code MouseHandler.onPress} /
 * {@code MouseHandler.onMove} (private, reached via reflection so the full
 * vanilla+NeoForge dispatch - screen hooks, KeyMapping.set/click, view
 * turning, input-type tracking - runs verbatim). No game action methods
 * (openScreen, KeyMapping.click alone, ...) are called directly; a GUI click
 * is delivered as mouse PRESS/RELEASE at the handler's cursor position, a
 * key as PRESS (+ scheduled RELEASE), so anti-cheat-visible behaviour and
 * mod input handling are identical to a human at the keyboard.
 *
 * <p><b>Threading:</b> handlers run on a WebSocket thread; validation, rate
 * limiting, JPEG encoding and file writes happen there, while every event
 * callback invocation is scheduled onto the client main (render) thread via
 * {@link ToolContext#onMainThread} (a {@code Minecraft.execute} - exactly
 * how vanilla's own GLFW callbacks marshal onto the main thread, see
 * {@code KeyboardHandler.setup}). The handler blocks on a latch for the
 * FIRST event of a call only (10 s timeout); subsequent RELEASEs / repeated
 * clicks are scheduled on {@link #SCHEDULER} and submitted to
 * {@code Minecraft.execute} at their due time, so precise inter-event gaps
 * are not tied to request round-trips.
 *
 * <p><b>Guard rails (spec 8.2 security model):</b> a shared token bucket
 * limits all four tools combined to {@code rate_limit_per_sec} calls/s
 * (default 20) - over-rate calls answer error {@code -32010 rate_limited};
 * {@code input_enabled = false} disables all four (error {@code -32011});
 * the M2-D permission tier can deny all input.* ({@code observe}) or gate
 * them on the live screen state ({@code input_gui}/{@code input_world},
 * error {@code -32012} - the screen check runs INSIDE the main-thread task,
 * because the screen can change any tick); every call is audited to
 * {@code logs/sirius_bridge.log}; and a GUI click (a Screen being open)
 * first saves a small JPEG evidence shot to
 * {@code logs/sirius_evidence/} (config {@code gui_click_evidence}).
 */
final class InputTools {

    /** Upper bound for waiting on the client main thread (iconified window = tasks starve). */
    private static final long MAIN_THREAD_TIMEOUT_SECONDS = 10;

    /** Evidence JPEG settings (spec 7.1: small, low quality, on-disk proof). */
    private static final int EVIDENCE_QUALITY = 40;
    private static final int EVIDENCE_LONGEST_EDGE = 1024;
    private static final String EVIDENCE_DIR = "sirius_evidence";

    /** Schedules RELEASE events and click bursts off the request thread. */
    private static final ScheduledExecutorService SCHEDULER =
            Executors.newSingleThreadScheduledExecutor(r -> {
                Thread t = new Thread(r, "sirius-bridge-input-scheduler");
                t.setDaemon(true);
                return t;
            });

    // ---- reflective entry points (private in 1.21.1, semantics verified against decompiled sources) ----
    // MouseHandler.onPress(long window, int button, int action, int mods)
    private static final Method ON_MOUSE_PRESS = accessible(
            MouseHandler.class, "onPress", long.class, int.class, int.class, int.class);
    // MouseHandler.onMove(long window, double x, double y) - window client-area px, top-left origin
    private static final Method ON_MOUSE_MOVE = accessible(
            MouseHandler.class, "onMove", long.class, double.class, double.class);
    // KeyboardHandler.charTyped(long window, int codepoint, int mods) - true unicode path (incl. CJK)
    private static final Method CHAR_TYPED = accessible(
            KeyboardHandler.class, "charTyped", long.class, int.class, int.class);

    private InputTools() {
    }

    /** Registers the four tools; called once from the BridgeServer constructor. */
    static void registerAll(ToolRegistry tools, InputGuard guard) {
        tools.register("input.key", (ctx, params) -> key(ctx, params, guard));
        tools.register("input.text", (ctx, params) -> text(ctx, params, guard));
        tools.register("input.mouseMove", (ctx, params) -> mouseMove(ctx, params, guard));
        tools.register("input.click", (ctx, params) -> click(ctx, params, guard));
    }

    /** Stops the input scheduler (called from BridgeServer.shutdown). */
    static void shutdown() {
        SCHEDULER.shutdownNow();
    }

    // ------------------------------------------------------------------ input.key

    private static JsonObject key(ToolContext ctx, JsonObject params, InputGuard guard) throws Exception {
        final InputContracts.KeyParams p;
        try {
            p = InputContracts.keyParams(params);
        } catch (ToolContracts.InvalidParams e) {
            return invalidParams(ctx, e);
        }
        if (!guard.enabled()) {
            ctx.audit("INPUT", summary("input.key", p.keyName()) + " result=input_disabled");
            return Json.errorResponse(ctx.id(), Json.INPUT_DISABLED, InputContracts.inputDisabledMessage(), null);
        }
        // Tier veto that does not depend on the live screen state (observe):
        // deny pre-flight, before burning a rate-limit token.
        if (PermissionContracts.deniedRegardlessOfScreen(guard.tier(), PermissionContracts.Action.INPUT)) {
            return permissionDenied(ctx, guard, "input.key", p.keyName(), null);
        }
        if (!guard.tryAcquire()) {
            ctx.audit("INPUT", summary("input.key", p.keyName()) + " result=rate_limited");
            return Json.errorResponse(ctx.id(), Json.RATE_LIMITED,
                    InputContracts.rateLimitedMessage(guard.limitPerSecond()), null);
        }

        final long effectiveDurationMs = p.durationMs() > 0 ? p.durationMs() : InputContracts.DEFAULT_TAP_MS;
        final Object outcome = callOnMainThread(ctx, () -> {
            Minecraft mc = Minecraft.getInstance();
            // Screen-dependent tiers (input_gui/input_world) decide HERE - the
            // screen can change any tick, so the gate belongs inside the task.
            boolean screenOpen = mc.screen != null;
            if (!PermissionContracts.allows(guard.tier(), screenOpen, PermissionContracts.Action.INPUT)) {
                return new PermissionDeniedMarker(screenOpen);
            }
            int scancode = Math.max(0, GLFW.glfwGetKeyScancode(p.glfwKey()));
            // PRESS through the real callback: screen hooks -> KeyMapping.set/click -> NeoForge onKeyInput
            mc.keyboardHandler.keyPress(mc.getWindow().getWindow(), p.glfwKey(), scancode,
                    InputConstants.PRESS, p.modifiers());
            return Boolean.valueOf(screenOpen);
        });
        if (outcome instanceof PermissionDeniedMarker denied) {
            return permissionDenied(ctx, guard, "input.key", p.keyName(), denied.screenOpen());
        }
        final boolean screenOpen = (Boolean) outcome;

        scheduleKeyRelease(p, effectiveDurationMs);

        ctx.audit("INPUT", summary("input.key", p.keyName()) + " glfw=" + p.glfwKey()
                + " mods=" + p.modifiers() + " duration_ms=" + effectiveDurationMs + " screen=" + screenOpen
                + " result=ok");
        return Json.okResponse(ctx.id(), InputContracts.keyResult(
                p.keyName(), p.glfwKey(), effectiveDurationMs, p.modifiers(), screenOpen));
    }

    /** Queues the RELEASE {@code durationMs} after the PRESS (submitted to the main thread at due time). */
    private static void scheduleKeyRelease(InputContracts.KeyParams p, long durationMs) {
        SCHEDULER.schedule(() -> {
            try {
                Minecraft.getInstance().execute(() -> {
                    try {
                        Minecraft mc = Minecraft.getInstance();
                        int scancode = Math.max(0, GLFW.glfwGetKeyScancode(p.glfwKey()));
                        mc.keyboardHandler.keyPress(mc.getWindow().getWindow(), p.glfwKey(), scancode,
                                InputConstants.RELEASE, 0);
                    } catch (Throwable t) {
                        SiriusBridge.LOGGER.debug("sirius-bridge: key release dispatch failed: {}", t.toString());
                    }
                });
            } catch (Throwable t) {
                SiriusBridge.LOGGER.debug("sirius-bridge: key release scheduling failed: {}", t.toString());
            }
        }, durationMs, TimeUnit.MILLISECONDS);
    }

    // ------------------------------------------------------------------ input.text

    private static JsonObject text(ToolContext ctx, JsonObject params, InputGuard guard) throws Exception {
        final InputContracts.TextParams p;
        try {
            p = InputContracts.textParams(params);
        } catch (ToolContracts.InvalidParams e) {
            return invalidParams(ctx, e);
        }
        if (!guard.enabled()) {
            ctx.audit("INPUT", summary("input.text", p.codepoints() + "cp") + " result=input_disabled");
            return Json.errorResponse(ctx.id(), Json.INPUT_DISABLED, InputContracts.inputDisabledMessage(), null);
        }
        if (PermissionContracts.deniedRegardlessOfScreen(guard.tier(), PermissionContracts.Action.INPUT)) {
            return permissionDenied(ctx, guard, "input.text", p.codepoints() + "cp", null);
        }
        if (!guard.tryAcquire()) {
            ctx.audit("INPUT", summary("input.text", p.codepoints() + "cp") + " result=rate_limited");
            return Json.errorResponse(ctx.id(), Json.RATE_LIMITED,
                    InputContracts.rateLimitedMessage(guard.limitPerSecond()), null);
        }
        if (CHAR_TYPED == null) {
            return Json.errorResponse(ctx.id(), Json.INTERNAL_ERROR,
                    "KeyboardHandler.charTyped is not reachable on this client", null);
        }

        final String text = p.text();
        final Object outcome = callOnMainThread(ctx, () -> {
            Minecraft mc = Minecraft.getInstance();
            boolean screenOpen = mc.screen != null;
            if (!PermissionContracts.allows(guard.tier(), screenOpen, PermissionContracts.Action.INPUT)) {
                return new PermissionDeniedMarker(screenOpen);
            }
            // charTyped only reaches an open Screen (chat/sign/search fields); overlay blocks it too.
            if (mc.screen == null || mc.getOverlay() != null) {
                return Integer.valueOf(0);
            }
            long window = mc.getWindow().getWindow();
            text.codePoints().forEachOrdered(codepoint ->
                    dispatch(CHAR_TYPED, mc.keyboardHandler, window, codepoint, 0));
            return Integer.valueOf(text.codePointCount(0, text.length()));
        });
        if (outcome instanceof PermissionDeniedMarker denied) {
            return permissionDenied(ctx, guard, "input.text", p.codepoints() + "cp", denied.screenOpen());
        }
        final int delivered = (Integer) outcome;

        ctx.audit("INPUT", summary("input.text", p.codepoints() + "cp") + " delivered=" + delivered
                + " result=" + (delivered == p.codepoints() ? "ok" : "no_screen"));
        return Json.okResponse(ctx.id(), InputContracts.textResult(delivered, p.codepoints(), delivered > 0));
    }

    // ------------------------------------------------------------------ input.mouseMove

    private static JsonObject mouseMove(ToolContext ctx, JsonObject params, InputGuard guard) throws Exception {
        final InputContracts.MouseMoveParams p;
        try {
            p = InputContracts.mouseMoveParams(params);
        } catch (ToolContracts.InvalidParams e) {
            return invalidParams(ctx, e);
        }
        if (!guard.enabled()) {
            ctx.audit("INPUT", summary("input.mouseMove", p.x() + "," + p.y()) + " result=input_disabled");
            return Json.errorResponse(ctx.id(), Json.INPUT_DISABLED, InputContracts.inputDisabledMessage(), null);
        }
        if (PermissionContracts.deniedRegardlessOfScreen(guard.tier(), PermissionContracts.Action.INPUT)) {
            return permissionDenied(ctx, guard, "input.mouseMove", p.x() + "," + p.y(), null);
        }
        if (!guard.tryAcquire()) {
            ctx.audit("INPUT", summary("input.mouseMove", p.x() + "," + p.y()) + " result=rate_limited");
            return Json.errorResponse(ctx.id(), Json.RATE_LIMITED,
                    InputContracts.rateLimitedMessage(guard.limitPerSecond()), null);
        }
        if (ON_MOUSE_MOVE == null) {
            return Json.errorResponse(ctx.id(), Json.INTERNAL_ERROR,
                    "MouseHandler.onMove is not reachable on this client", null);
        }

        final Object outcome = callOnMainThread(ctx, () -> {
            Minecraft mc = Minecraft.getInstance();
            boolean screenOpen = mc.screen != null;
            if (!PermissionContracts.allows(guard.tier(), screenOpen, PermissionContracts.Action.INPUT)) {
                return new PermissionDeniedMarker(screenOpen);
            }
            Window window = mc.getWindow();
            // clamp to the client area - a human cursor cannot be outside it
            double cx = Math.max(0.0, Math.min(p.x(), window.getScreenWidth()));
            double cy = Math.max(0.0, Math.min(p.y(), window.getScreenHeight()));
            dispatch(ON_MOUSE_MOVE, mc.mouseHandler, window.getWindow(), cx, cy);
            // the GUI coordinate the game derives from this position (same formula as MouseHandler.onPress)
            double guiX = cx * window.getGuiScaledWidth() / window.getScreenWidth();
            double guiY = cy * window.getGuiScaledHeight() / window.getScreenHeight();
            return new double[]{cx, cy, guiX, guiY, screenOpen ? 1 : 0};
        });
        if (outcome instanceof PermissionDeniedMarker denied) {
            return permissionDenied(ctx, guard, "input.mouseMove", p.x() + "," + p.y(), denied.screenOpen());
        }
        final double[] moved = (double[]) outcome;

        ctx.audit("INPUT", summary("input.mouseMove", p.x() + "," + p.y()) + " delivered="
                + (int) moved[0] + "," + (int) moved[1] + " screen=" + (moved[4] > 0) + " result=ok");
        return Json.okResponse(ctx.id(), InputContracts.mouseMoveResult(
                moved[0], moved[1], moved[2], moved[3], moved[4] > 0));
    }

    // ------------------------------------------------------------------ input.click

    private static JsonObject click(ToolContext ctx, JsonObject params, InputGuard guard) throws Exception {
        final InputContracts.ClickParams p;
        try {
            p = InputContracts.clickParams(params);
        } catch (ToolContracts.InvalidParams e) {
            return invalidParams(ctx, e);
        }
        if (!guard.enabled()) {
            ctx.audit("INPUT", summary("input.click", "btn" + p.button()) + " result=input_disabled");
            return Json.errorResponse(ctx.id(), Json.INPUT_DISABLED, InputContracts.inputDisabledMessage(), null);
        }
        if (PermissionContracts.deniedRegardlessOfScreen(guard.tier(), PermissionContracts.Action.INPUT)) {
            return permissionDenied(ctx, guard, "input.click", "btn" + p.button(), null);
        }
        if (!guard.tryAcquire()) {
            ctx.audit("INPUT", summary("input.click", "btn" + p.button()) + " result=rate_limited");
            return Json.errorResponse(ctx.id(), Json.RATE_LIMITED,
                    InputContracts.rateLimitedMessage(guard.limitPerSecond()), null);
        }
        if (ON_MOUSE_PRESS == null) {
            return Json.errorResponse(ctx.id(), Json.INTERNAL_ERROR,
                    "MouseHandler.onPress is not reachable on this client", null);
        }

        // One main-thread task: permission gate, evidence grab (Screen open +
        // evidence on) then the first PRESS.
        final Object outcome = callOnMainThread(ctx, () -> {
            Minecraft mc = Minecraft.getInstance();
            boolean screenOpen = mc.screen != null;
            if (!PermissionContracts.allows(guard.tier(), screenOpen, PermissionContracts.Action.INPUT)) {
                return new PermissionDeniedMarker(screenOpen);
            }
            long window = mc.getWindow().getWindow();
            BufferedImage shot = null;
            String screenName = null;
            if (screenOpen) {
                screenName = mc.screen.getClass().getSimpleName();
                if (guard.guiClickEvidence()) {
                    shot = PerceptionTools.grabScreen(); // render thread: pixel download only
                }
            }
            dispatch(ON_MOUSE_PRESS, mc.mouseHandler, window, p.button(), InputConstants.PRESS, 0);
            return new ClickStart(shot, screenName);
        });
        if (outcome instanceof PermissionDeniedMarker denied) {
            return permissionDenied(ctx, guard, "input.click", "btn" + p.button(), denied.screenOpen());
        }
        final ClickStart start = (ClickStart) outcome;

        Evidence evidence = saveEvidence(start, p);

        // RELEASE of the first press at +CLICK_HOLD_MS; further clicks every CLICK_INTERVAL_MS.
        for (int i = 0; i < p.count(); i++) {
            final long pressAt = i * InputContracts.CLICK_INTERVAL_MS;
            final long releaseAt = i * InputContracts.CLICK_INTERVAL_MS + InputContracts.CLICK_HOLD_MS;
            if (i > 0) {
                SCHEDULER.schedule(() -> submitMouse(p.button(), InputConstants.PRESS), pressAt, TimeUnit.MILLISECONDS);
            }
            SCHEDULER.schedule(() -> submitMouse(p.button(), InputConstants.RELEASE), releaseAt, TimeUnit.MILLISECONDS);
        }

        ctx.audit("INPUT", summary("input.click", "btn" + p.button()) + " count=" + p.count()
                + " screen=" + start.screenName()
                + (evidence.file() != null ? " evidence=" + evidence.file() : "")
                + " result=ok");
        return Json.okResponse(ctx.id(), InputContracts.clickResult(
                p.button(), p.count(), start.screenName() != null, start.screenName(),
                evidence.file(), evidence.bytes()));
    }

    /** Queues a mouse button event onto the main thread (best effort). */
    private static void submitMouse(int button, int action) {
        try {
            Minecraft.getInstance().execute(() -> {
                try {
                    Minecraft mc = Minecraft.getInstance();
                    dispatch(ON_MOUSE_PRESS, mc.mouseHandler, mc.getWindow().getWindow(), button, action, 0);
                } catch (Throwable t) {
                    SiriusBridge.LOGGER.debug("sirius-bridge: mouse event dispatch failed: {}", t.toString());
                }
            });
        } catch (Throwable t) {
            SiriusBridge.LOGGER.debug("sirius-bridge: mouse event scheduling failed: {}", t.toString());
        }
    }

    /**
     * Invokes one of the reflective GLFW-callback entry points on the current
     * (main) thread; access problems and callback failures surface as
     * {@link IllegalStateException} for the dispatcher's {@code -32603} path.
     */
    private static void dispatch(Method method, Object target, Object... args) {
        try {
            method.invoke(target, args);
        } catch (IllegalAccessException e) {
            throw new IllegalStateException("input callback not accessible: " + method.getName(), e);
        } catch (InvocationTargetException e) {
            throw new IllegalStateException("input callback " + method.getName() + " threw", e.getCause());
        }
    }

    /** First-press outcome: optional evidence shot pixels + the open screen's class name. */
    private record ClickStart(BufferedImage shot, String screenName) {
    }

    private record Evidence(String file, long bytes) {
        static final Evidence NONE = new Evidence(null, 0);
    }

    /** Encodes + writes the evidence JPEG on the WS thread; failures never fail the click. */
    private static Evidence saveEvidence(ClickStart start, InputContracts.ClickParams p) {
        if (start.shot() == null) {
            return Evidence.NONE;
        }
        try {
            BufferedImage small = ImageOps.scaleLongestEdge(start.shot(), EVIDENCE_LONGEST_EDGE);
            byte[] jpeg = ImageOps.encodeJpeg(small, EVIDENCE_QUALITY);
            long now = System.currentTimeMillis();
            String name = InputContracts.evidenceFileName("click", now);
            Path dir = FMLPaths.GAMEDIR.get().resolve("logs").resolve(EVIDENCE_DIR);
            Files.createDirectories(dir);
            Path file = dir.resolve(name);
            Files.write(file, jpeg);
            return new Evidence(name, jpeg.length);
        } catch (IOException | RuntimeException e) {
            SiriusBridge.LOGGER.warn("sirius-bridge: failed to save GUI click evidence: {}", e.toString());
            return Evidence.NONE;
        }
    }

    // ------------------------------------------------------------------ guard + helpers

    /**
     * Marker a main-thread task RETURNS (never throws - throwing would map to
     * -32603) when the permission tier vetoes the injection after the screen
     * state was read on the main thread. Carries that screen state for the
     * audit line / error message.
     */
    record PermissionDeniedMarker(boolean screenOpen) {
    }

    /**
     * The {@code -32012} denial: INPUT_DENIED audit line mirroring the other
     * guard outcomes plus the standard error frame. {@code screenOpen} is
     * {@code null} for pre-flight vetoes (observe tier) where the live screen
     * state was never read.
     */
    private static JsonObject permissionDenied(ToolContext ctx, InputGuard guard, String method,
                                               String detail, Boolean screenOpen) {
        ctx.audit("INPUT_DENIED", "reason=permission tier=" + guard.tier().configName()
                + " " + summary(method, detail)
                + (screenOpen != null ? " screen=" + screenOpen : ""));
        boolean screen = screenOpen != null && screenOpen;
        return Json.errorResponse(ctx.id(), Json.PERMISSION_DENIED,
                PermissionContracts.deniedMessage(guard.tier(), screen, PermissionContracts.Action.INPUT), null);
    }

    /** Makes a private callback reachable; returns null (and logs) when the signature is absent. */
    private static Method accessible(Class<?> type, String name, Class<?>... params) {
        try {
            Method method = type.getDeclaredMethod(name, params);
            method.setAccessible(true);
            return method;
        } catch (NoSuchMethodException e) {
            SiriusBridge.LOGGER.error("sirius-bridge: {}.{} not found - the input tool depending on it"
                    + " will answer -32603", type.getSimpleName(), name);
            return null;
        }
    }

    /** Maps a schema violation to the standard {@code -32602} response. */
    private static JsonObject invalidParams(ToolContext ctx, ToolContracts.InvalidParams e) {
        return Json.errorResponse(ctx.id(), Json.INVALID_PARAMS, e.getMessage(), null);
    }

    private static String summary(String method, String detail) {
        return "method=" + method + " " + detail;
    }

    /**
     * Runs {@code supplier} on the client main thread and blocks the calling
     * WS thread for its result. Times out (iconified window / shutdown)
     * instead of hanging; a failure inside the task propagates to the caller
     * (the dispatcher turns it into {@code -32603}).
     */
    private static <T> T callOnMainThread(ToolContext ctx, Supplier<T> supplier) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        Object[] box = new Object[2]; // [0] result, [1] failure
        ctx.onMainThread(() -> {
            try {
                box[0] = supplier.get();
            } catch (Throwable t) {
                box[1] = t;
            } finally {
                done.countDown();
            }
        });
        if (!done.await(MAIN_THREAD_TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
            throw new IllegalStateException("client main thread did not run the task within "
                    + MAIN_THREAD_TIMEOUT_SECONDS + "s (game iconified or shutting down?)");
        }
        if (box[1] != null) {
            if (box[1] instanceof Exception e) {
                throw e;
            }
            if (box[1] instanceof Error e) {
                throw e;
            }
            throw new IllegalStateException(String.valueOf(box[1]));
        }
        @SuppressWarnings("unchecked")
        T result = (T) box[0];
        return result;
    }

    /** Initialization sanity flag for tests/diagnostics. */
    static boolean reflectionReady() {
        return ON_MOUSE_PRESS != null && ON_MOUSE_MOVE != null && CHAR_TYPED != null;
    }
}
