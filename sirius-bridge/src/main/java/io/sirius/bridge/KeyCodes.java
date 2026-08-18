package io.sirius.bridge;

import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

/**
 * Logical key name -> GLFW keycode mapping for {@code input.key} (pure JDK,
 * no Minecraft classes - covered by the smoke test).
 *
 * <p>Keycodes are the GLFW constants exactly as 1.21.1's
 * {@code InputConstants} declares them (verified against the decompiled
 * sources): letters 65..90, digits 48..57, F1..F12 = 290..301, ESC 256,
 * ENTER 257, TAB 258, BACKSPACE 259, cursor keys 262..265, modifiers
 * 340..347 etc. Names are matched case-insensitively; common aliases
 * ({@code RETURN}/{@code ENTER}, {@code CTRL}/{@code CONTROL}/{@code LCTRL}, ...) resolve to the same
 * code. Reverse lookup ({@link #name(int)}) returns the canonical name used
 * for auditing and result echoing.
 */
public final class KeyCodes {

    private KeyCodes() {
    }

    /** GLFW keycodes in this range reach {@code InputConstants.getKey} as KEYSYM keys. */
    public static final int MIN_KEYCODE = 32;
    public static final int MAX_KEYCODE = 348;

    /** alias (upper case) -> keycode */
    private static final Map<String, Integer> ALIASES = new HashMap<>();
    /** canonical keycode -> canonical name (first alias registered wins) */
    private static final Map<Integer, String> CANONICAL = new TreeMap<>();

    static {
        // letters + digits (canonical: single character)
        for (char c = 'A'; c <= 'Z'; c++) {
            register(String.valueOf(c), c);
        }
        for (char c = '0'; c <= '9'; c++) {
            register(String.valueOf(c), c);
        }
        // whitespace / editing
        register("SPACE", 32);
        register("ENTER", 257);
        alias("RETURN", 257);
        register("ESCAPE", 256);
        alias("ESC", 256);
        register("TAB", 258);
        register("BACKSPACE", 259);
        register("INSERT", 260);
        alias("INS", 260);
        register("DELETE", 261);
        alias("DEL", 261);
        register("PAGE_UP", 266);
        alias("PAGEUP", 266);
        register("PAGE_DOWN", 267);
        alias("PAGEDOWN", 267);
        register("HOME", 268);
        register("END", 269);
        // cursor
        register("UP", 265);
        register("DOWN", 264);
        register("LEFT", 263);
        register("RIGHT", 262);
        // modifiers (canonical: the un-suffixed short form -> left variant)
        register("SHIFT", 340);
        alias("LSHIFT", 340);
        alias("LEFT_SHIFT", 340);
        register("RSHIFT", 344);
        alias("RIGHT_SHIFT", 344);
        register("CTRL", 341);
        alias("CONTROL", 341);
        alias("LCTRL", 341);
        alias("LEFT_CTRL", 341);
        register("RCTRL", 345);
        alias("RIGHT_CTRL", 345);
        register("ALT", 342);
        alias("LALT", 342);
        alias("LEFT_ALT", 342);
        register("RALT", 346);
        alias("RIGHT_ALT", 346);
        register("SUPER", 343);
        alias("WIN", 343);
        alias("META", 343);
        alias("LSUPER", 343);
        alias("LWIN", 343);
        alias("LEFT_WIN", 343);
        register("RSUPER", 347);
        alias("RWIN", 347);
        alias("RIGHT_WIN", 347);
        // punctuation (US layout keys)
        register("GRAVE", 96);
        alias("BACKQUOTE", 96);
        register("MINUS", 45);
        register("EQUAL", 61);
        alias("EQUALS", 61);
        register("LEFT_BRACKET", 91);
        alias("LBRACKET", 91);
        register("RIGHT_BRACKET", 93);
        alias("RBRACKET", 93);
        register("BACKSLASH", 92);
        register("SEMICOLON", 59);
        register("APOSTROPHE", 39);
        alias("QUOTE", 39);
        register("COMMA", 44);
        register("PERIOD", 46);
        alias("DOT", 46);
        register("SLASH", 47);
        // lock / system keys
        register("CAPS_LOCK", 280);
        alias("CAPSLOCK", 280);
        register("SCROLL_LOCK", 281);
        alias("SCROLLLOCK", 281);
        register("NUM_LOCK", 282);
        alias("NUMLOCK", 282);
        register("PRINT_SCREEN", 283);
        alias("PRINTSCREEN", 283);
        alias("PRTSC", 283);
        register("PAUSE", 284);
        register("MENU", 348);
        register("WORLD_1", 161);
        register("WORLD_2", 162);
        // function keys F1..F25
        for (int i = 1; i <= 25; i++) {
            register("F" + i, 289 + i);
        }
        // numpad
        for (int i = 0; i <= 9; i++) {
            register("NUMPAD" + i, 320 + i);
            alias("NUM" + i, 320 + i);
            alias("KP" + i, 320 + i);
        }
        register("NUMPAD_DECIMAL", 330);
        alias("NUMDEC", 330);
        alias("KPDEC", 330);
        register("NUMPAD_DIVIDE", 331);
        alias("NUMDIV", 331);
        alias("KPDIV", 331);
        register("NUMPAD_MULTIPLY", 332);
        alias("NUMMUL", 332);
        alias("KPMUL", 332);
        register("NUMPAD_SUBTRACT", 333);
        alias("NUMSUB", 333);
        alias("KPSUB", 333);
        register("NUMPAD_ADD", 334);
        alias("NUMADD", 334);
        alias("KPADD", 334);
        register("NUMPAD_ENTER", 335);
        alias("NUMENTER", 335);
        alias("KPENTER", 335);
        register("NUMPAD_EQUAL", 336);
        alias("NUMEQUAL", 336);
        alias("KPEQUAL", 336);
    }

    private static void register(String name, int code) {
        ALIASES.put(name, code);
        CANONICAL.putIfAbsent(code, name);
    }

    private static void alias(String name, int code) {
        ALIASES.put(name, code);
    }

    /** The canonical display name for {@code code} ("E", "ENTER", ...), or {@code null} when unknown. */
    public static String name(int code) {
        return CANONICAL.get(code);
    }

    /** True when {@code code} is inside the GLFW keysym range {@code input.key} accepts. */
    public static boolean isValidKeycode(int code) {
        return code >= MIN_KEYCODE && code <= MAX_KEYCODE;
    }

    /**
     * Resolves a logical key name (any case, alias allowed) to its GLFW
     * keycode. Throws {@link ToolContracts.InvalidParams} for unknown names,
     * listing a few examples so the caller can self-correct.
     */
    public static int resolve(String keyName) throws ToolContracts.InvalidParams {
        String normalized = keyName.trim().toUpperCase(Locale.ROOT);
        Integer code = ALIASES.get(normalized);
        if (code == null) {
            throw new ToolContracts.InvalidParams("unknown key name: \"" + keyName
                    + "\" (examples: \"E\", \"W\", \"ENTER\", \"SPACE\", \"BACKSPACE\", \"F3\","
                    + " \"LEFT_SHIFT\", \"UP\", \"NUMPAD5\"; or pass the raw GLFW keycode as an integer)");
        }
        return code;
    }
}
