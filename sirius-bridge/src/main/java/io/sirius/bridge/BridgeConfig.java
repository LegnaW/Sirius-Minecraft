package io.sirius.bridge;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;

/**
 * sirius-bridge configuration, stored as a flat {@code config/sirius_bridge.toml}.
 *
 * <p>Deliberately a tiny hand-rolled TOML subset (flat {@code key = "value"} /
 * {@code key = 123} lines, {@code #} comments) instead of the NeoForge config
 * system: only a handful of keys exist, and the token must be (re)generated
 * and persisted on first launch regardless of config UIs.
 *
 * <p>On first launch a random token is generated, written to the file and
 * reported back so the caller can print it to the audit log.
 */
public final class BridgeConfig {

    public static final int DEFAULT_PORT = 8765;
    /** Default input injection rate limit (calls/s across all input.* tools; spec 8.2 ~20/s). */
    public static final int DEFAULT_RATE_LIMIT_PER_SEC = 20;
    /**
     * Default for {@code keep_running_unfocused}: keep ticking when the game
     * window loses focus by disabling vanilla's {@code pauseOnLostFocus} at
     * runtime (the AI-plays-human-watches scenario dies without this).
     */
    public static final boolean DEFAULT_KEEP_RUNNING_UNFOCUSED = true;
    /**
     * Default for {@code permission}: {@code full} - every acting tool allowed,
     * byte-identical to the pre-M2-D behaviour (tiers are strictly opt-in).
     */
    public static final PermissionContracts.Tier DEFAULT_PERMISSION = PermissionContracts.Tier.FULL;

    /** Resolved settings. */
    public final int port;
    public final String token;
    /** True when the token was freshly generated this launch (first run or token removed). */
    public final boolean tokenGenerated;
    /** Master switch for the input.* tools (input_enabled). */
    public final boolean inputEnabled;
    /** Combined rate limit for all input.* calls per second (rate_limit_per_sec). */
    public final int rateLimitPerSec;
    /** Save an evidence screenshot before every GUI click (gui_click_evidence). */
    public final boolean guiClickEvidence;
    /**
     * Disable vanilla's pause-on-lost-focus at runtime (keep_running_unfocused).
     * When true the bridge sets {@code Options.pauseOnLostFocus = false} once
     * at startup; the user's {@code options.txt} file is never written by us.
     */
    public final boolean keepRunningUnfocused;
    /**
     * The M2-D permission tier (permission): {@code observe} / {@code input_gui}
     * / {@code input_world} / {@code full} (default - backward compatible, every
     * acting tool allowed). See {@link PermissionContracts} for the matrix.
     */
    public final PermissionContracts.Tier permission;
    /** Human-readable notes gathered while loading (logged by the caller, never fatal). */
    public final String notes;

    private BridgeConfig(int port, String token, boolean tokenGenerated,
                         boolean inputEnabled, int rateLimitPerSec, boolean guiClickEvidence,
                         boolean keepRunningUnfocused, PermissionContracts.Tier permission,
                         String notes) {
        this.port = port;
        this.token = token;
        this.tokenGenerated = tokenGenerated;
        this.inputEnabled = inputEnabled;
        this.rateLimitPerSec = rateLimitPerSec;
        this.guiClickEvidence = guiClickEvidence;
        this.keepRunningUnfocused = keepRunningUnfocused;
        this.permission = permission;
        this.notes = notes;
    }

