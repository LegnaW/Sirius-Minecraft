package io.sirius.bridge;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

/**
 * Frozen-contract logic for the M2-D look tools ({@code look},
 * {@code lookAt}): parameter validation (mirroring the JSON schemas in
 * {@code sirius-brain/schema/tools}), the eye-&gt;target rotation math and
 * response assembly. Pure Gson + JDK - no Minecraft classes - so the smoke
 * test covers it without a running game.
 *
 * <p><b>Rotation convention</b> (derived by inverting vanilla
 * {@code Entity.calculateViewVector} and verified to match vanilla's own
 * {@code Entity.lookAt(Anchor, Vec3)} verbatim, 1.21.1 decompiled sources):
 * for a view direction {@code d} in degrees,
 * {@code view = (-cos(pitch)*sin(yaw), -sin(pitch), cos(pitch)*cos(yaw))},
 * so looking at a target means
 * <pre>
 *   dx, dy, dz = target - eye
 *   horizontal = sqrt(dx^2 + dz^2)
 *   yaw   = wrapDegrees(toDegrees(atan2(dz, dx)) - 90)
 *   pitch = wrapDegrees(-toDegrees(atan2(dy, horizontal)))
 * </pre>
 * i.e. yaw 0 looks at +Z (south), yaw -90 at +X (east), negative pitch is up -
 * exactly what the F3 debug screen shows. Vanilla computes this in float
 * precision; we compute in double and cast once at the setter (sub-arcsecond
 * difference, and the values are clamped to the schema ranges anyway).
 */
public final class LookContracts {

    private LookContracts() {
    }

    // ------------------------------------------------------------------ params

    /** Schema bounds for {@code look.yaw} (frozen schema tools/look.json). */
    public static final double YAW_MIN = -180.0;
    public static final double YAW_MAX = 180.0;

    /** Schema bounds for {@code look.pitch} (frozen schema tools/look.json). */
    public static final double PITCH_MIN = -90.0;
    public static final double PITCH_MAX = 90.0;

    /** Validated {@code look} params: yaw -180..180, pitch -90..90 (both REQUIRED). */
    public record LookParams(double yaw, double pitch) {
    }

    /**
     * Validates {@code look} params per the frozen schema: {@code yaw} and
     * {@code pitch} are REQUIRED finite numbers within their bounds.
     */
    public static LookParams lookParams(JsonObject params) throws ToolContracts.InvalidParams {
        return new LookParams(
                boundedNumber(params, "yaw", "look", YAW_MIN, YAW_MAX),
                boundedNumber(params, "pitch", "look", PITCH_MIN, PITCH_MAX));
    }

    /** Validated {@code lookAt} params: finite {@code x}/{@code y}/{@code z} (all REQUIRED). */
    public record LookAtParams(double x, double y, double z) {
    }

    /** Validates {@code lookAt} params per the frozen schema (finite numbers). */
    public static LookAtParams lookAtParams(JsonObject params) throws ToolContracts.InvalidParams {
        Double x = finiteNumber(params, "x");
        Double y = finiteNumber(params, "y");
        Double z = finiteNumber(params, "z");
        if (x == null || y == null || z == null) {
            throw new ToolContracts.InvalidParams("lookAt requires finite numeric x, y and z"
                    + " (the block/world position to look at)");
        }
        return new LookAtParams(x, y, z);
    }

    // ------------------------------------------------------------------ rotation math

