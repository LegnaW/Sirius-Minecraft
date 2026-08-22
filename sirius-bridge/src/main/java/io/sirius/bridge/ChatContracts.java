package io.sirius.bridge;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

/**
 * Frozen-contract logic for the M4.1 v1.3 {@code chat.send} tool: parameter
 * validation and response assembly. Pure Gson + JDK - no Minecraft classes -
 * so the smoke test covers it without a running game.
 *
 * <p>The tool's reason to exist (an operation-type feature in the bridge,
 * like {@code dig} and {@code look} before it): the T-key chat path
 * ({@code input.key T} + {@code input.text} + ENTER) is physically blocked
 * while any GUI screen holds the keyboard focus - most notably the death
 * screen, which swallowed the reflex layer's death report on the real
 * machine (M4-rerun §3.3: sent on the wire, never appeared in game chat).
 * {@code chat.send} goes through {@code ClientPacketListener.sendChat} in
 *-process - the same entry point the vanilla chat screen uses - so the
 * message is delivered no matter what screen is open.
 */
public final class ChatContracts {

    private ChatContracts() {
    }

    /** Vanilla chat length limit (256 chars) - {@code ChatScreen} enforces the same. */
    public static final int MAX_LENGTH = 256;

    /** Validated {@code chat.send} params: {@code string} REQUIRED, 1..256 chars. */
    public record ChatSendParams(String text) {
    }

    /**
     * Validates {@code chat.send} params per the frozen schema: a required
     * string of 1..256 characters (the vanilla limit; longer input is a
     * caller bug, not something to truncate silently).
     */
    public static ChatSendParams chatSendParams(JsonObject params) throws ToolContracts.InvalidParams {
        JsonElement element = params.get("string");
        if (element == null || element.isJsonNull()
                || !element.isJsonPrimitive() || !element.getAsJsonPrimitive().isString()) {
            throw new ToolContracts.InvalidParams(
                    "chat.send requires a string member \"string\" of 1.." + MAX_LENGTH + " characters");
        }
        String text = element.getAsString();
        if (text.isEmpty() || text.codePointCount(0, text.length()) > MAX_LENGTH) {
            throw new ToolContracts.InvalidParams(
                    "chat.send string must be 1.." + MAX_LENGTH + " characters (code points), got: "
                            + (text.isEmpty() ? 0 : text.codePointCount(0, text.length())));
        }
        return new ChatSendParams(text);
    }

    /** {@code chat.send} when the player is not in a world: not an error (getStats convention). */
    public static JsonObject notInGameChat() {
        JsonObject result = new JsonObject();
        result.addProperty("in_game", false);
        result.addProperty("sent", false);
        return result;
    }

    /** {@code chat.send} success result: what was handed to the connection. */
    public static JsonObject sentResult(String text) {
        JsonObject result = new JsonObject();
        result.addProperty("in_game", true);
        result.addProperty("sent", true);
        result.addProperty("length", text.codePointCount(0, text.length()));
        return result;
    }
}
