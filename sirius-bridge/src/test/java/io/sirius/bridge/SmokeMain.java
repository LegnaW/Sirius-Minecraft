package io.sirius.bridge;

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
        permissionContracts();

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
        check(c.button() == 0 && c.count() == 1, "input.click: defaults (count 1)");
        c = validParams(() -> InputContracts.clickParams(json("{\"button\":2,\"count\":3}")));
        check(c.button() == 2 && c.count() == 3, "input.click: button/count parsed");
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
                "evidence_click_20260818_120000000.jpg", 12345);
        check(clickResult.get("clicked").getAsBoolean()
                && "InventoryScreen".equals(clickResult.get("screen").getAsString())
                && clickResult.get("evidence").getAsJsonObject().get("bytes").getAsLong() == 12345,
                "input results: clickResult shape with evidence");

        JsonObject clickNoEvidence = InputContracts.clickResult(1, 1, false, null, null, 0);
        check(!clickNoEvidence.has("screen") && !clickNoEvidence.has("evidence"),
                "input results: clickResult omits null fields");

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

        // --- statsResult
        ToolContracts.StatsSnapshot stats = new ToolContracts.StatsSnapshot(
                18.5f, 17, 4.2f, 300, 27, 0.6f,
                1.5, 64.0, -12.25, "minecraft:overworld", "survival",
                List.of(new ToolContracts.EffectFact("minecraft:speed", 1200, 1)), true);
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
                && statsJson.get("alive").getAsBoolean(),
                "contracts: statsResult full shape");

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
                new ToolContracts.EntityFact("u-item", "Diamond", "minecraft:diamond", 2, 64, 0, Float.NaN));
        JsonObject entities = ToolContracts.filterEntities(facts, 0, 64, 0, 8);
        check(entities.get("count").getAsInt() == 3
                        && entities.get("entities").getAsJsonArray().size() == 3,
                "contracts: entities filtered by range");
        JsonElement item = entities.get("entities").getAsJsonArray().get(2);
        check(!item.getAsJsonObject().has("health"),
                "contracts: NaN health omitted from entity entry");

        List<ToolContracts.EntityFact> crowd = new ArrayList<>();
        for (int i = 0; i < 200; i++) {
            crowd.add(new ToolContracts.EntityFact("u" + i, "E" + i, "minecraft:zombie", i % 4, 64, 0, 1f));
        }
        check(ToolContracts.filterEntities(crowd, 0, 64, 0, 16).get("count").getAsInt()
                        == ToolContracts.ENTITIES_CAP,
                "contracts: entities capped at " + ToolContracts.ENTITIES_CAP);
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
                GuiContracts.ROLE_HOTBAR, GuiContracts.ROLE_PLAYER, GuiContracts.ROLE_CONTAINER);
        check(roles.size() == 5, "gui: five distinct role strings");
        check("crafting".equals(GuiContracts.ROLE_CRAFTING) && "result".equals(GuiContracts.ROLE_RESULT)
                        && "hotbar".equals(GuiContracts.ROLE_HOTBAR) && "player".equals(GuiContracts.ROLE_PLAYER)
                        && "container".equals(GuiContracts.ROLE_CONTAINER),
                "gui: role string mapping (crafting/result/hotbar/player/container)");

        // --- roleOf: hotbar boundary uses the CONTAINER slot index, never the
        // menu position (Slot.index is clobbered by addSlot - real-machine bug:
        // armor at menu positions 5-8 was classified hotbar)
        check(GuiContracts.roleOf(false, false, true, 0) == GuiContracts.ROLE_HOTBAR
                        && GuiContracts.roleOf(false, false, true, 8) == GuiContracts.ROLE_HOTBAR,
                "gui: roleOf container slots 0-8 are hotbar");
        check(GuiContracts.roleOf(false, false, true, 9) == GuiContracts.ROLE_PLAYER
                        && GuiContracts.roleOf(false, false, true, 35) == GuiContracts.ROLE_PLAYER,
                "gui: roleOf container slots 9-35 are player");
        check(GuiContracts.roleOf(false, false, true, 36) == GuiContracts.ROLE_PLAYER
                        && GuiContracts.roleOf(false, false, true, 39) == GuiContracts.ROLE_PLAYER
                        && GuiContracts.roleOf(false, false, true, 40) == GuiContracts.ROLE_PLAYER,
                "gui: roleOf armor (36-39) and offhand (40) are player despite menu positions 5-8/45");
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
