package io.sirius.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.util.ArrayDeque;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Set;

/**
 * Pure logic for the M2-B event push channel: the frozen event levels, the
 * subscription matcher, {@code events.subscribe} parameter validation and
 * response assembly, notification wire-frame assembly, and the screenshot
 * stream's throttle state machine. Gson + JDK only - no Minecraft classes -
 * so everything here is covered by the in-process smoke test
 * ({@code SmokeMain}) without a running game.
 *
 * <p>Wire contract (verified against the Python side, which is NOT touched
 * by M2-B): {@code sirius_brain.protocol.frames.NotificationFrame} requires
 * {@code type/event/data/timestamp:float/seq:int>=0}; the brain validates
 * {@code seq} to be monotonically increasing per connection (violations are
 * warnings, not fatal) and reads the event level from {@code data["level"]}
 * by convention. The mock server's {@code push_notification} builds
 * {@code timestamp=time.time()} - i.e. <b>epoch seconds as a float</b> - so
 * the Java side must send {@code System.currentTimeMillis() / 1000.0}, NOT
 * milliseconds.
 */
public final class EventsContracts {

    private EventsContracts() {
    }

    // ------------------------------------------------------------------ levels

    /**
     * Event severity, spec 8.2: CRITICAL (drown/fire/attack/death - reflex
     * layer acts immediately), WARNING (hunger/GUI change - queued), INFO
     * (chat/weather - buffered). The three names are frozen in the schema's
     * {@code EventLevel} enum; ordering CRITICAL > WARNING > INFO drives
     * {@code min_level} filtering.
     */
    public enum EventLevel {
        CRITICAL(3),
        WARNING(2),
        INFO(1);

        private final int severity;

        EventLevel(int severity) {
            this.severity = severity;
        }

        /** True when this level is at least as severe as {@code other}. */
        public boolean atLeast(EventLevel other) {
            return severity >= other.severity;
        }

        /** Parses a wire level name; null when {@code text} is not one of the three. */
        public static EventLevel fromName(String text) {
            if (text == null) {
                return null;
            }
            try {
                return EventLevel.valueOf(text.trim().toUpperCase(Locale.ROOT));
            } catch (IllegalArgumentException notALevel) {
                return null;
            }
        }
    }

    // ------------------------------------------------------------------ subscription

    /**
     * One client's event subscription. {@code types} is a set of event names;
     * {@code "*"} subscribes to everything and an <b>empty set also means
     * everything</b> (the frozen schema requires the array but permits an
     * empty one; treating it as "no filter" matches the wildcard convention
     * the Python client exposes with {@code on_event("*")}). {@code minLevel}
     * is the {@code min_level} filter; null defaults to INFO (no filtering).
     */
    public record Subscription(Set<String> types, EventLevel minLevel) {

        /** Effective level filter - never null (null maps to INFO). */
        public EventLevel effectiveMinLevel() {
            return minLevel != null ? minLevel : EventLevel.INFO;
        }

        /**
         * True when an event of {@code type} at {@code level} should be
         * delivered to this subscriber: the type must match (exact name,
         * {@code "*"} in the set, or an empty set) AND the level must be at
         * least the subscribed minimum.
         */
        public boolean matches(String type, EventLevel level) {
            boolean typeMatch = types.isEmpty() || types.contains("*") || types.contains(type);
            return typeMatch && level.atLeast(effectiveMinLevel());
        }
    }

    /** Validated {@code events.subscribe} params (frozen schema tools/events.subscribe.json). */
    public record SubscribeParams(Set<String> types, EventLevel minLevel) {
    }