    /**
     * Rotation that makes an eye at {@code (ex,ey,ez)} look at
     * {@code (tx,ty,z)}: {@code {yaw, pitch, distance}} (degrees; distance is
     * the Euclidean eye-to-target distance). The formula is vanilla
     * {@code Entity.lookAt} inverted-exactly (see class javadoc); the vanilla
     * {@code Mth.wrapDegrees} reduction is applied so the result always sits
     * inside the schema's [-180, 180] / [-90, 90] bounds.
     *
     * <p>Degenerate case (target == eye): atan2(0, 0) = 0 yields yaw = -90,
     * pitch = 0 - the same arbitrary-but-harmless answer vanilla's own
     * {@code lookAt} gives (e.g. {@code /tp} facing your own eye position).
     */
    public static double[] rotationTowards(double ex, double ey, double ez,
                                           double tx, double ty, double tz) {
        double dx = tx - ex;
        double dy = ty - ey;
        double dz = tz - ez;
        double horizontal = Math.sqrt(dx * dx + dz * dz);
        double yaw = wrapDegrees(Math.toDegrees(Math.atan2(dz, dx)) - 90.0);
        double pitch = wrapDegrees(-Math.toDegrees(Math.atan2(dy, horizontal)));
        double distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
        return new double[]{yaw, pitch, distance};
    }

    /**
     * Degree reduction identical to vanilla {@code Mth.wrapDegrees} (1.21.1):
     * modulo 360, then shifted into [-180, 180). Re-implemented pure so the
     * smoke test can pin the boundary behaviour.
     */
    public static double wrapDegrees(double degrees) {
        double f = degrees % 360.0;
        if (f >= 180.0) {
            f -= 360.0;
        }
        if (f < -180.0) {
            f += 360.0;
        }
        return f;
    }

    // ------------------------------------------------------------------ results

    /** {@code look}/{@code lookAt} when no player exists: not an error (getStats convention). */
    public static JsonObject notInGameLook() {
        JsonObject result = new JsonObject();
        result.addProperty("in_game", false);
        result.addProperty("looked", false);
        return result;
    }

    /**
     * {@code look} result: the applied rotation plus where the view came
     * from (so callers can restore/chain turns).
     */
    public static JsonObject lookResult(double previousYaw, double previousPitch,
                                        double yaw, double pitch) {
        JsonObject previous = new JsonObject();
        previous.addProperty("yaw", previousYaw);
        previous.addProperty("pitch", previousPitch);

        JsonObject result = new JsonObject();
        result.addProperty("in_game", true);
        result.addProperty("looked", true);
        result.addProperty("yaw", yaw);
        result.addProperty("pitch", pitch);
        result.add("previous", previous);
        return result;
    }

    /** {@code lookAt} result: the target, the derived rotation and the eye-to-target distance. */
    public static JsonObject lookAtResult(double x, double y, double z,
                                          double yaw, double pitch, double distance) {
        JsonObject target = new JsonObject();
        target.addProperty("x", x);
        target.addProperty("y", y);
        target.addProperty("z", z);

        JsonObject result = new JsonObject();
        result.addProperty("in_game", true);
        result.addProperty("looked", true);
        result.add("target", target);
        result.addProperty("yaw", yaw);
        result.addProperty("pitch", pitch);
        result.addProperty("distance", distance);
        return result;
    }

    // ------------------------------------------------------------------ helpers

    /** Reads a finite number member; null when absent/not numeric/not finite. */
    private static Double finiteNumber(JsonObject params, String member) {
        JsonElement e = params.get(member);
        if (e == null || e.isJsonNull() || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) {
            return null;
        }
        double v = e.getAsDouble();
        return Double.isFinite(v) ? v : null;
    }

    /** Reads a REQUIRED finite number within [min, max]; -32602 message on any violation. */
    private static double boundedNumber(JsonObject params, String member, String tool,
                                        double min, double max) throws ToolContracts.InvalidParams {
        Double v = finiteNumber(params, member);
        if (v == null) {
            throw new ToolContracts.InvalidParams(tool + " requires finite numeric " + member
                    + " in [" + (int) min + ", " + (int) max + "]");
        }
        if (v < min || v > max) {
            throw new ToolContracts.InvalidParams(tool + " " + member + " must be within ["
                    + (int) min + ", " + (int) max + "], got: " + v);
        }
        return v;
    }
}
