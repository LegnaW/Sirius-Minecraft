package io.sirius.bridge;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.player.LocalPlayer;
import net.neoforged.neoforge.client.event.ClientChatReceivedEvent;
import net.neoforged.neoforge.client.event.ScreenEvent;
import org.java_websocket.WebSocket;

import java.awt.image.BufferedImage;
import java.io.IOException;
import java.util.ArrayDeque;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * The M2-B event push channel: the single choke point every outbound
 * notification goes through. Game-coupled event sources (chat, GUI
 * open/close, the danger sampler, the screenshot stream) call
 * {@link #push}; delivery decisions - who subscribed, at what level, with
 * which per-connection {@code seq} - all live here so the semantics stay
 * in one place. Pure decisions (level filtering, wire shape, throttle)
 * live in {@link EventsContracts} and are smoke-tested.
 *
 * <p><b>Semantics (spec 8.2 + Python side, which M2-B does not touch):</b>
 * a client receives pushes ONLY after a successful {@code events.subscribe};
 * each frame is {@code {type:"notification", event, data(+level),
 * timestamp:<epoch seconds float>, seq:<per-connection, from 0>}}; the brain
 * warns on non-monotonic seq, so {@code seq} is assigned exactly once per
 * delivered frame via an atomic per-session counter.
 *
 * <p><b>Threading:</b> sources fire on the client main thread (NeoForge
 * events + the tick sampler) and on the internal events thread (screenshot
 * encode + delayed flush). {@code push} is thread-safe: the session map is
 * concurrent, subscriptions are volatile and {@link BridgeServer#sendFrame}
 * guards the connection. The screenshot stream follows the M1-C split - the
 * framebuffer read ({@link PerceptionTools#grabScreen}) runs on the client
 * main/render thread (from the tick sampler, where a complete previous frame
 * is in the render target), while JPEG encoding and base64 run on the events
 * thread so the render thread never pays for encoding.
 *
 * <p><b>Honest drop accounting:</b> a push to a connection that is already
 * closed counts as dropped (audit line + counter) instead of vanishing
 * silently; encoding failures on the stream abort that frame with a warn
 * log. Nothing here ever throws into the game.
 */
final class EventPusher {

    // ------------------------------------------------------------------ danger sampling

    /** Danger states are sampled ~1/s (every 20 ticks). */
    private static final int DANGER_SAMPLE_TICKS = 20;
    /** Per-type cooldown that suppresses flapping edges (e.g. fire on/off). */
    private static final long DANGER_COOLDOWN_MS = 5_000;
    /** health_low threshold (half a heart times three; spec leaves the number to us). */
    private static final float HEALTH_LOW_THRESHOLD = 6.0F;
    /** Drown = underwater AND air below the vanilla maximum of 300. */
    private static final int DROWN_AIR_THRESHOLD = 300;

    // ------------------------------------------------------------------ state

    private final BridgeServer server;
    /** Latest-wins throttle over assembled screenshot payloads. */
    private final EventsContracts.StreamThrottle<JsonObject> streamThrottle =
            new EventsContracts.StreamThrottle<>(EventsContracts.STREAM_MIN_INTERVAL_MS);
    /** Ring buffer of the last encoded stream frames (spec: 3, latest consumed). */
    private final ArrayDeque<JsonObject> streamRing = new ArrayDeque<>();
    /** Encodes stream frames and runs the delayed boundary flush (single thread -> serialized). */
    private final ScheduledExecutorService scheduler =
            Executors.newSingleThreadScheduledExecutor(r -> {
                Thread t = new Thread(r, "sirius-bridge-events");
                t.setDaemon(true);
                return t;
            });

    /** Tick counter for the 1 Hz stream + danger sampling. */
    private int tickCounter;
    /** Danger edge trackers: last observed state per type (edge = false -> true). */
    private final Map<String, Boolean> dangerActive = new HashMap<>();
    /** Danger cooldown trackers: last fire time per type. */
    private final Map<String, Long> dangerLastFiredMs = new HashMap<>();
    /** Honest accounting counters, reported on shutdown. */
    private final AtomicLong pushedCount = new AtomicLong();
    private final AtomicLong droppedCount = new AtomicLong();

    EventPusher(BridgeServer server) {
        this.server = server;
    }

    // ------------------------------------------------------------------ the emit choke point

    /**
     * Pushes an event to every authenticated session whose subscription
     * matches {@code (type, level)}. Each receiving session consumes exactly
     * one {@code seq} (atomic get-and-increment, first frame = 0) so the
     * brain's monotonicity check never trips on our side. Never throws.
     */
    void push(String type, EventsContracts.EventLevel level, JsonObject data) {
        double timestamp = EventsContracts.timestampNowSeconds(System.currentTimeMillis());
        for (Map.Entry<WebSocket, BridgeServer.ClientSession> entry : server.sessionsView().entrySet()) {
            BridgeServer.ClientSession session = entry.getValue();
            if (!session.authenticated) {
                continue;
            }
            EventsContracts.Subscription subscription = session.subscription;
            if (subscription == null || !subscription.matches(type, level)) {
                continue;
            }
            if (!entry.getKey().isOpen()) {
                // Honest accounting: the session map lags onClose briefly.
                droppedCount.incrementAndGet();
                server.audit().event("EVENT_DROP", "event=" + type + " reason=connection_closed");
                continue;
            }
            long seq = session.nextEventSeq();
            server.sendFrame(entry.getKey(),
                    EventsContracts.notification(type, data, level, timestamp, seq));
            pushedCount.incrementAndGet();
        }
    }

    /** True when at least one authenticated session subscribes to {@code (type, level)}. */
    boolean hasSubscriber(String type, EventsContracts.EventLevel level) {
        for (BridgeServer.ClientSession session : server.sessionsView().values()) {
            if (session.authenticated) {
                EventsContracts.Subscription subscription = session.subscription;
                if (subscription != null && subscription.matches(type, level)) {
                    return true;
                }
            }
        }
        return false;
    }

    // ------------------------------------------------------------------ NeoForge sources (main thread)

    /**
     * Chat lines (player + system) as INFO "chat" events. Player messages
     * carry the sender UUID; system lines (NIL_UUID sender) just say so.
     */
    void onChatReceived(ClientChatReceivedEvent event) {
        JsonObject data = new JsonObject();
        data.addProperty("message", event.getMessage().getString());
        boolean system = event.isSystem();
        data.addProperty("system", system);
        if (!system) {
            data.addProperty("sender", event.getSender().toString());
        }
        push("chat", EventsContracts.EventLevel.INFO, data);
    }

    /**
     * A screen is about to open (WARNING "gui_open"). NeoForge fires
     * {@link ScreenEvent.Opening} only for a non-null new screen
     * (Minecraft.setScreen, verified against 1.21.1 sources).
     */
    void onScreenOpening(ScreenEvent.Opening event) {
        Screen screen = event.getNewScreen();
        if (screen == null) {
            return; // defensive; setScreen(null) fires Closing, not Opening
        }
        JsonObject data = new JsonObject();
        data.addProperty("screen", screen.getClass().getSimpleName());
        push("gui_open", EventsContracts.EventLevel.WARNING, data);
    }

    /**
     * A screen was replaced or closed (WARNING "gui_close"). Fires on every
     * transition where an old screen existed and the new one differs -
     * including screen-to-screen switches (vanilla Closing semantics).
     */
    void onScreenClosing(ScreenEvent.Closing event) {
        JsonObject data = new JsonObject();
        data.addProperty("screen", event.getScreen().getClass().getSimpleName());
        push("gui_close", EventsContracts.EventLevel.WARNING, data);
    }

    // ------------------------------------------------------------------ tick sampler (main thread)

    /**
     * Per-tick hook wired from {@link SiriusBridge#onClientTick}: every
     * {@link #DANGER_SAMPLE_TICKS} ticks (~1 s) samples danger states and
     * grabs a stream screenshot (only while someone subscribes to it).
     * Runs on the client main/render thread.
     */
    void onClientTick() {
        if (++tickCounter < DANGER_SAMPLE_TICKS) {
            return;
        }
        tickCounter = 0;
        try {
            sampleDanger();
        } catch (RuntimeException e) {
            SiriusBridge.LOGGER.debug("sirius-bridge: danger sampling failed: {}", e.toString());
        }
        try {
            sampleStreamFrame();
        } catch (RuntimeException e) {
            SiriusBridge.LOGGER.debug("sirius-bridge: stream frame grab failed: {}", e.toString());
        }
    }

    /**
     * Edge-triggered danger detection: death / fire / health_low / drown as
     * CRITICAL events. An event fires only on the false->true transition AND
     * at most once per {@link #DANGER_COOLDOWN_MS} per type (flapping
     * suppression); leaving a world resets the edges so re-entering re-fires.
     */
    private void sampleDanger() {
        LocalPlayer player = Minecraft.getInstance().player;
        if (player == null) {
            dangerActive.clear();
            return;
        }
        long now = System.currentTimeMillis();
        float health = player.getHealth();
        int air = player.getAirSupply();
        boolean dead = player.isDeadOrDying();

        JsonObject deathData = new JsonObject();
        deathData.addProperty("health", health);
        deathData.addProperty("air", air);
        deathData.addProperty("on_fire", player.isOnFire());
        fireDanger("death", dead, now, deathData);

        JsonObject fireData = new JsonObject();
        fireData.addProperty("health", health);
        fireDanger("fire", !dead && player.isOnFire(), now, fireData);

        JsonObject lowData = new JsonObject();
        lowData.addProperty("health", health);
        lowData.addProperty("threshold", HEALTH_LOW_THRESHOLD);
        fireDanger("health_low", !dead && health <= HEALTH_LOW_THRESHOLD, now, lowData);

        JsonObject drownData = new JsonObject();
        drownData.addProperty("air", air);
        fireDanger("drown", !dead && player.isUnderWater() && air < DROWN_AIR_THRESHOLD, now, drownData);
    }

    /** One edge+cooldown slot of the danger sampler. */
    private void fireDanger(String type, boolean active, long nowMs, JsonObject data) {
        boolean wasActive = dangerActive.getOrDefault(type, Boolean.FALSE);
        if (active && !wasActive) {
            long lastFired = dangerLastFiredMs.getOrDefault(type, 0L);
            if (nowMs - lastFired >= DANGER_COOLDOWN_MS) {
                dangerLastFiredMs.put(type, nowMs);
                push(type, EventsContracts.EventLevel.CRITICAL, data);
            }
        }
        dangerActive.put(type, active);
    }

    // ------------------------------------------------------------------ screenshot stream

    /**
     * Grabs one frame for the stream (main/render thread - the render target
     * holds the previous complete frame here, same guarantee
     * {@code Minecraft.execute} tasks get) and hands it to the events thread
     * for encoding; only runs while at least one subscriber matches the
     * "screenshot" event, so an idle bridge costs nothing.
     */
    private void sampleStreamFrame() {
        if (!hasSubscriber(EventsContracts.STREAM_EVENT, EventsContracts.EventLevel.INFO)) {
            return;
        }
        BufferedImage frame = PerceptionTools.grabScreen(); // pixel download only, ~10-30ms
        scheduler.execute(() -> {
            try {
                onFrame(frame);
            } catch (Exception e) {
                SiriusBridge.LOGGER.warn("sirius-bridge: screenshot stream frame dropped: {}", e.toString());
            }
        });
    }

    /**
     * Encodes one sampled frame and runs it through the throttle
     * (events thread). Window open -> push now; window closed -> replace the
     * pending slot (latest wins) and arm one delayed flush at the boundary.
     * The payload rides the 100KB budget ladder; a frame is never dropped
     * for size reasons (ship-smallest fallback inside the ladder).
     */
    void onFrame(BufferedImage frame) throws IOException {
        ImageOps.Encoded encoded = ImageOps.encodeWithinBudget(frame, EventsContracts.STREAM_QUALITY,
                EventsContracts.STREAM_MAX_BASE64, EventsContracts.STREAM_LONGEST_EDGE);
        JsonObject data = new JsonObject();
        data.addProperty("image_b64", ImageOps.base64(encoded.jpeg()));
        data.addProperty("format", "jpeg");
        data.addProperty("width", encoded.width());
        data.addProperty("height", encoded.height());
        data.addProperty("quality", encoded.quality());
        EventsContracts.ringAdd(streamRing, data, EventsContracts.STREAM_RING_SIZE);

        long now = System.currentTimeMillis();
        long armedBefore = streamThrottle.flushAtMs();
        EventsContracts.StreamThrottle.Decision decision = streamThrottle.offer(data, now);
        if (decision == EventsContracts.StreamThrottle.Decision.PUSH_NOW) {
            push(EventsContracts.STREAM_EVENT, EventsContracts.EventLevel.INFO, data);
            return;
        }
        long flushAt = streamThrottle.flushAtMs();
        if (armedBefore == -1 && flushAt > now) {
            // Newly armed: one flush per closed window, at the window boundary.
            scheduler.schedule(this::flushPendingStream, flushAt - now, TimeUnit.MILLISECONDS);
        }
    }

    /** Pushes the pending stream frame when the throttle window has opened (events thread). */
    private void flushPendingStream() {
        JsonObject pending = streamThrottle.takeDue(System.currentTimeMillis());
        if (pending != null) {
            push(EventsContracts.STREAM_EVENT, EventsContracts.EventLevel.INFO, pending);
        }
    }

    // ------------------------------------------------------------------ lifecycle

    /**
     * Stops the events thread and clears the stream state; called from
     * {@link BridgeServer#shutdown()} after {@code InputTools.shutdown()}.
     * Reports the honest push/drop accounting to the audit log.
     */
    void shutdown() {
        scheduler.shutdownNow();
        streamRing.clear();
        server.audit().event("EVENTS_STOP", "pushed=" + pushedCount.get()
                + " dropped=" + droppedCount.get()
                + " note=counters are per-delivery (one push to two sessions counts twice)");
    }
}
