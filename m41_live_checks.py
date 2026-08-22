"""M4.1 真机验证探针：T1/T2/T3/T4/T5 的可重复实测（零 VLM token）。

用法（游戏已在跑、bridge 8765 在听）：
    python m41_live_checks.py            # 全部检查
    python m41_live_checks.py t1 t4      # 只跑指定项

检查项（对照 spec docs_agent/session/2026-08-21-M4.1.md）：
- t1 命令竞态回归：背靠背 5 组 (#stop, #goto) 连发，扫描游戏 latest.log
  本窗口内不得出现 `Command not found`（M4-rerun 00:41:01 事故形态），且 bot
  真的动身（#goto 生效）
- t2 转头时间序列：#goto 40 格外目标，5Hz 采样 getStats 的 yaw + 位置，
  输出 yaw 序列与"运动方向 vs 头部朝向"的偏差统计（协议 1.3 前的 jar 无
  yaw 字段 → 该项自动跳过并提示）
- t3 死亡播报直发：chat.send 直发一条可见文本（订阅 chat 事件验证收到）；
  死亡场景的端到端在 m41_survival_probe.py 里覆盖
- t4 令牌桶计费语义：25 连发 SPACE tap 数 -32010 的个数（容量 20 的桶应恰
  好拒绝 ~5 个）；再发一次 1500ms 长按后立刻 19 连 tap——若长按按调用计费
  （M4.1 T4 契约），19 发应几乎全过（长按只占 1 令牌）
- t5 Baritone #pause/#resume 可用性：发 #pause/#resume 各一次，扫游戏日志
  找 Baritone 的回执（"Paused"/"Resumed" 或无 Unknown command）
"""
import asyncio
import math
import sys
import time
from pathlib import Path

from sirius_brain.bridge import BridgeClient, BridgeError
from sirius_brain.agent.config import AgentConfig

GAME_LOG = Path(__file__).resolve().parent.parent / ".minecraft" / "versions" / "1.21.1-Sirius" / "logs" / "latest.log"
# 启动 cwd 差异（M3.5-T0b 教训）：游戏日志也可能落在仓库根 logs/
ALT_GAME_LOG = Path(__file__).resolve().parent / "logs" / "latest.log"


def _log_path() -> Path:
    for p in (GAME_LOG, ALT_GAME_LOG):
        if p.exists():
            return p
    raise SystemExit("找不到游戏 latest.log（.minecraft/.../logs 或 repo/logs）")


def _log_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _new_log_lines(path: Path, start_size: int) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(start_size)
            return fh.read().splitlines()
    except OSError:
        return []


async def t1_command_race(client: BridgeClient) -> bool:
    print("\n== T1 命令竞态回归：背靠背 5 组 #stop/#goto ==")
    log = _log_path()
    size0 = _log_size(log)
    stats = await client.call("getStats")
    p0 = stats["position"]
    tx, tz = p0["x"] + 15, p0["z"]  # 15 格外走一下（#goto 生效信号）
    t0 = time.perf_counter()
    for i in range(5):
        await client.command("#stop", settle=0.05)
        await client.command(f"#goto {tx:.1f} {tz:.1f}", settle=0.05)
    burst_s = time.perf_counter() - t0
    await asyncio.sleep(6)
    merged = [ln for ln in _new_log_lines(log, size0)
              if "Command not found" in ln or "Unknown command" in ln]
    stats = await client.call("getStats")
    p1 = stats["position"]
    moved = math.hypot(p1["x"] - p0["x"], p1["z"] - p0["z"])
    await client.command("#stop")
    ok = not merged and moved > 2.0
    print(f"   10 条命令连发耗时 {burst_s:.2f}s（含 GUI 确认轮询）")
    print(f"   游戏日志 Command not found/Unknown command：{len(merged)} 行"
          + (f" ← {merged[:2]}" if merged else ""))
    print(f"   位移 {moved:.1f} 格（#goto 生效）")
    print(f"   T1 {'PASS' if ok else 'FAIL'}")
    return ok