    /**
     * Validates {@code events.subscribe} params: {@code types} is a REQUIRED
     * array of strings (empty allowed = all events; {@code "*"} = all events);
     * {@code min_level} is optional and must be one of
     * CRITICAL/WARNING/INFO or null. Violations throw
     * {@link ToolContracts.InvalidParams} for the standard {@code -32602}.
     */
    public static SubscribeParams subscribeParams(JsonObject params) throws ToolContracts.InvalidParams {
        JsonElement typesElement = params.get("types");
        if (typesElement == null || typesElement.isJsonNull()) {
            throw new ToolContracts.InvalidParams("events.subscribe requires array params.types (strings)");
        }
        if (!typesElement.isJsonArray()) {
            throw new ToolContracts.InvalidParams("events.subscribe types must be an array of strings");
        }
        Set<String> types = new LinkedHashSet<>(); // stable echo order in the response
        for (JsonElement item : typesElement.getAsJsonArray()) {
            if (item == null || !item.isJsonPrimitive() || !item.getAsJsonPrimitive().isString()) {
                throw new ToolContracts.InvalidParams("events.subscribe types entries must be strings");
            }
            types.add(item.getAsString());
        }

        EventLevel minLevel = null;
        JsonElement levelElement = params.get("min_level");
        if (levelElement != null && !levelElement.isJsonNull()) {
            if (!levelElement.isJsonPrimitive() || !levelElement.getAsJsonPrimitive().isString()) {
                throw new ToolContracts.InvalidParams(
                        "events.subscribe min_level must be \"CRITICAL\"|\"WARNING\"|\"INFO\" or null");
            }
            minLevel = EventLevel.fromName(levelElement.getAsString());
            if (minLevel == null) {
                throw new ToolContracts.InvalidParams("events.subscribe min_level must be "
                        + "\"CRITICAL\"|\"WARNING\"|\"INFO\" or null, got: " + levelElement.getAsString());
            }
        }
        return new SubscribeParams(types, minLevel);
    }

    /**
     * The {@code events.subscribe} success result: echoes the effective
     * subscription and states the (deliberate) default - a client that never
     * subscribes receives no pushes at all.
     */
    public static JsonObject subscribeResult(SubscribeParams params) {
        JsonObject result = new JsonObject();
        result.addProperty("subscribed", true);
        JsonArray types = new JsonArray();
        params.types().forEach(types::add);
        result.add("types", types);
        result.addProperty("min_level", new Subscription(params.types(), params.minLevel()).effectiveMinLevel().name());
        result.addProperty("note", "unsubscribed clients receive no pushes");
        return result;
    }

    // ------------------------------------------------------------------ notification frame

    /**
     * Assembles the wire notification frame:
     * {@code {"type":"notification","event":..,"data":{...original..., "level":..},
     * "timestamp":<epoch seconds, float - matches Python time.time()>,"seq":<int>}}.
     *
     * <p>The level is injected into a COPY of {@code data} with put-if-absent
     * semantics (mirrors the mock's {@code payload.setdefault("level", ...)}),
     * so a producer that already set a level field wins. {@code data} may be
     * null (the frame then carries just the level).
     */
    public static JsonObject notification(String event, JsonObject data, EventLevel level,
                                          double timestampSeconds, long seq) {
        JsonObject payload = data != null ? data.deepCopy() : new JsonObject();
        if (!payload.has("level") && level != null) {
            payload.addProperty("level", level.name());
        }
        JsonObject frame = new JsonObject();
        frame.addProperty("type", "notification");
        frame.addProperty("event", event);
        frame.add("data", payload);
        frame.addProperty("timestamp", timestampSeconds);
        frame.addProperty("seq", seq);
        return frame;
    }

    /** Epoch seconds as a float - the timestamp unit the Python NotificationFrame expects. */
    public static double timestampNowSeconds(long nowMs) {
        return nowMs / 1000.0;
    }

    // ------------------------------------------------------------------ screenshot stream throttle

    /**
     * The screenshot stream throttle (N.E.K.O service.py:1037-1079, adopted):
     * a minimum push interval with <b>latest-wins deferral</b>.
     *
     * <ul>
     *   {@link #offer}: window open (first frame ever, or {@code >= minIntervalMs}
     *   since the last push) -> PUSH_NOW: the caller pushes this frame
     *   immediately, any armed delayed flush is cancelled and the pending slot
     *   cleared (a stale scheduled flush would surface an older frame right
     *   after the fresher push). Window closed -> DEFER: the pending slot is
     *   REPLACED with this frame (old screenshots are obsolete the moment a
     *   newer one exists - only the latest is ever flushed) and exactly ONE
     *   delayed flush is armed for the window-open time.</li>
     *   {@link #takeDue}: called at the flush deadline; returns (and clears)
     *   the pending frame when due, null otherwise.</li>
     * </ul>
     *
     * <p>The newest frame is therefore never lost, only delayed to the
     * boundary. Synchronized because offers arrive from the stream encoder
     * thread and flushes fire on the event scheduler thread.
     *
     * @param <T> the pushed unit (encoded frame payload)
     */
    public static final class StreamThrottle<T> {

