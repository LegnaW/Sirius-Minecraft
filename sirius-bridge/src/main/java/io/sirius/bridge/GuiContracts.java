package io.sirius.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

import java.util.ArrayList;
import java.util.List;

/**
 * Frozen-contract logic for the M2-C GUI state tool ({@code getGuiState}):
 * parameter validation (the frozen schema declares EMPTY params), widget/slot
 * JSON assembly with the node cap + truncation discipline, and the three
 * response shapes (no screen / standard / fallback). Pure Gson + JDK - no
 * Minecraft classes - so the smoke test covers it without a running game.
 *
 * <p>The Minecraft shell ({@code GuiTools}) streams in plain records
 * extracted on the client main thread; every decision about shape, caps and
 * field omission lives here.
 *
 * <p><b>Coordinate basis:</b> all x/y values produced here are
 * <b>GUI-scaled</b> coordinates - the same basis as the {@code gui_scaled}
 * object {@code input.mouseMove} RETURNS (widget {@code getX()/getY()} and
 * {@code leftPos + slot.x} are already in that space). They are NOT window
 * pixels (what mouseMove TAKES): to click a slot at gui (gx, gy), convert
 * with the scale derivable from any mouseMove response
 * ({@code window_px = gui * guiScaledWidth -> screenWidth factor}, i.e.
 * delivered px / returned gui_scaled for one probe move).
 */
public final class GuiContracts {

    private GuiContracts() {
    }

    // ------------------------------------------------------------------ params

    /**
     * Validates {@code getGuiState} params: the frozen schema declares an
     * empty object ({@code getGuiState()} takes no arguments), so any member
     * present is a {@code -32602} violation.
     */
    public static void guiStateParams(JsonObject params) throws ToolContracts.InvalidParams {
        if (!params.entrySet().isEmpty()) {
            throw new ToolContracts.InvalidParams("getGuiState takes no parameters (got: "
                    + String.join(",", params.keySet()) + ")");
        }
    }

    // ------------------------------------------------------------------ widget nodes

    /** Max widget nodes in one response (world.query discipline: stop + flag). */
    public static final int WIDGETS_CAP = 512;

    /**
     * One GUI widget, extracted from {@code AbstractWidget} by the shell.
     * {@code message}/{@code text} are null when absent/empty (field omitted
     * from JSON); {@code text} is only set for text fields (EditBox).
     */
    public record WidgetNode(String type, int x, int y, int width, int height,
                             boolean visible, boolean active, String message, String text) {

        /** Same node without the flag/text fields - the fallback "rect" shape. */
        public JsonObject toRect() {
            JsonObject rect = new JsonObject();
            rect.addProperty("type", type);
            rect.addProperty("x", x);
            rect.addProperty("y", y);
            rect.addProperty("width", width);
            rect.addProperty("height", height);
            return rect;
        }
    }

    /**
     * Streams widget nodes in traversal order, enforcing {@link #WIDGETS_CAP}:
     * once full, {@link #add} refuses entries and {@link #truncated()} flips -
     * callers stop enumerating (same discipline as the world.query 512 cap).
     */
    public static final class WidgetCollector {
        private final List<WidgetNode> nodes = new ArrayList<>();
        private boolean truncated;

        /** Adds a node unless the cap is hit; false = refused (stop traversing). */
        public boolean add(WidgetNode node) {
            if (nodes.size() >= WIDGETS_CAP) {
                truncated = true;
                return false;
            }
            nodes.add(node);
            return true;
        }

        /** Whether the cap is reached (callers should stop feeding). */
        public boolean full() {
            return nodes.size() >= WIDGETS_CAP;
        }

        /** Whether entries were refused because of the cap. */
        public boolean truncated() {
            return truncated;
        }

        /** The accepted nodes (defensive copy for assembly). */
        public List<WidgetNode> nodes() {
            return List.copyOf(nodes);
        }
    }

