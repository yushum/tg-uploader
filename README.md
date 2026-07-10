# Telegram Video Auto-Uploader

[中文版 (Chinese version)](README_zh.md)

An industrial-grade, fully automated, and extremely fast video uploader for Telegram channels. Designed for unattended server environments, this tool watches local directories and uploads videos directly to a Telegram channel. It features multi-connection parallel uploads, automatic lossless splitting for files >4GB, real-time remuxing, and auto-cleanup.

## 🚀 Key Features

*   **Multi-Connection FastTelethon Uploads**: Bypasses Telegram's single-connection bandwidth limit by slicing files in memory and uploading via 15 parallel MTProto connections. Reaches dozens of MB/s on good networks.
*   **Automatic 4GB+ File Splitting**: Telegram limits file uploads to 4GB (with Premium). This script seamlessly and losslessly splits any video larger than 4GB into chunks using `ffmpeg` without re-encoding, and uploads them consecutively.
*   **Auto-Remux for Stream Formats**: Automatically converts `.ts`, `.flv`, and `.mkv` files to `.mp4` instantly. Injects `+faststart` MOOV atoms so videos can be streamed directly in Telegram without waiting for a full download.
*   **Non-Blocking Radar & Sequential Scheduler**: Scans the filesystem asynchronously. Groups video chunks together, respects customizable stability timeout periods (to prevent reading incomplete files while they are still being recorded), and uploads them in perfect chronological order.
*   **OOM Defense & Rate Limit Handling**: Uses sliding memory-mapped windows to upload 40GB+ files without crashing the server. Automatically catches and sleeps through Telegram `FloodWaitError`s.
*   **Auto-Cleanup & Pruning**: Implements a true "burn-after-reading" mechanism. Upon successful upload and SQLite logging, original files are deleted. Empty subdirectories are recursively pruned.

---

## ⚙️ Configuration & Deployment

### 1. Prerequisites

1.  **Get `API_ID` and `API_HASH`**: Visit [Telegram Core: API Development Tools](https://my.telegram.org/auth) to register an application.
2.  **Get your `CHANNEL_ID`**: Create a private channel in Telegram, forward a message from it, and copy the link (e.g., `https://t.me/c/1234567890/1`). The ID is `-1001234567890`.

### 2. Environment Variables (`.env`)

Create a `.env` file in the root directory:

```env
API_ID=your_api_id
API_HASH=your_api_hash
CHANNEL_ID=-1001234567890

# Optional configuration
# MAX_SPLIT_SIZE_MB=4000
# DEVICE_MODEL=TG-Uploader-Server
# PROXY_TYPE=socks5
# PROXY_HOST=127.0.0.1
# PROXY_PORT=1080
```

### 3. First-time Authentication (Interactive)

The first time you run the bot, you **must** log in interactively to generate the `.session` file.

```bash
docker run -it --rm \
  -v $(pwd)/session:/app/session \
  -e API_ID=your_api_id \
  -e API_HASH=your_api_hash \
  -e CHANNEL_ID=-1001234567890 \
  yushum/tg-uploader python uploader.py
```
*   Enter your phone number with country code (e.g., `+1234567890`).
*   Enter the code you receive in your Telegram app.
*   Enter your 2FA password (if applicable).

Once you see `Telegram Client started successfully.`, press `Ctrl+C`. The session is now saved in the `./session` directory.

### 4. Run as Daemon

Start the unattended watcher in the background:

```bash
docker compose up -d
```

---

## 📂 Directory Structure & Naming Convention

By default, the `compose.yaml` mounts `./downloads` on your host to `/downloads` in the container. Any video files (`.mp4`, `.ts`, `.flv`, `.mkv`) placed inside `./downloads` (or its subdirectories) will be processed.

The uploader automatically parses file names to generate clean Telegram captions. It natively supports standard ISO date formats and chunked recordings (e.g., `StreamerName_2026-07-08T15_30_00_001.mp4`).

## 📊 Maintenance

*   **Logs**: `docker logs -f tg_uploader`
*   **Database**: The system uses a lightweight SQLite database (`./session/uploader.db`) to track uploaded files and prevent duplicates. It requires zero manual maintenance.
