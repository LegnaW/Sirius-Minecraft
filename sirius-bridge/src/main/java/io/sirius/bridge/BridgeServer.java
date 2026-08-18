package io.sirius.bridge;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonSyntaxException;
import org.java_websocket.WebSocket;
import org.java_websocket.handshake.ClientHandshake;
import org.java_websocket.server.WebSocketServer;

import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.security.MessageDigest;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * The bridge WebSocket server - the "body" the Sirius brain connects to.
 *
 * <p>Security model (spec 8.2, M1-B scope):
 * <ul>
 *   <li>binds {@code 127.0.0.1} only (never a wildcard address);</li>
 *   <li>the FIRST frame on every connection must be
 *       {@code {"type":"hello","token":...,"protocol_version":...}} with a
 *       token matching {@code config/sirius_bridge.toml} (constant-time
 *       compare). Anything else - wrong token, wrong frame, or silence for
 *       {@link #HELLO_TIMEOUT_SECONDS} - closes the connection (1008);</li>
 *   <li>every connection/disconnect/hello attempt/request is audited to
 *       {@code logs/sirius_bridge.log}.</li>
 * </ul>
 *
 * <p>Threading: Java-WebSocket invokes the {@code on*} callbacks on its own
 * selector/worker threads; handlers here therefore never touch game state
 * directly - game access goes through {@link ToolContext#onMainThread(Runnable)}
 * (which schedules on the client main thread via {@code Minecraft.execute}).
 * Writes back into the socket ({@link #sendFrame}) are safe from any thread.
 *
 * <p>Frame dispatch (behaviour aligned with the Python mock server):
 * <pre>
 *   invalid JSON            -> response error -32700 (parse error)
 *   non-object JSON         -> response error -32600 (invalid frame)
 *   unknown frame type      -> response error -32600 (invalid frame)
 *   request, known method   -> ToolRegistry handler (capabilities/list built in)
 *   request, unknown method -> response error -32601 "not implemented: <method>"
 *   task (NEKO)             -> immediate task_finished status=interrupted
 *                              text="not implemented" (placeholder until M1-C)
 * </pre>
 */
public final class BridgeServer extends WebSocketServer {

    /** How long a connection may stay silent before its hello frame. */
    public static final int HELLO_TIMEOUT_SECONDS = 10;

    private final BridgeConfig config;
    private final AuditLog audit;
    private final ToolRegistry tools = new ToolRegistry();
    private final Map<WebSocket, ClientSession> sessions = new ConcurrentHashMap<>();
    private final java.util.concurrent.ScheduledExecutorService scheduler =
            Executors.newSingleThreadScheduledExecutor(r -> {
                Thread t = new Thread(r, "sirius-bridge-hello-watchdog");
                t.setDaemon(true);
                return t;
            });

    /**
     * Per-connection handshake + subscription state. Package-private: the
     * M2-B {@link EventPusher} reads the subscription and allocates
     * per-connection notification {@code seq} values from here.
     */
    static final class ClientSession {
        volatile boolean authenticated;
        volatile ScheduledFuture<?> helloDeadline;
        /** Event subscription; null = unsubscribed (default: no pushes at all). */
        volatile EventsContracts.Subscription subscription;
        /** Per-connection notification counter; first delivered frame gets 0. */
        private final AtomicLong eventSeq = new AtomicLong();

        /** Auth transition is guarded so the watchdog cannot race the hello. */
        synchronized boolean authenticate() {
            if (authenticated) {
                return false;
            }
            authenticated = true;
            ScheduledFuture<?> deadline = helloDeadline;
            if (deadline != null) {
                deadline.cancel(false);
                helloDeadline = null;
            }
            return true;
        }

        /** Sets/changes the event subscription (volatile write, any thread). */
        void setSubscription(EventsContracts.Subscription subscription) {
            this.subscription = subscription;
        }

        /** Consumes the next notification seq for this connection (starts at 0). */
        long nextEventSeq() {
            return eventSeq.getAndIncrement();
        }
    }

    /** The M2-B event push channel (single emit choke point for notifications). */
    private final EventPusher eventPusher;

    public BridgeServer(BridgeConfig config, AuditLog audit) {
        super(new InetSocketAddress("127.0.0.1", config.port));
        this.config = config;
        this.audit = audit;
        this.eventPusher = new EventPusher(this);
        setReuseAddr(true);
        // Built-in tool implementations. M1-C adds screenshot/getStats/world.query,
        // M2-A the input.* primitives, M2-C getGuiState and M2-B events.subscribe
        // by registering handlers here - dispatcher untouched. M2-D adds look/
        // lookAt, sharing the InputGuard (master switch + rate limit + permission
        // tier) with the input.* tools.
        tools.register("capabilities/list", (ctx, params) ->
                Json.capabilitiesResponse(ctx.id(), Capabilities.list(), Capabilities.PROTOCOL_VERSION));
        PerceptionTools.registerAll(tools);
        InputGuard guard = new InputGuard(config);
        InputTools.registerAll(tools, guard);
        LookTools.registerAll(tools, guard);
        GuiTools.registerAll(tools);
        tools.register("events.subscribe", this::subscribeEvents);
    }

    AuditLog audit() {
        return audit;
    }

    /** The event push channel (M2-B); SiriusBridge wires its NeoForge listeners to this. */
    EventPusher eventPusher() {
        return eventPusher;
    }

    /** Live session map for the event pusher's delivery loop (package-private). */
    Map<WebSocket, ClientSession> sessionsView() {
        return sessions;
    }

    // ------------------------------------------------------------------ events.subscribe (M2-B)

    /**
     * {@code events.subscribe({types: [...], min_level?})}: installs (or
     * replaces) the calling connection's event subscription. Params follow
     * the frozen schema {@code sirius-brain/schema/tools/events.subscribe.json}
     * ({@code types}: REQUIRED array of strings - empty or {@code "*"} = all
     * events; {@code min_level}: CRITICAL/WARNING/INFO or null, default
     * INFO); violations answer {@code -32602} like every other tool.
     */
    private JsonObject subscribeEvents(ToolContext ctx, JsonObject params) {
        final EventsContracts.SubscribeParams parsed;
        try {
            parsed = EventsContracts.subscribeParams(params);
        } catch (ToolContracts.InvalidParams e) {
            return Json.errorResponse(ctx.id(), Json.INVALID_PARAMS, e.getMessage(), null);
        }
        ClientSession session = session(ctx.connection());
        session.setSubscription(new EventsContracts.Subscription(parsed.types(), parsed.minLevel()));
        audit.event("SUBSCRIBE", "remote=" + remote(ctx.connection())
                + " types=" + (parsed.types().isEmpty() ? "*" : parsed.types())
                + " min_level=" + session.subscription.effectiveMinLevel().name());
        return Json.okResponse(ctx.id(), EventsContracts.subscribeResult(parsed));
    }

    // ------------------------------------------------------------------ lifecycle

    /** Starts listening on its own thread (never blocks the caller). */
    public void startAsync() {
        audit.event("START", "binding=ws://127.0.0.1:" + config.port
                + " token=" + config.token
                + " note=token is also in config/sirius_bridge.toml");
        this.start();
    }

    /** Graceful shutdown: close clients, stop listening, flush the audit log. */
    public void shutdown() {
        for (WebSocket conn : sessions.keySet()) {
            try {
                conn.close(1001, "server shutting down");
            } catch (RuntimeException ignored) {
            }
        }
        scheduler.shutdownNow();
        InputTools.shutdown();
        eventPusher.shutdown();
        try {
            this.stop(1000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        audit.event("STOP", "connections=" + sessions.size());
        audit.close();
    }

    @Override
    public void onStart() {
        SiriusBridge.LOGGER.info("sirius-bridge: listening on ws://127.0.0.1:{} (token in config/sirius_bridge.toml "
                + "and logs/sirius_bridge.log)", config.port);
    }

    @Override
    public void onError(WebSocket conn, Exception ex) {
        if (conn == null) {
            // Bind/listen failures surface here (e.g. port already in use).
            SiriusBridge.LOGGER.error("sirius-bridge: server error, not listening on port {}: {}",
                    config.port, ex.toString());
            audit.event("ERROR", "ex=" + ex);
        } else {
            audit.event("ERROR", "remote=" + remote(conn) + " ex=" + ex);
        }
    }

    // ------------------------------------------------------------------ connections

    @Override
    public void onOpen(WebSocket conn, ClientHandshake handshake) {
        ClientSession session = new ClientSession();
        sessions.put(conn, session);
        audit.event("OPEN", "remote=" + remote(conn));
        session.helloDeadline = scheduler.schedule(() -> {
            if (!session.authenticated) {
                audit.event("HELLO_FAIL", "remote=" + remote(conn) + " reason=timeout>"
                        + HELLO_TIMEOUT_SECONDS + "s");
                conn.close(1008, "hello timeout");
            }
        }, HELLO_TIMEOUT_SECONDS, TimeUnit.SECONDS);
    }

    @Override
    public void onClose(WebSocket conn, int code, String reason, boolean remote) {
        ClientSession session = sessions.remove(conn);
        if (session != null && session.helloDeadline != null) {
            session.helloDeadline.cancel(false);
        }
        audit.event("CLOSE", "remote=" + remote(conn) + " code=" + code
                + " reason=" + (reason == null ? "" : reason)
                + " byRemote=" + remote + " authenticated=" + (session != null && session.authenticated));
    }

    // ------------------------------------------------------------------ frames

    @Override
    public void onMessage(WebSocket conn, String message) {
        ClientSession session = session(conn);
        JsonObject frame;
        try {
            frame = Json.parseObject(message);
        } catch (JsonSyntaxException e) {
            if (session.authenticated) {
                sendFrame(conn, Json.errorResponse("", Json.PARSE_ERROR,
                        "not valid JSON: " + shorten(e.getMessage()), null));
                audit.event("FRAME_ERROR", "remote=" + remote(conn) + " code=" + Json.PARSE_ERROR);
            } else {
                rejectBeforeHello(conn, "invalid JSON before hello");
            }
            return;
        }

        String type = Json.string(frame, "type");
        if (!session.authenticated) {
            handlePreHello(conn, session, frame, type);
            return;
        }

        switch (type == null ? "" : type) {
            case "request" -> handleRequest(conn, frame);
            case "task" -> handleTask(conn, frame);
            default -> {
                sendFrame(conn, Json.errorResponse(Json.bestEffortId(frame), Json.INVALID_FRAME,
                        "unknown frame type=" + type, null));
                audit.event("FRAME_ERROR", "remote=" + remote(conn) + " code=" + Json.INVALID_FRAME
                        + " type=" + type);
            }
        }
    }

    @Override
    public void onMessage(WebSocket conn, ByteBuffer message) {
        // The protocol is JSON text; binary frames are treated as parse errors.
        ClientSession session = session(conn);
        if (session.authenticated) {
            sendFrame(conn, Json.errorResponse("", Json.PARSE_ERROR,
                    "binary frames are not supported (JSON text only)", null));
        } else {
            rejectBeforeHello(conn, "binary frame before hello");
        }
    }

    // ---- hello handshake -------------------------------------------------

    private void handlePreHello(WebSocket conn, ClientSession session, JsonObject frame, String type) {
        if (!"hello".equals(type)) {
            rejectBeforeHello(conn, "expected hello as first frame, got type=" + type);
            return;
        }
        String token = Json.string(frame, "token");
        String clientVersion = Json.string(frame, "protocol_version");
        if (token == null || !tokenMatches(token)) {
            audit.event("HELLO_FAIL", "remote=" + remote(conn) + " reason=token-mismatch"
                    + " protocol_version=" + clientVersion);
            // Best-effort error frame so the peer can log why it is dropped.
            sendFrame(conn, Json.errorResponse("", Json.AUTH_FAILED, "auth failed: token mismatch", null));
            conn.close(1008, "token mismatch");
            return;
        }
        if (session.authenticate()) {
            sendFrame(conn, Json.helloAck(Capabilities.PROTOCOL_VERSION));
            audit.event("HELLO_OK", "remote=" + remote(conn)
                    + " client_protocol_version=" + clientVersion
                    + " server_protocol_version=" + Capabilities.PROTOCOL_VERSION);
        }
    }

    private void rejectBeforeHello(WebSocket conn, String reason) {
        audit.event("HELLO_FAIL", "remote=" + remote(conn) + " reason=" + reason);
        conn.close(1008, "expected hello first");
    }

    /** Constant-time token comparison. */
    private boolean tokenMatches(String candidate) {
        return MessageDigest.isEqual(
                config.token.getBytes(StandardCharsets.UTF_8),
                candidate.getBytes(StandardCharsets.UTF_8));
    }

    // ---- request / task dispatch ------------------------------------------

    private void handleRequest(WebSocket conn, JsonObject frame) {
        String id = Json.string(frame, "id");
        String method = Json.string(frame, "method");
        if (id == null || method == null) {
            sendFrame(conn, Json.errorResponse(Json.bestEffortId(frame), Json.INVALID_FRAME,
                    "request frame requires string id and method", null));
            audit.event("FRAME_ERROR", "remote=" + remote(conn) + " code=" + Json.INVALID_FRAME);
            return;
        }
        JsonElement paramsElement = frame.get("params");
        if (paramsElement != null && !paramsElement.isJsonObject()) {
            sendFrame(conn, Json.errorResponse(id, Json.INVALID_FRAME,
                    "request params must be a JSON object", null));
            audit.event("FRAME_ERROR", "remote=" + remote(conn) + " code=" + Json.INVALID_FRAME
                    + " method=" + method);
            return;
        }
        JsonObject params = paramsElement != null ? paramsElement.getAsJsonObject() : new JsonObject();

        ToolRegistry.Handler handler = tools.find(method);
        if (handler == null) {
            sendFrame(conn, Json.errorResponse(id, Json.METHOD_NOT_FOUND,
                    "not implemented: " + method, null));
            audit.event("REQUEST", "remote=" + remote(conn) + " id=" + id + " method=" + method
                    + " error=" + Json.METHOD_NOT_FOUND);
            return;
        }

        ToolContext context = new ToolContext(this, conn, id, method);
        JsonObject response;
        try {
            response = handler.handle(context, params);
        } catch (Exception e) {
            SiriusBridge.LOGGER.error("sirius-bridge: tool {} failed", method, e);
            response = Json.errorResponse(id, Json.INTERNAL_ERROR,
                    "internal error in " + method + ": " + e, null);
        }
        sendFrame(conn, response);
        boolean hasError = response.has("error") && response.get("error").isJsonObject();
        audit.event("REQUEST", "remote=" + remote(conn) + " id=" + id + " method=" + method
                + (hasError ? " error=" + response.getAsJsonObject("error").get("code").getAsInt() : " ok"));
    }

    private void handleTask(WebSocket conn, JsonObject frame) {
        String task = Json.string(frame, "task");
        String taskId = Json.string(frame, "task_id");
        if (task == null || taskId == null) {
            sendFrame(conn, Json.errorResponse(Json.bestEffortId(frame), Json.INVALID_FRAME,
                    "task frame requires string task and task_id", null));
            audit.event("FRAME_ERROR", "remote=" + remote(conn) + " code=" + Json.INVALID_FRAME + " type=task");
            return;
        }
        audit.event("TASK", "remote=" + remote(conn) + " task_id=" + taskId
                + " task=" + shorten(task) + " -> interrupted(not implemented)");
        // Placeholder until M1-C: refuse politely, echoing task_id verbatim
        // (out-of-order completion relies on that echo - spec 8.2).
        sendFrame(conn, Json.taskFinished(taskId, "interrupted", "not implemented"));
    }

    // ------------------------------------------------------------------ helpers

    /** Thread-safe frame write. */
    void sendFrame(WebSocket conn, JsonObject frame) {
        if (conn.isOpen()) {
            try {
                conn.send(Json.wire(frame));
            } catch (RuntimeException e) {
                SiriusBridge.LOGGER.debug("sirius-bridge: send failed (connection likely closed): {}", e.toString());
            }
        }
    }

    private ClientSession session(WebSocket conn) {
        ClientSession session = sessions.get(conn);
        if (session == null) { // should not happen; be defensive anyway
            session = new ClientSession();
            sessions.put(conn, session);
        }
        return session;
    }

    private static String remote(WebSocket conn) {
        InetSocketAddress addr = conn.getRemoteSocketAddress();
        return addr != null ? addr.toString() : "unknown";
    }

    private static String shorten(String text) {
        return text.length() <= 120 ? text : text.substring(0, 117) + "...";
    }
}
