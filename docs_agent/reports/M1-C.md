# M1-C 工作报告

- 任务：三个感知工具 screenshot / getStats / world.query 真实实现
- 日期：2026-08-18
- 状态：完成
- 验收：smokeTest 45/45（挂入 build）；M1-E 真机四步 + in-game 三连全过

## 交付物

- `ImageOps.java`（纯逻辑，不依赖 MC）：bbox 裁剪（钳制/不相交抛错）、JPEG 编码（quality）、2MB base64 预算降级阶梯（q-10 降至 40 → 缩长边 1024 重试）、base64
- `ToolContracts.java`（纯逻辑）：三工具参数校验（照冻结 schema，违规 -32602）、响应组装、scanBlocks/filterEntities（BlockProbe 可注入）
- `PerceptionTools.java`（MC 薄壳）：渲染线程抓 framebuffer、主线程读 player/level；latch 同步 10s 超时
- `SmokeMain.java`：进程内冒烟 45 检查（gradlew smokeTest）
- BridgeServer 构造器 +1 行 `PerceptionTools.registerAll(tools)`

## 关键决策与理由

- 纯逻辑/薄壳分离：ImageOps/ToolContracts 不 import 任何 Minecraft 类 → 不起游戏可测（45 项冒烟的基础）
- 截图在渲染线程只做像素下载（~10-30ms），裁剪/编码/base64 全放 WS 线程——渲染线程零阻塞

## 1.21.1 API 笔记（M2 必读）

1. **GUI 画进主渲染目标**：runTick 每帧 bindWrite(mainRenderTarget) 后才 gameRenderer.render，GUI pass 同一 target（GameRenderer.render ~L1034）→ `Screenshot.takeScreenshot(mc.getMainRenderTarget())` 天然含 HUD/GUI/标题屏，无需 glReadPixels
2. **Minecraft.execute 任务在帧首 runAllTasks 执行（渲染线程）；窗口最小化时任务饿死** → latch 必须带超时（返回 -32603 而非挂死）
3. `NativeImage.getPixelRGBA` 返回**小端 ABGR**：`(v&0xFF00FF00)|((v>>16)&0xFF)|((v<<16)&0xFF0000)` 转 ARGB；DataBufferInt 直写加速
4. NativeImage 只能导 PNG → BufferedImage + ImageIO JPEG（quality via ImageWriteParam）
5. `Holder.getRegisteredName()`（默认方法）/ `EntityType.getKey(type)` 拿注册名
6. game_mode：`PlayerInfo.getGameMode().getName()`，isSpectator/isCreative 兜底

## 实现要点

- 截图响应：`{image_b64, format:"jpeg", width, height, taken_at, quality(实际), downscaled}`；4K 噪声实测降级后 430KB b64 / 1024x576
- world.query：blocks 立方扫描非空气（上限 512 + truncated；未加载区块按空气）；entities 上限 128（health 仅 LivingEntity 且 >0 附）
- 未进世界：三工具统一 `{"in_game": false}`（不报错）

## 验证方式

smokeTest 45/45；M1-E 真机（标题屏 854x480 q80 72KB）；in-game（512 截断保护实证、地形合理、实体=玩家本人）

## 交接须知

- 下一步扩展点：getGuiState（M3 前置）走同一注册模式；事件推流的 1Hz 节流管线参数见 sirius-technical.md §8.2
- 已知限制：range 64 开阔空域 ~210 万次 getBlockState（主线程数百 ms 一次性）；客户端侧生物 health 常未同步；最小化窗口截图超时
- 关联报告：M1-B（注册表）、M1-E（真机证据）
