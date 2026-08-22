package io.sirius.bridge;

import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.platform.NativeImage;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.client.multiplayer.PlayerInfo;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.tags.BlockTags;
import net.minecraft.tags.TagKey;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.food.FoodData;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;

import java.awt.image.BufferedImage;
import java.awt.image.DataBufferInt;
import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

/**
 * The three M1-C perception tools: {@code screenshot}, {@code getStats} and
 * {@code world.query}. This class is the thin Minecraft-coupled shell - it
 * extracts raw data on the client main (render) thread and delegates all
 * decisions (validation, cropping, JPEG budget, response shape) to the pure
 * {@link ToolContracts} / {@link ImageOps}, which the smoke test covers
 * without a running game.
 *
 * <p>Threading: handlers run on a WebSocket thread. Every read of game state
 * goes through {@link #callOnMainThread}, which schedules via
 * {@link ToolContext#onMainThread} (a {@code Minecraft.execute} under the
 * hood) and blocks the calling WS thread until the frame loop runs the task
 * (bounded by {@link #MAIN_THREAD_TIMEOUT_SECONDS}; the render loop stops
 * running tasks while the window is iconified, so an unbounded wait would
 * hang the connection). Expensive post-processing (JPEG encode, base64)
 * deliberately runs on the WS thread so the render thread only pays for the
 * pixel download.
 *
 * <p>Framebuffer facts (verified against 1.21.1 sources): the HUD, open
 * screens and overlays are rendered INTO {@code Minecraft.getMainRenderTarget()}
 * ({@code Minecraft.runTick} binds it every frame before
 * {@code gameRenderer.render}; {@code GameRenderer.render} rebinds it for the
 * GUI pass), so {@link Screenshot#takeScreenshot} on that target yields
 * exactly what the player sees, GUI included. The task runs inside
 * {@code runAllTasks} at the start of a frame, i.e. on the render thread with
 * the previous complete frame still in the target.
 */
final class PerceptionTools {

    /** Upper bound for waiting on the client main thread (iconified window = tasks starve). */
    private static final long MAIN_THREAD_TIMEOUT_SECONDS = 10;

    private PerceptionTools() {
    }

    /** Registers the three tools; called once from the BridgeServer constructor. */
    static void registerAll(ToolRegistry tools) {
        tools.register("screenshot", PerceptionTools::screenshot);
        tools.register("getStats", PerceptionTools::getStats);
        tools.register("world.query", PerceptionTools::worldQuery);
    }

    // ------------------------------------------------------------------ screenshot

    private static JsonObject screenshot(ToolContext ctx, JsonObject params) throws Exception {
        final ToolContracts.ScreenshotParams p;
        try {
            p = ToolContracts.screenshotParams(params);
        } catch (ToolContracts.InvalidParams e) {
            return invalidParams(ctx, e);
        }

        BufferedImage full = callOnMainThread(ctx, PerceptionTools::grabScreen);
        long takenAt = System.currentTimeMillis();

        BufferedImage image = full;
        if ("crop".equals(p.tier())) {
            try {
                image = ImageOps.crop(full, p.bbox());
            } catch (IOException e) {
                return Json.errorResponse(ctx.id(), Json.INVALID_PARAMS, e.getMessage(), null);
            }
        }

        ImageOps.Encoded encoded = ImageOps.encodeWithinBudget(image, p.quality());
        return Json.okResponse(ctx.id(), ToolContracts.screenshotResult(
                ImageOps.base64(encoded.jpeg()),
                image.getWidth(),
                image.getHeight(),
                takenAt,
                encoded.quality(),
                encoded.downscaled()));
    }

    /**
     * Reads the main framebuffer. MUST run on the render thread (it binds the
     * colour texture and downloads it); call via {@link #callOnMainThread}.
     * Package-private: InputTools reuses it for GUI-click evidence shots.
     */
    static BufferedImage grabScreen() {
        RenderTarget target = Minecraft.getInstance().getMainRenderTarget();
        NativeImage shot = Screenshot.takeScreenshot(target); // world + hand + GUI, as on screen
        try {
            return toBufferedImage(shot);
        } finally {
            shot.close(); // native memory - always free it
        }
    }

    /** NativeImage RGBA (little-endian ABGR ints) -> TYPE_INT_RGB BufferedImage. */
    private static BufferedImage toBufferedImage(NativeImage image) {
        int width = image.getWidth();
        int height = image.getHeight();
        BufferedImage out = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
        int[] rgb = ((DataBufferInt) out.getRaster().getDataBuffer()).getData();
        int i = 0;
        for (int y = 0; y < height; y++) {
            for (int x = 0; x < width; x++) {
                int abgr = image.getPixelRGBA(x, y);
                // ABGR -> ARGB: swap the red and blue byte lanes, keep alpha+green.
                rgb[i++] = (abgr & 0xFF00FF00) | ((abgr >> 16) & 0x000000FF) | ((abgr << 16) & 0x00FF0000);
            }
        }
        return out;
    }

