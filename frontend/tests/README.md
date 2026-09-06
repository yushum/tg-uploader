# 推流工作台回归测试

测试使用真实 Chromium 渲染前端、模拟 `/api/*` 响应，不会连接 Telegram 或启动真实推流。

```sh
npm install --prefix /tmp/replay-ui-test playwright
/tmp/replay-ui-test/node_modules/.bin/playwright install chromium
PLAYWRIGHT_MODULE=/tmp/replay-ui-test/node_modules/playwright/index.mjs \
  node --test frontend/tests/live-studio.test.mjs
```

覆盖：日期校验、批量选择、失效录像、半选状态、顺序调整、草稿恢复、开播请求顺序、停止失败、后台错误、搜索失败与空结果、手机页签、不同宽度、Escape 关闭与焦点恢复。

截图输出到 `/tmp/replay-studio-*.png`，用于检查浅色、深色、桌面和手机布局。

真实 Telegram 权限、FFmpeg、音视频连续播放需要部署后单独验证。
