"""M4.1 T6 存活压测探针（分段场景制）：反射层独立存活——上层零任务零指令。

设计（spec docs_agent/session/2026-08-21-M4.1.md + 主管分段制修正）：
- AgentLoop 挂真实调度器随 run() 常驻，任务队列恒空（零 VLM token，纯反射层）
- 每场景独立 2 分钟：①怪群围攻 ②深水 ③火 ④卡位（注入移动意图+W，脱困
  反射的触发前提）⑤综合混灾；场景间重置（清怪、#stop、补状态）
- 饥饿变量消除：每场景前 /effect give @s saturation 999 255 true（持续饱食）
  + instant_health 回血；作弊不可用时先经 GUI 开 LAN+作弊（单机发布授权）
- 采样：每 10s 记 health/air/位置/yaw/反射简报计数；死亡即记录归因并
  GUI 重生（不自动重生是 DeathReflex 对玩家的承诺，探针扮演玩家点按钮）
- 收尾输出：场景结果表 + 死亡归因表

边界声明（诚实口径）：反射层职责 = 突发危险的即时自救（溺水/着火/围攻/
卡位）；慢性消耗（饥饿/长期低饱食）不在职责内（本压测已用饱和效果消除）。
"""
import asyncio
import time

from sirius_brain.bridge import BridgeClient, BridgeError
from sirius_brain.agent.config import AgentConfig
from sirius_brain.agent.loop import AgentLoop
from sirius_brain.agent.reflexes import ReflexScheduler
from sirius_brain.agent.vlm import QwenVLM

SCENARIO_SECONDS = 120.0   # 每场景时长
SAMPLE_INTERVAL = 10.0     # 采样间隔
GLFW_ESC = 256
GLFW_W = 87

REFLEX_KEYS = ("换气", "脱困", "着火撤离", "逃离", "低血", "死亡上报")


# ------------------------------------------------------------------ GUI 自动化小件


_PS_RESTORE = r"""
$sig = @'
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
'@
Add-Type -MemberDefinition $sig -Name Win -Namespace U
$p = Get-Process java -ErrorAction SilentlyContinue |
     Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if ($p) {
    [U.Win]::ShowWindow($p.MainWindowHandle, 9) | Out-Null
    [U.Win]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
    Write-Output "restored"
} else {
    Write-Output "no-window"
}
"""


def restore_window() -> None:
    """恢复被最小化的游戏窗口（M4 已知运维坑：最小化饿死 Minecraft.execute）。"""
    try:
        import subprocess
        import tempfile
        from pathlib import Path
        with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(_PS_RESTORE)
            script = fh.name
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", script], capture_output=True, timeout=15)
        Path(script).unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 —— 恢复失败不致命（click 有 latch 超时兜底）
        print(f"   [!] 窗口恢复失败（忽略）：{exc}")


async def gui_scale(client: BridgeClient) -> tuple[float, float]:
    """gui 坐标 → 窗口像素的比例（mouseMove 回包同时给两套坐标）。"""
    result = await client.call("input.mouseMove", {"x": 96, "y": 96})
    gui = result["gui_scaled"]
    return (result["x"] / gui["x"], result["y"] / gui["y"])


async def click_widget(client: BridgeClient, *needles: str) -> bool:
    """找 message 含任一关键词的按钮并点击（gui→窗口像素换算）。"""
    gui = await client.call("getGuiState")
    if not gui.get("screen_open"):
        return False
    sx, sy = await gui_scale(client)
    for node in gui.get("widgets") or []:
        message = str(node.get("message") or "")
        if any(n in message for n in needles):
            cx = (node["x"] + node["width"] / 2) * sx
            cy = (node["y"] + node["height"] / 2) * sy
            await client.call("input.mouseMove", {"x": cx, "y": cy})
            await asyncio.sleep(0.15)
            await client.call("input.click", {"button": 0})
            return True
    return False


# ------------------------------------------------------------------ 主探针