    // ------------------------------------------------------------------ getStats

    private static JsonObject getStats(ToolContext ctx, JsonObject params) throws Exception {
        ToolContracts.StatsSnapshot snapshot = callOnMainThread(ctx, () -> {
            LocalPlayer player = Minecraft.getInstance().player;
            return player == null ? null : readStats(player);
        });
        return Json.okResponse(ctx.id(),
                snapshot != null ? ToolContracts.statsResult(snapshot) : ToolContracts.notInGame());
    }

    /** Reads everything getStats reports; runs on the main thread. */
    private static ToolContracts.StatsSnapshot readStats(LocalPlayer player) {
        Minecraft mc = Minecraft.getInstance();
        FoodData food = player.getFoodData();
        List<ToolContracts.EffectFact> effects = new ArrayList<>();
        for (MobEffectInstance instance : player.getActiveEffects()) {
            effects.add(new ToolContracts.EffectFact(
                    instance.getEffect().getRegisteredName(), // e.g. "minecraft:speed"
                    instance.getDuration(),
                    instance.getAmplifier()));
        }
        return new ToolContracts.StatsSnapshot(
                player.getHealth(),
                food.getFoodLevel(),
                food.getSaturationLevel(),
                player.getAirSupply(),
                player.experienceLevel,
                player.experienceProgress,
                player.getX(), player.getY(), player.getZ(),
                player.level().dimension().location().toString(),
                gameMode(mc, player),
                effects,
                player.isAlive(),
                player.getYRot(),   // M4.1 v1.3: view direction (unstuck fan base / turn diagnostics)
                player.getXRot());
    }

    /** Game mode from the client's player info, with an ability-based fallback. */
    private static String gameMode(Minecraft mc, LocalPlayer player) {
        ClientPacketListener connection = mc.getConnection();
        if (connection != null) {
            PlayerInfo info = connection.getPlayerInfo(player.getUUID());
            if (info != null) {
                GameType mode = info.getGameMode();
                if (mode != null) {
                    return mode.getName();
                }
            }
        }
        if (player.isSpectator()) {
            return "spectator";
        }
        return player.isCreative() ? "creative" : "survival";
    }

    // ------------------------------------------------------------------ world.query

    private static JsonObject worldQuery(ToolContext ctx, JsonObject params) throws Exception {
        final ToolContracts.WorldQueryParams p;
        try {
            p = ToolContracts.worldQueryParams(params);
        } catch (ToolContracts.InvalidParams e) {
            return invalidParams(ctx, e);
        }

        JsonObject result = callOnMainThread(ctx, () -> {
            Minecraft mc = Minecraft.getInstance();
            LocalPlayer player = mc.player;
            ClientLevel level = mc.level;
            if (player == null || level == null) {
                return null; // not in a world -> graceful {"in_game": false}
            }
            if ("blocks".equals(p.type())) {
                BlockPos.MutableBlockPos cursor = new BlockPos.MutableBlockPos(); // reused, no per-block garbage
                if (p.filter() != null) {
                    // v1.1 filtered path: nearest-first via ToolContracts' bounded
                    // heap; tag membership resolved here because TagKey is a
                    // Minecraft type (Contracts stays pure for the smoke test).
                    ToolContracts.BlockFilter filter = ToolContracts.BlockFilter.parse(p.filter());
                    return ToolContracts.scanBlocks(player.getX(), player.getY(), player.getZ(), p.range(),
                            nameProbe(level, cursor), filter, tagProbe(filter));
                }
                // v1.0 unfiltered path - unchanged.
                BlockPos center = player.blockPosition();
                return ToolContracts.scanBlocks(center.getX(), center.getY(), center.getZ(), p.range(),
                        nameProbe(level, cursor));
            }
            List<ToolContracts.EntityFact> facts = new ArrayList<>();
            for (Entity entity : level.entitiesForRendering()) {
                if (entity.isRemoved()) {
                    continue;
                }
                float health = Float.NaN; // omitted unless reliably available
                if (entity instanceof LivingEntity living && living.getHealth() > 0.0F) {
                    health = living.getHealth();
                }
                // T7: item entities carry the dropped stack's registry id + size.
                // The display name is client-localized and cannot be matched
                // against block/item ids brain-side, so the registry id rides
                // along - the same reason blocks report "minecraft:oak_log".
                String item = null;
                int itemCount = 0;
                if (entity instanceof ItemEntity itemEntity) {
                    ItemStack stack = itemEntity.getItem();
                    var itemId = BuiltInRegistries.ITEM.getKey(stack.getItem());
                    item = itemId != null ? itemId.toString() : "unknown";
                    itemCount = stack.getCount();
                }
                // M4: registry mob category (lowercase MobCategory name - a
                // registry datum, so modded monsters classify themselves as
                // "monster" without a hardcoded list) and collision-box width
                // (the brain's flee reflex approximates melee reach as
                // width/2 + 1.5). Both additive fields; protocol stays 1.2.
                String category = entity.getType().getCategory()
                        .name().toLowerCase(Locale.ROOT);
                double width = entity.getDimensions(entity.getPose()).width();
                facts.add(new ToolContracts.EntityFact(
                        entity.getStringUUID(),
                        entity.getName().getString(),
                        EntityType.getKey(entity.getType()).toString(),
                        entity.getX(), entity.getY(), entity.getZ(),
                        health, item, itemCount, category, width));
            }
            return ToolContracts.filterEntities(facts, player.getX(), player.getY(), player.getZ(),
                    p.range(), p.filter());
        });
        return Json.okResponse(ctx.id(), result != null ? result : ToolContracts.notInGame());
    }

