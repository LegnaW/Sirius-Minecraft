package io.sirius.bridge;

import javax.imageio.IIOImage;
import javax.imageio.ImageIO;
import javax.imageio.ImageWriteParam;
import javax.imageio.ImageWriter;
import javax.imageio.stream.ImageOutputStream;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Iterator;
import java.util.List;

/**
 * Pure image pipeline for the {@code screenshot} tool: bbox cropping, JPEG
 * encoding with a quality parameter, the size-budget degradation ladder and
 * base64 wrapping. Deliberately free of Minecraft classes so it is testable
 * in a plain JVM (see {@code SmokeMain}).
 *
 * <p>Budget ladder (task M1-C): the base64 payload must stay within
 * {@link #MAX_BASE64_LENGTH} characters (~2MB). If the requested quality
 * overruns, quality is reduced in steps of 10 down to {@link #MIN_QUALITY};
 * if it still overruns, the image is scaled so its longest edge is
 * {@link #DOWNSCALE_LONGEST_EDGE}px and the ladder repeats. The response
 * reports {@code downscaled=true} whenever the fallback scale was needed.
 * (The ~100KB streaming budget from spec 8.2 is served by the separate
 * 4-argument {@link #encodeWithinBudget(BufferedImage, int, int, int)}
 * ladder added in M2-B; on-demand RPC screenshots stay high quality.)
 */
public final class ImageOps {

    /** Hard cap on the base64 string length (~2MB of base64 text). */
    public static final int MAX_BASE64_LENGTH = 2 * 1024 * 1024;

    /** Lowest quality the automatic degradation may use. */
    public static final int MIN_QUALITY = 40;

    /** Longest edge the fallback scale targets, in pixels. */
    public static final int DOWNSCALE_LONGEST_EDGE = 1024;

    private ImageOps() {
    }

    /**
     * Result of the budgeted encode: JPEG bytes + metadata about the path
     * taken. {@code width}/{@code height} are the dimensions of the image
     * that was actually encoded (after any ladder scaling) - the streaming
     * pipeline reports them in its notification payload because it never
     * sees the scaled intermediate itself.
     */
    public record Encoded(byte[] jpeg, int quality, boolean downscaled, int width, int height) {

        /** Length the base64 text of {@link #jpeg()} will have. */
        public int base64Length() {
            return base64(jpeg).length();
        }
    }

    /**
     * Crops {@code bbox = [x, y, w, h]} (pixels, origin = top-left of the
     * screenshot) out of {@code image}, clamping the rectangle to the image
     * bounds the way a viewer would. Throws when the box does not intersect
     * the image at all.
     */
    public static BufferedImage crop(BufferedImage image, int[] bbox) throws IOException {
        if (bbox == null) {
            return image;
        }
        int x = Math.max(0, bbox[0]);
        int y = Math.max(0, bbox[1]);
        int w = Math.min(bbox[2], image.getWidth() - x);
        int h = Math.min(bbox[3], image.getHeight() - y);
        if (w <= 0 || h <= 0) {
            throw new IOException("bbox " + java.util.Arrays.toString(bbox)
                    + " does not intersect the " + image.getWidth() + "x" + image.getHeight() + " image");
        }
        return image.getSubimage(x, y, w, h);
    }

    /** Encodes {@code image} as JPEG at {@code quality} (0..100). */
    public static byte[] encodeJpeg(BufferedImage image, int quality) throws IOException {
        Iterator<ImageWriter> writers = ImageIO.getImageWritersByFormatName("jpg");
        if (!writers.hasNext()) {
            throw new IOException("no JPEG writer available in this JRE");
        }
        ImageWriter writer = writers.next();
        try (ByteArrayOutputStream out = new ByteArrayOutputStream();
             ImageOutputStream imageOut = ImageIO.createImageOutputStream(out)) {
            ImageWriteParam param = writer.getDefaultWriteParam();
            param.setCompressionMode(ImageWriteParam.MODE_EXPLICIT);
            param.setCompressionQuality(Math.max(0.01f, Math.min(1.0f, quality / 100.0f)));
            writer.setOutput(imageOut);
            writer.write(null, new IIOImage(image, null, null), param);
            imageOut.flush();
            return out.toByteArray();
        } finally {
            writer.dispose();
        }
    }