class SurvivalProbe:
    def __init__(self, client: BridgeClient, agent: AgentLoop):
        self.client = client
        self.agent = agent
        self.scheduler = agent.scheduler
        self.cheats = False
        self.report: list[dict] = []

    # -------------------------------------------------- 基础动作

    async def cmd(self, text: str) -> None:
        try:
            await self.client.command(text, settle=0.3)
        except BridgeError as exc:
            if exc.code != -32002:
                raise
            # GUI 被占（大概率死亡屏）：重生后重试一次
            if await self.respawn_if_dead():
                await self.client.command(text, settle=0.3)
            else:
                raise

    async def pos(self) -> tuple[float, float, float]:
        stats = await self.client.call("getStats")
        p = stats["position"]
        return p["x"], p["y"], p["z"]

    async def respawn_if_dead(self) -> bool:
        """死亡屏上点「重生」（探针扮演玩家；DeathReflex 本体绝不自动重生）。"""
        gui = await self.client.call("getGuiState")
        if not (gui.get("screen_open") and "Death" in str(gui.get("screen_class"))):
            return False
        restore_window()
        for _ in range(6):
            if await click_widget(self.client, "重生", "Respawn"):
                await asyncio.sleep(1.0)
                return True
            await asyncio.sleep(0.5)
        return False

    async def ensure_cheats(self) -> bool:
        """作弊可用性探测 → 不可用则 GUI 开 LAN+作弊。"""
        await self.cmd("/effect give @s saturation 999 255 true")
        await asyncio.sleep(0.6)
        stats = await self.client.call("getStats")
        if any("saturation" in str(e.get("id")) for e in stats.get("effects") or []):
            self.cheats = True
            print("   [0] 作弊可用（/effect 生效）")
            return True
        print("   [0] 作弊不可用——尝试 GUI「对局域网开放 + 作弊:开启」")
        restore_window()
        await self.client.call("input.key", {"code": GLFW_ESC})
        await asyncio.sleep(0.6)
        if not await click_widget(self.client, "局域网", "LAN"):
            print("   [!] 没找到「对局域网开放」按钮——请人工开作弊后重跑")
            await self.client.call("input.key", {"code": GLFW_ESC})
            return False
        await asyncio.sleep(0.8)
        # 作弊开关：消息里写着当前状态（关闭/开启），点一下翻到开启
        gui = await self.client.call("getGuiState")
        for node in gui.get("widgets") or []:
            message = str(node.get("message") or "")
            if "作弊" in message and "关闭" in message:
                sx, sy = await gui_scale(self.client)
                await self.client.call("input.mouseMove", {
                    "x": (node["x"] + node["width"] / 2) * sx,
                    "y": (node["y"] + node["height"] / 2) * sy})
                await asyncio.sleep(0.15)
                await self.client.call("input.click", {"button": 0})
                await asyncio.sleep(0.4)
                break
        started = await click_widget(self.client, "开启对局域网世界", "Start")
        print(f"   [0] LAN 启动按钮点击={'成功' if started else '未找到'}")
        await asyncio.sleep(2.5)
        # 残留菜单清屏（任何屏都挡 T 键）：ESC 一下再试
        gui = await self.client.call("getGuiState")
        if gui.get("screen_open"):
            print(f"   [0] 残留屏 {gui.get('screen_class')}——ESC 清除")
            await self.client.call("input.key", {"code": GLFW_ESC})
            await asyncio.sleep(0.8)
        await self.cmd("/effect give @s saturation 999 255 true")
        await asyncio.sleep(0.6)
        stats = await self.client.call("getStats")
        self.cheats = any("saturation" in str(e.get("id"))
                          for e in stats.get("effects") or [])
        print(f"   [0] LAN+作弊流程后作弊可用={self.cheats}")
        return self.cheats

    async def refresh_body(self) -> None:
        """场景间补状态：回血 + 持续饱食（消除饥饿变量——主管分段制要求）。"""
        await self.cmd("/effect give @s instant_health 1 255 true")
        await self.cmd("/effect give @s saturation 999 255 true")
        await self.cmd("/effect clear @s minecraft:fire_resistance")

    async def clear_mobs(self) -> None:
        for mob in ("zombie", "drowned", "skeleton", "creeper"):
            await self.cmd(f"/kill @e[type=minecraft:{mob},distance=..48]")

    # -------------------------------------------------- 场景

    async def run_scenario(self, name: str, setup, *, inject_movement: bool = False,
                           periodic=None) -> None:
        print(f"\n== 场景 {name}（{SCENARIO_SECONDS:.0f}s）==")
        await self.cmd("#stop")
        await self.clear_mobs()
        await self.refresh_body()
        await setup()
        entry: dict = {"scenario": name, "samples": [], "deaths": [],
                       "reflexes": {}, "result": "PASS"}
        log_before = len(self.scheduler.behavior_log)
        started = time.perf_counter()
        last_periodic = 0.0
        try:
            while time.perf_counter() - started < SCENARIO_SECONDS:
                if inject_movement:
                    # 卡位场景：探针注入"上层在走"的信号 + W 键（脱困反射的
                    # 触发前提是 movement_active——零任务模式下没有原语在跑）
                    self.scheduler.note_movement(True)
                    await self.client.call("input.key",
                                           {"code": GLFW_W, "duration_ms": 400})
                now = time.perf_counter() - started
                if periodic is not None and now - last_periodic >= 15:
                    last_periodic = now
                    await periodic()
                stats = await self.client.call("getStats")
                entry["samples"].append({
                    "t": round(now, 1), "health": stats.get("health"),
                    "air": stats.get("air"), "yaw": round(stats.get("yaw") or 0),
                    "pos": (round(stats["position"]["x"], 1),
                            round(stats["position"]["y"], 1),
                            round(stats["position"]["z"], 1))})
                if stats.get("alive") is False:
                    tail = list(self.scheduler.behavior_log)[-6:]
                    entry["deaths"].append({"t": round(now, 1),
                                            "hp_curve": [s["health"] for s in entry["samples"][-4:]],
                                            "behavior_tail": tail})
                    entry["result"] = "DEATH"
                    print(f"   [{now:5.1f}s] 死亡！health 曲线 "
                          f"{[s['health'] for s in entry['samples'][-4:]]}")
                    for line in tail:
                        print(f"          {line}")
                    if await self.respawn_if_dead():
                        print("   已重生（GUI 点击）——场景中止，归因留档")
                    break
                await asyncio.sleep(SAMPLE_INTERVAL)
        finally:
            if inject_movement:
                self.scheduler.note_movement(False)
            await self.cmd("#stop")
        new_lines = list(self.scheduler.behavior_log)[log_before:]
        for key in REFLEX_KEYS:
            hits = [l for l in new_lines if key in l]
            if hits:
                entry["reflexes"][key] = len(hits)
        hps = [s["health"] for s in entry["samples"]]
        print(f"   结果={entry['result']} 反射触发={entry['reflexes'] or '无'} "
              f"health {min(hps):.1f}~{max(hps):.1f}")
        self.report.append(entry)

    # -- 各场景 setup/periodic

    async def setup_monsters(self) -> None:
        x, y, z = await self.pos()
        for dx, dz in ((3, 0), (-3, 0), (0, 3)):
            await self.cmd(f"/summon minecraft:zombie {x + dx:.0f} {y:.0f} {z + dz:.0f}")
        await self.cmd(f"/summon minecraft:drowned {x:.0f} {y:.0f} {z + 4:.0f}")
        await self.cmd(f"/summon minecraft:drowned {x:.0f} {y:.0f} {z - 4:.0f}")

    async def periodic_monsters(self) -> None:
        """活怪少于 2 只时补 2 只（围攻压力持续）。"""
        result = await self.client.call("world.query",
                                        {"type": "entities", "range": 12})
        mobs = [e for e in result.get("entities") or []
                if e.get("category") == "monster"]
        if len(mobs) < 2:
            x, y, z = await self.pos()
            await self.cmd(f"/summon minecraft:zombie {x + 3:.0f} {y:.0f} {z:.0f}")
            await self.cmd(f"/summon minecraft:zombie {x - 3:.0f} {y:.0f} {z:.0f}")

    async def setup_water(self) -> None:
        result = await self.client.call(
            "world.query", {"type": "blocks", "range": 48,
                            "filter": ["minecraft:water"]})
        blocks = result.get("blocks") or []
        if not blocks:
            print("   [!] 48 格内没水——深水场景改为造 3x3x3 水池")
            x, y, z = await self.pos()
            await self.cmd(f"/fill {x+2:.0f} {y-2:.0f} {z+2:.0f} "
                           f"{x+4:.0f} {y:.0f} {z+4:.0f} minecraft:water")
            await asyncio.sleep(0.5)
            await self.cmd(f"/tp @s {x+3:.1f} {y-1:.1f} {z+3:.1f}")
            return
        deepest = min(blocks, key=lambda b: b["y"])
        await self.cmd(f"/tp @s {deepest['x'] + 0.5:.1f} {deepest['y']:.1f} "
                       f"{deepest['z'] + 0.5:.1f}")

    async def periodic_water(self) -> None:
        stats = await self.client.call("getStats")
        if stats.get("air", 300) >= 300:   # 浮出水面后把它再按回去（反复施压）
            await self.setup_water()

    async def setup_fire(self) -> None:
        x, y, z = await self.pos()
        await self.cmd(f"/execute at @s run setblock ~ ~ ~ minecraft:fire")

    async def periodic_fire(self) -> None:
        await self.cmd("/execute at @s run setblock ~ ~ ~ minecraft:fire")

    async def setup_stuck(self) -> None:
        """1 宽 2 深坑 + 四壁围死（卡位；W 意图由 run_scenario 注入）。"""
        x, y, z = await self.pos()
        bx, by, bz = int(x), int(y) - 1, int(z)
        await self.cmd(f"/fill {bx-1} {by} {bz-1} {bx+1} {by} {bz+1} minecraft:stone")
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            await self.cmd(f"/fill {bx+dx} {by+1} {bz+dz} {bx+dx} {by+2} {bz+dz} "
                           "minecraft:stone")
        await self.cmd(f"/tp @s {bx + 0.5:.1f} {by:.1f} {bz + 0.5:.1f}")

    async def setup_mixed(self) -> None:
        await self.setup_monsters()
        await self.setup_fire()

    async def periodic_mixed(self) -> None:
        await self.periodic_fire()
        await self.periodic_monsters()

    # -------------------------------------------------- 汇总

    def summarize(self) -> None:
        print("\n================ T6 存活压测汇总 ================")
        deaths_total = 0
        for entry in self.report:
            deaths_total += len(entry["deaths"])
            print(f"[{entry['scenario']:<12}] {entry['result']:<6} "
                  f"反射={entry['reflexes'] or '无'}")
        print(f"\n死亡归因表（共 {deaths_total} 次）：")
        for entry in self.report:
            for d in entry["deaths"]:
                print(f"  - 场景[{entry['scenario']}] t={d['t']}s "
                      f"hp曲线={d['hp_curve']}")
                for line in d["behavior_tail"]:
                    print(f"      {line}")
        print("\n采样明细（health/air 轨迹）：")
        for entry in self.report:
            track = " ".join(f"{s['health']:.0f}/{s['air']}" for s in entry["samples"])
            print(f"  [{entry['scenario']:<12}] {track}")