    // ------------------------------------------------------------------ world.query block access

    /** Registry name of the block at (x,y,z), or null for air / unloaded chunk. */
    private static ToolContracts.BlockProbe nameProbe(ClientLevel level, BlockPos.MutableBlockPos cursor) {
        return (x, y, z) -> {
            BlockState state = level.getBlockState(cursor.set(x, y, z));
            if (state.isAir()) {
                return null; // unloaded chunks also surface as air
            }
            var id = BuiltInRegistries.BLOCK.getKey(state.getBlock());
            return id != null ? id.toString() : "unknown";
        };
    }

    /**
     * Tag membership for the filtered scan, over the real holder tags
     * ({@code BlockState.is(TagKey)} semantics - tags live on the registry
     * holder, so the block's default state answers for every state of that
     * type). TagKeys are resolved once per query; the name -> matching-tag-set
     * memo costs one registry lookup per DISTINCT block name in range (a
     * handful even in dense forests) instead of per position.
     */
    private static ToolContracts.TagProbe tagProbe(ToolContracts.BlockFilter filter) {
        if (filter.tagIds().isEmpty()) {
            return null; // ids-only filter - the scan never consults the probe
        }
        List<TagKey<Block>> tagKeys = new ArrayList<>(filter.tagIds().size());
        for (String tagId : filter.tagIds()) {
            // BlockTags.create == TagKey.create(Registries.BLOCK, id); unknown
            // tags simply match nothing (holder.is checks set membership).
            tagKeys.add(BlockTags.create(ResourceLocation.parse(tagId)));
        }
        Map<String, Set<String>> memo = new HashMap<>();
        return (blockName, tagId) -> memo
                .computeIfAbsent(blockName, name -> {
                    Block block = BuiltInRegistries.BLOCK.get(ResourceLocation.parse(name));
                    Set<String> hits = new HashSet<>();
                    if (block != null) { // names come from the registry itself; null only for exotic failures
                        for (int i = 0; i < tagKeys.size(); i++) {
                            if (block.defaultBlockState().is(tagKeys.get(i))) {
                                hits.add(filter.tagIds().get(i));
                            }
                        }
                    }
                    return hits;
                })
                .contains(tagId);
    }

    // ------------------------------------------------------------------ helpers

    /** Maps a schema violation to the standard {@code -32602} response. */
    private static JsonObject invalidParams(ToolContext ctx, ToolContracts.InvalidParams e) {
        return Json.errorResponse(ctx.id(), Json.INVALID_PARAMS, e.getMessage(), null);
    }

    /**
     * Runs {@code supplier} on the client main thread and blocks the calling
     * WS thread for its result. Times out (iconified window / shutdown)
     * instead of hanging; a failure inside the task propagates to the caller
     * (the dispatcher turns it into {@code -32603}).
     *
     * <p>Package-private: the M2-C {@link GuiTools} reuses this latch - the
     * only change from its former {@code private} visibility (M2-A chose a
     * local copy back then; new tools should prefer this shared one).
     */
    static <T> T callOnMainThread(ToolContext ctx, Supplier<T> supplier) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        Object[] box = new Object[2]; // [0] result, [1] failure
        ctx.onMainThread(() -> {
            try {
                box[0] = supplier.get();
            } catch (Throwable t) {
                box[1] = t;
            } finally {
                done.countDown();
            }
        });
        if (!done.await(MAIN_THREAD_TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
            throw new IllegalStateException("client main thread did not run the task within "
                    + MAIN_THREAD_TIMEOUT_SECONDS + "s (game iconified or shutting down?)");
        }
        if (box[1] != null) {
            if (box[1] instanceof Exception e) {
                throw e;
            }
            if (box[1] instanceof Error e) {
                throw e;
            }
            throw new IllegalStateException(String.valueOf(box[1]));
        }
        @SuppressWarnings("unchecked")
        T result = (T) box[0];
        return result;
    }
}
