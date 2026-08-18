package io.sirius.bridge;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import org.java_websocket.WebSocket;

/**
 * Per-call context handed to {@link ToolRegistry.Handler} implementations.
 *
 * <p>Threading contract (spec 8.2 security model / M1-B task):
 * the handler body runs on a WebSocket server thread; any access to game
 * state MUST be wrapped in {@link #onMainThread(Runnable)}, which schedules
 * onto the client's render/main thread via {@link Minecraft#execute}.
 * Sending frames back ({@link #send(JsonObject)}) is safe from any thread.
 */
public final class ToolContext {

    private final BridgeServer server;
    private final WebSocket connection;
    private final String id;
    private final String method;

    ToolContext(BridgeServer server, WebSocket connection, String id, String method) {
        this.server = server;
        this.connection = connection;
        this.id = id;
        this.method = method;
    }

    /** The request id this handler is answering (echo it in the response). */
    public String id() {
        return id;
    }

    /** The requested method name. */
    public String method() {
        return method;
    }

    /**
     * The calling connection (package-private: only bridge-internal tools
     * that need per-connection state - M2-B {@code events.subscribe} - use
     * it; everything else goes through {@link #send}/{@link #audit}).
     */
    WebSocket connection() {
        return connection;
    }

    /** Queues work on the client main (render) thread - mandatory for game state. */
    public void onMainThread(Runnable action) {
        Minecraft.getInstance().execute(action);
    }

    /** Sends a frame to the calling client; thread-safe. */
    public void send(JsonObject frame) {
        server.sendFrame(connection, frame);
    }

    /** Audits a one-line event tied to this call. */
    public void audit(String event, String detail) {
        server.audit().event(event, detail);
    }
}
