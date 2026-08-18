package io.sirius.bridge;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Frozen-contract logic for the M2-A input tools ({@code input.key},
 * {@code input.text}, {@code input.mouseMove}, {@code input.click}):
 * parameter validation (mirroring the JSON schemas in
 * {@code sirius-brain/schema/tools}) plus response assembly and evidence
 * file naming. Pure Gson + JDK - no Minecraft classes - so the smoke test
 * covers it without a running game.
 *
 * <p>One deliberate extension beyond the frozen schema: {@code input.key}'s
 * {@code code} accepts a logical key NAME ("E", "ENTER", "LEFT_SHIFT" ...)
 * in addition to a raw GLFW keycode integer (which is what the schema
 * declares). The frozen schemas are not touched; the mod simply resolves
 * names to the same integers before injecting.
 */
public final class InputContracts {

    private InputContracts() {
    }

    // ------------------------------------------------------------------ key params

    /** Validated {@code input.key} params. */
    public record KeyParams(int glfwKey, String keyName, long durationMs, int modifiers, List<String> modifierNames) {
    }

    /** Default key press duration when {@code duration_ms} is 0/absent (a "tap"). */
    public static final long DEFAULT_TAP_MS = 50;

    /** Upper bound for {@code duration_ms} - guards against stuck keys. */
    public static final long MAX_DURATION_MS = 60_000;

    /** GLFW modifier bits (match GLFW_MOD_*). */
    public static final int MOD_SHIFT = 0x1;
    public static final int MOD_CONTROL = 0x2;
    public static final int MOD_ALT = 0x4;
    public static final int MOD_SUPER = 0x8;

