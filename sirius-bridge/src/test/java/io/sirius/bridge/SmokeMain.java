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
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Random;

/**
 * In-process smoke test for the M1-C perception tools and the M2-A pure
 * input logic (run via {@code gradlew smokeTest}). No game, no client: it
 * exercises the pure halves - parameter validation, bbox cropping, the JPEG
 * budget ladder, response assembly, block scanning and entity filtering,
 * plus the key-name -> GLFW keycode table, the rate-limiter token bucket,
 * input param validation and evidence file naming - exactly the logic that
 * would otherwise only be verifiable inside a running Minecraft.
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
