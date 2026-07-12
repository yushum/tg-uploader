# Telegram 视频全自动监控上传工具

[English version (英文版)](README.md)

这是一个工业级、具备高可用性和极致性能的 Telegram 视频全自动无人值守上传工具。主要用于在服务器环境后台运行，通过监控本地文件夹，将新生成的视频全自动上传到 Telegram 频道中。它支持多线程极速上传、大于 4GB 文件的无损自动切割、流媒体格式实时转码以及自动清理。

## 🚀 核心特性

* **多线程极速上传 (FastTelethon)**：打破 Telegram 原生的单线程连接限速。系统将文件在内存中切分，并建立 20 个并发 MTProto 连接同时向 TG 服务器发送数据，能轻易跑满你的服务器上传带宽。
* **超大文件 (4GB+) 自动无损切割**：Telegram 限制单文件最大体积为 4GB（即使是 Premium 账号）。当检测到大于该限制的视频时，系统会自动在后台调用 `ffmpeg`，将视频无损切割为多个小分段（不会重新编码），并按顺序排队上传。
* **直播流格式秒级转码**：自动拦截 `.ts`、`.flv` 和 `.mkv` 等格式，使用极速的 Remux 技术将其转换为标准的 `.mp4`。同时会强制写入 `+faststart` 参数，确保视频传到 Telegram 后可以直接在线缓冲播放。
* **非阻塞雷达与调度塔**：抛弃死板的文件监控机制。系统会异步地高速巡视硬盘，将分段视频文件按直播场次智能分组。配合严谨的“静默期”机制，绝不争抢仍在录制中或未落盘的文件，确保视频按最完美的先后顺序上传。
* **内存节流防御 (OOM Defense) 与抗频控**：通过精准的 Semaphore 并发锁，实现大文件的滑动内存映射读取。面对 40GB 的巨型视频也只占用极其微小的内存。自动接管并处理 Telegram 的各种请求速率超限 (`FloodWaitError`)。
* **阅后即焚全托管**：在确认文件 100% 成功上传到频道并写入 SQLite 本地记录后，系统将自动将物理硬盘上的原始视频彻底粉碎删除，并递归清理生成的空文件夹，确保你的硬盘永远干净如新。

---

## ⚙️ 部署与配置

### 1. 前期准备

1. **申请 API_ID 和 API_HASH**：
   访问 [Telegram Core: API Development Tools](https://my.telegram.org/auth) 创建应用，获取你的 `api_id` 和 `api_hash`。
2. **获取频道 CHANNEL_ID**：
   在 Telegram 客户端创建一个私有频道（Private Channel），转发任意一条消息出去，复制链接（例如 `https://t.me/c/1234567890/1`）。中间的那串数字加上 `-100` 就是频道 ID：`-1001234567890`。

### 2. 环境变量配置 (`.env`)

在项目根目录下创建一个 `.env` 文件，填入你的配置信息：

```env
API_ID=你的api_id
API_HASH=你的api_hash
CHANNEL_ID=-1001234567890

# 可选配置
# DEVICE_MODEL=TG-Uploader-Server
# PROXY_TYPE=socks5
# PROXY_HOST=127.0.0.1
# PROXY_PORT=1080
```

### 3. 首次交互式授权登录

首次启动时，你必须进入交互式终端，通过手机短信验证码在本地生成 `.session` 授权文件。

```bash
docker run -it --rm \
  -v $(pwd)/session:/app/session \
  -e API_ID=你的api_id \
  -e API_HASH=你的api_hash \
  -e CHANNEL_ID=-1001234567890 \
  yushum/tg-uploader python uploader.py
```
* 根据终端提示，输入包含国家区号的手机号码（例如 `+8613800000000`）。
* 输入 Telegram App 内收到的验证码数字。
* 输入两步验证 (2FA) 密码（如果有）。

当屏幕打印出 `Telegram Client started successfully.` 时，立刻按下 `Ctrl+C` 退出。至此，`.session` 文件已经持久化保存在了 `./session` 目录。

### 4. 守护进程后台运行

完成授权后，即可彻底放手不管，启动无人值守后台监控：

```bash
docker compose up -d
```

---

## 📂 目录结构与命名规则

在默认配置中，`compose.yaml` 会将你宿主机的 `./downloads` 挂载至容器内。任何出现在该目录及其所有子目录中的 `.mp4`, `.ts`, `.flv`, `.mkv` 都会被自动处理。

上传器拥有智能名称解析系统，会自动生成整洁优美的 Telegram 标题文案。系统原生支持解析标准的 ISO 日期结构以及各种切片流录制工具产生的多 P 视频命名（例如 `某主播_2026-07-08T15_30_00_001.mp4`）。

## 📊 运维管理

* **查看日志**：`docker logs -f tg_uploader`
* **防重防漏**：系统利用超轻量级的 SQLite 数据库 (`./session/uploader.db`) 记录已经传过的文件路径与状态，绝不会重复上传同一文件。数据库完全免维护。