    /** Scales {@code image} so its longest edge is at most {@code maxEdge} (no-op when already smaller). */
    public static BufferedImage scaleLongestEdge(BufferedImage image, int maxEdge) {
        int w = image.getWidth();
        int h = image.getHeight();
        int longest = Math.max(w, h);
        if (longest <= maxEdge) {
            return image;
        }
        double scale = (double) maxEdge / longest;
        int nw = Math.max(1, (int) Math.round(w * scale));
        int nh = Math.max(1, (int) Math.round(h * scale));
        BufferedImage scaled = new BufferedImage(nw, nh, BufferedImage.TYPE_INT_RGB);
        Graphics2D g = scaled.createGraphics();
        try {
            g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR);
            g.setRenderingHint(RenderingHints.KEY_RENDERING, RenderingHints.VALUE_RENDER_QUALITY);
            g.drawImage(image, 0, 0, nw, nh, null);
        } finally {
            g.dispose();
        }
        return scaled;
    }

    /**
     * Encodes within {@link #MAX_BASE64_LENGTH}: tries {@code quality}, then
     * quality - 10 ... down to {@link #MIN_QUALITY}; if still over budget,
     * scales to longest edge {@link #DOWNSCALE_LONGEST_EDGE}px and repeats
     * the ladder. A pathological image that overruns even the last rung is
     * returned as-is (never fails the request).
     *
     * <p>Real-machine verified in M1-C; this overload's ladder deliberately
     * stays EXACTLY as it was (quality descent first, single fallback scale)
     * and must not be re-expressed through the 4-argument streaming ladder,
     * whose rung order and quality list differ.
     */
    public static Encoded encodeWithinBudget(BufferedImage image, int quality) throws IOException {
        BufferedImage current = image;
        boolean scaled = false;
        Encoded last = null;
        for (int attempt = 0; attempt < 2; attempt++) {
            for (int q : qualityLadder(quality)) {
                byte[] jpeg = encodeJpeg(current, q);
                last = new Encoded(jpeg, q, scaled, current.getWidth(), current.getHeight());
                if (base64Length(jpeg) <= MAX_BASE64_LENGTH) {
                    return last;
                }
            }
            current = scaleLongestEdge(current, DOWNSCALE_LONGEST_EDGE);
            scaled = true;
        }
        return last; // safety valve: over budget but delivered
    }

    /**
     * Streaming-budget encode (M2-B, spec 8.2 ~100KB pipeline; ladder adopted
     * from N.E.K.O service.py:1241-1307): quality-first descent
     * {@code [quality, 65, 50, 40, 30]} (never above the requested quality)
     * crossed with an edge ladder {@code [maxLongestEdge, /2, /4]} - for each
     * quality the image shrinks step by step before quality is given up
     * further. The FIRST combination whose base64 length fits
     * {@code maxBase64Length} wins; if nothing fits, the SMALLEST attempt is
     * shipped anyway ({@code downscaled=true} when any scaling happened) - a
     * frame is never dropped for budget reasons, matching N.E.K.O's
     * "ship smallest with a warning" policy.
     *
     * <p>{@code maxLongestEdge <= 0} disables scaling (encode at native size,
     * quality ladder only). Dimensions of the actually-encoded image are
     * reported in the {@link Encoded} record.
     */
    public static Encoded encodeWithinBudget(BufferedImage image, int quality,
                                             int maxBase64Length, int maxLongestEdge) throws IOException {
        int[] qualities = streamQualityLadder(quality);
        int[] edges = streamEdgeLadder(maxLongestEdge);
        int sourceEdge = Math.max(image.getWidth(), image.getHeight());

        // Pre-scale once per edge rung (reused across the quality list).
        BufferedImage[] frames = new BufferedImage[edges.length];
        for (int i = 0; i < edges.length; i++) {
            frames[i] = scaleLongestEdge(image, edges[i]);
        }
        if (edges.length == 0) { // scaling disabled -> single native-size rung
            edges = new int[]{sourceEdge};
            frames = new BufferedImage[]{image};
        }

        Encoded smallest = null;
        for (int q : qualities) {
            for (int i = 0; i < frames.length; i++) {
                BufferedImage frame = frames[i];
                byte[] jpeg = encodeJpeg(frame, q);
                boolean downscaled = Math.max(frame.getWidth(), frame.getHeight()) < sourceEdge;
                Encoded candidate = new Encoded(jpeg, q, downscaled, frame.getWidth(), frame.getHeight());
                if (base64Length(jpeg) <= maxBase64Length) {
                    return candidate;
                }
                if (smallest == null || jpeg.length < smallest.jpeg().length) {
                    smallest = candidate;
                }
            }
        }
        return smallest; // over budget but delivered - never drop the frame
    }

    /**
     * The streaming quality ladder: {@code [start, 65, 50, 40, 30]} capped at
     * {@code start} (never encode above the requested quality), de-duplicated
     * and descending; a {@code start} below 30 collapses to just
     * {@code [start]}.
     */
    static int[] streamQualityLadder(int start) {
        int q0 = Math.max(1, Math.min(100, start));
        List<Integer> ladder = new ArrayList<>();
        for (int q : new int[]{q0, 65, 50, 40, 30}) {
            if (q <= q0 && !ladder.contains(q)) {
                ladder.add(q);
            }
        }
        if (ladder.isEmpty()) {
            ladder.add(q0);
        }
        int[] out = new int[ladder.size()];
        for (int i = 0; i < out.length; i++) {
            out[i] = ladder.get(i);
        }
        return out;
    }

    /**
     * The streaming edge ladder: {@code [maxEdge, maxEdge/2, maxEdge/4]},
     * positive values only, de-duplicated (an edge of 1024 yields
     * 1024/512/256). {@code maxEdge <= 0} yields an empty ladder (scaling
     * disabled).
     */
    static int[] streamEdgeLadder(int maxEdge) {
        if (maxEdge <= 0) {
            return new int[0];
        }
        List<Integer> ladder = new ArrayList<>();
        for (int e : new int[]{maxEdge, maxEdge / 2, maxEdge / 4}) {
            if (e > 0 && !ladder.contains(e)) {
                ladder.add(e);
            }
        }
        int[] out = new int[ladder.size()];
        for (int i = 0; i < out.length; i++) {
            out[i] = ladder.get(i);
        }
        return out;
    }

    /**
     * The qualities to try, descending from {@code from} in steps of 10 but
     * never below {@link #MIN_QUALITY}; {@link #MIN_QUALITY} itself is always
     * the final rung (unless the caller asked for less to begin with).
     */
    static int[] qualityLadder(int from) {
        int start = Math.max(0, Math.min(100, from));
        int floor = Math.min(start, MIN_QUALITY);
        int[] ladder = new int[Math.max(1, (start - floor) / 10 + 1)];
        ladder[0] = start;
        for (int i = 1; i < ladder.length; i++) {
            ladder[i] = Math.max(floor, start - 10 * i);
        }
        if (ladder[ladder.length - 1] > floor) {
            int[] withFloor = new int[ladder.length + 1];
            System.arraycopy(ladder, 0, withFloor, 0, ladder.length);
            withFloor[ladder.length] = floor;
            ladder = withFloor;
        }
        return ladder;
    }

    /** Standard base64 (RFC 4648, with padding) of {@code data}. */
    public static String base64(byte[] data) {
        return Base64.getEncoder().encodeToString(data);
    }

    /** Length {@link #base64(byte[])} would produce, without building the string. */
    static int base64Length(byte[] data) {
        return 4 * ((data.length + 2) / 3);
    }
}
