package io.sirius.bridge;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/**
 * Gson helpers for building wire frames of the frozen sirius protocol (v1.0).
 *
 * <p>Frame shapes mirror the Python side exactly (sirius_brain.protocol.frames):
 * a {@code response} always carries {@code type/id/result/error}; the
 * {@code capabilities/list} response additionally carries
 * {@code protocol_version} and its {@code result} is an array of capabilities.
 */
public final class Json {

    /**
     * Shared Gson instance: no HTML escaping (frames are JSON, not HTML);
     * nulls are serialized so frames carry the documented
     * {@code "result":null} / {@code "error":null} members verbatim.
     */
    public static final Gson GSON = new GsonBuilder().disableHtmlEscaping().serializeNulls().create();

    /** JSON-RPC style error codes shared with the Python mock/reference implementation. */
    public static final int PARSE_ERROR = -32700;
    public static final int INVALID_FRAME = -32600;
    public static final int METHOD_NOT_FOUND = -32601;
    public static final int INVALID_PARAMS = -32602;
    public static final int INTERNAL_ERROR = -32603;
    /** Implementation-defined server error segment (-32000..-32099), used for auth failures. */
    public static final int AUTH_FAILED = -32000;
    /** Input tool over the configured rate limit (M2-A; see config rate_limit_per_sec). */
    public static final int RATE_LIMITED = -32010;
    /** Input tools disabled via config (input_enabled = false). */
    public static final int INPUT_DISABLED = -32011;

    private Json() {
    }

    /** Parses JSON text; throws {@link com.google.gson.JsonSyntaxException} on invalid input. */
    public static JsonObject parseObject(String text) {
        JsonElement element = JsonParser.parseString(text);
        if (!element.isJsonObject()) {
            throw new com.google.gson.JsonSyntaxException("frame must be a JSON object");
        }
        return element.getAsJsonObject();
    }

    /** Reads a string member; returns {@code null} when absent or not a string. */
    public static String string(JsonObject obj, String member) {
        JsonElement e = obj.get(member);
        return e != null && e.isJsonPrimitive() && e.getAsJsonPrimitive().isString() ? e.getAsString() : null;
    }

    /** Best-effort id extraction from an invalid frame (mirrors the Python mock). */
    public static String bestEffortId(JsonObject frame) {
        String id = string(frame, "id");
        return id != null ? id : "";
    }

    /** Normal {@code response} frame: {@code {"type":"response","id":..,"result":..,"error":null}}. */
    public static JsonObject okResponse(String id, JsonElement result) {
        JsonObject resp = new JsonObject();
        resp.addProperty("type", "response");
        resp.addProperty("id", id);
        resp.add("result", result != null ? result : JsonNull.INSTANCE);
        resp.add("error", JsonNull.INSTANCE);
        return resp;
    }

    /** Error {@code response} frame: {@code {"type":"response","id":..,"result":null,"error":{code,message,data}}}. */
    public static JsonObject errorResponse(String id, int code, String message, JsonElement data) {
        JsonObject error = new JsonObject();
        error.addProperty("code", code);
        error.addProperty("message", message);
        error.add("data", data != null ? data : JsonNull.INSTANCE);

        JsonObject resp = new JsonObject();
        resp.addProperty("type", "response");
        resp.addProperty("id", id);
        resp.add("result", JsonNull.INSTANCE);
        resp.add("error", error);
        return resp;
    }

    /**
     * {@code capabilities/list} response:
     * {@code {"type":"response","id":..,"result":[Capability..],"protocol_version":"1.0","error":null}}.
     */
    public static JsonObject capabilitiesResponse(String id, JsonArray capabilities, String protocolVersion) {
        JsonObject resp = new JsonObject();
        resp.addProperty("type", "response");
        resp.addProperty("id", id);
        resp.add("result", capabilities);
        resp.addProperty("protocol_version", protocolVersion);
        resp.add("error", JsonNull.INSTANCE);
        return resp;
    }

    /** hello handshake acknowledgement. */
    public static JsonObject helloAck(String protocolVersion) {
        JsonObject ack = new JsonObject();
        ack.addProperty("type", "hello_ack");
        ack.addProperty("ok", true);
        ack.addProperty("protocol_version", protocolVersion);
        return ack;
    }

    /** NEKO task_finished frame with the task_id echoed back verbatim. */
    public static JsonObject taskFinished(String taskId, String status, String text) {
        JsonObject frame = new JsonObject();
        frame.addProperty("type", "task_finished");
        frame.addProperty("status", status);
        frame.addProperty("task_id", taskId);
        frame.addProperty("text", text);
        return frame;
    }

    /** Serializes a frame to wire text. */
    public static String wire(JsonObject frame) {
        return GSON.toJson(frame);
    }
}
