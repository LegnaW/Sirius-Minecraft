package io.sirius.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Random;

/**
 * In-process smoke test for the M1-C perception tools, the M2-A pure input
 * logic, the M2-B event push channel and the M2-C GUI state tool (run via
 * {@code gradlew smokeTest}). No game, no client: it exercises the pure
 * halves - parameter validation, bbox cropping, the JPEG budget ladders,
 * response assembly, block scanning and entity filtering, the key-name ->
 * GLFW keycode table, the rate-limiter token bucket, input param validation
 * and evidence file naming, event subscription matching, notification frame
 * assembly, the screenshot stream throttle state machine and the streaming
 * budget ladder, plus widget/slot node assembly, the node cap + truncation
 * and all three getGuiState response shapes, the M2-D look param validation,
 * the eye->target rotation math (hand-computed cases), the permission-tier
 * decision matrix and the config round-trip of the new keys - exactly the
 * logic that would otherwise only be verifiable inside a running Minecraft.
 *
 * <p>Exit code 0 = all checks passed; any failure prints the check name and
 * exits 1.
 */
public final class SmokeMain {

    private static int passed;
    private static final List<String> failures = new ArrayList<>();

    public static void main(String[] args) throws Exception {
        screenshotParams();
        worldQueryParams();
        imageOps();
        contracts();
        keyCodes();
        tokenBucket();
        inputContracts();
        evidenceNaming();
        bridgeConfig();
        eventsContracts();
        streamThrottle();
        streamLadder();
        guiContracts();
        lookContracts();
        digContracts();
        permissionContracts();
        chatContracts();
        movementLook();

        System.out.println();
        System.out.println("smoke: " + passed + " passed, " + failures.size() + " failed");
        if (!failures.isEmpty()) {
            failures.forEach(f -> System.out.println("  FAILED: " + f));
            System.exit(1);
        }
        System.out.println("smoke: OK");
    }

    // ------------------------------------------------------------------ checks

    private static void keyCodes() throws Exception {
        // --- letters/digits, case-insensitive
        check(KeyCodes.resolve("E") == 69 && KeyCodes.resolve("e") == 69 && KeyCodes.resolve("W") == 87,
                "keycodes: letters E/W resolve to GLFW 69/87");
        check(KeyCodes.resolve("0") == 48 && KeyCodes.resolve("9") == 57, "keycodes: digits 0/9 resolve");
        // --- common control keys and aliases
        check(KeyCodes.resolve("ENTER") == 257 && KeyCodes.resolve("RETURN") == 257,
                "keycodes: ENTER/RETURN -> 257");
        check(KeyCodes.resolve("ESC") == 256 && KeyCodes.resolve("ESCAPE") == 256, "keycodes: ESC -> 256");
        check(KeyCodes.resolve("SPACE") == 32, "keycodes: SPACE -> 32");
        check(KeyCodes.resolve("TAB") == 258 && KeyCodes.resolve("BACKSPACE") == 259,
                "keycodes: TAB/BACKSPACE -> 258/259");
        check(KeyCodes.resolve("F1") == 290 && KeyCodes.resolve("F3") == 292 && KeyCodes.resolve("F12") == 301,
                "keycodes: F1/F3/F12 -> 290/292/301");
        check(KeyCodes.resolve("SHIFT") == 340 && KeyCodes.resolve("LSHIFT") == 340
                && KeyCodes.resolve("LEFT_SHIFT") == 340 && KeyCodes.resolve("RSHIFT") == 344,
                "keycodes: SHIFT aliases -> 340/344");
        check(KeyCodes.resolve("CTRL") == 341 && KeyCodes.resolve("CONTROL") == 341
                && KeyCodes.resolve("RCTRL") == 345 && KeyCodes.resolve("ALT") == 342,
                "keycodes: CTRL/ALT aliases -> 341/345/342");
        check(KeyCodes.resolve("UP") == 265 && KeyCodes.resolve("DOWN") == 264
                && KeyCodes.resolve("LEFT") == 263 && KeyCodes.resolve("RIGHT") == 262,
                "keycodes: arrows -> 262..265");
        check(KeyCodes.resolve("PAGEUP") == 266 && KeyCodes.resolve("PAGE_DOWN") == 267
                && KeyCodes.resolve("HOME") == 268 && KeyCodes.resolve("END") == 269,
                "keycodes: PAGE_UP/HOME/END");
        check(KeyCodes.resolve("NUMPAD5") == 325 && KeyCodes.resolve("KPENTER") == 335,
                "keycodes: numpad keys");
        check(KeyCodes.resolve("GRAVE") == 96 && KeyCodes.resolve("SLASH") == 47,
                "keycodes: punctuation keys");
        // --- unknown / empty names rejected
        expectInvalid(() -> KeyCodes.resolve("NOPE"), "keycodes: unknown name rejected");
        expectInvalid(() -> KeyCodes.resolve(""), "keycodes: empty name rejected");
        expectInvalid(() -> KeyCodes.resolve("  "), "keycodes: blank name rejected");
        // --- reverse lookup
        check("E".equals(KeyCodes.name(69)) && "ENTER".equals(KeyCodes.name(257))
                && "SHIFT".equals(KeyCodes.name(340)),
                "keycodes: canonical reverse names");
        check(KeyCodes.name(292) != null && KeyCodes.name(31) == null,
                "keycodes: reverse lookup misses unknown codes");
        // --- integer keycode validity range
        check(KeyCodes.isValidKeycode(32) && KeyCodes.isValidKeycode(348) && !KeyCodes.isValidKeycode(31)
                && !KeyCodes.isValidKeycode(349) && !KeyCodes.isValidKeycode(-1),
                "keycodes: keycode range 32..348 enforced");
    }

    private static void tokenBucket() {
        // Deterministic clock: 1 tick = 1 ms.
        long[] nanos = {0};
        TokenBucket bucket = new TokenBucket(20, () -> nanos[0]);

        boolean[] results = new boolean[25];
        for (int i = 0; i < results.length; i++) {
            results[i] = bucket.tryAcquire(1);
        }
        boolean first20 = true;
        for (int i = 0; i < 20; i++) {
            first20 &= results[i];
        }
        check(first20 && !results[20] && !results[21],
                "bucket: burst of 20 passes, 21st/22nd rejected");

        nanos[0] += 50_000_000L; // +50ms -> exactly 1 token refilled
        check(bucket.tryAcquire(1) && !bucket.tryAcquire(1),
                "bucket: 50ms at 20/s refills exactly one token");

        nanos[0] += 2_000_000_000L; // +2s idle -> capped at capacity
        double available = bucket.availableTokens();
        check(available <= 20.0 && bucket.tryAcquire(20) && !bucket.tryAcquire(1),
                "bucket: idle 2s refills to capacity cap (20)");
        check(bucket.tryAcquire(0), "bucket: zero-token acquire succeeds");
        check(bucket.refillPerSecond() == 20, "bucket: rate exposed");
    }

    private static void inputContracts() throws Exception {
        // --- input.key
        InputContracts.KeyParams k = validParams(() -> InputContracts.keyParams(json("{\"code\":\"E\"}")));
        check(k.glfwKey() == 69 && "E".equals(k.keyName()) && k.durationMs() == 0 && k.modifiers() == 0,
                "input.key: name code with defaults");

        k = validParams(() -> InputContracts.keyParams(
                json("{\"code\":\"LEFT_SHIFT\",\"duration_ms\":500,\"modifiers\":[\"CTRL\"]}")));
        check(k.glfwKey() == 340 && k.durationMs() == 500 && k.modifiers() == InputContracts.MOD_CONTROL,
                "input.key: duration + single modifier parsed");

        k = validParams(() -> InputContracts.keyParams(
                json("{\"code\":257,\"modifiers\":[\"SHIFT\",\"CTRL\",\"ALT\",\"SUPER\"]}")));
        check(k.glfwKey() == 257 && k.modifiers() == 0xF,
                "input.key: integer code + all modifiers -> bits 0xF");

        k = validParams(() -> InputContracts.keyParams(json("{\"code\":69.0}")));
        check(k.glfwKey() == 69, "input.key: whole-number float code accepted");

        expectInvalid(() -> InputContracts.keyParams(json("{}")), "input.key: missing code rejected");
        expectInvalid(() -> InputContracts.keyParams(json("{\"code\":\"NOPE\"}")),
                "input.key: unknown name rejected");
        expectInvalid(() -> InputContracts.keyParams(json("{\"code\":1000}")),
                "input.key: out-of-range keycode rejected");
        expectInvalid(() -> InputContracts.keyParams(json("{\"code\":69.5}")),
                "input.key: fractional keycode rejected");
        expectInvalid(() -> InputContracts.keyParams(json("{\"code\":[69]}")),
                "input.key: array code rejected");
        expectInvalid(() -> InputContracts.keyParams(json("{\"code\":\"E\",\"duration_ms\":-1}")),
                "input.key: negative duration rejected");
        expectInvalid(() -> InputContracts.keyParams(json("{\"code\":\"E\",\"duration_ms\":61000}")),
                "input.key: duration over cap rejected");
        expectInvalid(() -> InputContracts.keyParams(json("{\"code\":\"E\",\"duration_ms\":80.5}")),
                "input.key: fractional duration rejected");
        expectInvalid(() -> InputContracts.keyParams(json("{\"code\":\"E\",\"modifiers\":\"SHIFT\"}")),
                "input.key: non-array modifiers rejected");
        expectInvalid(() -> InputContracts.keyParams(json("{\"code\":\"E\",\"modifiers\":[\"HYPER\"]}")),
                "input.key: unknown modifier rejected");
        expectInvalid(() -> InputContracts.keyParams(json("{\"code\":\"E\",\"modifiers\":[3]}")),
                "input.key: numeric modifier entry rejected");

        // --- input.text
        InputContracts.TextParams t = validParams(() -> InputContracts.textParams(json("{\"string\":\"hi 你好\"}")));
        check("hi 你好".equals(t.text()) && t.codepoints() == 5, "input.text: unicode string parsed");

        t = validParams(() -> InputContracts.textParams(json("{\"string\":\"\\ud83d\\ude00\"}")));
        check(t.codepoints() == 1, "input.text: surrogate pair counts as one codepoint");

        expectInvalid(() -> InputContracts.textParams(json("{}")), "input.text: missing string rejected");
        expectInvalid(() -> InputContracts.textParams(json("{\"string\":\"\"}")),
                "input.text: empty string rejected");
        expectInvalid(() -> InputContracts.textParams(json("{\"string\":42}")),
                "input.text: non-string rejected");
        StringBuilder longText = new StringBuilder();
        for (int i = 0; i <= InputContracts.MAX_TEXT_CODEPOINTS; i++) {
            longText.append('x');
        }
        final String tooLong = longText.toString();
        expectInvalid(() -> InputContracts.textParams(json("{\"string\":\"" + tooLong + "\"}")),
                "input.text: over-length string rejected");

        // --- input.mouseMove
        InputContracts.MouseMoveParams m = validParams(() -> InputContracts.mouseMoveParams(
                json("{\"x\":100,\"y\":200.5}")));
        check(m.x() == 100.0 && m.y() == 200.5, "input.mouseMove: numeric pair parsed");
        expectInvalid(() -> InputContracts.mouseMoveParams(json("{\"x\":100}")),
                "input.mouseMove: missing y rejected");
        expectInvalid(() -> InputContracts.mouseMoveParams(json("{\"x\":\"100\",\"y\":200}")),
                "input.mouseMove: string coordinate rejected");

        // --- input.click
        InputContracts.ClickParams c = validParams(() -> InputContracts.clickParams(json("{\"button\":0}")));
        check(c.button() == 0 && c.count() == 1 && c.holdMs() == null,
                "input.click: defaults (count 1, no hold - v1.0 behaviour)");
        c = validParams(() -> InputContracts.clickParams(json("{\"button\":2,\"count\":3}")));
        check(c.button() == 2 && c.count() == 3 && c.holdMs() == null, "input.click: button/count parsed");
        expectInvalid(() -> InputContracts.clickParams(json("{}")), "input.click: missing button rejected");
        expectInvalid(() -> InputContracts.clickParams(json("{\"button\":3}")),
                "input.click: button 3 rejected");
        expectInvalid(() -> InputContracts.clickParams(json("{\"button\":-1}")),
                "input.click: negative button rejected");
        expectInvalid(() -> InputContracts.clickParams(json("{\"button\":0,\"count\":0}")),
                "input.click: count 0 rejected");
        expectInvalid(() -> InputContracts.clickParams(
                json("{\"button\":0,\"count\":" + (InputContracts.MAX_CLICK_COUNT + 1) + "}")),
                "input.click: count over cap rejected");
        expectInvalid(() -> InputContracts.clickParams(json("{\"button\":0.5}")),
                "input.click: fractional button rejected");

        // --- input.click hold_ms (M3.5 v1.1)
        c = validParams(() -> InputContracts.clickParams(json("{\"button\":0,\"hold_ms\":600}")));
        check(c.button() == 0 && c.holdMs() == 600 && c.count() == 1,
                "input.click: hold_ms parsed (count defaults 1)");
        c = validParams(() -> InputContracts.clickParams(json("{\"button\":1,\"hold_ms\":0}")));
        check(c.holdMs() == 0, "input.click: hold_ms 0 accepted (immediate release)");
        c = validParams(() -> InputContracts.clickParams(json("{\"button\":1,\"hold_ms\":10000}")));
        check(c.holdMs() == 10000, "input.click: hold_ms boundary 10000 accepted");
        c = validParams(() -> InputContracts.clickParams(json("{\"button\":1,\"hold_ms\":null}")));
        check(c.holdMs() == null, "input.click: explicit null hold_ms -> v1.0 tap");
        expectInvalid(() -> InputContracts.clickParams(json("{\"button\":0,\"hold_ms\":10001}")),
                "input.click: hold_ms over cap rejected");
        expectInvalid(() -> InputContracts.clickParams(json("{\"button\":0,\"hold_ms\":-1}")),
                "input.click: negative hold_ms rejected");
        expectInvalid(() -> InputContracts.clickParams(json("{\"button\":0,\"hold_ms\":0.5}")),
                "input.click: fractional hold_ms rejected");
        expectInvalid(() -> InputContracts.clickParams(json("{\"button\":0,\"hold_ms\":\"600\"}")),
                "input.click: string hold_ms rejected");
        expectInvalid(() -> InputContracts.clickParams(json("{\"button\":0,\"hold_ms\":[600]}")),
                "input.click: array hold_ms rejected");
        expectInvalid(() -> InputContracts.clickParams(json("{\"button\":0,\"count\":2,\"hold_ms\":100}")),
                "input.click: count + hold_ms rejected (mutually exclusive)");
        expectInvalid(() -> InputContracts.clickParams(json("{\"button\":0,\"count\":1,\"hold_ms\":100}")),
                "input.click: explicit count 1 + hold_ms still rejected");

        // --- input.click schedule: the timed PRESS/RELEASE plan (pure, so the
        // hold timing is verifiable without a client)
        List<InputContracts.ScheduledClick> tap = InputContracts.clickSchedule(
                new InputContracts.ClickParams(0, 1, null));
        check(tap.size() == 1 && !tap.get(0).press() && tap.get(0).delayMs() == InputContracts.CLICK_HOLD_MS,
                "input.click: default schedule = single RELEASE at +25ms (v1.0 unchanged)");
        List<InputContracts.ScheduledClick> burst = InputContracts.clickSchedule(
                new InputContracts.ClickParams(0, 3, null));
        boolean burstOrder = burst.size() == 5;
        boolean[] burstPresses = {false, true, false, true, false};
        long[] burstDelays = {25, 50, 75, 100, 125};
        for (int i = 0; burstOrder && i < burst.size(); i++) {
            burstOrder &= burst.get(i).press() == burstPresses[i] && burst.get(i).delayMs() == burstDelays[i];
        }
        check(burstOrder, "input.click: count 3 schedule = R@25 P@50 R@75 P@100 R@125 (v1.0 unchanged)");
        List<InputContracts.ScheduledClick> hold = InputContracts.clickSchedule(
                new InputContracts.ClickParams(0, 1, 600));
        check(hold.size() == 1 && !hold.get(0).press() && hold.get(0).delayMs() == 600
                        && hold.get(0).delayMs() > InputContracts.CLICK_HOLD_MS,
                "input.click: hold_ms schedule = single RELEASE delayed to +600ms");
        List<InputContracts.ScheduledClick> zeroHold = InputContracts.clickSchedule(
                new InputContracts.ClickParams(2, 1, 0));
        check(zeroHold.size() == 1 && zeroHold.get(0).delayMs() == 0,
                "input.click: hold_ms 0 schedules the RELEASE immediately");

        // --- result shapes
        JsonObject keyResult = InputContracts.keyResult("E", 69, 50, 1, true);
        check(keyResult.get("injected").getAsBoolean() && "E".equals(keyResult.get("key").getAsString())
                && keyResult.get("glfw_key").getAsInt() == 69 && keyResult.get("duration_ms").getAsInt() == 50,
                "input results: keyResult shape");

        JsonObject textResult = InputContracts.textResult(3, 3, true);
        check(textResult.get("delivered").getAsInt() == 3 && textResult.get("screen_open").getAsBoolean(),
                "input results: textResult shape");

        JsonObject moveResult = InputContracts.mouseMoveResult(100, 200, 50, 100, false);
        check(moveResult.get("moved").getAsBoolean() && moveResult.get("gui_scaled").getAsJsonObject()
                        .get("x").getAsDouble() == 50,
                "input results: mouseMoveResult shape");

        JsonObject clickResult = InputContracts.clickResult(0, 2, true, "InventoryScreen",
                "evidence_click_20260818_120000000.jpg", 12345, null);
        check(clickResult.get("clicked").getAsBoolean()
                        && "InventoryScreen".equals(clickResult.get("screen").getAsString())
                        && clickResult.get("evidence").getAsJsonObject().get("bytes").getAsLong() == 12345
                        && !clickResult.has("hold_ms"),
                "input results: clickResult shape");

        JsonObject clickNoEvidence = InputContracts.clickResult(1, 1, false, null, null, 0, null);
        check(!clickNoEvidence.has("screen") && !clickNoEvidence.has("evidence")
                        && !clickNoEvidence.has("hold_ms") && !clickNoEvidence.has("release_scheduled"),
                "input results: clickResult omits null fields");

        JsonObject clickHold = InputContracts.clickResult(0, 1, false, null, null, 0, 600);
        check(clickHold.get("hold_ms").getAsInt() == 600
                        && clickHold.get("release_scheduled").getAsBoolean(),
                "input results: clickResult echoes hold_ms + release_scheduled (v1.1)");

        check(InputContracts.rateLimitedMessage(20).contains("20"),
                "input results: rate-limited message mentions the limit");
    }

