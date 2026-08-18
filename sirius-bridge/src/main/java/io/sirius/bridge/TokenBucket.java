package io.sirius.bridge;

import java.util.function.LongSupplier;

/**
 * Token-bucket rate limiter for the {@code input.*} tools (spec 8.2 security
 * model: "input rate limiting ~20/s"). Pure JDK, clock injectable so the
 * smoke test can verify refill deterministically.
 *
 * <p>Behaviour: the bucket holds {@code capacity} tokens (burst == one
 * second of allowance) and refills continuously at
 * {@code refillPerSecond}. A call consumes a token only when one is
 * available - there is no waiting/borrowing, matching the hard-fail
 * {@code rate_limited} error the protocol prescribes.
 */
public final class TokenBucket {

    private final int capacity;
    private final double refillPerSecond;
    private final LongSupplier clockNanos;

    private double tokens;
    private long lastRefillNanos;

    public TokenBucket(int refillPerSecond) {
        this(refillPerSecond, System::nanoTime);
    }

    /** Test constructor: capacity = {@code refillPerSecond}, clock fully injectable. */
    public TokenBucket(int refillPerSecond, LongSupplier clockNanos) {
        if (refillPerSecond < 1) {
            throw new IllegalArgumentException("refillPerSecond must be >= 1, got " + refillPerSecond);
        }
        this.capacity = refillPerSecond;
        this.refillPerSecond = refillPerSecond;
        this.clockNanos = clockNanos;
        this.tokens = capacity;
        this.lastRefillNanos = clockNanos.getAsLong();
    }

    /** Attempts to take {@code n} tokens; false when the bucket cannot satisfy the request. */
    public synchronized boolean tryAcquire(int n) {
        if (n < 0) {
            throw new IllegalArgumentException("n must be >= 0");
        }
        refill();
        if (n <= tokens) {
            tokens -= n;
            return true;
        }
        return false;
    }

    /** Tokens available right now (fractional, after refilling). */
    public synchronized double availableTokens() {
        refill();
        return tokens;
    }

    /** The configured refill rate in tokens per second. */
    public int refillPerSecond() {
        return (int) refillPerSecond;
    }

    private void refill() {
        long now = clockNanos.getAsLong();
        double elapsedSeconds = (now - lastRefillNanos) / 1_000_000_000.0;
        if (elapsedSeconds > 0) {
            tokens = Math.min(capacity, tokens + elapsedSeconds * refillPerSecond);
            lastRefillNanos = now;
        }
    }
}