async def main() -> None:
    cfg = AgentConfig.from_local_md("../local.md")
    client = BridgeClient(cfg.bridge)
    agent = AgentLoop(client, QwenVLM(cfg.vlm), cfg)
    agent.install()
    await client.connect()
    # 只挂反射调度器（反射层独立存活的验收口径）：不跑任务循环——
    # chat 事件照收但零任务消费，Baritone 回执等聊天行绝不可能触发 VLM
    await client.subscribe_events(["chat", *ReflexScheduler.DANGER_EVENTS])
    runner = asyncio.create_task(agent.scheduler.run())
    probe = SurvivalProbe(client, agent)
    try:
        import sys
        wanted = [a for a in sys.argv[1:]]    # 场景名过滤（空 = 全部）
        scenarios = [
            ("怪群围攻", probe.setup_monsters, False, probe.periodic_monsters),
            ("深水", probe.setup_water, False, probe.periodic_water),
            ("火", probe.setup_fire, False, probe.periodic_fire),
            ("卡位", probe.setup_stuck, True, None),
            ("综合混灾", probe.setup_mixed, False, probe.periodic_mixed),
        ]
        restore_window()
        await probe.respawn_if_dead()   # 上轮测试可能停在死亡屏
        if not await probe.ensure_cheats():
            print("!! 无作弊权限，压测无法布置场景——中止（见上方提示）")
            return
        for name, setup, inject, periodic in scenarios:
            if wanted and not any(w in name for w in wanted):
                continue
            print(f"########## 场景开始：{name} ##########", flush=True)
            await probe.run_scenario(name, setup, inject_movement=inject,
                                     periodic=periodic)
        probe.summarize()
    finally:
        try:
            await probe.cmd("#stop")
            await probe.clear_mobs()
            await probe.cmd("/effect clear @s")
        except Exception:  # noqa: BLE001
            pass
        agent.scheduler.stop()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        await agent.shutdown()
        await client.close()


async def _wait(predicate, timeout: float = 15.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.2)
    return False


if __name__ == "__main__":
    asyncio.run(main())