    private static void evidenceNaming() {
        long epochMs = 1755470400123L; // 2025-08-18 (local) ish - exact value irrelevant
        String name = InputContracts.evidenceFileName("click", epochMs);
        check(name.startsWith("evidence_click_") && name.endsWith(".jpg"),
                "evidence: name has prefix and .jpg suffix");
        check(name.matches("evidence_click_\\d{8}_\\d{9}\\.jpg"),
                "evidence: name matches timestamp pattern yyyyMMdd_HHmmssSSS");
        check(name.chars().allMatch(ch -> Character.isLetterOrDigit(ch) || ch == '_' || ch == '.'),
                "evidence: filesystem-safe characters only");
        check(!InputContracts.evidenceFileName("click", epochMs + 1).equals(name),
                "evidence: names differ across milliseconds");
    }

    private static void bridgeConfig() throws Exception {
        Path dir = Files.createTempDirectory("sirius_smoke_config");

        // --- fresh file: focus key defaults to true, file gains the new key
        Path fresh = dir.resolve("fresh.toml");
        BridgeConfig c = BridgeConfig.load(fresh, new SecureRandom());
        check(c.keepRunningUnfocused == BridgeConfig.DEFAULT_KEEP_RUNNING_UNFOCUSED,
                "config: keep_running_unfocused defaults to " + BridgeConfig.DEFAULT_KEEP_RUNNING_UNFOCUSED);
        String saved = Files.readString(fresh);
        check(saved.contains("keep_running_unfocused = true"),
                "config: fresh save writes keep_running_unfocused = true");
        check(saved.contains("rate_limit_per_sec = 20") && saved.contains("gui_click_evidence = true")
                        && saved.contains("input_enabled = true") && saved.contains("port = 8765"),
                "config: fresh save keeps the M2-A keys with their defaults");

        // --- explicit false round-trips through save + reload
        Path offFile = dir.resolve("off.toml");
        Files.writeString(offFile, """
                port = 9000
                token = "smoketoken"
                input_enabled = false
                keep_running_unfocused = false
                """);
        BridgeConfig off = BridgeConfig.load(offFile, new SecureRandom());
        check(!off.keepRunningUnfocused && !off.inputEnabled && off.port == 9000
                        && "smoketoken".equals(off.token) && !off.tokenGenerated,
                "config: keep_running_unfocused=false parsed alongside M2-A keys");
        check(Files.readString(offFile).contains("keep_running_unfocused = false")
                        && Files.readString(offFile).contains("rate_limit_per_sec = 20"),
                "config: save rewrites false value and keeps other keys");

        // --- case-insensitive booleans
        Path caseFile = dir.resolve("case.toml");
        Files.writeString(caseFile, "token = \"t\"\nkeep_running_unfocused = FALSE\n");
        check(!BridgeConfig.load(caseFile, new SecureRandom()).keepRunningUnfocused,
                "config: FALSE (any case) parses as false");

        // --- invalid value degrades to the default + a note, never fatal
        Path badFile = dir.resolve("bad.toml");
        Files.writeString(badFile, "token = \"t\"\nkeep_running_unfocused = maybe\n");
        BridgeConfig bad = BridgeConfig.load(badFile, new SecureRandom());
        check(bad.keepRunningUnfocused == BridgeConfig.DEFAULT_KEEP_RUNNING_UNFOCUSED
                        && bad.notes.contains("keep_running_unfocused"),
                "config: invalid value falls back to default with a note");

        // --- unknown keys are still ignored (forward compatibility)
        Path extraFile = dir.resolve("extra.toml");
        Files.writeString(extraFile, "token = \"t\"\nsome_future_key = 42\nkeep_running_unfocused = false\n");
        check(!BridgeConfig.load(extraFile, new SecureRandom()).keepRunningUnfocused,
                "config: unknown keys ignored, focus key still parsed");

        // --- M2-D permission key: default full, round-trip, case, invalid fallback
        Path permFile = dir.resolve("perm.toml");
        BridgeConfig perm = BridgeConfig.load(permFile, new SecureRandom());
        check(perm.permission == BridgeConfig.DEFAULT_PERMISSION
                        && perm.permission == PermissionContracts.Tier.FULL,
                "config: permission defaults to full (pre-M2-D behaviour)");
        check(Files.readString(permFile).contains("permission = \"full\""),
                "config: fresh save writes permission = \"full\" (auto-added to old files too)");
        Files.writeString(permFile, "token = \"t\"\npermission = \"observe\"\n");
        check(BridgeConfig.load(permFile, new SecureRandom()).permission == PermissionContracts.Tier.OBSERVE
                        && Files.readString(permFile).contains("permission = \"observe\""),
                "config: observe round-trips through save + reload");
        Files.writeString(permFile, "token = \"t\"\npermission = INPUT_WORLD\n");
        check(BridgeConfig.load(permFile, new SecureRandom()).permission
                        == PermissionContracts.Tier.INPUT_WORLD,
                "config: tier names parse case-insensitively");
        Files.writeString(permFile, "token = \"t\"\npermission = sudo\n");
        BridgeConfig badPerm = BridgeConfig.load(permFile, new SecureRandom());
        check(badPerm.permission == BridgeConfig.DEFAULT_PERMISSION
                        && badPerm.notes.contains("permission"),
                "config: invalid permission falls back to full with a note");
    }

    private static void screenshotParams() throws Exception {
        ToolContracts.ScreenshotParams p;

        p = validParams(() -> ToolContracts.screenshotParams(json("{\"tier\":\"full\"}")));
        check("full".equals(p.tier()) && p.bbox() == null && p.quality() == 80,
                "screenshot: full defaults (bbox null, quality 80)");

        p = validParams(() -> ToolContracts.screenshotParams(
                json("{\"tier\":\"crop\",\"bbox\":[10,20,300,400],\"quality\":90}")));
        check(Arrays.equals(p.bbox(), new int[]{10, 20, 300, 400}) && p.quality() == 90,
                "screenshot: crop + bbox + quality parsed");

        p = validParams(() -> ToolContracts.screenshotParams(
                json("{\"tier\":\"crop\",\"bbox\":[10.4,20.6,100.9,50.2],\"quality\":null}")));
        check(Arrays.equals(p.bbox(), new int[]{10, 21, 101, 50}) && p.quality() == 80,
                "screenshot: float bbox rounds, null quality defaults");

        expectInvalid(() -> ToolContracts.screenshotParams(json("{\"tier\":\"crop\"}")),
                "screenshot: crop without bbox rejected");
        expectInvalid(() -> ToolContracts.screenshotParams(json("{}")),
                "screenshot: missing tier rejected");
        expectInvalid(() -> ToolContracts.screenshotParams(json("{\"tier\":\"huge\"}")),
                "screenshot: bad tier enum rejected");
        expectInvalid(() -> ToolContracts.screenshotParams(json("{\"tier\":\"crop\",\"bbox\":[1,2,3]}")),
                "screenshot: short bbox rejected");
        expectInvalid(() -> ToolContracts.screenshotParams(json("{\"tier\":\"crop\",\"bbox\":[\"a\",2,3,4]}")),
                "screenshot: non-numeric bbox rejected");
        expectInvalid(() -> ToolContracts.screenshotParams(json("{\"tier\":\"crop\",\"bbox\":[0,0,0,10]}")),
                "screenshot: zero-width bbox rejected");
        expectInvalid(() -> ToolContracts.screenshotParams(json("{\"tier\":\"full\",\"quality\":101}")),
                "screenshot: quality 101 rejected");
        expectInvalid(() -> ToolContracts.screenshotParams(json("{\"tier\":\"full\",\"quality\":80.5}")),
                "screenshot: fractional quality rejected");
        expectInvalid(() -> ToolContracts.screenshotParams(json("{\"tier\":\"full\",\"quality\":\"80\"}")),
                "screenshot: string quality rejected");
    }