    /**
     * Validates {@code input.key} params: {@code code} is a logical key name
     * ("E", "ENTER", ...) or a GLFW keycode integer; {@code duration_ms} an
     * integer 0..60000 (default 0 = tap, released after
     * {@link #DEFAULT_TAP_MS}); {@code modifiers} an array whose entries are
     * SHIFT/CTRL/ALT/SUPER (aliases allowed, duplicates collapse).
     */
    public static KeyParams keyParams(JsonObject params) throws ToolContracts.InvalidParams {
        JsonElement codeElement = params.get("code");
        if (codeElement == null || codeElement.isJsonNull()) {
            throw new ToolContracts.InvalidParams("input.key requires code: a key name (\"E\", \"ENTER\", ...)"
                    + " or a GLFW keycode integer");
        }
        int glfwKey;
        if (codeElement.isJsonPrimitive() && codeElement.getAsJsonPrimitive().isString()) {
            String name = codeElement.getAsString();
            if (name.trim().isEmpty()) {
                throw new ToolContracts.InvalidParams("input.key code must not be an empty string");
            }
            glfwKey = KeyCodes.resolve(name);
        } else if (codeElement.isJsonPrimitive() && codeElement.getAsJsonPrimitive().isNumber()) {
            double v = codeElement.getAsDouble();
            if (v != Math.floor(v)) {
                throw new ToolContracts.InvalidParams("input.key code must be an integer keycode, got: " + v);
            }
            glfwKey = (int) v;
            if (!KeyCodes.isValidKeycode(glfwKey)) {
                throw new ToolContracts.InvalidParams("input.key code " + glfwKey
                        + " is outside the GLFW keysym range " + KeyCodes.MIN_KEYCODE + ".." + KeyCodes.MAX_KEYCODE);
            }
        } else {
            throw new ToolContracts.InvalidParams("input.key code must be a string key name or an integer keycode");
        }
        String keyName = KeyCodes.name(glfwKey);
        if (keyName == null) {
            keyName = String.valueOf(glfwKey); // in-range but not in our table (e.g. intl key)
        }

        long durationMs = 0;
        JsonElement durationElement = params.get("duration_ms");
        if (durationElement != null && !durationElement.isJsonNull()) {
            if (!durationElement.isJsonPrimitive() || !durationElement.getAsJsonPrimitive().isNumber()) {
                throw new ToolContracts.InvalidParams("input.key duration_ms must be an integer 0.."
                        + MAX_DURATION_MS);
            }
            double d = durationElement.getAsDouble();
            if (d != Math.floor(d)) {
                throw new ToolContracts.InvalidParams("input.key duration_ms must be an integer, got: " + d);
            }
            if (d < 0) {
                throw new ToolContracts.InvalidParams("input.key duration_ms must be >= 0, got: " + (int) d);
            }
            if (d > MAX_DURATION_MS) {
                throw new ToolContracts.InvalidParams("input.key duration_ms must be <= " + MAX_DURATION_MS
                        + ", got: " + (long) d);
            }
            durationMs = (long) d;
        }

        java.util.ArrayList<String> modifierNames = new java.util.ArrayList<>();
        int modifiers = 0;
        JsonElement modifiersElement = params.get("modifiers");
        if (modifiersElement != null && !modifiersElement.isJsonNull()) {
            if (!modifiersElement.isJsonArray()) {
                throw new ToolContracts.InvalidParams("input.key modifiers must be an array of strings"
                        + " (SHIFT/CTRL/ALT/SUPER)");
            }
            for (JsonElement e : modifiersElement.getAsJsonArray()) {
                if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString()) {
                    throw new ToolContracts.InvalidParams("input.key modifiers entries must be strings"
                            + " (SHIFT/CTRL/ALT/SUPER)");
                }
                String raw = e.getAsString();
                String mod = raw.trim().toUpperCase(Locale.ROOT);
                switch (mod) {
                    case "SHIFT" -> modifiers |= MOD_SHIFT;
                    case "CTRL", "CONTROL" -> modifiers |= MOD_CONTROL;
                    case "ALT" -> modifiers |= MOD_ALT;
                    case "SUPER", "WIN", "META", "CMD" -> modifiers |= MOD_SUPER;
                    default -> throw new ToolContracts.InvalidParams("unknown modifier \"" + raw
                            + "\" (supported: SHIFT, CTRL, ALT, SUPER)");
                }
                if (!modifierNames.contains(mod)) {
                    modifierNames.add(mod);
                }
            }
        }
        return new KeyParams(glfwKey, keyName, durationMs, modifiers, List.copyOf(modifierNames));
    }

    // ------------------------------------------------------------------ text params

    /** Max text length, in Unicode codepoints. */
    public static final int MAX_TEXT_CODEPOINTS = 512;

    /** Validated {@code input.text} params. */
    public record TextParams(String text, int codepoints) {
    }

    /**
     * Validates {@code input.text} params: {@code string} required, 1..
     * {@link #MAX_TEXT_CODEPOINTS} codepoints (UTF-16 surrogate pairs are
     * delivered as single characters through the game's charTyped path).
     */
    public static TextParams textParams(JsonObject params) throws ToolContracts.InvalidParams {
        JsonElement stringElement = params.get("string");
        if (stringElement == null || stringElement.isJsonNull()
                || !stringElement.isJsonPrimitive() || !stringElement.getAsJsonPrimitive().isString()) {
            throw new ToolContracts.InvalidParams("input.text requires string (the text to type)");
        }
        String text = stringElement.getAsString();
        int codepoints = text.codePointCount(0, text.length());
        if (codepoints == 0) {
            throw new ToolContracts.InvalidParams("input.text string must not be empty");
        }
        if (codepoints > MAX_TEXT_CODEPOINTS) {
            throw new ToolContracts.InvalidParams("input.text string too long: " + codepoints
                    + " codepoints (max " + MAX_TEXT_CODEPOINTS + ")");
        }
        return new TextParams(text, codepoints);
    }

    // ------------------------------------------------------------------ mouseMove params

    /** Validated {@code input.mouseMove} params. */
    public record MouseMoveParams(double x, double y) {
    }

    /** Validates {@code input.mouseMove} params: finite numeric {@code x}/{code y}. */
    public static MouseMoveParams mouseMoveParams(JsonObject params) throws ToolContracts.InvalidParams {
        Double x = finiteNumber(params, "x", "input.mouseMove");
        Double y = finiteNumber(params, "y", "input.mouseMove");
        if (x == null || y == null) {
            throw new ToolContracts.InvalidParams("input.mouseMove requires finite numeric x and y"
                    + " (window client-area pixels, origin top-left)");
        }
        return new MouseMoveParams(x, y);
    }

    // ------------------------------------------------------------------ click params

    /** Max clicks per call (bounds the scheduled PRESS/RELEASE burst). */
    public static final int MAX_CLICK_COUNT = 8;

    /** Gap between repeated clicks and between PRESS and RELEASE inside one click. */
    public static final long CLICK_INTERVAL_MS = 50;
    public static final long CLICK_HOLD_MS = 25;

    /** Validated {@code input.click} params. */
    public record ClickParams(int button, int count) {
    }

    /**
     * Validates {@code input.click} params: {@code button} 0 (left) / 1
     * (right) / 2 (middle); {@code count} an integer 1..
     * {@link #MAX_CLICK_COUNT} (default 1; repeats fire every
     * {@link #CLICK_INTERVAL_MS} ms).
     */
    public static ClickParams clickParams(JsonObject params) throws ToolContracts.InvalidParams {
        JsonElement buttonElement = params.get("button");
        if (buttonElement == null || buttonElement.isJsonNull()
                || !buttonElement.isJsonPrimitive() || !buttonElement.getAsJsonPrimitive().isNumber()) {
            throw new ToolContracts.InvalidParams("input.click requires integer button: 0=left, 1=right, 2=middle");
        }
        double b = buttonElement.getAsDouble();
        if (b != Math.floor(b)) {
            throw new ToolContracts.InvalidParams("input.click button must be an integer, got: " + b);
        }
        int button = (int) b;
        if (button < 0 || button > 2) {
            throw new ToolContracts.InvalidParams("input.click button must be 0 (left), 1 (right) or 2 (middle),"
                    + " got: " + button);
        }

        int count = 1;
        JsonElement countElement = params.get("count");
        if (countElement != null && !countElement.isJsonNull()) {
            if (!countElement.isJsonPrimitive() || !countElement.getAsJsonPrimitive().isNumber()) {
                throw new ToolContracts.InvalidParams("input.click count must be an integer 1.." + MAX_CLICK_COUNT);
            }
            double c = countElement.getAsDouble();
            if (c != Math.floor(c)) {
                throw new ToolContracts.InvalidParams("input.click count must be an integer, got: " + c);
            }
            count = (int) c;
            if (count < 1) {
                throw new ToolContracts.InvalidParams("input.click count must be >= 1, got: " + count);
            }
            if (count > MAX_CLICK_COUNT) {
                throw new ToolContracts.InvalidParams("input.click count must be <= " + MAX_CLICK_COUNT
                        + ", got: " + count);
            }
        }
        return new ClickParams(button, count);
    }

    // ------------------------------------------------------------------ results

    /** {@code input.key} result: what was injected and when the RELEASE fires. */
    public static JsonObject keyResult(String keyName, int glfwKey, long effectiveDurationMs,
                                       int modifiers, boolean screenOpen) {
        JsonObject result = new JsonObject();
        result.addProperty("injected", true);
        result.addProperty("key", keyName);
        result.addProperty("glfw_key", glfwKey);
        result.addProperty("modifiers", modifiers);
        result.addProperty("duration_ms", effectiveDurationMs);
        result.addProperty("release_scheduled", true);
        result.addProperty("screen_open", screenOpen);
        return result;
    }

    /** {@code input.text} result: how many codepoints reached the charTyped path. */
    public static JsonObject textResult(int delivered, int total, boolean screenOpen) {
        JsonObject result = new JsonObject();
        result.addProperty("delivered", delivered);
        result.addProperty("codepoints", total);
        result.addProperty("screen_open", screenOpen);
        return result;
    }

    /** {@code input.mouseMove} result with the GUI-scaled position the game will use. */
    public static JsonObject mouseMoveResult(double x, double y, double guiX, double guiY, boolean screenOpen) {
        JsonObject guiScaled = new JsonObject();
        guiScaled.addProperty("x", guiX);
        guiScaled.addProperty("y", guiY);

        JsonObject result = new JsonObject();
        result.addProperty("moved", true);
        result.addProperty("x", x);
        result.addProperty("y", y);
        result.add("gui_scaled", guiScaled);
        result.addProperty("screen_open", screenOpen);
        return result;
    }

    /** {@code input.click} result; {@code evidence} is the saved JPEG's name+size or null. */
    public static JsonObject clickResult(int button, int count, boolean screenOpen, String screenName,
                                         String evidenceFile, long evidenceBytes) {
        JsonObject result = new JsonObject();
        result.addProperty("clicked", true);
        result.addProperty("button", button);
        result.addProperty("count", count);
        result.addProperty("screen_open", screenOpen);
        if (screenName != null) {
            result.addProperty("screen", screenName);
        }
        if (evidenceFile != null) {
            JsonObject evidence = new JsonObject();
            evidence.addProperty("file", evidenceFile);
            evidence.addProperty("bytes", evidenceBytes);
            result.add("evidence", evidence);
        }
        return result;
    }

    /** Error-frame message body for the {@code rate_limited} error. */
    public static String rateLimitedMessage(int limitPerSecond) {
        return "rate_limited: input.* calls are limited to ~" + limitPerSecond
                + "/s (token bucket empty); retry shortly";
    }

    /** Error-frame message body for the {@code input_disabled} error. */
    public static String inputDisabledMessage() {
        return "input_disabled: input injection is disabled in config/sirius_bridge.toml (input_enabled = false)";
    }

    // ------------------------------------------------------------------ evidence naming

    private static final DateTimeFormatter TIMESTAMP =
            DateTimeFormatter.ofPattern("yyyyMMdd_HHmmssSSS", Locale.ROOT);

    /**
     * Evidence file name for a GUI click screenshot:
     * {@code evidence_click_<yyyyMMdd_HHmmssSSS>.jpg} in the game's local
     * time zone (auditing is a human activity). Contains only
     * filesystem-safe characters.
     */
    public static String evidenceFileName(String kind, long epochMs) {
        LocalDateTime time = LocalDateTime.ofInstant(Instant.ofEpochMilli(epochMs), ZoneId.systemDefault());
        return "evidence_" + kind.replace('-', '_') + "_" + time.format(TIMESTAMP) + ".jpg";
    }

    // ------------------------------------------------------------------ helpers

    /** Reads a finite number member; null when absent/not numeric/not finite. */
    private static Double finiteNumber(JsonObject params, String member, String tool) {
        JsonElement e = params.get(member);
        if (e == null || e.isJsonNull() || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) {
            return null;
        }
        double v = e.getAsDouble();
        return Double.isFinite(v) ? v : null;
    }

    /** The fixed set of modifier names accepted in {@code input.key.modifiers} (for docs/tests). */
    public static final Set<String> MODIFIER_NAMES = Set.of("SHIFT", "CTRL", "CONTROL",
            "ALT", "SUPER", "WIN", "META", "CMD");
}