    /** Builds the {@code widgets} array: cap-safe, empty message/text omitted. */
    static JsonArray widgetsArray(List<WidgetNode> nodes) {
        JsonArray widgets = new JsonArray();
        for (WidgetNode node : nodes) {
            JsonObject w = new JsonObject();
            w.addProperty("type", node.type());
            w.addProperty("x", node.x());
            w.addProperty("y", node.y());
            w.addProperty("width", node.width());
            w.addProperty("height", node.height());
            w.addProperty("visible", node.visible());
            w.addProperty("active", node.active());
            if (node.message() != null && !node.message().isEmpty()) {
                w.addProperty("message", node.message());
            }
            if (node.text() != null) {
                w.addProperty("text", node.text());
            }
            widgets.add(w);
        }
        return widgets;
    }

    // ------------------------------------------------------------------ slots

    /**
     * Slot role strings (Numen GuiOps generic detection): "crafting" (container
     * is a CraftingContainer), "result" (ResultSlot), "hotbar" (player
     * inventory, container index 0-8), "player" (player inventory, index >= 9),
     * "container" (anything else - chest/furnace/modded containers).
     */
    public static final String ROLE_CRAFTING = "crafting";
    public static final String ROLE_RESULT = "result";
    public static final String ROLE_HOTBAR = "hotbar";
    public static final String ROLE_PLAYER = "player";
    public static final String ROLE_CONTAINER = "container";

    /**
     * One container slot. {@code item} is the registry name ("minecraft:oak_log")
     * or null (empty / access failed); {@code count} is 0 when empty;
     * {@code note} explains a degraded entry (item access failure) or is null.
     */
    public record SlotFact(int index, int x, int y, String role, String item, int count, String note) {
    }

    /**
     * Builds the {@code slots} array. No separate cap: vanilla containers stay
     * under ~50 slots and even large modded menus are two-digit; the 512
     * widget cap protects the response size (slots are flat, not recursive).
     */
    static JsonArray slotsArray(List<SlotFact> slots) {
        JsonArray array = new JsonArray();
        for (SlotFact slot : slots) {
            JsonObject s = new JsonObject();
            s.addProperty("index", slot.index());
            s.addProperty("x", slot.x());
            s.addProperty("y", slot.y());
            s.addProperty("role", slot.role());
            s.add("item", slot.item() != null ? new JsonPrimitive(slot.item()) : JsonNull.INSTANCE);
            s.addProperty("count", slot.count());
            if (slot.note() != null) {
                s.addProperty("note", slot.note());
            }
            array.add(s);
        }
        return array;
    }

    // ------------------------------------------------------------------ response shapes

    /** {@code {"screen_open": false}} - no Screen open (not an error). */
    public static JsonObject noScreenResult() {
        JsonObject result = new JsonObject();
        result.addProperty("screen_open", false);
        return result;
    }

    /**
     * Standard {@code getGuiState} result. {@code slots} is null for
     * non-container screens (the field is omitted entirely); coordinates are
     * GUI-scaled, the same basis as mouseMove's {@code gui_scaled} return.
     */
    public static JsonObject guiStateResult(boolean inGame, String screenClass,
                                            WidgetCollector widgets, List<SlotFact> slots) {
        JsonObject result = new JsonObject();
        result.addProperty("screen_open", true);
        result.addProperty("in_game", inGame);
        result.addProperty("screen_class", screenClass);
        result.add("widgets", widgetsArray(widgets.nodes()));
        if (slots != null) {
            result.add("slots", slotsArray(slots));
        }
        result.addProperty("truncated", widgets.truncated());
        return result;
    }

    /**
     * Fallback result: widget traversal threw, so we degrade to bare rects
     * (whatever was collected before the failure) plus the failure note - a
     * partial answer beats a {@code -32603}.
     */
    public static JsonObject fallbackResult(boolean inGame, String screenClass,
                                            WidgetCollector partial, String note) {
        JsonObject result = new JsonObject();
        result.addProperty("screen_open", true);
        result.addProperty("in_game", inGame);
        result.addProperty("screen_class", screenClass);
        result.addProperty("fallback", true);
        JsonArray rects = new JsonArray();
        for (WidgetNode node : partial.nodes()) {
            rects.add(node.toRect());
        }
        result.add("rects", rects);
        result.addProperty("note", note);
        return result;
    }
}