        /** Outcome of {@link #offer}: push immediately, or park in the pending slot. */
        public enum Decision {PUSH_NOW, DEFER}

        private final long minIntervalMs;

        private boolean pushedOnce;
        private long lastPushMs;
        private T pending;
        private boolean flushArmed;
        private long flushAtMs;

        public StreamThrottle(long minIntervalMs) {
            this.minIntervalMs = Math.max(0, minIntervalMs);
        }

        /** Offers a frame at {@code nowMs} (an injected clock keeps tests deterministic). */
        public synchronized Decision offer(T frame, long nowMs) {
            if (!pushedOnce || nowMs - lastPushMs >= minIntervalMs) {
                // Window open: push now; a pending delayed flush would emit a
                // frame older than this one right after it - disarm and drop it.
                pushedOnce = true;
                lastPushMs = nowMs;
                flushArmed = false;
                flushAtMs = 0;
                pending = null;
                return Decision.PUSH_NOW;
            }
            // Window closed: latest frame wins the single slot; arm ONE flush
            // at the window boundary if none is armed yet.
            pending = frame;
            if (!flushArmed) {
                flushArmed = true;
                flushAtMs = lastPushMs + minIntervalMs;
            }
            return Decision.DEFER;
        }

        /**
         * Takes the pending frame when the armed flush is due at {@code nowMs};
         * null when nothing is armed or the boundary has not been reached yet.
         * The take marks the push time (the flush IS a push).
         */
        public synchronized T takeDue(long nowMs) {
            if (!flushArmed || nowMs < flushAtMs) {
                return null;
            }
            flushArmed = false;
            flushAtMs = 0;
            T frame = pending;
            pending = null;
            pushedOnce = true;
            lastPushMs = nowMs;
            return frame;
        }

        /** The armed flush deadline (for scheduling), or -1 when none is armed. */
        public synchronized long flushAtMs() {
            return flushArmed ? flushAtMs : -1;
        }

        /** True when a frame sits in the pending slot (diagnostics). */
        public synchronized boolean hasPending() {
            return pending != null;
        }
    }

    /** Adds {@code item} to {@code ring}, evicting the OLDEST beyond {@code max} entries. */
    public static <T> void ringAdd(ArrayDeque<T> ring, T item, int max) {
        ring.addLast(item);
        while (ring.size() > max) {
            ring.pollFirst();
        }
    }

    // ------------------------------------------------------------------ stream parameters

    /** Screenshot stream: minimum push interval (spec 8.2: 6s, latest-wins coalescing). */
    public static final long STREAM_MIN_INTERVAL_MS = 6_000;
    /** Screenshot stream: framebuffer sampling rate (~1Hz, spec 8.2). */
    public static final int STREAM_SAMPLE_TICKS = 20;
    /** Screenshot stream: JPEG quality requested first (never exceeded; spec 8.2 q80). */
    public static final int STREAM_QUALITY = 80;
    /** Screenshot stream: longest edge target (spec 8.2: 1024px, halved by the ladder). */
    public static final int STREAM_LONGEST_EDGE = 1024;
    /**
     * Screenshot stream: hard budget on the BASE64 text length (spec 8.2
     * "100KB"). Bounding the base64 - not the raw JPEG - also bounds the wire
     * message at ~100KB, because the notification frame carries base64 only
     * (no raw copy, unlike N.E.K.O's message_plane envelope).
     */
    public static final int STREAM_MAX_BASE64 = 100 * 1024;
    /** Screenshot stream: ring buffer depth (spec 8.2: 3 frames, latest consumed). */
    public static final int STREAM_RING_SIZE = 3;
    /** Screenshot stream: event type name. */
    public static final String STREAM_EVENT = "screenshot";
}
