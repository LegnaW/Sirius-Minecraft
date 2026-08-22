package io.sirius.bridge;

import net.minecraft.client.Minecraft;
import net.minecraft.client.Options;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.fml.loading.FMLEnvironment;
import net.neoforged.fml.loading.FMLPaths;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.client.event.ClientChatReceivedEvent;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import net.neoforged.neoforge.client.event.ScreenEvent;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.GameShuttingDownEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Path;
import java.security.SecureRandom;

/**
 * Sirius Bridge - the "eyes and hands" of the Sirius AI companion on the real
 * Minecraft client (spec 8.2).
 *
 * <p>M1-B scope: runs a WebSocket server on {@code 127.0.0.1:<port>} (default
 * 8765, see {@code config/sirius_bridge.toml}) with a token handshake
 * ({@code hello}/{@code hello_ack}), capability negotiation
 * ({@code capabilities/list} from the frozen schema resources), a frame
 * dispatch skeleton ({@code request}/{@code task}, JSON-RPC style error codes)
 * and an audit log at {@code logs/sirius_bridge.log}. Real tool
 * implementations (screenshot/getStats/world.query/...) land in M1-C via
 * {@link ToolRegistry#register(String, ToolRegistry.Handler)}.
 *
 * <p>Lifecycle: the mod constructor only registers event listeners - the
 * server starts on the first client tick after the loading overlay is gone
 * (title screen reached), so mod/resource loading is never blocked, and shuts
 * down gracefully on {@link GameShuttingDownEvent}.
 */
@Mod(SiriusBridge.MOD_ID)
public class SiriusBridge {

    public static final String MOD_ID = "sirius_bridge";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    /** The bridge server once started (client side only; null before that). */
    private BridgeServer server;

    /**
     * M4.1 movement look switch, cached from the config at {@link #start()}
     * (the tick pump runs before the server exists, so the flag lives here).
     */
    private volatile boolean movementLook = true;

    public SiriusBridge() {
        if (FMLEnvironment.dist != Dist.CLIENT) {
            LOGGER.info("sirius-bridge is client-only; nothing to do on dist {}", FMLEnvironment.dist);
            return;
        }
        NeoForge.EVENT_BUS.addListener(this::onClientTick);
        NeoForge.EVENT_BUS.addListener(this::onGameShuttingDown);
        LOGGER.info("sirius-bridge loaded (M1-B: WebSocket server + hello/token + capabilities + dispatch skeleton)");
    }

    /** Starts the server once the client finished its initial loading. */
    private void onClientTick(ClientTickEvent.Post event) {
        // M3.5 v1.2 per-tick pumps: the smooth-turn controller and the dig
        // monitor advance here (main thread; no-ops when idle) - they must
        // run whether or not the server has started, so they sit BEFORE the
        // early return, riding this same listener. The M4.1 movement-look
        // pump rides along (yields to any active explicit turn).
        TurnController.onClientTick();
        MovementLook.onClientTick(movementLook);
        DigTools.onClientTick();
        if (server != null) {
            // M2-B: the event push channel's tick sampler (danger states ~1/s,
            // screenshot stream grabs ~1Hz while subscribed) rides the same
            // listener - it must run on the client main/render thread anyway.
            server.eventPusher().onClientTick();
            return; // server already started
        }
        Minecraft client = Minecraft.getInstance();
        if (client.getOverlay() != null) {
            return; // still in the initial loading overlay
        }
        start();
    }

    private void onGameShuttingDown(GameShuttingDownEvent event) {
        if (server != null) {
            server.shutdown();
            server = null;
        }
    }

    private void start() {
        try {
            Path gameDir = FMLPaths.GAMEDIR.get();
            Path configFile = FMLPaths.CONFIGDIR.get().resolve("sirius_bridge.toml");
            BridgeConfig config = BridgeConfig.load(configFile, new SecureRandom());
            if (!config.notes.isEmpty()) {
                LOGGER.info("sirius-bridge: config: {}", config.notes);
            }
            movementLook = config.movementLook;
            applyFocusPolicy(config);
            AuditLog audit = AuditLog.create(gameDir);
            server = new BridgeServer(config, audit);
            server.startAsync();
            registerEventListeners();
            LOGGER.info("sirius-bridge: connect with ws://127.0.0.1:{} (token: config/sirius_bridge.toml "
                    + "or logs/sirius_bridge.log)", config.port);
        } catch (Exception e) {
            LOGGER.error("sirius-bridge: failed to start the WebSocket server", e);
        }
    }

    /**
     * M2-B: wires the NeoForge game-event sources (chat lines, GUI
     * open/close) into the bridge's event push channel, right after the
     * server exists. The handlers run on the client main thread and merely
     * extract data - delivery, filtering and seq assignment all happen in
     * {@link EventPusher#push}, which is thread-safe. The tick sampler is
     * NOT registered here: it rides the existing {@link #onClientTick}
     * listener (see there).
     */
    private void registerEventListeners() {
        EventPusher pusher = server.eventPusher();
        NeoForge.EVENT_BUS.addListener(pusher::onChatReceived);
        NeoForge.EVENT_BUS.addListener(pusher::onScreenOpening);
        NeoForge.EVENT_BUS.addListener(pusher::onScreenClosing);
        LOGGER.info("sirius-bridge: event push channel armed (chat/gui_open/gui_close + danger sampler"
                + " + screenshot stream; clients must events.subscribe to receive anything)");
    }

    /**
     * M2-A2: with {@code keep_running_unfocused = true} (the default), the
     * world keeps ticking while the game window is unfocused - the human
     * "switches to a terminal/browser and the bot's world must not stop"
     * scenario.
     *
     * <p>Mechanics (verified against the 1.21.1 decompiled sources): vanilla
     * pauses 500 ms after focus loss because {@code GameRenderer.render}
     * calls {@code pauseGame(false)} when {@code !isWindowActive() &&
     * options.pauseOnLostFocus}; the opened {@code PauseScreen} is what
     * freezes singleplayer ticks. {@code pauseOnLostFocus} is a plain
     * {@code public boolean} field on {@code Options} (NOT an
     * {@code OptionInstance} in 1.21.1), so the whole runtime API is a field
     * write. F3+P proves this is a supported runtime toggle (it flips the
     * field the same way, plus {@code options.save()}).
     *
     * <p>We deliberately do NOT call {@code options.save()}: the user's
     * {@code options.txt} is never written by the bridge. Caveat (documented
     * in the README): if the user later saves options themselves (any change
     * in the vanilla settings screen, or F3+P), vanilla persists ALL fields
     * including our runtime {@code false}. That is vanilla behaviour, not a
     * bridge write; setting {@code keep_running_unfocused = false} stops the
     * bridge from touching the field at all.
     */
    private void applyFocusPolicy(BridgeConfig config) {
        if (!config.keepRunningUnfocused) {
            return;
        }
        try {
            Options options = Minecraft.getInstance().options;
            options.pauseOnLostFocus = false;
            LOGGER.info("sirius-bridge: keep_running_unfocused=true -> vanilla pauseOnLostFocus disabled"
                    + " (runtime only, options.txt untouched); view rotation still needs a focused window");
        } catch (Throwable t) {
            LOGGER.warn("sirius-bridge: could not disable pauseOnLostFocus: {}", t.toString());
        }
    }

    /** Exposed for tests/diagnostics; may be null before the client finished loading. */
    public BridgeServer server() {
        return server;
    }
}