    /**
     * Loads the config from {@code file}, creating/repairing it as needed.
     * Never throws: any problem degrades to defaults plus a note.
     */
    public static BridgeConfig load(Path file, SecureRandom random) {
        int port = DEFAULT_PORT;
        String token = null;
        boolean inputEnabled = true;
        int rateLimitPerSec = DEFAULT_RATE_LIMIT_PER_SEC;
        boolean guiClickEvidence = true;
        boolean keepRunningUnfocused = DEFAULT_KEEP_RUNNING_UNFOCUSED;
        PermissionContracts.Tier permission = DEFAULT_PERMISSION;
        StringBuilder notes = new StringBuilder();

        if (Files.exists(file)) {
            try {
                for (String rawLine : Files.readAllLines(file, StandardCharsets.UTF_8)) {
                    String line = rawLine.trim();
                    if (line.isEmpty() || line.startsWith("#")) {
                        continue;
                    }
                    int eq = line.indexOf('=');
                    if (eq < 0) {
                        continue;
                    }
                    String key = line.substring(0, eq).trim();
                    String value = unquote(line.substring(eq + 1).trim());
                    switch (key) {
                        case "port" -> {
                            try {
                                port = Integer.parseInt(value);
                                if (port < 1 || port > 65535) {
                                    notes.append("invalid port ").append(value)
                                            .append(", using default ").append(DEFAULT_PORT).append("; ");
                                    port = DEFAULT_PORT;
                                }
                            } catch (NumberFormatException e) {
                                notes.append("port not a number: ").append(value).append("; ");
                            }
                        }
                        case "token" -> {
                            if (!value.isEmpty()) {
                                token = value;
                            }
                        }
                        // M2-A input keys - appended, existing keys keep their meaning.
                        case "input_enabled" -> {
                            Boolean parsed = parseBoolean(value);
                            if (parsed != null) {
                                inputEnabled = parsed;
                            } else {
                                notes.append("input_enabled not true/false: ").append(value).append("; ");
                            }
                        }
                        case "rate_limit_per_sec" -> {
                            try {
                                int parsed = Integer.parseInt(value);
                                if (parsed >= 1 && parsed <= 1000) {
                                    rateLimitPerSec = parsed;
                                } else {
                                    notes.append("rate_limit_per_sec out of range 1..1000: ").append(value)
                                            .append(", using ").append(DEFAULT_RATE_LIMIT_PER_SEC).append("; ");
                                }
                            } catch (NumberFormatException e) {
                                notes.append("rate_limit_per_sec not a number: ").append(value).append("; ");
                            }
                        }
                        case "gui_click_evidence" -> {
                            Boolean parsed = parseBoolean(value);
                            if (parsed != null) {
                                guiClickEvidence = parsed;
                            } else {
                                notes.append("gui_click_evidence not true/false: ").append(value).append("; ");
                            }
                        }
                        // M2-A2 focus key - appended, existing keys keep their meaning.
                        case "keep_running_unfocused" -> {
                            Boolean parsed = parseBoolean(value);
                            if (parsed != null) {
                                keepRunningUnfocused = parsed;
                            } else {
                                notes.append("keep_running_unfocused not true/false: ").append(value).append("; ");
                            }
                        }
                        // M2-D permission tier - appended, existing keys keep their meaning.
                        case "permission" -> {
                            PermissionContracts.Tier parsed = PermissionContracts.parse(value);
                            if (parsed != null) {
                                permission = parsed;
                            } else {
                                notes.append("invalid permission ").append(value)
                                        .append(", using default ").append(DEFAULT_PERMISSION.configName()).append("; ");
                            }
                        }
                        default -> {
                        }
                    }
                }
            } catch (IOException e) {
                notes.append("config unreadable (").append(e.getMessage()).append("); ");
            }
        } else {
            notes.append("config created; ");
        }

        boolean generated = false;
        if (token == null) {
            token = generateToken(random);
            generated = true;
            notes.append("token generated; ");
        }

        BridgeConfig config = new BridgeConfig(port, token, generated,
                inputEnabled, rateLimitPerSec, guiClickEvidence, keepRunningUnfocused, permission,
                notes.toString().trim());
        config.save(file);
        return config;
    }

    private void save(Path file) {
        String content = """
                # sirius-bridge configuration
                # The WebSocket server ALWAYS binds to 127.0.0.1 (loopback) only -
                # it is not reachable from the network.
                #
                # port : TCP port to listen on (default 8765).
                # token: shared secret for the hello handshake. To rotate it, delete
                #        the line (or set token = "") and restart the game; the new
                #        token is printed to logs/sirius_bridge.log on startup.
                # input_enabled     : master switch for the input.* injection tools
                #                     (default true). When false they answer -32011.
                # rate_limit_per_sec: combined calls/s budget for all input.* tools
                #                     (token bucket; default 20, range 1..1000).
                #                     Over-rate calls answer -32010 rate_limited.
                # gui_click_evidence: when true, every input.click while a GUI screen
                #                     is open first saves a small JPEG screenshot to
                #                     logs/sirius_evidence/ (default true).
                # keep_running_unfocused: when true (default), the bridge disables
                #                     vanilla's "Pause On Lost Focus" ONCE at startup
                #                     (runtime Options.pauseOnLostFocus=false) so the
                #                     world keeps ticking while the window is
                #                     unfocused. options.txt is never written by the
                #                     bridge; to make it permanent manually add
                #                     "pauseOnLostFocus:false" there instead.
                # permission        : which acting tools the bridge may run (M2-D).
                #                     observe    = read-only; input.* and look/lookAt
                #                                 answer -32012.
                #                     input_gui  = input.* only while a GUI screen is
                #                                 open; look/lookAt denied.
                #                     input_world= input.* only while NO GUI is open;
                #                                 look/lookAt allowed.
                #                     full       = everything allowed (default,
                #                                 identical to the pre-M2-D behaviour).
                port = %d
                token = "%s"
                input_enabled = %s
                rate_limit_per_sec = %d
                gui_click_evidence = %s
                keep_running_unfocused = %s
                permission = "%s"
                """.formatted(port, token, inputEnabled, rateLimitPerSec, guiClickEvidence,
                keepRunningUnfocused, permission.configName());
        try {
            Files.createDirectories(file.getParent());
            Files.writeString(file, content, StandardCharsets.UTF_8);
        } catch (IOException e) {
            SiriusBridge.LOGGER.error("Failed to write config {}: {}", file, e.toString());
        }
    }

    /** 64-char hex token from 32 random bytes. */
    private static String generateToken(SecureRandom random) {
        byte[] bytes = new byte[32];
        random.nextBytes(bytes);
        StringBuilder hex = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            hex.append(Character.forDigit((b >> 4) & 0xF, 16)).append(Character.forDigit(b & 0xF, 16));
        }
        return hex.toString();
    }

    /** Strips one pair of matching single/double quotes, if present. */
    private static String unquote(String value) {
        if (value.length() >= 2
                && ((value.startsWith("\"") && value.endsWith("\""))
                || (value.startsWith("'") && value.endsWith("'")))) {
            return value.substring(1, value.length() - 1);
        }
        return value;
    }

    /** Parses "true"/"false" (any case); null when the value is neither. */
    private static Boolean parseBoolean(String value) {
        if ("true".equalsIgnoreCase(value)) {
            return true;
        }
        if ("false".equalsIgnoreCase(value)) {
            return false;
        }
        return null;
    }
}
