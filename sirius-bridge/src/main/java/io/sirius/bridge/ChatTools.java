package io.sirius.bridge;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientPacketListener;

/**
 * The M4.1 v1.3 {@code chat.send} tool: hand one chat line straight to the
 * client's connection ({@code ClientPacketListener.sendChat}) instead of
 * typing it through the T-key GUI.
 *
 * <p><b>Why an action-layer tool instead of the event layer (the T-key
 * path):</b> the death screen - and any other open screen - swallows the T
 * key, so a reflex-layer broadcast sent while dead never reaches game chat
 * (real-machine evidence M4-rerun §3.3: the death report was confirmed SENT
 * on the wire yet never appeared in the chat window). The vanilla chat
 * screen itself sends through {@code ClientPacketListener.sendChat}; calling
 * it in-process is the same code path a human player's ENTER takes, without
 * depending on keyboard focus. This mirrors the earlier "operation-type
 * feature in the bridge" precedents ({@code look}, {@code dig}): when the
 * honest input pipeline physically cannot express an action, the bridge
 * performs it at the layer vanilla uses for it.
 *
 * <p><b>Guard rails:</b> the same {@link InputGuard} preamble as input.* -
 * {@code input_enabled = false} answers -32011 (speaking IS acting); the
 * {@link PermissionContracts.Action#CHAT CHAT} action class is denied only
 * under the read-only {@code observe} tier (-32012); one call costs exactly
 * one token-bucket token (the M4.1 T4 semantics: per tool CALL, never per
 * tick); every call is audited. Screen state is deliberately NOT consulted -
 * being screen-immune is the tool's entire purpose.
 *
 * <p><b>Threading:</b> the send runs on the client main thread via the
 * shared {@code PerceptionTools.callOnMainThread} latch (10 s timeout,
 * -32603 when the window is iconified); validation and response assembly
 * run on the WebSocket thread.
 */
final class ChatTools {

    private ChatTools() {
    }

    /** Registers the tool; called once from the BridgeServer constructor. */
    static void registerAll(ToolRegistry tools, InputGuard guard) {
        tools.register("chat.send", (ctx, params) -> chatSend(ctx, params, guard));
    }

    private static JsonObject chatSend(ToolContext ctx, JsonObject params, InputGuard guard) throws Exception {
        final ChatContracts.ChatSendParams p;
        try {
            p = ChatContracts.chatSendParams(params);
        } catch (ToolContracts.InvalidParams e) {
            return Json.errorResponse(ctx.id(), Json.INVALID_PARAMS, e.getMessage(), null);
        }
        if (!guard.enabled()) {
            ctx.audit("INPUT", "method=chat.send " + p.text().codePointCount(0, p.text().length())
                    + "cp result=input_disabled");
            return Json.errorResponse(ctx.id(), Json.INPUT_DISABLED, InputContracts.inputDisabledMessage(), null);
        }
        // CHAT is screen-independent; only observe vetoes pre-flight.
        if (PermissionContracts.deniedRegardlessOfScreen(guard.tier(), PermissionContracts.Action.CHAT)) {
            ctx.audit("INPUT_DENIED", "reason=permission tier=" + guard.tier().configName()
                    + " method=chat.send " + p.text().codePointCount(0, p.text().length()) + "cp");
            return Json.errorResponse(ctx.id(), Json.PERMISSION_DENIED,
                    PermissionContracts.deniedMessage(guard.tier(), false, PermissionContracts.Action.CHAT), null);
        }
        if (!guard.tryAcquire()) {
            ctx.audit("INPUT", "method=chat.send " + p.text().codePointCount(0, p.text().length())
                    + "cp result=rate_limited");
            return Json.errorResponse(ctx.id(), Json.RATE_LIMITED,
                    InputContracts.rateLimitedMessage(guard.limitPerSecond()), null);
        }

        final JsonObject result = PerceptionTools.callOnMainThread(ctx, () -> {
            Minecraft mc = Minecraft.getInstance();
            ClientPacketListener connection = mc.getConnection();
            if (mc.player == null || connection == null) {
                return ChatContracts.notInGameChat(); // title screen etc.: not an error
            }
            // Same entry the vanilla ChatScreen's ENTER uses (1.21.1): handles the
            // 256-char split and the signed-chat pipeline exactly like a human.
            connection.sendChat(p.text());
            return ChatContracts.sentResult(p.text());
        });
        ctx.audit("INPUT", "method=chat.send " + p.text().codePointCount(0, p.text().length())
                + "cp screen_immune=true result=sent");
        return Json.okResponse(ctx.id(), result);
    }
}
