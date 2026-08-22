package io.sirius.bridge;

/**
 * Pure M2-D permission-tier logic: which tool classes a tier allows, given
 * whether a GUI screen is open. Pure JDK only - no Minecraft classes - so the
 * whole decision matrix is covered by the in-process smoke test without a
 * running game.
 *
 * <p>Tiers (config key {@code permission} in {@code sirius_bridge.toml},
 * default {@code full} = M2-A behaviour unchanged):
 * <ul>
 *   <li>{@code observe}: read-only. Every {@code input.*} tool AND
 *       {@code look}/{@code lookAt} answer {@code -32012}.</li>
 *   <li>{@code input_gui}: {@code input.*} allowed ONLY while a GUI screen is
 *       open (the {@code mc.screen != null} branch of input.click; for
 *       key/text/mouseMove the keypress would otherwise reach the
 *       KeyMapping/world, i.e. act outside the GUI - so with no screen the
 *       call is denied). {@code look}/{@code lookAt} denied (world-level
 *       actions).</li>
 *   <li>{@code input_world}: the inverse - {@code input.*} allowed ONLY when
 *       NO GUI is open; {@code look}/{@code lookAt} allowed.</li>
 *   <li>{@code full}: everything allowed (backward compatible default).</li>
 * </ul>
 *
 * <p>Threading note: for {@code input_gui}/{@code input_world} the decision
 * depends on the live screen state, which can change every tick - the caller
 * MUST evaluate it on the client main thread inside the tool's existing
 * main-thread task (see InputTools), not pre-flight on the WebSocket thread.
 */
public final class PermissionContracts {

    private PermissionContracts() {
    }

    /** The configured permission tier. */
    public enum Tier {
        OBSERVE, INPUT_GUI, INPUT_WORLD, FULL;

        /** Config-file spelling (lowercase, e.g. {@code input_gui}). */
        public String configName() {
            return name().toLowerCase(java.util.Locale.ROOT);
        }
    }

    /** The class of acting a tool performs. */
    public enum Action {
        /** Any {@code input.*} injection tool (event layer). */
        INPUT,
        /** {@code look}/{@code lookAt}: world-level view rotation (action layer). */
        LOOK,
        /**
         * {@code chat.send}: the M4.1 v1.3 direct chat channel. Speaking is a
         * social, GUI-immune action (its whole point is working while a screen
         * - e.g. the death screen - blocks the T-key path), so it is allowed
         * under every tier except read-only {@code observe}.
         */
        CHAT
    }

    /**
     * Parses a tier name (case-insensitive, surrounding whitespace ignored);
     * returns {@code null} when the value is not one of the four tiers (the
     * config loader then falls back to the default plus a note).
     */
    public static Tier parse(String raw) {
        if (raw == null) {
            return null;
        }
        String value = raw.trim();
        for (Tier tier : Tier.values()) {
            if (tier.name().equalsIgnoreCase(value)) {
                return tier;
            }
        }
        return null;
    }

    /**
     * The decision matrix: may {@code action} run under {@code tier} right
     * now? {@code screenOpen} is the live {@code mc.screen != null} state and
     * only matters for {@code input_gui}/{@code input_world} + {@code INPUT}
     * (look is world-level, so the screen state never changes its verdict).
     */
    public static boolean allows(Tier tier, boolean screenOpen, Action action) {
        return switch (tier) {
            case OBSERVE -> false;
            // CHAT is deliberately screen-independent (death-screen broadcast path)
            case INPUT_GUI -> (action == Action.INPUT && screenOpen) || action == Action.CHAT;
            case INPUT_WORLD -> action == Action.LOOK || action == Action.CHAT || !screenOpen;
            case FULL -> true;
        };
    }

    /**
     * True when {@code action} is denied under BOTH screen states - the
     * caller can veto pre-flight (on the WebSocket thread, before burning a
     * rate-limit token or touching the game). When false, the screen-dependent
     * verdict must be taken on the main thread via {@link #allows}.
     */
    public static boolean deniedRegardlessOfScreen(Tier tier, Action action) {
        return !allows(tier, true, action) && !allows(tier, false, action);
    }

    /** Error-frame message body for the {@code -32012} permission denial. */
    public static String deniedMessage(Tier tier, boolean screenOpen, Action action) {
        String what = action == Action.LOOK ? "look/lookAt"
                : action == Action.CHAT ? "chat.send" : "input.*";
        String need = switch (tier) {
            case OBSERVE -> "tier \"observe\" is read-only (no input.*, no look, no chat.send)";
            case INPUT_GUI -> action == Action.INPUT
                    ? "tier \"input_gui\" allows input.* only while a GUI screen is open (screen_open=" + screenOpen + ")"
                    : action == Action.LOOK
                    ? "tier \"input_gui\" does not allow look/lookAt (world-level)"
                    : "tier \"input_gui\" does not allow chat.send";
            case INPUT_WORLD -> action == Action.INPUT
                    ? "tier \"input_world\" allows input.* only while NO GUI screen is open (screen_open=" + screenOpen + ")"
                    : "tier \"input_world\" allows look/lookAt";
            case FULL -> "tier \"full\" allows everything";
        };
        return "permission_denied: " + what + " denied (" + need
                + "). Check permission in config/sirius_bridge.toml";
    }
}