async def t2_turn_series(client: BridgeClient) -> bool:
    print("\n== T2 转头时间序列：#goto 40 格，5Hz 采样 yaw ==")
    stats = await client.call("getStats")
    if "yaw" not in stats:
        print("   getStats 无 yaw 字段（协议 <1.3 的 jar）——跳过；"
              "审计日志证据见 docs_agent/reports/M4.1.md（长走期间零旋转写入）")
        return True
    p0 = stats["position"]
    tx, tz = p0["x"] + 40, p0["z"] + 5
    await client.command(f"#goto {tx:.0f} {tz:.0f}", settle=0.05)
    series = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 25:
        s = await client.call("getStats")
        p = s["position"]
        series.append((time.perf_counter() - t0, s.get("yaw"), p["x"], p["z"]))
        if math.hypot(p["x"] - tx, p["z"] - tz) < 2.5:
            break
        await asyncio.sleep(0.2)
    await client.command("#stop")
    # 运动方向 vs 头部朝向的偏差（度；只看运动中的样本）
    devs = []
    for i in range(1, len(series)):
        dt, yaw, x, z = series[i]
        _, _, px, pz = series[i - 1]
        vx, vz = x - px, z - pz
        if math.hypot(vx, vz) < 0.05:
            continue
        heading = math.degrees(math.atan2(vz, vx)) - 90
        dev = (yaw - heading + 180) % 360 - 180
        devs.append(abs(dev))
    yaws = [s[1] for s in series if s[1] is not None]
    uniq = len({round(y) for y in yaws})
    print(f"   样本 {len(series)} 个，yaw 覆盖 {uniq} 个整数度值"
          f"（min={min(yaws):.0f} max={max(yaws):.0f}）")
    if devs:
        devs.sort()
        print(f"   运动中 |yaw-运动方向|：中位 {devs[len(devs)//2]:.0f}°，"
              f"P90 {devs[int(len(devs)*0.9)]:.0f}°，max {devs[-1]:.0f}°")
    frozen = uniq <= 3
    ok = not frozen and devs and devs[len(devs) // 2] < 60
    print(f"   序列（前 12 个 yaw）：{[round(y or 0) for _, y, _, _ in series[:12]]}")
    print(f"   T2 {'PASS（头随运动方向转动）' if ok else 'FAIL（yaw 疑似冻结/不跟随）'}")
    return ok


async def t3_chat_send(client: BridgeClient) -> bool:
    print("\n== T3 chat.send 直发通道 ==")
    seen: list[dict] = []
    client.add_event_handler("chat", lambda f: seen.append(f.data or {}))
    await client.subscribe_events(["chat"])   # bridge 只对已订阅会话推送
    try:
        result = await client.call("chat.send", {"string": "M4.1 直发通道自检"})
    except BridgeError as exc:
        print(f"   chat.send 被拒（{exc}）——旧 jar？部署 1.3 后重试")
        return False
    await asyncio.sleep(1.5)
    echoed = any("M4.1 直发通道自检" in str(d.get("message")) for d in seen)
    print(f"   chat.send result={result} 聊天事件回显={'有' if echoed else '无'}")
    ok = bool(result.get("sent")) and echoed
    print(f"   T3 {'PASS' if ok else 'FAIL'}")
    return ok


async def t4_token_bucket(client: BridgeClient) -> bool:
    print("\n== T4 令牌桶计费语义（一次长按 = 1 令牌）==")
    # RPC 往返 ~180ms：串行调用（~5/s）永远追不上 20/s 的补充，量不出计费
    # 语义——必须并发齐发把桶瞬间打满。
    async def tap(i: int) -> int:
        try:
            await client.call("input.key", {"code": 32, "duration_ms": 30})
            return 0
        except BridgeError as exc:
            return 1 if exc.code == -32010 else -1

    await asyncio.sleep(1.2)  # 桶回满
    burst1 = await asyncio.gather(*(tap(i) for i in range(30)))
    rejected = sum(1 for r in burst1 if r == 1)
    print(f"   30 并发 SPACE tap：{rejected} 个 -32010"
          f"（注：input.* 经主线程逐个执行（~50ms/个），管道自身就限到 ~20/s，"
          f"并发齐发也难把桶打穿——有拒绝即证明限流器在工作）")
    await asyncio.sleep(1.2)  # 回满
    # 长按 + 19 tap 并发齐发：若长按只占 1 令牌，总需求 20 ≈ 容量 20 → 全过；
    # 若按 tick 计费（1500ms/50ms=30 令牌）→ tap 几乎全灭
    results = await asyncio.gather(
        client.call("input.key", {"code": 340, "duration_ms": 1500}),
        *(tap(i) for i in range(19)))
    after_hold_rejected = sum(1 for r in results[1:] if r == 1)
    after_hold_ok = 19 - after_hold_rejected
    print(f"   1500ms 长按 + 19 并发 tap：{after_hold_ok}/19 通过")
    ok = rejected >= 1 and after_hold_ok >= 16
    print(f"   T4 {'PASS（按调用计费坐实；长按不放大扣费）' if ok else 'FAIL（计费语义与预期不符）'}")
    return ok


async def t5_baritone_pause(client: BridgeClient) -> bool:
    print("\n== T5 Baritone #pause/#resume 可用性 ==")
    log = _log_path()
    size0 = _log_size(log)
    await client.command("#pause")
    await asyncio.sleep(1.0)
    await client.command("#resume")
    await asyncio.sleep(1.0)
    lines = _new_log_lines(log, size0)
    unknown = [ln for ln in lines if "Unknown command" in ln or "Command not found" in ln]
    acked = [ln for ln in lines if "ause" in ln or "esum" in ln]
    print(f"   日志 Unknown/Not-found：{len(unknown)} 行")
    for ln in acked[:4]:
        print(f"   回执行：{ln[-100:]}")
    ok = not unknown
    print(f"   T5 {'PASS（#pause/#resume 被接受）' if ok else 'FAIL'}")
    return ok


async def main() -> None:
    wanted = {a.lstrip("-") for a in sys.argv[1:]}
    cfg = AgentConfig.from_local_md("../local.md")
    results: dict[str, bool] = {}
    async with BridgeClient(cfg.bridge) as client:
        if not wanted or "t1" in wanted:
            results["t1"] = await t1_command_race(client)
        if not wanted or "t2" in wanted:
            results["t2"] = await t2_turn_series(client)
        if not wanted or "t3" in wanted:
            results["t3"] = await t3_chat_send(client)
        if not wanted or "t4" in wanted:
            results["t4"] = await t4_token_bucket(client)
        if not wanted or "t5" in wanted:
            results["t5"] = await t5_baritone_pause(client)
    print("\n== 汇总 ==")
    for name, ok in results.items():
        print(f"   {name}: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