    private static void worldQueryParams() throws Exception {
        ToolContracts.WorldQueryParams p;

        p = validParams(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"range\":16}")));
        check("blocks".equals(p.type()) && p.range() == 16.0, "world.query: blocks range 16 parsed");

        p = validParams(() -> ToolContracts.worldQueryParams(json("{\"type\":\"entities\",\"range\":0.5}")));
        check(p.range() == 0.5, "world.query: fractional range accepted");

        p = validParams(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"range\":64}")));
        check(p.range() == 64.0, "world.query: range boundary 64 accepted");

        p = validParams(() -> ToolContracts.worldQueryParams(json("{\"type\":\"entities\"}")));
        check(p.range() == 16.0, "world.query: missing range defaults to 16");

        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"range\":16}")),
                "world.query: missing type rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"chunks\",\"range\":8}")),
                "world.query: bad type enum rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"range\":0}")),
                "world.query: zero range rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"range\":-3}")),
                "world.query: negative range rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"range\":64.5}")),
                "world.query: range over cap rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"range\":\"16\"}")),
                "world.query: string range rejected");

        // --- filter (M3.5 v1.1): absent/null keeps the v1.0 behaviour
        check(validParams(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"range\":16}")))
                .filter() == null, "world.query: absent filter -> null (v1.0 default)");
        check(validParams(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"filter\":null}")))
                .filter() == null, "world.query: explicit null filter -> v1.0 default");

        p = validParams(() -> ToolContracts.worldQueryParams(
                json("{\"type\":\"blocks\",\"filter\":[\"spruce_log\"]}")));
        check(p.filter().equals(List.of("minecraft:spruce_log")),
                "world.query: bare id gains minecraft: prefix");

        p = validParams(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"filter\":[\"#logs\"]}")));
        check(p.filter().equals(List.of("#minecraft:logs")),
                "world.query: bare #tag gains minecraft: prefix");

        p = validParams(() -> ToolContracts.worldQueryParams(json(
                "{\"type\":\"blocks\",\"filter\":[\"minecraft:oak_log\",\"#somemod:ores\",\"modid:block_2.x\"]}")));
        check(p.filter().equals(List.of("minecraft:oak_log", "#somemod:ores", "modid:block_2.x")),
                "world.query: namespaced ids/tags pass through unchanged");

        p = validParams(() -> ToolContracts.worldQueryParams(json("{\"type\":\"entities\",\"filter\":[\"zombie\"]}")));
        check(p.filter().equals(List.of("minecraft:zombie")),
                "world.query: entities filter entries normalized too");

        // boundary sizes: 16 entries / 128 chars are still valid
        JsonObject maxEntries = new JsonObject();
        maxEntries.addProperty("type", "blocks");
        JsonArray sixteen = new JsonArray();
        for (int i = 0; i < 16; i++) {
            sixteen.add("minecraft:block_" + i);
        }
        maxEntries.add("filter", sixteen);
        check(validParams(() -> ToolContracts.worldQueryParams(maxEntries)).filter().size() == 16,
                "world.query: 16 filter entries accepted (boundary)");
        p = validParams(() -> ToolContracts.worldQueryParams(
                json("{\"type\":\"blocks\",\"filter\":[\"" + "a".repeat(128) + "\"]}")));
        check(p.filter().get(0).equals("minecraft:" + "a".repeat(128)),
                "world.query: 128-char filter entry accepted (boundary, post-normalization)");

        JsonObject seventeen = new JsonObject();
        seventeen.addProperty("type", "blocks");
        JsonArray tooMany = new JsonArray();
        for (int i = 0; i < 17; i++) {
            tooMany.add("minecraft:block_" + i);
        }
        seventeen.add("filter", tooMany);
        expectInvalid(() -> ToolContracts.worldQueryParams(seventeen),
                "world.query: 17 filter entries rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"filter\":[]}")),
                "world.query: empty filter array rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"filter\":[\"\"]}")),
                "world.query: empty filter entry rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(
                json("{\"type\":\"blocks\",\"filter\":[\"" + "a".repeat(129) + "\"]}")),
                "world.query: 129-char filter entry rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"filter\":[\"Spruce_Log\"]}")),
                "world.query: uppercase filter entry rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"filter\":[\"oak log\"]}")),
                "world.query: filter entry with space rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"filter\":[\"a:b:c\"]}")),
                "world.query: filter entry with two colons rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"filter\":[\":stone\"]}")),
                "world.query: leading-colon filter entry rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"filter\":[\"#\"]}")),
                "world.query: bare # tag marker rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"filter\":\"spruce_log\"}")),
                "world.query: non-array filter rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(json("{\"type\":\"blocks\",\"filter\":[7]}")),
                "world.query: numeric filter entry rejected");
        expectInvalid(() -> ToolContracts.worldQueryParams(
                json("{\"type\":\"entities\",\"filter\":[\"#minecraft:skeletons\"]}")),
                "world.query: #tag filter on entities rejected");
    }

    private static void imageOps() throws Exception {
        // --- crop
        BufferedImage base = new BufferedImage(100, 60, BufferedImage.TYPE_INT_RGB);
        check(ImageOps.crop(base, new int[]{10, 10, 50, 20}).getWidth() == 50
                && ImageOps.crop(base, new int[]{10, 10, 50, 20}).getHeight() == 20,
                "image: exact crop is 50x20");
        BufferedImage clamped = ImageOps.crop(base, new int[]{90, 50, 100, 100});
        check(clamped.getWidth() == 10 && clamped.getHeight() == 10, "image: bbox clamped to image bounds");
        boolean threw = false;
        try {
            ImageOps.crop(base, new int[]{200, 200, 10, 10});
        } catch (IOException expected) {
            threw = true;
        }
        check(threw, "image: non-intersecting bbox throws");

        // --- JPEG round trip
        BufferedImage gradient = new BufferedImage(64, 48, BufferedImage.TYPE_INT_RGB);
        for (int y = 0; y < 48; y++) {
            for (int x = 0; x < 64; x++) {
                gradient.setRGB(x, y, (x * 4) << 16 | (y * 5) << 8 | 128);
            }
        }
        byte[] jpeg = ImageOps.encodeJpeg(gradient, 80);
        BufferedImage decoded = ImageIO.read(new ByteArrayInputStream(jpeg));
        check(decoded != null && decoded.getWidth() == 64 && decoded.getHeight() == 48,
                "image: JPEG decodable with matching dimensions");
        check(ImageOps.base64Length(jpeg) == ImageOps.base64(jpeg).length(),
                "image: base64 length predictor matches actual");

        // --- quality ladder
        check(Arrays.equals(ImageOps.qualityLadder(80), new int[]{80, 70, 60, 50, 40}),
                "image: ladder 80 -> [80,70,60,50,40]");
        check(Arrays.equals(ImageOps.qualityLadder(45), new int[]{45, 40}), "image: ladder 45 -> [45,40]");
        check(Arrays.equals(ImageOps.qualityLadder(30), new int[]{30}), "image: ladder 30 stays at 30");
        check(Arrays.equals(ImageOps.qualityLadder(40), new int[]{40}), "image: ladder 40 stays at 40");

        // --- scale
        BufferedImage wide = new BufferedImage(2048, 1024, BufferedImage.TYPE_INT_RGB);
        BufferedImage scaled = ImageOps.scaleLongestEdge(wide, 1024);
        check(scaled.getWidth() == 1024 && scaled.getHeight() == 512, "image: longest edge scaled to 1024");
        check(ImageOps.scaleLongestEdge(scaled, 1024) == scaled, "image: small image not rescaled");

        // --- budget: compressible image stays at full size
        ImageOps.Encoded flat = ImageOps.encodeWithinBudget(gradient, 80);
        check(!flat.downscaled() && flat.base64Length() <= ImageOps.MAX_BASE64_LENGTH,
                "image: small image within budget without downscale");
        check(flat.width() == 64 && flat.height() == 48,
                "image: 2MB overload reports the encoded dimensions");

        // --- budget: 4K incompressible noise must degrade (4K noise at q40 still
        // overruns 2MB of base64, so the ladder has to reach the 1024px scale).
        BufferedImage noise = new BufferedImage(3840, 2160, BufferedImage.TYPE_INT_RGB);
        java.util.Random random = new Random(42);
        int[] row = new int[3840];
        for (int y = 0; y < 2160; y++) {
            for (int x = 0; x < 3840; x++) {
                row[x] = random.nextInt(0x1000000);
            }
            noise.setRGB(0, y, 3840, 1, row, 0, 3840);
        }
        ImageOps.Encoded huge = ImageOps.encodeWithinBudget(noise, 80);
        BufferedImage hugeDecoded = ImageIO.read(new ByteArrayInputStream(huge.jpeg()));
        check(huge.base64Length() <= ImageOps.MAX_BASE64_LENGTH,
                "image: 4K noise ends within budget (" + huge.base64Length() + " b64 chars)");
        check(huge.downscaled() && Math.max(hugeDecoded.getWidth(), hugeDecoded.getHeight()) <= 1024,
                "image: 4K noise downscaled to <=1024 longest edge ("
                        + hugeDecoded.getWidth() + "x" + hugeDecoded.getHeight() + ")");
    }

    private static void contracts() {
        // --- notInGame
        check(!ToolContracts.notInGame().get("in_game").getAsBoolean(), "contracts: notInGame shape");

        // --- screenshotResult
        JsonObject shot = ToolContracts.screenshotResult("QUJD", 64, 48, 1724000000000L, 70, false);
        check("QUJD".equals(shot.get("image_b64").getAsString())
                && "jpeg".equals(shot.get("format").getAsString())
                && shot.get("width").getAsInt() == 64
                && shot.get("height").getAsInt() == 48
                && shot.get("taken_at").getAsLong() == 1724000000000L
                && shot.get("quality").getAsInt() == 70
                && !shot.get("downscaled").getAsBoolean(),
                "contracts: screenshotResult fields");

        // --- statsResult (yaw/pitch: M4.1 v1.3 additions, additive fields)
        ToolContracts.StatsSnapshot stats = new ToolContracts.StatsSnapshot(
                18.5f, 17, 4.2f, 300, 27, 0.6f,
                1.5, 64.0, -12.25, "minecraft:overworld", "survival",
                List.of(new ToolContracts.EffectFact("minecraft:speed", 1200, 1)), true,
                -91.5f, 12.25f);
        JsonObject statsJson = ToolContracts.statsResult(stats);
        check(statsJson.get("in_game").getAsBoolean()
                && statsJson.get("health").getAsFloat() == 18.5f
                && statsJson.get("food").getAsInt() == 17
                && statsJson.get("saturation").getAsFloat() == 4.2f
                && statsJson.get("air").getAsInt() == 300
                && statsJson.get("xp_level").getAsInt() == 27
                && statsJson.get("xp_progress").getAsFloat() == 0.6f
                && statsJson.get("position").getAsJsonObject().get("y").getAsDouble() == 64.0
                && "minecraft:overworld".equals(statsJson.get("dimension").getAsString())
                && "survival".equals(statsJson.get("game_mode").getAsString())
                && statsJson.get("effects").getAsJsonArray().size() == 1
                && statsJson.get("effects").getAsJsonArray().get(0).getAsJsonObject()
                        .get("id").getAsString().equals("minecraft:speed")
                && statsJson.get("alive").getAsBoolean()
                && statsJson.get("yaw").getAsFloat() == -91.5f
                && statsJson.get("pitch").getAsFloat() == 12.25f,
                "contracts: statsResult full shape (incl. M4.1 yaw/pitch)");

        // --- scanBlocks: solid 3x3x3 with one air hole, range 1
        JsonObject small = ToolContracts.scanBlocks(0, 0, 0, 1,
                (x, y, z) -> (x == 0 && y == 1 && z == 0) ? null : "minecraft:stone");
        check(small.get("count").getAsInt() == 26 && !small.get("truncated").getAsBoolean()
                        && small.get("blocks").getAsJsonArray().size() == 26
                        && "minecraft:stone".equals(small.get("blocks").getAsJsonArray().get(0)
                                .getAsJsonObject().get("block").getAsString()),
                "contracts: scanBlocks counts non-air, skips air");

        // --- scanBlocks: 11x11x11 solid -> truncated at 512
        JsonObject big = ToolContracts.scanBlocks(0, 0, 0, 5, (x, y, z) -> "minecraft:dirt");
        check(big.get("count").getAsInt() == ToolContracts.BLOCKS_CAP
                        && big.get("truncated").getAsBoolean(),
                "contracts: scanBlocks truncates at " + ToolContracts.BLOCKS_CAP);

        // --- scanBlocks: range 0.5 -> radius 1 cube (27 blocks max)
        JsonObject tiny = ToolContracts.scanBlocks(0, 0, 0, 0.5, (x, y, z) -> "minecraft:stone");
        check(tiny.get("count").getAsInt() == 27, "contracts: fractional range uses ceil radius");

        // --- filterEntities: distance + health omission + cap
        List<ToolContracts.EntityFact> facts = List.of(
                new ToolContracts.EntityFact("u-self", "Steve", "minecraft:player", 0, 64, 0, 20f),
                new ToolContracts.EntityFact("u-near", "Zombie", "minecraft:zombie", 5, 64, 0, 12f),
                new ToolContracts.EntityFact("u-far", "Zombie", "minecraft:zombie", 10, 64, 0, 12f),
                new ToolContracts.EntityFact("u-item", "Diamond", "minecraft:item", 2, 64, 0, Float.NaN));
        JsonObject entities = ToolContracts.filterEntities(facts, 0, 64, 0, 8, null);
        check(entities.get("count").getAsInt() == 3
                        && entities.get("entities").getAsJsonArray().size() == 3
                        && !entities.get("truncated").getAsBoolean(),
                "contracts: entities filtered by range (truncated false below cap)");
        JsonElement item = entities.get("entities").getAsJsonArray().get(2);
        check(!item.getAsJsonObject().has("health"),
                "contracts: NaN health omitted from entity entry");

        // --- filterEntities: item entities carry the dropped stack's registry
        // id + count (T7); non-item entities keep the exact pre-T7 shape
        List<ToolContracts.EntityFact> dropFacts = List.of(
                new ToolContracts.EntityFact("d-1", "Diamond", "minecraft:item",
                        2, 64, 1, Float.NaN, "minecraft:oak_log", 3),
                new ToolContracts.EntityFact("d-2", "Zombie", "minecraft:zombie",
                        3, 64, 1, 8f));
        JsonObject drops = ToolContracts.filterEntities(dropFacts, 0, 64, 0, 8, null);
        JsonObject dropEntry = drops.get("entities").getAsJsonArray().get(0).getAsJsonObject();
        check(dropEntry.has("item")
                        && "minecraft:oak_log".equals(dropEntry.get("item").getAsString())
                        && dropEntry.get("count").getAsInt() == 3,
                "contracts: item entity carries stack registry id + count (T7)");
        JsonObject nonItemEntry = drops.get("entities").getAsJsonArray().get(1).getAsJsonObject();
        check(!nonItemEntry.has("item") && !nonItemEntry.has("count"),
                "contracts: non-item entity keeps the pre-T7 shape (no item/count)");
        check(ToolContracts.filterEntities(dropFacts, 0, 64, 0, 16,
                        List.of("minecraft:item")).get("count").getAsInt() == 1,
                "contracts: entities type filter picks the item entity by type id");

        // --- filterEntities: M4 additive category + width fields
        List<ToolContracts.EntityFact> mobFacts = List.of(
                new ToolContracts.EntityFact("m-1", "Zombie", "minecraft:zombie",
                        2, 64, 1, 20f, null, 0, "monster", 0.6),
                new ToolContracts.EntityFact("m-2", "Cow", "minecraft:cow",
                        3, 64, 1, 10f, null, 0, "creature", 0.9),
                new ToolContracts.EntityFact("m-3", "Old", "minecraft:zombie",
                        1, 64, 1, 20f));
        JsonObject mobs = ToolContracts.filterEntities(mobFacts, 0, 64, 0, 8, null);
        JsonObject zombie = mobs.get("entities").getAsJsonArray().get(0).getAsJsonObject();
        check(zombie.has("category") && "monster".equals(zombie.get("category").getAsString())
                        && zombie.has("width") && Math.abs(zombie.get("width").getAsDouble() - 0.6) < 1e-9,
                "contracts: entity entry carries registry mob category + collision width (M4)");
        JsonObject cow = mobs.get("entities").getAsJsonArray().get(1).getAsJsonObject();
        check("creature".equals(cow.get("category").getAsString()),
                "contracts: passive mobs report their own category");
        JsonObject oldShape = mobs.get("entities").getAsJsonArray().get(2).getAsJsonObject();
        check(!oldShape.has("category") && !oldShape.has("width"),
                "contracts: pre-M4 constructors keep the old entry shape (additive only)");

        List<ToolContracts.EntityFact> crowd = new ArrayList<>();
        for (int i = 0; i < 200; i++) {
            crowd.add(new ToolContracts.EntityFact("u" + i, "E" + i, "minecraft:zombie", i % 4, 64, 0, 1f));
        }
        check(ToolContracts.filterEntities(crowd, 0, 64, 0, 16, null).get("count").getAsInt()
                        == ToolContracts.ENTITIES_CAP,
                "contracts: entities capped at " + ToolContracts.ENTITIES_CAP);
        check(ToolContracts.filterEntities(crowd, 0, 64, 0, 16, null).get("truncated").getAsBoolean(),
                "contracts: entity overflow flagged truncated (v1.1 fix)");

        List<ToolContracts.EntityFact> exactly = new ArrayList<>();
        for (int i = 0; i < ToolContracts.ENTITIES_CAP; i++) {
            exactly.add(new ToolContracts.EntityFact("u" + i, "E" + i, "minecraft:zombie", 1, 64, 0, 1f));
        }
        JsonObject full = ToolContracts.filterEntities(exactly, 0, 64, 0, 16, null);
        check(full.get("count").getAsInt() == ToolContracts.ENTITIES_CAP
                        && !full.get("truncated").getAsBoolean(),
                "contracts: exactly CAP entities -> truncated false");

        // --- filterEntities: type filter (v1.1)
        JsonObject zombies = ToolContracts.filterEntities(facts, 0, 64, 0, 16, List.of("minecraft:zombie"));
        check(zombies.get("count").getAsInt() == 2
                        && "minecraft:zombie".equals(zombies.get("entities").getAsJsonArray().get(0)
                                .getAsJsonObject().get("type").getAsString()),
                "contracts: entities type filter keeps only matching type");
        check(ToolContracts.filterEntities(facts, 0, 64, 0, 16, List.of("minecraft:creeper"))
                        .get("count").getAsInt() == 0,
                "contracts: entities type filter with no matches -> empty");
        check(ToolContracts.filterEntities(crowd, 0, 64, 0, 16, List.of("minecraft:zombie"))
                        .get("truncated").getAsBoolean(),
                "contracts: filtered entity overflow still flags truncated");

        // --- scanBlocks filtered (v1.1): id match, nearest-first, tie-break,
        // truncation, #tag and mixed filters. Player at (0.5, 64.5, 0.5) -
        // block centers are (x+0.5, y+0.5, z+0.5), so distSq(x,y,z) is
        // dx^2+dy^2+dz^2 against those offsets.
        ToolContracts.BlockProbe woods = (x, y, z) -> {
            if (x == 0 && y == 65 && z == 0) return "minecraft:oak_log";   // distSq 1
            if (x == 2 && y == 64 && z == 0) return "minecraft:oak_log";   // distSq 4
            if (x == -3 && y == 64 && z == 0) return "minecraft:oak_log";  // distSq 9 (tie, x breaks it)
            if (x == 0 && y == 64 && z == 3) return "minecraft:oak_log";   // distSq 9 (tie)
            if (x == 4 && y == 64 && z == 0) return "minecraft:stone";     // non-match
            return null;
        };
        JsonObject filtered = ToolContracts.scanBlocks(0.5, 64.5, 0.5, 8, woods,
                ToolContracts.BlockFilter.parse(List.of("minecraft:oak_log")), null);
        JsonArray kept = filtered.get("blocks").getAsJsonArray();
        check(filtered.get("count").getAsInt() == 4 && !filtered.get("truncated").getAsBoolean()
                        && kept.size() == 4,
                "contracts: filtered scan keeps only id matches");
        boolean nearestOrder = true;
        int[][] expected = {{0, 65, 0}, {2, 64, 0}, {-3, 64, 0}, {0, 64, 3}};
        double lastDist = -1;
        for (int i = 0; i < expected.length; i++) {
            JsonObject block = kept.get(i).getAsJsonObject();
            nearestOrder &= block.get("x").getAsInt() == expected[i][0]
                    && block.get("y").getAsInt() == expected[i][1]
                    && block.get("z").getAsInt() == expected[i][2]
                    && "minecraft:oak_log".equals(block.get("block").getAsString());
            double dx = block.get("x").getAsInt() + 0.5 - 0.5;
            double dy = block.get("y").getAsInt() + 0.5 - 64.5;
            double dz = block.get("z").getAsInt() + 0.5 - 0.5;
            double distSq = dx * dx + dy * dy + dz * dz;
            nearestOrder &= distSq >= lastDist;
            lastDist = distSq;
        }
        check(nearestOrder, "contracts: filtered scan ordered nearest-first (ties by x,y,z)");

        // 40 matches -> the 32 NEAREST survive, truncated true
        ToolContracts.BlockProbe row = (x, y, z) ->
                (y == 64 && z == 0 && x >= 1 && x <= 40) ? "minecraft:oak_log" : null;
        JsonObject overflow = ToolContracts.scanBlocks(0.5, 64.5, 0.5, 64, row,
                ToolContracts.BlockFilter.parse(List.of("minecraft:oak_log")), null);
        JsonArray overflowBlocks = overflow.get("blocks").getAsJsonArray();
        check(overflow.get("count").getAsInt() == ToolContracts.FILTERED_BLOCKS_CAP
                        && overflow.get("truncated").getAsBoolean(),
                "contracts: filtered scan caps at " + ToolContracts.FILTERED_BLOCKS_CAP + " with truncated");
        check(overflowBlocks.get(0).getAsJsonObject().get("x").getAsInt() == 1
                        && overflowBlocks.get(overflowBlocks.size() - 1).getAsJsonObject().get("x").getAsInt() == 32,
                "contracts: overflow keeps the NEAREST 32 (x=1..32 of 1..40)");

        // exactly 32 matches -> truncated false (boundary)
        ToolContracts.BlockProbe row32 = (x, y, z) ->
                (y == 64 && z == 0 && x >= 1 && x <= 32) ? "minecraft:oak_log" : null;
        JsonObject exact = ToolContracts.scanBlocks(0.5, 64.5, 0.5, 64, row32,
                ToolContracts.BlockFilter.parse(List.of("minecraft:oak_log")), null);
        check(exact.get("count").getAsInt() == ToolContracts.FILTERED_BLOCKS_CAP
                        && !exact.get("truncated").getAsBoolean(),
                "contracts: exactly 32 matches -> truncated false");

        // #tag filter through a fake TagProbe (real one wraps BlockState.is)
        ToolContracts.TagProbe logsTag = (name, tagId) -> "minecraft:logs".equals(tagId)
                && (name.equals("minecraft:oak_log") || name.equals("minecraft:spruce_log"));
        ToolContracts.BlockProbe grove = (x, y, z) -> switch (x + "," + y + "," + z) {
            case "1,64,0" -> "minecraft:oak_log";     // distSq 1
            case "-2,64,0" -> "minecraft:spruce_log"; // distSq 4
            case "3,64,0" -> "minecraft:stone";       // not in tag
            default -> null;
        };
        JsonObject tagged = ToolContracts.scanBlocks(0.5, 64.5, 0.5, 8, grove,
                ToolContracts.BlockFilter.parse(List.of("#minecraft:logs")), logsTag);
        JsonArray taggedBlocks = tagged.get("blocks").getAsJsonArray();
        check(tagged.get("count").getAsInt() == 2
                        && "minecraft:oak_log".equals(taggedBlocks.get(0).getAsJsonObject().get("block").getAsString())
                        && "minecraft:spruce_log".equals(taggedBlocks.get(1).getAsJsonObject().get("block").getAsString()),
                "contracts: #tag filter matches tag members only, nearest-first");

        // mixed id + tag entries
        JsonObject mixed = ToolContracts.scanBlocks(0.5, 64.5, 0.5, 8, grove,
                ToolContracts.BlockFilter.parse(List.of("minecraft:stone", "#minecraft:logs")), logsTag);
        check(mixed.get("count").getAsInt() == 3 && !mixed.get("truncated").getAsBoolean(),
                "contracts: mixed id+#tag filter unions matches");

        // nothing matches -> empty result, not an error
        JsonObject miss = ToolContracts.scanBlocks(0.5, 64.5, 0.5, 8, woods,
                ToolContracts.BlockFilter.parse(List.of("minecraft:diamond_ore")), null);
        check(miss.get("count").getAsInt() == 0 && miss.get("blocks").getAsJsonArray().size() == 0
                        && !miss.get("truncated").getAsBoolean(),
                "contracts: filter with no matches -> empty, truncated false");

        // --- T6 dense-scene regression (M3.5-T5a evidence): a NEAR match must
        // survive a filtered query with far more than 512 matches. Player at
        // (0.5,64.5,0.5) - i.e. standing INSIDE block (0,64,0) (distSq 0) with
        // a second match at distSq 1; the rel-band filler yields ~1700 farther
        // matches across the cube (x ascending - the same corner order that
        // made the unfiltered v1.0 path drop near blocks at range>=5.5).
        // Collect-all + sort must keep the two nearest regardless.
        ToolContracts.BlockProbe dense = (x, y, z) -> {
            if (x == 0 && y == 65 && z == 0) return "minecraft:oak_log"; // distSq 1 - the "3.71-block target" stand-in
            if (x == 0 && y == 64 && z == 0) return "minecraft:oak_log"; // distSq 0 - block at the player's feet
            int rel = x + (y - 64) + z;
            return rel >= -5 && rel <= 5 ? "minecraft:oak_log" : null; // ~1700 farther fillers
        };
        JsonObject denseQuery = ToolContracts.scanBlocks(0.5, 64.5, 0.5, 8, dense,
                ToolContracts.BlockFilter.parse(List.of("minecraft:oak_log")), null);
        JsonArray denseBlocks = denseQuery.get("blocks").getAsJsonArray();
        boolean nearKept = false;
        for (JsonElement e : denseBlocks) {
            JsonObject b = e.getAsJsonObject();
            nearKept |= b.get("x").getAsInt() == 0 && b.get("y").getAsInt() == 65 && b.get("z").getAsInt() == 0;
        }
        check(denseQuery.get("count").getAsInt() == ToolContracts.FILTERED_BLOCKS_CAP
                        && denseQuery.get("truncated").getAsBoolean()
                        && denseBlocks.get(0).getAsJsonObject().get("x").getAsInt() == 0
                        && denseBlocks.get(0).getAsJsonObject().get("y").getAsInt() == 64
                        && denseBlocks.get(0).getAsJsonObject().get("z").getAsInt() == 0
                        && nearKept,
                "contracts: T6 dense filtered query keeps the NEAREST matches despite >512 matches");

        // containment law (the T5a suggested regression): a smaller range's
        // hits must all appear in the bigger range's (truncated) result -
        // truncation may only ever drop the FAR tail.
        ToolContracts.BlockProbe sparse = (x, y, z) ->
                ((y == 64 || y == 65) && Math.abs(x) <= 6 && Math.abs(z) <= 6
                        && (x + z) % 2 == 0) ? "minecraft:oak_log" : null;
        JsonObject smallRange = ToolContracts.scanBlocks(0.5, 64.5, 0.5, 1.5, sparse,
                ToolContracts.BlockFilter.parse(List.of("minecraft:oak_log")), null);
        JsonObject bigRange = ToolContracts.scanBlocks(0.5, 64.5, 0.5, 6.4, sparse,
                ToolContracts.BlockFilter.parse(List.of("minecraft:oak_log")), null);
        java.util.Set<String> bigSet = new java.util.HashSet<>();
        for (JsonElement e : bigRange.get("blocks").getAsJsonArray()) {
            JsonObject b = e.getAsJsonObject();
            bigSet.add(b.get("x") + "," + b.get("y") + "," + b.get("z"));
        }
        boolean contained = !smallRange.get("blocks").getAsJsonArray().isEmpty();
        for (JsonElement e : smallRange.get("blocks").getAsJsonArray()) {
            JsonObject b = e.getAsJsonObject();
            contained &= bigSet.contains(b.get("x") + "," + b.get("y") + "," + b.get("z"));
        }
        check(contained && !smallRange.get("truncated").getAsBoolean()
                        && bigRange.get("truncated").getAsBoolean(),
                "contracts: T6 small-range hits are contained in the big-range result (truncation drops only the far tail)");

        // memory guard: a pathological filter over a solid cube flags truncated
        // at the guard without building an unbounded list
        ToolContracts.BlockProbe solid = (x, y, z) -> "minecraft:stone";
        JsonObject guarded = ToolContracts.scanBlocks(0.5, 64.5, 0.5, 64, solid,
                ToolContracts.BlockFilter.parse(List.of("minecraft:stone")), null);
        check(guarded.get("count").getAsInt() == ToolContracts.FILTERED_BLOCKS_CAP
                        && guarded.get("truncated").getAsBoolean(),
                "contracts: T6 filtered memory guard flags truncated on pathological filters");
    }

    // ------------------------------------------------------------------ M2-B: events

    private static void eventsContracts() throws Exception {
        EventsContracts.EventLevel CRITICAL = EventsContracts.EventLevel.CRITICAL;
        EventsContracts.EventLevel WARNING = EventsContracts.EventLevel.WARNING;
        EventsContracts.EventLevel INFO = EventsContracts.EventLevel.INFO;

        // --- level ordering + parsing
        check(CRITICAL.atLeast(WARNING) && WARNING.atLeast(INFO) && CRITICAL.atLeast(CRITICAL)
                        && !INFO.atLeast(WARNING) && !WARNING.atLeast(CRITICAL),
                "events: level ordering CRITICAL > WARNING > INFO");
        check(EventsContracts.EventLevel.fromName("critical") == CRITICAL
                        && EventsContracts.EventLevel.fromName("WARNING") == WARNING
                        && EventsContracts.EventLevel.fromName("nope") == null
                        && EventsContracts.EventLevel.fromName(null) == null,
                "events: level name parsing (case-insensitive, unknown/null -> null)");

        // --- subscription matching: exact / wildcard / empty / level filter
        EventsContracts.Subscription all = new EventsContracts.Subscription(java.util.Set.of("*"), null);
        check(all.matches("chat", INFO) && all.matches("death", CRITICAL),
                "events: \"*\" subscription matches every type");
        EventsContracts.Subscription empty = new EventsContracts.Subscription(java.util.Set.of(), WARNING);
        check(empty.matches("chat", WARNING) && empty.matches("death", CRITICAL) && !empty.matches("chat", INFO),
                "events: empty types = all types, min_level still filters");
        EventsContracts.Subscription chatOnly = new EventsContracts.Subscription(java.util.Set.of("chat"), null);
        check(chatOnly.matches("chat", INFO) && !chatOnly.matches("gui_open", WARNING),
                "events: exact type match, other types rejected");
        EventsContracts.Subscription criticalOnly =
                new EventsContracts.Subscription(java.util.Set.of("death", "fire"), CRITICAL);
        check(criticalOnly.matches("death", CRITICAL) && !criticalOnly.matches("chat", CRITICAL),
                "events: exact types + CRITICAL floor");
        check(new EventsContracts.Subscription(java.util.Set.of("chat"), null).effectiveMinLevel() == INFO,
                "events: null min_level defaults to INFO (no filtering)");

        // --- events.subscribe params (frozen schema tools/events.subscribe.json)
        EventsContracts.SubscribeParams p = validParams(() ->
                EventsContracts.subscribeParams(json("{\"types\":[\"chat\",\"death\"]}")));
        check(p.types().size() == 2 && p.types().contains("chat") && p.types().contains("death")
                        && p.minLevel() == null,
                "events.subscribe: types parsed, min_level defaults null");
        p = validParams(() -> EventsContracts.subscribeParams(
                json("{\"types\":[\"*\"],\"min_level\":\"CRITICAL\"}")));
        check(p.types().contains("*") && p.minLevel() == CRITICAL,
                "events.subscribe: wildcard + CRITICAL min_level parsed");
        p = validParams(() -> EventsContracts.subscribeParams(json("{\"types\":[]}")));
        check(p.types().isEmpty(), "events.subscribe: empty types array accepted (= all events)");
        p = validParams(() -> EventsContracts.subscribeParams(
                json("{\"types\":[\"chat\"],\"min_level\":null}")));
        check(p.minLevel() == null, "events.subscribe: explicit null min_level accepted");
        p = validParams(() -> EventsContracts.subscribeParams(
                json("{\"types\":[\"chat\",\"chat\"]}")));
        check(p.types().size() == 1, "events.subscribe: duplicate types collapse");

        expectInvalid(() -> EventsContracts.subscribeParams(json("{}")),
                "events.subscribe: missing types rejected");
        expectInvalid(() -> EventsContracts.subscribeParams(json("{\"types\":\"chat\"}")),
                "events.subscribe: non-array types rejected");
        expectInvalid(() -> EventsContracts.subscribeParams(json("{\"types\":[42]}")),
                "events.subscribe: non-string type entry rejected");
        expectInvalid(() -> EventsContracts.subscribeParams(json("{\"types\":null}")),
                "events.subscribe: null types rejected");
        expectInvalid(() -> EventsContracts.subscribeParams(
                json("{\"types\":[\"chat\"],\"min_level\":\"LOUD\"}")),
                "events.subscribe: unknown min_level rejected");
        expectInvalid(() -> EventsContracts.subscribeParams(
                json("{\"types\":[\"chat\"],\"min_level\":3}")),
                "events.subscribe: numeric min_level rejected");

        // --- subscribe result shape
        JsonObject result = EventsContracts.subscribeResult(
                new EventsContracts.SubscribeParams(new java.util.LinkedHashSet<>(List.of("chat", "gui_open")),
                        WARNING));
        check(result.get("subscribed").getAsBoolean()
                        && result.get("types").getAsJsonArray().size() == 2
                        && "WARNING".equals(result.get("min_level").getAsString())
                        && result.get("note").getAsString().contains("no pushes"),
                "events.subscribe: result echoes types/min_level/note");
        JsonObject defaultLevel = EventsContracts.subscribeResult(
                new EventsContracts.SubscribeParams(java.util.Set.of("chat"), null));
        check("INFO".equals(defaultLevel.get("min_level").getAsString()),
                "events.subscribe: result echoes effective INFO default");

        // --- notification frame assembly
        JsonObject data = new JsonObject();
        data.addProperty("hp", 3);
        JsonObject frame = EventsContracts.notification("fire", data, CRITICAL, 1724000000.5, 7);
        check("notification".equals(frame.get("type").getAsString())
                        && "fire".equals(frame.get("event").getAsString()),
                "events: notification type/event fields");
        check(frame.get("data").getAsJsonObject().get("hp").getAsInt() == 3
                        && "CRITICAL".equals(frame.get("data").getAsJsonObject().get("level").getAsString()),
                "events: level injected into data alongside original fields");
        check(frame.get("timestamp").getAsJsonPrimitive().isNumber()
                        && frame.get("timestamp").getAsDouble() == 1724000000.5,
                "events: timestamp is a float number (epoch seconds)");
        check(frame.get("seq").getAsJsonPrimitive().isNumber()
                        && frame.get("seq").getAsLong() == 7
                        && frame.get("seq").getAsDouble() == 7.0,
                "events: seq is an integral number");
        check(!data.has("level"),
                "events: caller data object not mutated (deep copy)");
        JsonObject preset = new JsonObject();
        preset.addProperty("level", "INFO");
        frame = EventsContracts.notification("x", preset, CRITICAL, 0.0, 0);
        check("INFO".equals(frame.get("data").getAsJsonObject().get("level").getAsString()),
                "events: existing data.level wins over injected level");
        frame = EventsContracts.notification("x", null, WARNING, 0.0, 0);
        check(frame.get("data").getAsJsonObject().size() == 1
                        && frame.get("data").getAsJsonObject().has("level"),
                "events: null data yields level-only payload");
        check(EventsContracts.timestampNowSeconds(1724000000123L) == 1724000000.123,
                "events: milliseconds -> epoch seconds conversion (Python time.time() units)");
    }

    private static void streamThrottle() {
        // Deterministic injected clock; 6s window like the real stream.
        EventsContracts.StreamThrottle<String> throttle =
                new EventsContracts.StreamThrottle<>(6_000);

        check(throttle.offer("a", 0) == EventsContracts.StreamThrottle.Decision.PUSH_NOW,
                "throttle: first frame pushes immediately");
        check(throttle.offer("b", 5_000) == EventsContracts.StreamThrottle.Decision.DEFER
                        && throttle.hasPending(),
                "throttle: frame inside window defers to pending slot");
        check(throttle.takeDue(5_999) == null,
                "throttle: flush not due before the boundary");
        check(throttle.offer("c", 5_500) == EventsContracts.StreamThrottle.Decision.DEFER,
                "throttle: deferred frame replaces pending (latest wins)");
        check("c".equals(throttle.takeDue(6_000)) && !throttle.hasPending(),
                "throttle: delayed flush fires at the boundary with the newest frame");
        check(throttle.offer("d", 11_999) == EventsContracts.StreamThrottle.Decision.DEFER,
                "throttle: window closed again right after a boundary flush");
        check(throttle.offer("d", 12_000) == EventsContracts.StreamThrottle.Decision.PUSH_NOW,
                "throttle: window reopens exactly min-interval after the flush");

        // PUSH_NOW cancels an armed flush and clears the slot (N.E.K.O race note).
        EventsContracts.StreamThrottle<String> armed =
                new EventsContracts.StreamThrottle<>(6_000);
        armed.offer("a", 0);
        armed.offer("b", 1_000);
        check(armed.flushAtMs() == 6_000,
                "throttle: one flush armed at window-open time");
        check(armed.offer("c", 7_000) == EventsContracts.StreamThrottle.Decision.PUSH_NOW,
                "throttle: window reopened by wall clock");
        check(armed.flushAtMs() == -1 && !armed.hasPending(),
                "throttle: immediate push disarms flush and clears pending slot");
        check(armed.takeDue(8_000) == null,
                "throttle: no stale flush surfaces an older frame");

        EventsContracts.StreamThrottle<String> zero =
                new EventsContracts.StreamThrottle<>(0);
        check(zero.offer("x", 1) == EventsContracts.StreamThrottle.Decision.PUSH_NOW
                        && zero.offer("y", 1) == EventsContracts.StreamThrottle.Decision.PUSH_NOW,
                "throttle: zero interval never defers");
    }

    private static void streamLadder() throws Exception {
        // --- ladder parameterization
        check(Arrays.equals(ImageOps.streamQualityLadder(80), new int[]{80, 65, 50, 40, 30}),
                "ladder: quality 80 -> [80,65,50,40,30]");
        check(Arrays.equals(ImageOps.streamQualityLadder(100), new int[]{100, 65, 50, 40, 30}),
                "ladder: quality 100 -> [100,65,50,40,30]");
        check(Arrays.equals(ImageOps.streamQualityLadder(65), new int[]{65, 50, 40, 30}),
                "ladder: quality 65 -> [65,50,40,30]");
        check(Arrays.equals(ImageOps.streamQualityLadder(30), new int[]{30}),
                "ladder: quality 30 stays at 30");
        check(Arrays.equals(ImageOps.streamQualityLadder(20), new int[]{20}),
                "ladder: quality below the list floor collapses to itself");
        check(Arrays.equals(ImageOps.streamEdgeLadder(1024), new int[]{1024, 512, 256}),
                "ladder: edge 1024 -> [1024,512,256]");
        check(Arrays.equals(ImageOps.streamEdgeLadder(100), new int[]{100, 50, 25}),
                "ladder: edge 100 -> [100,50,25]");
        check(ImageOps.streamEdgeLadder(0).length == 0,
                "ladder: edge 0 disables scaling (empty ladder)");

        // --- streaming budget encode: incompressible noise forces ladder descent
        BufferedImage noise = new BufferedImage(800, 600, BufferedImage.TYPE_INT_RGB);
        java.util.Random random = new Random(7);
        int[] row = new int[800];
        for (int y = 0; y < 600; y++) {
            for (int x = 0; x < 800; x++) {
                row[x] = random.nextInt(0x1000000);
            }
            noise.setRGB(0, y, 800, 1, row, 0, 800);
        }
        ImageOps.Encoded stream = ImageOps.encodeWithinBudget(noise, EventsContracts.STREAM_QUALITY,
                EventsContracts.STREAM_MAX_BASE64, EventsContracts.STREAM_LONGEST_EDGE);
        BufferedImage decoded = ImageIO.read(new ByteArrayInputStream(stream.jpeg()));
        check(stream.base64Length() <= EventsContracts.STREAM_MAX_BASE64,
                "ladder: noise stream frame ends within the 100KB budget ("
                        + stream.base64Length() + " b64 chars)");
        check(stream.quality() <= EventsContracts.STREAM_QUALITY
                        && Math.max(decoded.getWidth(), decoded.getHeight()) <= EventsContracts.STREAM_LONGEST_EDGE,
                "ladder: descent respected quality cap and edge cap (q" + stream.quality()
                        + " " + decoded.getWidth() + "x" + decoded.getHeight() + ")");
        check(stream.downscaled() && stream.width() == decoded.getWidth()
                        && stream.height() == decoded.getHeight(),
                "ladder: Encoded reports downscaled + the encoded image's dimensions");

        // --- small image, generous budget: first rung, no descent
        BufferedImage flat = new BufferedImage(64, 48, BufferedImage.TYPE_INT_RGB);
        ImageOps.Encoded easy = ImageOps.encodeWithinBudget(flat, 80, EventsContracts.STREAM_MAX_BASE64, 1024);
        check(!easy.downscaled() && easy.quality() == 80 && easy.width() == 64 && easy.height() == 48,
                "ladder: tiny image stays at first rung unscaled");

        // --- impossible budget: ship the smallest attempt, never drop the frame
        ImageOps.Encoded lastResort = ImageOps.encodeWithinBudget(noise, 80, 500, 1024);
        check(lastResort != null && lastResort.jpeg().length > 0 && lastResort.base64Length() > 500,
                "ladder: unreachable budget ships the smallest attempt (no drop)");
        check(lastResort.quality() == 30 && lastResort.downscaled(),
                "ladder: smallest attempt is the last rung (q30, scaled)");

        // --- ring buffer maxlen (spec: 3 frames)
        ArrayDeque<Integer> ring = new ArrayDeque<>();
        for (int i = 1; i <= 5; i++) {
            EventsContracts.ringAdd(ring, i, EventsContracts.STREAM_RING_SIZE);
        }
        check(ring.size() == EventsContracts.STREAM_RING_SIZE && ring.peekFirst() == 3 && ring.peekLast() == 5,
                "events: ring buffer keeps the 3 newest frames, oldest evicted");
    }

    // ------------------------------------------------------------------ M2-C: gui state

    private static void guiContracts() throws Exception {
        // --- params: frozen schema declares an empty object
        GuiContracts.guiStateParams(json("{}"));
        check(true, "gui: empty params accepted");
        expectInvalid(() -> {
            GuiContracts.guiStateParams(json("{\"tier\":\"full\"}"));
            return null;
        }, "gui: non-empty params rejected");

        // --- collector cap + truncation (world.query discipline)
        GuiContracts.WidgetCollector small = new GuiContracts.WidgetCollector();
        small.add(new GuiContracts.WidgetNode("Button", 1, 2, 3, 4, true, true, "OK", null));
        check(small.nodes().size() == 1 && !small.truncated() && !small.full(),
                "gui: collector accepts nodes below the cap");
        GuiContracts.WidgetCollector full = new GuiContracts.WidgetCollector();
        for (int i = 0; i < GuiContracts.WIDGETS_CAP + 5; i++) {
            full.add(new GuiContracts.WidgetNode("W" + i, 0, 0, 1, 1, true, true, null, null));
        }
        check(full.nodes().size() == GuiContracts.WIDGETS_CAP && full.truncated() && full.full(),
                "gui: collector stops at " + GuiContracts.WIDGETS_CAP + " and flags truncated");

        // --- widget node JSON: fields, empty message omitted, EditBox text kept
        GuiContracts.WidgetCollector widgets = new GuiContracts.WidgetCollector();
        widgets.add(new GuiContracts.WidgetNode("Button", 10, 20, 200, 20, true, false, "Done", null));
        widgets.add(new GuiContracts.WidgetNode("EditBox", 5, 6, 100, 16, true, true, "", "hello"));
        widgets.add(new GuiContracts.WidgetNode("Image", 0, 0, 16, 16, false, true, null, null));
        JsonObject standard = GuiContracts.guiStateResult(true, "ChatScreen", widgets, null);
        JsonObject first = standard.get("widgets").getAsJsonArray().get(0).getAsJsonObject();
        check("Button".equals(first.get("type").getAsString())
                        && first.get("x").getAsInt() == 10 && first.get("y").getAsInt() == 20
                        && first.get("width").getAsInt() == 200 && first.get("height").getAsInt() == 20
                        && first.get("visible").getAsBoolean() && !first.get("active").getAsBoolean()
                        && "Done".equals(first.get("message").getAsString()),
                "gui: widget node carries type/geometry/flags/message");
        JsonObject second = standard.get("widgets").getAsJsonArray().get(1).getAsJsonObject();
        check(!second.has("message") && "hello".equals(second.get("text").getAsString()),
                "gui: empty message omitted, EditBox text included");
        JsonObject third = standard.get("widgets").getAsJsonArray().get(2).getAsJsonObject();
        check(!third.has("message") && !third.has("text") && !third.get("visible").getAsBoolean(),
                "gui: null message/text omitted, invisible widget still reported");

        // --- standard response shape (non-container: no slots field)
        check(standard.get("screen_open").getAsBoolean() && standard.get("in_game").getAsBoolean()
                        && "ChatScreen".equals(standard.get("screen_class").getAsString())
                        && !standard.has("slots") && !standard.get("truncated").getAsBoolean(),
                "gui: standard result shape, slots omitted for non-container screens");

        // --- container response shape + slot JSON (item null vs name, note)
        GuiContracts.WidgetCollector empty = new GuiContracts.WidgetCollector();
        List<GuiContracts.SlotFact> slots = List.of(
                new GuiContracts.SlotFact(0, 0, 100, 20, GuiContracts.ROLE_RESULT, "minecraft:oak_log", 12, null),
                new GuiContracts.SlotFact(9, 9, 30, 40, GuiContracts.ROLE_PLAYER, null, 0, null),
                new GuiContracts.SlotFact(40, 40, 77, 62, GuiContracts.ROLE_CONTAINER, null, 0,
                        "item access failed: boom"));
        JsonObject container = GuiContracts.guiStateResult(true, "InventoryScreen", empty, slots);
        check(container.has("slots") && container.get("widgets").getAsJsonArray().size() == 0,
                "gui: container result carries slots and can have zero widgets");
        JsonObject slot0 = container.get("slots").getAsJsonArray().get(0).getAsJsonObject();
        check(slot0.get("index").getAsInt() == 0 && slot0.get("container_slot").getAsInt() == 0
                        && slot0.get("x").getAsInt() == 100
                        && slot0.get("y").getAsInt() == 20
                        && "minecraft:oak_log".equals(slot0.get("item").getAsString())
                        && slot0.get("count").getAsInt() == 12 && !slot0.has("note"),
                "gui: slot entry fields (index/container_slot/x/y/role/item/count), note omitted when fine");
        JsonObject slot1 = container.get("slots").getAsJsonArray().get(1).getAsJsonObject();
        check(slot1.get("item").isJsonNull() && slot1.get("count").getAsInt() == 0,
                "gui: empty slot reports item:null count:0");
        JsonObject slot2 = container.get("slots").getAsJsonArray().get(2).getAsJsonObject();
        check(slot2.get("item").isJsonNull() && slot2.get("note").getAsString().contains("boom"),
                "gui: broken slot degrades to item:null + note");

        // --- no-screen shape
        JsonObject none = GuiContracts.noScreenResult();
        check(!none.get("screen_open").getAsBoolean() && none.keySet().size() == 1,
                "gui: no-screen result is exactly {screen_open:false}");

        // --- fallback shape: rects from partial collection, note, no widgets field
        GuiContracts.WidgetCollector partial = new GuiContracts.WidgetCollector();
        partial.add(new GuiContracts.WidgetNode("Button", 1, 2, 30, 40, true, true, "Done", "txt"));
        JsonObject fallback = GuiContracts.fallbackResult(false, "ModdedScreen", partial,
                "widget traversal failed: NPE");
        check(fallback.get("screen_open").getAsBoolean() && !fallback.get("in_game").getAsBoolean()
                        && fallback.get("fallback").getAsBoolean()
                        && "ModdedScreen".equals(fallback.get("screen_class").getAsString())
                        && fallback.get("note").getAsString().contains("traversal failed"),
                "gui: fallback result flags fallback + carries screen_class and note");
        JsonObject rect = fallback.get("rects").getAsJsonArray().get(0).getAsJsonObject();
        check("Button".equals(rect.get("type").getAsString()) && rect.get("width").getAsInt() == 30
                        && !rect.has("visible") && !rect.has("message") && !rect.has("text")
                        && !fallback.has("widgets"),
                "gui: fallback rects keep geometry only (no flags/message/text)");
        JsonObject emptyFallback = GuiContracts.fallbackResult(true, "X", new GuiContracts.WidgetCollector(), "e");
        check(emptyFallback.get("rects").getAsJsonArray().size() == 0,
                "gui: fallback with no collected nodes yields empty rects");

        // --- role strings (the generic classification vocabulary)
        java.util.Set<String> roles = java.util.Set.of(GuiContracts.ROLE_CRAFTING, GuiContracts.ROLE_RESULT,
                GuiContracts.ROLE_HOTBAR, GuiContracts.ROLE_PLAYER, GuiContracts.ROLE_ARMOR,
                GuiContracts.ROLE_OFFHAND, GuiContracts.ROLE_CONTAINER);
        check(roles.size() == 7, "gui: seven distinct role strings");
        check("crafting".equals(GuiContracts.ROLE_CRAFTING) && "result".equals(GuiContracts.ROLE_RESULT)
                        && "hotbar".equals(GuiContracts.ROLE_HOTBAR) && "player".equals(GuiContracts.ROLE_PLAYER)
                        && "armor".equals(GuiContracts.ROLE_ARMOR) && "offhand".equals(GuiContracts.ROLE_OFFHAND)
                        && "container".equals(GuiContracts.ROLE_CONTAINER),
                "gui: role string mapping (crafting/result/hotbar/player/armor/offhand/container)");

        // --- roleOf: hotbar boundary uses the CONTAINER slot index, never the
        // menu position (Slot.index is clobbered by addSlot - real-machine bug:
        // armor at menu positions 5-8 was classified hotbar)
        check(GuiContracts.roleOf(false, false, true, 0) == GuiContracts.ROLE_HOTBAR
                        && GuiContracts.roleOf(false, false, true, 8) == GuiContracts.ROLE_HOTBAR,
                "gui: roleOf container slots 0-8 are hotbar");
        check(GuiContracts.roleOf(false, false, true, 9) == GuiContracts.ROLE_PLAYER
                        && GuiContracts.roleOf(false, false, true, 35) == GuiContracts.ROLE_PLAYER,
                "gui: roleOf container slots 9-35 are player");
        check(GuiContracts.roleOf(false, false, true, 36) == GuiContracts.ROLE_ARMOR
                        && GuiContracts.roleOf(false, false, true, 39) == GuiContracts.ROLE_ARMOR,
                "gui: roleOf armor (36-39) is its own role - storage filters must exclude it");
        check(GuiContracts.roleOf(false, false, true, 40) == GuiContracts.ROLE_OFFHAND,
                "gui: roleOf offhand (40) is its own role despite menu position 45");
        check(GuiContracts.roleOf(true, true, true, 0) == GuiContracts.ROLE_CRAFTING
                        && GuiContracts.roleOf(false, true, false, 0) == GuiContracts.ROLE_RESULT,
                "gui: roleOf crafting takes precedence, then result");
        check(GuiContracts.roleOf(false, false, false, 3) == GuiContracts.ROLE_CONTAINER,
                "gui: roleOf non-player containers fall back to container");

        // --- typeName: anonymous subclasses report the first named superclass
        // (ChatScreen's EditBox is an anonymous override - getSimpleName() is "")
        check("String".equals(GuiContracts.typeName(String.class)),
                "gui: typeName returns the simple name for named classes");
        check("FakeWidget".equals(GuiContracts.typeName(new FakeWidget() {}.getClass()))
                        && "FakeWidget".equals(GuiContracts.typeName(FakeWidget.class)),
                "gui: typeName falls back to the named superclass for anonymous widgets");
    }

    // ------------------------------------------------------------------ M2-D: look + permissions

    private static void lookContracts() throws Exception {
        // --- look params: frozen schema bounds are inclusive
        LookContracts.LookParams p = validParams(() ->
                LookContracts.lookParams(json("{\"yaw\":180,\"pitch\":-90}")));
        check(p.yaw() == 180.0 && p.pitch() == -90.0,
                "look: inclusive upper/lower bounds (180 / -90) accepted");
        p = validParams(() -> LookContracts.lookParams(json("{\"yaw\":-180,\"pitch\":90}")));
        check(p.yaw() == -180.0 && p.pitch() == 90.0,
                "look: inclusive lower/upper bounds (-180 / 90) accepted");
        p = validParams(() -> LookContracts.lookParams(json("{\"yaw\":0.5,\"pitch\":-0.25}")));
        check(p.yaw() == 0.5 && p.pitch() == -0.25, "look: fractional angles accepted");
        expectInvalid(() -> LookContracts.lookParams(json("{\"yaw\":180.5,\"pitch\":0}")),
                "look: yaw 180.5 rejected");
        expectInvalid(() -> LookContracts.lookParams(json("{\"yaw\":-180.5,\"pitch\":0}")),
                "look: yaw -180.5 rejected");
        expectInvalid(() -> LookContracts.lookParams(json("{\"yaw\":0,\"pitch\":90.5}")),
                "look: pitch 90.5 rejected");
        expectInvalid(() -> LookContracts.lookParams(json("{\"yaw\":0,\"pitch\":-90.5}")),
                "look: pitch -90.5 rejected");
        expectInvalid(() -> LookContracts.lookParams(json("{\"pitch\":0}")),
                "look: missing yaw rejected");
        expectInvalid(() -> LookContracts.lookParams(json("{\"yaw\":0}")),
                "look: missing pitch rejected");
        expectInvalid(() -> LookContracts.lookParams(json("{\"yaw\":\"10\",\"pitch\":0}")),
                "look: string yaw rejected");
        expectInvalid(() -> LookContracts.lookParams(json("{\"yaw\":null,\"pitch\":0}")),
                "look: null yaw rejected");

        // --- lookAt params
        LookContracts.LookAtParams q = validParams(() ->
                LookContracts.lookAtParams(json("{\"x\":1.5,\"y\":64,\"z\":-12.25}")));
        check(q.x() == 1.5 && q.y() == 64.0 && q.z() == -12.25, "lookAt: numeric triple parsed");
        expectInvalid(() -> LookContracts.lookAtParams(json("{\"x\":1,\"y\":2}")),
                "lookAt: missing z rejected");
        expectInvalid(() -> LookContracts.lookAtParams(json("{\"x\":1,\"y\":\"2\",\"z\":3}")),
                "lookAt: non-number member rejected");
        expectInvalid(() -> LookContracts.lookAtParams(json("{}")),
                "lookAt: empty object rejected");

        // --- rotationTowards: hand-computed cases (vanilla Entity.lookAt formula).
        // yaw 0 = +Z (south), yaw -90 = +X (east), negative pitch = up - F3 axes.
        double[] south = LookContracts.rotationTowards(0, 64, 0, 0, 64, 10);
        check(south[0] == 0.0 && south[1] == 0.0 && Math.abs(south[2] - 10.0) < 1e-9,
                "look math: +Z target -> yaw 0, pitch 0, distance 10");
        double[] east = LookContracts.rotationTowards(0, 64, 0, 10, 64, 0);
        check(east[0] == -90.0 && east[1] == 0.0, "look math: +X target -> yaw -90 (east)");
        double[] north = LookContracts.rotationTowards(0, 64, 0, 0, 64, -10);
        check(north[0] == -180.0 && north[1] == 0.0, "look math: -Z target -> yaw -180 (north)");
        double[] up = LookContracts.rotationTowards(0, 64, 0, 0, 74, 0);
        check(up[1] == -90.0 && up[0] == -90.0,
                "look math: straight up -> pitch -90 (degenerate yaw -90, vanilla atan2(0,0)=0)");
        double[] down = LookContracts.rotationTowards(0, 64, 0, 0, 54, 0);
        check(down[1] == 90.0, "look math: straight down -> pitch +90");
        double[] diag = LookContracts.rotationTowards(0, 64, 0, 3, 68, 4);
        check(Math.abs(diag[0] - (-36.86989764584402)) < 1e-9
                        && Math.abs(diag[1] - (-38.65980825409009)) < 1e-9
                        && Math.abs(diag[2] - Math.sqrt(41.0)) < 1e-9,
                "look math: 3-4-5 diagonal -> yaw atan2(4,3)-90, pitch -atan2(4,5), distance sqrt(41)");
        // result yaw/pitch always inside the frozen schema bounds
        boolean bounded = true;
        for (int angle = 0; angle < 360; angle += 7) {
            double rad = Math.toRadians(angle);
            double[] r = LookContracts.rotationTowards(0, 64, 0, Math.cos(rad) * 10, Math.sin(rad) * 10,
                    Math.sin(rad) * 5);
            bounded &= r[0] >= LookContracts.YAW_MIN && r[0] <= LookContracts.YAW_MAX
                    && r[1] >= LookContracts.PITCH_MIN && r[1] <= LookContracts.PITCH_MAX;
        }
        check(bounded, "look math: results stay within schema bounds for a full circle of targets");

        // --- wrapDegrees (vanilla Mth.wrapDegrees boundary behaviour)
        check(LookContracts.wrapDegrees(190.0) == -170.0 && LookContracts.wrapDegrees(-190.0) == 170.0
                        && LookContracts.wrapDegrees(180.0) == -180.0 && LookContracts.wrapDegrees(-180.0) == -180.0
                        && LookContracts.wrapDegrees(540.0) == -180.0 && LookContracts.wrapDegrees(0.0) == 0.0,
                "look math: wrapDegrees matches vanilla (180 wraps to -180, [-180, 180) reduction)");

        // --- result shapes
        JsonObject look = LookContracts.lookResult(10, -5, -90, 30);
        check(look.get("in_game").getAsBoolean() && look.get("looked").getAsBoolean()
                        && look.get("yaw").getAsDouble() == -90.0 && look.get("pitch").getAsDouble() == 30.0
                        && look.get("previous").getAsJsonObject().get("yaw").getAsDouble() == 10.0
                        && look.get("previous").getAsJsonObject().get("pitch").getAsDouble() == -5.0,
                "look results: lookResult carries new + previous rotation");
        JsonObject lookAt = LookContracts.lookAtResult(1, 2, 3, -15.5, 22.25, 7.5);
        check(lookAt.get("in_game").getAsBoolean() && lookAt.get("looked").getAsBoolean()
                        && lookAt.get("target").getAsJsonObject().get("y").getAsDouble() == 2.0
                        && lookAt.get("yaw").getAsDouble() == -15.5
                        && lookAt.get("pitch").getAsDouble() == 22.25
                        && lookAt.get("distance").getAsDouble() == 7.5,
                "look results: lookAtResult carries target, rotation and distance");
        JsonObject absent = LookContracts.notInGameLook();
        check(!absent.get("in_game").getAsBoolean() && !absent.get("looked").getAsBoolean()
                        && absent.keySet().size() == 2,
                "look results: not-in-game shape is exactly {in_game:false, looked:false}");
    }

    // ------------------------------------------------------------------ M3.5 v1.2: dig + smooth turn

    private static void digContracts() throws Exception {
        // --- dig params
        DigContracts.DigParams p = validParams(() -> DigContracts.digParams(
                json("{\"x\":11,\"y\":63,\"z\":26}")));
        check(p.x() == 11 && p.y() == 63 && p.z() == 26
                        && p.timeoutMs() == DigContracts.DEFAULT_TIMEOUT_MS,
                "dig: integer coords parsed, timeout defaults to " + DigContracts.DEFAULT_TIMEOUT_MS);
        p = validParams(() -> DigContracts.digParams(
                json("{\"x\":-5,\"y\":0,\"z\":3.0,\"timeout_ms\":600}")));
        check(p.x() == -5 && p.timeoutMs() == 600, "dig: negative/whole-float coords + min timeout accepted");
        p = validParams(() -> DigContracts.digParams(
                json("{\"x\":0,\"y\":64,\"z\":0,\"timeout_ms\":30000}")));
        check(p.timeoutMs() == 30000, "dig: max timeout boundary accepted");
        p = validParams(() -> DigContracts.digParams(
                json("{\"x\":0,\"y\":64,\"z\":0,\"timeout_ms\":null}")));
        check(p.timeoutMs() == DigContracts.DEFAULT_TIMEOUT_MS, "dig: explicit null timeout -> default");
        expectInvalid(() -> DigContracts.digParams(json("{}")), "dig: missing coords rejected");
        expectInvalid(() -> DigContracts.digParams(json("{\"x\":1,\"y\":2}")), "dig: missing z rejected");
        expectInvalid(() -> DigContracts.digParams(json("{\"x\":1.5,\"y\":2,\"z\":3}")),
                "dig: fractional coord rejected");
        expectInvalid(() -> DigContracts.digParams(json("{\"x\":\"1\",\"y\":2,\"z\":3}")),
                "dig: string coord rejected");
        expectInvalid(() -> DigContracts.digParams(json("{\"x\":1,\"y\":2,\"z\":3,\"timeout_ms\":599}")),
                "dig: timeout below min rejected");
        expectInvalid(() -> DigContracts.digParams(json("{\"x\":1,\"y\":2,\"z\":3,\"timeout_ms\":30001}")),
                "dig: timeout above max rejected");
        expectInvalid(() -> DigContracts.digParams(json("{\"x\":1,\"y\":2,\"z\":3,\"timeout_ms\":1000.5}")),
                "dig: fractional timeout rejected");

        // --- result shapes
        JsonObject broken = DigContracts.digResult(DigContracts.RESULT_BROKEN, "minecraft:oak_log",
                3210L, true, null);
        check(broken.get("in_game").getAsBoolean()
                        && "broken".equals(broken.get("result").getAsString())
                        && "minecraft:oak_log".equals(broken.get("block").getAsString())
                        && broken.get("elapsed_ms").getAsLong() == 3210L
                        && broken.get("broken_via_occluder").getAsBoolean() && !broken.has("reason"),
                "dig results: broken + via_occluder shape");
        JsonObject air = DigContracts.digResult(DigContracts.RESULT_ALREADY_AIR, null, 4L, null, null);
        check(air.get("block").isJsonNull() && !air.has("broken_via_occluder"),
                "dig results: already_air carries null block");
        JsonObject blocked = DigContracts.digResult(DigContracts.RESULT_BLOCKED_LIQUID, "minecraft:stone",
                9L, null, "liquid minecraft:water at neighbor 12,63,26 would flow in");
        check("blocked_liquid".equals(blocked.get("result").getAsString())
                        && blocked.get("reason").getAsString().contains("water"),
                "dig results: blocked_* carries the reason");

        // --- M3.6 empirical drops: aggregate new in-radius item entities
        List<ToolContracts.EntityFact> facts = Arrays.asList(
                new ToolContracts.EntityFact("d1", "Oak Log", "minecraft:item",
                        6.5, 64.5, 0.5, Float.NaN, "minecraft:oak_log", 1),
                new ToolContracts.EntityFact("d2", "Oak Log", "minecraft:item",
                        7.5, 64.5, 0.5, Float.NaN, "minecraft:oak_log", 2),
                new ToolContracts.EntityFact("foreign", "Oak Log", "minecraft:item",
                        6.5, 64.5, 1.5, Float.NaN, "minecraft:oak_log", 1),
                new ToolContracts.EntityFact("far", "Oak Log", "minecraft:item",
                        20.0, 64.0, 0.0, Float.NaN, "minecraft:oak_log", 1),
                new ToolContracts.EntityFact("z1", "Zombie", "minecraft:zombie",
                        6.0, 64.0, 0.0, 20.0F));
        java.util.Set<String> seenBefore = java.util.Set.of("foreign");
        List<JsonObject> drops = DigContracts.aggregateDrops(facts, seenBefore, 6.5, 64.5, 0.5,
                DigContracts.DROPS_SCAN_RADIUS);
        check(drops.size() == 1
                        && "minecraft:oak_log".equals(drops.get(0).get("item").getAsString())
                        && drops.get(0).get("count").getAsInt() == 3,
                "dig drops: new in-radius item entities summed per id (pre-existing/far/non-item excluded)");
        check(DigContracts.aggregateDrops(facts,
                        java.util.Set.of("d1", "d2", "foreign", "far"), 6.5, 64.5, 0.5, 4.0).isEmpty(),
                "dig drops: fully-snapshotted scene -> empty report (nothing new appeared)");
        JsonObject withDrops = DigContracts.digResult(DigContracts.RESULT_BROKEN, "minecraft:oak_log",
                3210L, null, null, drops);
        check(withDrops.get("drops").isJsonArray()
                        && withDrops.get("drops").getAsJsonArray().size() == 1
                        && withDrops.get("drops").getAsJsonArray().get(0).getAsJsonObject()
                        .get("count").getAsInt() == 3,
                "dig results: broken carries the drops [{item,count}] array");
        check(!DigContracts.digResult(DigContracts.RESULT_ALREADY_AIR, null, 4L, null, null).has("drops")
                        && !DigContracts.digResult(DigContracts.RESULT_BROKEN, "minecraft:oak_log",
                        100L, null, null, (List<JsonObject>) null).has("drops"),
                "dig results: null drops omitted (old-jar response shape intact)");
        check(DigContracts.DROPS_WAIT_TICKS >= 10 && DigContracts.DROPS_WAIT_TICKS <= 20
                        && DigContracts.DROPS_SCAN_RADIUS == 4.0,
                "dig drops: wait window bounded to <= 1 s, scan radius 4 (RPC latency budget)");

        // --- monitor: aim -> press -> break (the happy path)
        DigContracts.DigMonitor m = new DigContracts.DigMonitor(15000, false);
        DigContracts.TickView aiming = new DigContracts.TickView(false, false, false, false, 0);
        DigContracts.TickView aimed = new DigContracts.TickView(true, false, false, false, 0);
        DigContracts.TickView pressing = new DigContracts.TickView(true, false, false, true, 100);
        check(m.tick(aiming).action() == DigContracts.Action.WAIT, "dig monitor: waits while aiming");
        DigContracts.Verdict press = m.tick(aimed);
        check(press.action() == DigContracts.Action.PRESS, "dig monitor: presses once aim converges");
        DigContracts.Verdict hold = m.tick(pressing);
        check(hold.action() == DigContracts.Action.HOLD, "dig monitor: holds while destroying");
        DigContracts.Verdict done = m.tick(new DigContracts.TickView(true, true, false, false, 3000));
        check(done.action() == DigContracts.Action.FINISH
                        && DigContracts.RESULT_BROKEN.equals(done.result()) && !done.viaOccluder(),
                "dig monitor: block change -> FINISH broken");

        // --- monitor: target already gone during aiming -> idempotent broken
        m = new DigContracts.DigMonitor(15000, false);
        done = m.tick(new DigContracts.TickView(false, true, false, false, 50));
        check(done.action() == DigContracts.Action.FINISH
                        && DigContracts.RESULT_BROKEN.equals(done.result()),
                "dig monitor: target gone while aiming -> broken (someone else broke it)");

        // --- monitor: occluder tolerance + broken_via_occluder (the T5a leaves-in-front case)
        m = new DigContracts.DigMonitor(15000, false);
        m.tick(aimed); // PRESS
        boolean held = true;
        for (int i = 0; i < 80; i++) { // 4s of chewing a leaf occluder, stage active, hit NOT on target
            DigContracts.Verdict v = m.tick(new DigContracts.TickView(true, false, true, true, 100 + i * 50));
            held &= v.action() == DigContracts.Action.HOLD;
        }
        DigContracts.Verdict through = m.tick(new DigContracts.TickView(true, true, true, false, 4200));
        check(held, "dig monitor: occluder hit NEVER fails the dig (hysteresis)");
        check(through.action() == DigContracts.Action.FINISH
                        && DigContracts.RESULT_BROKEN.equals(through.result()) && through.viaOccluder(),
                "dig monitor: broken after occluder -> via_occluder=true");
        // hit jitter during AIMING must NOT count as an occluder (crosshair sweeps)
        DigContracts.DigMonitor aimOnly = new DigContracts.DigMonitor(15000, false);
        aimOnly.tick(new DigContracts.TickView(false, false, true, false, 0)); // WAIT (aiming, occluder hit)
        DigContracts.Verdict noOccluder = aimOnly.tick(new DigContracts.TickView(true, true, false, false, 100));
        check(!noOccluder.viaOccluder(),
                "dig monitor: occluder hit during AIMING is not tracked as via_occluder");

        // --- monitor: aim stall guard (view never lands -> not_digging, not a bare timeout)
        m = new DigContracts.DigMonitor(15000, false);
        DigContracts.Verdict stalled = null;
        for (int i = 0; i < DigContracts.AIM_STALL_TICKS; i++) {
            stalled = m.tick(new DigContracts.TickView(false, false, false, false, i * 50));
        }
        check(stalled.action() == DigContracts.Action.FINISH
                        && DigContracts.RESULT_NOT_DIGGING.equals(stalled.result()),
                "dig monitor: aim stall for " + DigContracts.AIM_STALL_TICKS + " ticks -> not_digging (fail fast)");

        // --- monitor: not_digging telemetry early-stop (stage -1 for 40 pressing ticks)
        m = new DigContracts.DigMonitor(15000, false);
        m.tick(aimed); // PRESS
        DigContracts.Verdict last = null;
        for (int i = 1; i <= DigContracts.NOT_DIGGING_TICKS; i++) {
            last = m.tick(new DigContracts.TickView(true, false, false, false, i * 50));
        }
        check(last.action() == DigContracts.Action.FINISH
                        && DigContracts.RESULT_NOT_DIGGING.equals(last.result()),
                "dig monitor: 40 consecutive idle-stage ticks -> not_digging");
        // stage flicker resets the streak
        m = new DigContracts.DigMonitor(15000, false);
        m.tick(aimed);
        DigContracts.Action a39 = null;
        for (int i = 1; i <= 39; i++) {
            a39 = m.tick(new DigContracts.TickView(true, false, false, i % 5 == 0, i * 50)).action();
        }
        check(a39 == DigContracts.Action.HOLD,
                "dig monitor: intermittent stage activity resets the not-digging streak");

        // --- monitor: timeout (also while aiming)
        m = new DigContracts.DigMonitor(15000, false);
        DigContracts.Verdict to = m.tick(new DigContracts.TickView(false, false, false, false, 15000));
        check(to.action() == DigContracts.Action.FINISH
                        && DigContracts.RESULT_TIMEOUT.equals(to.result()),
                "dig monitor: elapsed >= timeout_ms -> timeout");
        m = new DigContracts.DigMonitor(15000, false);
        m.tick(aimed);
        to = m.tick(new DigContracts.TickView(true, false, false, true, 15100));
        check(to.action() == DigContracts.Action.FINISH
                        && DigContracts.RESULT_TIMEOUT.equals(to.result()),
                "dig monitor: timeout outranks an active stage");

        // --- monitor: insta-mine short press (protects the block behind)
        m = new DigContracts.DigMonitor(15000, true);
        m.tick(aimed); // PRESS
        boolean shortHold = true;
        for (int i = 1; i < DigContracts.INSTA_PRESS_TICKS; i++) {
            shortHold &= m.tick(new DigContracts.TickView(true, false, false, true, i * 50)).action()
                    == DigContracts.Action.HOLD;
        }
        DigContracts.Verdict unpress = m.tick(new DigContracts.TickView(true, false, false, true, 150));
        check(shortHold && unpress.action() == DigContracts.Action.UNPRESS,
                "dig monitor: insta-mine holds " + DigContracts.INSTA_PRESS_TICKS + " ticks then unpresses");
        DigContracts.Verdict instaBroken = m.tick(new DigContracts.TickView(true, true, false, false, 250));
        check(instaBroken.action() == DigContracts.Action.FINISH
                        && DigContracts.RESULT_BROKEN.equals(instaBroken.result()),
                "dig monitor: insta-mine break lands during verify -> broken");
        // ...and when the insta press misses: bounded retries then not_digging
        m = new DigContracts.DigMonitor(15000, true);
        int presses = 0;
        DigContracts.Verdict v = null;
        for (int i = 0; i < 200 && (v == null || v.action() != DigContracts.Action.FINISH); i++) {
            v = m.tick(new DigContracts.TickView(true, false, false, false, i * 50 + 100));
            if (v.action() == DigContracts.Action.PRESS) {
                presses++;
            }
        }
        check(v.action() == DigContracts.Action.FINISH
                        && DigContracts.RESULT_NOT_DIGGING.equals(v.result())
                        && presses == DigContracts.INSTA_MAX_PRESSES,
                "dig monitor: failed insta presses bounded at " + DigContracts.INSTA_MAX_PRESSES
                        + " then not_digging");

        // --- smooth-turn math (v1.2)
        check(LookContracts.yawDelta(0, 180) == -180.0 && LookContracts.yawDelta(0, -180) == -180.0,
                "turn math: exactly-180 deltas reduce to -180 (vanilla [-180,180) wrap; turns left)");
        check(LookContracts.yawDelta(0, 190) == -170.0 && LookContracts.yawDelta(350, 10) == 20.0,
                "turn math: yawDelta wraps the short way round");
        check(LookContracts.approach(10, 5, 15) == 15.0 && LookContracts.approach(10, -5, 15) == 5.0,
                "turn math: approach clamps to maxStep");
        check(LookContracts.approach(10, 3, 15) == 13.0 && LookContracts.approach(10, -20, 15) == -5.0,
                "turn math: approach lands exactly when within one step");
        check(LookContracts.turnConverged(-0.5, 10.9, 0, 10)
                        && !LookContracts.turnConverged(1.5, 10, 0, 10)
                        && !LookContracts.turnConverged(0, 11.5, 0, 10),
                "turn math: convergence window is <1 deg on both axes");
        check(LookContracts.maxTurnMs(300) == 2700L && LookContracts.maxTurnMs(30) == 13500L,
                "turn math: maxTurnMs = 360deg/speed + 1.5s slack");
        check(DigContracts.DIG_AIM_TURN_SPEED_DEG_S == 300.0,
                "turn math: dig aims at 300 deg/s (the T6 ruling)");

        // --- lookAt turn_speed_deg_s params (v1.2)
        LookContracts.LookAtParams q = validParams(() -> LookContracts.lookAtParams(
                json("{\"x\":1,\"y\":2,\"z\":3}")));
        check(q.turnSpeedDegS() == null, "lookAt: absent turn_speed -> instant (v1.0 behaviour)");
        q = validParams(() -> LookContracts.lookAtParams(
                json("{\"x\":1,\"y\":2,\"z\":3,\"turn_speed_deg_s\":null}")));
        check(q.turnSpeedDegS() == null, "lookAt: explicit null turn_speed -> instant");
        q = validParams(() -> LookContracts.lookAtParams(
                json("{\"x\":1,\"y\":2,\"z\":3,\"turn_speed_deg_s\":300}")));
        check(q.turnSpeedDegS() == 300.0, "lookAt: turn_speed 300 parsed");
        q = validParams(() -> LookContracts.lookAtParams(
                json("{\"x\":1,\"y\":2,\"z\":3,\"turn_speed_deg_s\":30}")));
        check(q.turnSpeedDegS() == 30.0, "lookAt: turn_speed min boundary 30 accepted");
        q = validParams(() -> LookContracts.lookAtParams(
                json("{\"x\":1,\"y\":2,\"z\":3,\"turn_speed_deg_s\":720}")));
        check(q.turnSpeedDegS() == 720.0, "lookAt: turn_speed max boundary 720 accepted");
        q = validParams(() -> LookContracts.lookAtParams(
                json("{\"x\":1,\"y\":2,\"z\":3,\"turn_speed_deg_s\":180.5}")));
        check(q.turnSpeedDegS() == 180.5, "lookAt: fractional turn_speed accepted (number, not integer)");
        expectInvalid(() -> LookContracts.lookAtParams(
                json("{\"x\":1,\"y\":2,\"z\":3,\"turn_speed_deg_s\":29.9}")),
                "lookAt: turn_speed below 30 rejected");
        expectInvalid(() -> LookContracts.lookAtParams(
                json("{\"x\":1,\"y\":2,\"z\":3,\"turn_speed_deg_s\":721}")),
                "lookAt: turn_speed above 720 rejected");
        expectInvalid(() -> LookContracts.lookAtParams(
                json("{\"x\":1,\"y\":2,\"z\":3,\"turn_speed_deg_s\":\"300\"}")),
                "lookAt: string turn_speed rejected");

        // --- smooth lookAt result shape
        JsonObject smooth = LookContracts.lookAtSmoothResult(1, 2, 3, -15.5, 22.25, 7.5, true, 620L);
        check(smooth.get("converged").getAsBoolean() && smooth.get("elapsed_ms").getAsLong() == 620L
                        && smooth.get("distance").getAsDouble() == 7.5,
                "lookAt results: smooth result carries converged + elapsed_ms");

        // --- PermissionContracts re-check for dig's class: dig rides Action.INPUT
        // (the input_world tier allows it without a screen - same as input.*;
        // full matrix covered by permissionContracts() below)
        check(PermissionContracts.allows(PermissionContracts.Tier.INPUT_WORLD, false,
                        PermissionContracts.Action.INPUT)
                        && PermissionContracts.deniedRegardlessOfScreen(
                        PermissionContracts.Tier.INPUT_GUI, PermissionContracts.Action.LOOK),
                "dig tier: dig rides the INPUT action class (input_world: yes without a screen)");
    }

    private static void permissionContracts() {
        PermissionContracts.Tier OBSERVE = PermissionContracts.Tier.OBSERVE;
        PermissionContracts.Tier INPUT_GUI = PermissionContracts.Tier.INPUT_GUI;
        PermissionContracts.Tier INPUT_WORLD = PermissionContracts.Tier.INPUT_WORLD;
        PermissionContracts.Tier FULL = PermissionContracts.Tier.FULL;
        PermissionContracts.Action INPUT = PermissionContracts.Action.INPUT;
        PermissionContracts.Action LOOK = PermissionContracts.Action.LOOK;

        // --- the decision matrix (tier x screen x action)
        check(!PermissionContracts.allows(OBSERVE, true, INPUT)
                        && !PermissionContracts.allows(OBSERVE, false, INPUT)
                        && !PermissionContracts.allows(OBSERVE, true, LOOK)
                        && !PermissionContracts.allows(OBSERVE, false, LOOK),
                "permissions: observe denies input.* and look regardless of screen");
        check(PermissionContracts.allows(INPUT_GUI, true, INPUT)
                        && !PermissionContracts.allows(INPUT_GUI, false, INPUT)
                        && !PermissionContracts.allows(INPUT_GUI, true, LOOK)
                        && !PermissionContracts.allows(INPUT_GUI, false, LOOK),
                "permissions: input_gui allows input.* ONLY with a screen, never look");
        check(!PermissionContracts.allows(INPUT_WORLD, true, INPUT)
                        && PermissionContracts.allows(INPUT_WORLD, false, INPUT)
                        && PermissionContracts.allows(INPUT_WORLD, true, LOOK)
                        && PermissionContracts.allows(INPUT_WORLD, false, LOOK),
                "permissions: input_world allows input.* ONLY without a screen, always look");
        check(PermissionContracts.allows(FULL, true, INPUT) && PermissionContracts.allows(FULL, false, INPUT)
                        && PermissionContracts.allows(FULL, true, LOOK) && PermissionContracts.allows(FULL, false, LOOK),
                "permissions: full allows everything (default = M2-A behaviour)");

        // --- M4.1 chat.send rides the CHAT action: GUI-immune, only observe denies
        PermissionContracts.Action CHAT = PermissionContracts.Action.CHAT;
        check(!PermissionContracts.allows(OBSERVE, true, CHAT)
                        && !PermissionContracts.allows(OBSERVE, false, CHAT),
                "permissions: observe denies chat.send (read-only tier)");
        check(PermissionContracts.allows(INPUT_GUI, true, CHAT)
                        && PermissionContracts.allows(INPUT_GUI, false, CHAT)
                        && PermissionContracts.allows(INPUT_WORLD, true, CHAT)
                        && PermissionContracts.allows(INPUT_WORLD, false, CHAT)
                        && PermissionContracts.deniedRegardlessOfScreen(OBSERVE, CHAT)
                        && !PermissionContracts.deniedRegardlessOfScreen(INPUT_GUI, CHAT),
                "permissions: chat.send is screen-immune under every acting tier (M4.1 death-screen path)");

        // --- pre-flight veto helper (callers deny without a main-thread round trip)
        check(PermissionContracts.deniedRegardlessOfScreen(OBSERVE, INPUT)
                        && PermissionContracts.deniedRegardlessOfScreen(OBSERVE, LOOK)
                        && PermissionContracts.deniedRegardlessOfScreen(INPUT_GUI, LOOK),
                "permissions: screen-independent vetoes (observe any, input_gui look)");
        check(!PermissionContracts.deniedRegardlessOfScreen(INPUT_GUI, INPUT)
                        && !PermissionContracts.deniedRegardlessOfScreen(INPUT_WORLD, INPUT)
                        && !PermissionContracts.deniedRegardlessOfScreen(INPUT_WORLD, LOOK)
                        && !PermissionContracts.deniedRegardlessOfScreen(FULL, INPUT)
                        && !PermissionContracts.deniedRegardlessOfScreen(FULL, LOOK),
                "permissions: gui-gated combos need the live screen state");

        // --- parse: case-insensitive + trimmed; unknown -> null (config falls back)
        check(PermissionContracts.parse("observe") == OBSERVE
                        && PermissionContracts.parse("INPUT_GUI") == INPUT_GUI
                        && PermissionContracts.parse("  input_world ") == INPUT_WORLD
                        && PermissionContracts.parse("full") == FULL,
                "permissions: tier names parse case-insensitively (whitespace trimmed)");
        check(PermissionContracts.parse("bogus") == null && PermissionContracts.parse("") == null
                        && PermissionContracts.parse(null) == null,
                "permissions: unknown/empty/null tier names rejected");

        // --- message + config spelling
        check("input_gui".equals(INPUT_GUI.configName()) && "observe".equals(OBSERVE.configName()),
                "permissions: config spelling is lowercase");
        String message = PermissionContracts.deniedMessage(OBSERVE, false, INPUT);
        check(message.startsWith("permission_denied")
                        && message.contains("observe")
                        && message.contains("sirius_bridge.toml"),
                "permissions: deniedMessage names the tier and the config key");
    }

    private static void chatContracts() throws Exception {
        // --- validation: required, 1..256 code points
        expectInvalid(() -> ChatContracts.chatSendParams(json("{}")),
                "chat.send: missing string rejected");
        expectInvalid(() -> ChatContracts.chatSendParams(json("{\"string\": \"\"}")),
                "chat.send: empty string rejected");
        expectInvalid(() -> ChatContracts.chatSendParams(json("{\"string\": 42}")),
                "chat.send: non-string rejected");
        expectInvalid(() -> ChatContracts.chatSendParams(
                json("{\"string\": \"" + "x".repeat(257) + "\"}")),
                "chat.send: 257 chars rejected (vanilla limit)");
        ChatContracts.ChatSendParams ok = ChatContracts.chatSendParams(
                json("{\"string\": \"我死了……\"}"));
        check(ok.text().equals("我死了……"),
                "chat.send: CJK text passes validation verbatim");

        // --- result shapes
        JsonObject notInGame = ChatContracts.notInGameChat();
        check(!notInGame.get("in_game").getAsBoolean() && !notInGame.get("sent").getAsBoolean(),
                "chat.send: not-in-game result is not an error");
        JsonObject sent = ChatContracts.sentResult("hello");
        check(sent.get("in_game").getAsBoolean() && sent.get("sent").getAsBoolean()
                        && sent.get("length").getAsInt() == 5,
                "chat.send: sent result carries in_game/sent/length");
    }

    private static void movementLook() {
        // --- stationary / vertical-only motion: stay silent
        check(MovementLook.nextYaw(90.0, 0.0, 0.0, 300.0) == null
                        && MovementLook.nextYaw(90.0, 0.0, 0.0, 300.0) == null,
                "movement look: no horizontal speed -> no write");
        check(MovementLook.nextYaw(90.0, 0.0, 0.03, 300.0) == null,
                "movement look: sub-threshold drift (fall wobble) -> no write");

        // --- heading math: yaw 0 = +Z (south); moving +Z targets yaw 0,
        //     so from current yaw 90 one 300deg/s tick steps -15 (shortest way)
        Double north = MovementLook.nextYaw(90.0, 0.0, 0.215, 300.0);
        check(north != null && Math.abs(north - 75.0) < 1e-9,
                "movement look: one 50ms tick steps 15 deg toward the heading");
        Double east = MovementLook.nextYaw(0.0, 0.215, 0.0, 300.0);
        check(east != null && Math.abs(east - (-15.0)) < 1e-9,
                "movement look: moving +X (east, yaw -90) turns the short way negative");

        // --- deadzone: already facing the movement direction
        check(MovementLook.nextYaw(0.0, 0.0, 0.215, 300.0) == null,
                "movement look: within deadzone of the heading -> no write");

        // --- wrap-around takes the short way: current 170, moving toward
        //     heading -170 (velocity = the -170 view direction) -> delta +20
        //     -> one step lands at 185 (unwrapped; vanilla normalizes on use)
        Double wrap = MovementLook.nextYaw(170.0, 0.1736, -0.9848, 300.0);
        check(wrap != null && Math.abs(wrap - 185.0) < 1e-9,
                "movement look: wraps across +-180 along the shortest signed path");

        // --- step clamps at the fixed angular speed, never overshoots a near target
        Double small = MovementLook.nextYaw(5.0, 0.0, 0.06, 30.0);
        check(small != null && Math.abs(small - 3.5) < 1e-9,
                "movement look: 30deg/s tick (1.5 deg) never overshoots a 5-deg correction");
    }

    /** Named stand-in for an AbstractWidget subclass (typeName smoke checks). */
    private static class FakeWidget {
    }

    // ------------------------------------------------------------------ helpers

    private static JsonObject json(String text) {
        return JsonParser.parseString(text).getAsJsonObject();
    }

    private static <T> T validParams(Supplier_<T> parse) throws Exception {
        return parse.get();
    }

    private static void expectInvalid(Supplier_<?> parse, String name) {
        try {
            parse.get();
            check(false, name + " (no exception thrown)");
        } catch (ToolContracts.InvalidParams expected) {
            check(true, name);
        } catch (Exception other) {
            check(false, name + " (wrong exception: " + other + ")");
        }
    }

    @FunctionalInterface
    private interface Supplier_<T> {
        T get() throws Exception;
    }

    private static void check(boolean condition, String name) {
        if (condition) {
            passed++;
            System.out.println("  ok    " + name);
        } else {
            failures.add(name);
            System.out.println("  FAIL  " + name);
        }
    }
}
