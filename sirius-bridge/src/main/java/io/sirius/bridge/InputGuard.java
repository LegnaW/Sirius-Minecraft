package io.sirius.bridge;

/**
 * Shared guard rails for every bridge tool that ACTS on the game: the
 * {@code input_enabled} master switch, the combined input.* token-bucket rate
 * limit, the GUI-click-evidence flag and the M2-D permission tier.
 *
 * <p>Constructed once per server from {@link BridgeConfig} and handed to the
 * tool classes at registration ({@code InputTools.registerAll},
 * {@code LookTools.registerAll}) so the guard preamble (enabled -&gt; tier
 * -&gt; rate) sits identically in every acting tool. Package-private on
 * purpose: it is bridge-internal wiring, not a protocol type.
 *
 * <p>Extracted from InputTools' private inner class in M2-D so look/lookAt
 * can share the same tier/switch state; the pure decision logic lives in
 * {@link PermissionContracts} (smoke-testable without a game).
 */
final class InputGuard {

    private final boolean enabled;
    private final boolean guiClickEvidence;
    private final PermissionContracts.Tier tier;
    private final TokenBucket bucket;

    InputGuard(BridgeConfig config) {
        this.enabled = config.inputEnabled;
        this.guiClickEvidence = config.guiClickEvidence;
        this.tier = config.permission;
        this.bucket = new TokenBucket(config.rateLimitPerSec);
    }

    /** False when {@code input_enabled = false}: acting tools answer -32011. */
    boolean enabled() {
        return enabled;
    }

    /** The M2-D permission tier (see {@link PermissionContracts}). */
    PermissionContracts.Tier tier() {
        return tier;
    }

    /** Takes one call's worth of rate-limit allowance. */
    boolean tryAcquire() {
        return bucket.tryAcquire(1);
    }

    /** The configured calls/s budget (for the rate-limited error message). */
    int limitPerSecond() {
        return bucket.refillPerSecond();
    }

    /** Whether GUI clicks save an evidence screenshot first. */
    boolean guiClickEvidence() {
        return guiClickEvidence;
    }
}
