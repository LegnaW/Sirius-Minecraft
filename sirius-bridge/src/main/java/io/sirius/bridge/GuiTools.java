package io.sirius.bridge;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.events.ContainerEventHandler;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.inventory.CraftingContainer;
import net.minecraft.world.inventory.ResultSlot;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;

import java.util.ArrayList;
import java.util.List;

/**
 * The M2-C GUI state tool: {@code getGuiState()} - a structural snapshot of
 * the currently open {@link Screen} (widget tree + container slots) so the
 * brain can reason about "what am I looking at" without OCR-ing screenshots.
 *
 * <p><b>Coordinate basis:</b> every x/y in the response is GUI-scaled - the
 * same basis as the {@code gui_scaled} object {@code input.mouseMove}
 * RETURNS (widget {@code getX()/getY()} and {@code getGuiLeft()+slot.x} are
 * already in that space), NOT the window pixels mouseMove TAKES. To click a
 * slot at gui (gx, gy), convert with the scale derivable from any mouseMove
 * response (delivered px / returned gui_scaled of one probe move), then
 * mouseMove + click.
 *
 * <p><b>Threading:</b> the handler runs on a WebSocket thread; all game
 * reads AND the (fast) JSON assembly happen in one task on the client main
 * thread via the established latch pattern (10 s timeout, iconified-window
 * protection; shared {@link PerceptionTools#callOnMainThread}).
 *
 * <p><b>Never throws to the dispatcher:</b> a broken modded screen degrades
 * to the fallback tier ({@code fallback:true} + rects collected so far +
 * note) instead of a {@code -32603}; per-slot item access failures degrade
 * that one slot to {@code item:null} + note.
 */
final class GuiTools {

    /** Defensive recursion limit for the children() tree (vanilla depth ~3). */
    private static final int MAX_WIDGET_DEPTH = 12;

    private GuiTools() {
    }

    /** Registers the tool; called once from the BridgeServer constructor. */
    static void registerAll(ToolRegistry tools) {
        tools.register("getGuiState", GuiTools::getGuiState);
    }

    // ------------------------------------------------------------------ getGuiState

    private static JsonObject getGuiState(ToolContext ctx, JsonObject params) throws Exception {
        try {
            GuiContracts.guiStateParams(params); // frozen schema: empty params
        } catch (ToolContracts.InvalidParams e) {
            return Json.errorResponse(ctx.id(), Json.INVALID_PARAMS, e.getMessage(), null);
        }

        JsonObject result = PerceptionTools.callOnMainThread(ctx, () -> {
            Minecraft mc = Minecraft.getInstance();
            Screen screen = mc.screen;
            if (screen == null) {
                return GuiContracts.noScreenResult(); // in-game without a GUI, or title flow
            }
            // A screen with no world (title/options screens) is still a screen;
            // in_game gives the brain the context getStats convention provides.
            boolean inGame = mc.level != null;
            String screenClass = screen.getClass().getSimpleName();

            GuiContracts.WidgetCollector widgets = new GuiContracts.WidgetCollector();
            try {
                collectWidgets(screen, widgets, 0);
            } catch (Throwable t) {
                SiriusBridge.LOGGER.warn("sirius-bridge: getGuiState traversal failed on {}",
                        screenClass, t);
                return GuiContracts.fallbackResult(inGame, screenClass, widgets,
                        "widget traversal failed: " + t);
            }

            List<GuiContracts.SlotFact> slots = null;
            if (screen instanceof AbstractContainerScreen<?> containerScreen) {
                slots = collectSlots(mc, containerScreen);
            }
            return GuiContracts.guiStateResult(inGame, screenClass, widgets, slots);
        });
        return Json.okResponse(ctx.id(), result);
    }

    // ------------------------------------------------------------------ widget traversal

    /**
     * Depth-first walk of the {@code children()} tree: every
     * {@link AbstractWidget} becomes a node (class simple name as type, plus
     * text for {@link EditBox}); containers ({@link ContainerEventHandler})
     * are recursed into - a widget can be both (e.g. scroll lists). Depth- and
     * cap-bounded; any throwable propagates to the fallback tier.
     */
    private static void collectWidgets(ContainerEventHandler parent,
                                       GuiContracts.WidgetCollector out, int depth) {
        if (out.full() || depth >= MAX_WIDGET_DEPTH) {
            return;
        }
        for (GuiEventListener child : parent.children()) {
            if (out.full()) {
                return;
            }
            if (child instanceof AbstractWidget widget) {
                String message = widget.getMessage() != null ? widget.getMessage().getString() : null;
                String text = child instanceof EditBox editBox ? editBox.getValue() : null;
                if (!out.add(new GuiContracts.WidgetNode(
                        widget.getClass().getSimpleName(),
                        widget.getX(), widget.getY(),
                        widget.getWidth(), widget.getHeight(),
                        widget.visible, widget.active,
                        message, text))) {
                    return; // cap hit - stop the whole enumeration
                }
            }
            if (child instanceof ContainerEventHandler container) {
                collectWidgets(container, out, depth + 1);
            }
        }
    }

    // ------------------------------------------------------------------ slots

    /**
     * Container path: every {@code menu.slots} entry as
     * {@code {index, x, y, role, item, count}} with screen position
     * {@code getGuiLeft()+slot.x / getGuiTop()+slot.y} (the same cells
     * {@code isHovering} hit-tests - slots are 16x16 and NOT part of the
     * children() widget tree). Each slot's item access is isolated in
     * try/catch: a broken mod slot degrades to {@code item:null} + note
     * instead of killing the response.
     */
    private static List<GuiContracts.SlotFact> collectSlots(Minecraft mc,
                                                            AbstractContainerScreen<?> screen) {
        Inventory playerInventory = mc.player != null ? mc.player.getInventory() : null;
        int left = screen.getGuiLeft();
        int top = screen.getGuiTop();
        List<GuiContracts.SlotFact> slots = new ArrayList<>();
        for (Slot slot : screen.getMenu().slots) {
            String role = roleOf(slot, playerInventory);
            String item = null;
            int count = 0;
            String note = null;
            try {
                ItemStack stack = slot.getItem();
                if (!stack.isEmpty()) {
                    count = stack.getCount();
                    var key = BuiltInRegistries.ITEM.getKey(stack.getItem());
                    item = key != null ? key.toString() : null;
                    if (item == null) {
                        note = "item is not registered in the ITEM registry";
                    }
                }
            } catch (Throwable t) {
                SiriusBridge.LOGGER.debug("sirius-bridge: getGuiState slot {} item access failed: {}",
                        slot.index, t.toString());
                item = null;
                count = 0;
                note = "item access failed: " + t;
            }
            slots.add(new GuiContracts.SlotFact(
                    slot.index, left + slot.x, top + slot.y, role, item, count, note));
        }
        return slots;
    }

    /**
     * Generic role detection (Numen GuiOps method): crafting container ->
     * "crafting"; ResultSlot -> "result"; the player's own inventory ->
     * "hotbar" (container index 0-8) / "player" (>= 9, which also covers
     * armor 36-39 and offhand 40); everything else -> "container".
     */
    private static String roleOf(Slot slot, Inventory playerInventory) {
        if (slot.container instanceof CraftingContainer) {
            return GuiContracts.ROLE_CRAFTING;
        }
        if (slot instanceof ResultSlot) {
            return GuiContracts.ROLE_RESULT;
        }
        if (playerInventory != null && slot.container == playerInventory) {
            return slot.index < 9 ? GuiContracts.ROLE_HOTBAR : GuiContracts.ROLE_PLAYER;
        }
        return GuiContracts.ROLE_CONTAINER;
    }
}
