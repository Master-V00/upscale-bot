# Upscale Bot

Discord bot + FastAPI server for upscaling webtoon/manhwa images using Real-ESRGAN on a GPU server (RTX 3060). Users submit Google Drive folder links via Discord; the bot downloads, upscales 4x, and re-uploads to Drive.

---

## Overview

| Component | Tech | Role |
|-----------|------|------|
| Bot Interface | Discord.js v14 (Node.js) | Slash commands, notifications |
| API Server | FastAPI + Uvicorn (Python) | HTTP API, request orchestration |
| Database | Supabase (PostgreSQL) | Users, requests, settings, transactions |
| Upscaling | Real-ESRGAN + CUDA GPU | 4x anime/webtoon upscaling |
| Storage | Google Drive (OAuth2) | Input/output file storage |

---

## Pipeline

```
User sends /upscale <google_drive_url>
         |
    1. Download images from Google Drive (+ extract ZIP/RAR)
    2. Calculate credits from total pixel height
    3. Split tall images into ~1500px strips (intelligent seam detection)
    4. Upscale each strip 4x via Real-ESRGAN (GPU)
    5. Merge strips back + compress to WebP (quality 95, DPI 96)
    6. Upload output to new Google Drive folder
    7. Deduct credits only after successful upload
         |
    Discord bot polls Supabase -> sends completion embed to user
         |
    Cleanup scheduler deletes output folder after 30 minutes
```

### Image Processing Details

- **Split**: Uses luminance-based horizontal gradient minimization to find natural cut points (tolerance: 100px). Preferred strip height: 1500px.
- **Upscale**: Real-ESRGAN 4x. Supports `RealESRGAN_x4plus_anime_6B` (best quality, RRDBNet) and `realesr-animevideov3` (fast, SRVGGNetCompact). Sequential processing (GPU bottleneck). `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` + `UPSCALE_TILE=512` to prevent OOM on 12GB VRAM.
- **Merge**: Recombines upscaled strips per original image. Output is always WebP. If merged height exceeds WebP limit (16383px), splits into sequential chunks (`01.webp`, `02.webp`, ...).
- **Compress**: Re-saves with WebP quality 95, DPI 96. All images processed in parallel (8 threads).
- **Memory**: Aggressive cleanup -- `gc.collect()` after each stage, images closed immediately after use, disk-based temp dirs (not in-memory).

### Parallelism

| Stage | Workers | Notes |
|-------|---------|-------|
| Split | 4 threads | Small memory per image |
| Upscale | 1 (sequential) | GPU bottleneck |
| Merge | 8 threads | Parallel per-image merge |
| Compress | 8 threads | Parallel per-image compress |

---

## Credit System

Credits are based on the **sum of all input image heights**:

| Total Height (px) | Credits |
|-------------------|---------|
| <= 200,000 | 1 |
| <= 300,000 | 2 |
| <= 400,000 | 3 |
| <= 500,000 | 4 |
| > 500,000 | 5 |

- Credits deducted **only after successful upload**
- New users get 10 credits (configurable in `settings` table)
- Admin can add/deduct credits via Discord commands

---

## Setup

### Prerequisites

- Python 3.10+
- Node.js 18+
- NVIDIA GPU with CUDA (tested on RTX 3060 12GB)
- Supabase account (PostgreSQL)
- Google Cloud project with Drive API enabled + OAuth2 credentials

### 1. Database

Run `setup.sql` in Supabase SQL Editor. Creates tables (`users`, `requests`, `transaction_log`, `settings`), RLS policies, triggers, and seeds default settings.

### 2. Environment

Edit `.env` with your credentials:

```env
# Supabase (use service_role key, NOT anon key)
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_KEY=eyJ...

# Discord
DISCORD_TOKEN=...
DISCORD_CLIENT_ID=...
DISCORD_API_KEY=...
ADMIN_USER_ID=...

# Google Drive OAuth2
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
GOOGLE_OUTPUT_FOLDER_ID=...

# API
API_HOST=0.0.0.0
API_PORT=10100
API_KEY=...

# Upscaling
UPSCALE_MODEL=RealESRGAN_x4plus_anime_6B
UPSCALE_SCALE=4
UPSCALE_TILE=512
UPSCALE_FORMAT=png
COMPRESSION_QUALITY=95
```

### 3. Install & Run

```bash
# Linux (interactive setup with GPU detection)
chmod +x start.sh stop.sh
./start.sh

# The script will:
# 1. Create venv and install dependencies
# 2. Detect CUDA and install correct PyTorch
# 3. Show interactive menu: validate / select model / start
```

```bash
# Stop
./stop.sh
```

### Server Deployment (Remote)

```bash
# From local machine (Windows)
.\sync_to_server.ps1

# On server
cd /workspace/Upscale\ bot && ./stop.sh && ./start.sh
```

The sync script creates a tar.gz archive (excluding `.git`, `venv`, `node_modules`, `logs`, `weights`, `.env`) and uploads via SCP.

---

## API Endpoints

All admin endpoints require `Authorization: Bearer <API_KEY>` header.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | GPU status + worker state |
| POST | `/upscale` | Create upscale request |
| GET | `/request/{id}` | Request status |
| GET | `/user/{id}` | User info (auto-creates) |
| GET | `/user/{id}/transactions` | Transaction history |
| POST | `/admin/add-credits` | Add credits |
| POST | `/admin/deduct-credits` | Deduct credits |

---

## Discord Commands

| Command | Description | Access |
|---------|-------------|--------|
| `/ping` | Bot latency | Everyone |
| `/balance [user]` | Credit balance | Self; admin for others |
| `/profile [user]` | Profile with stats | Self; admin for others |
| `/upscale <folder_url>` | Submit Google Drive folder | Everyone |
| `/history [user]` | Last 10 transactions | Self; admin for others |
| `/add_credits <user> <amount> [reason]` | Add credits | Admin only |
| `/deduct_credits <user> <amount> [reason]` | Deduct credits | Admin only |

**Notification flow**: Bot polls Supabase every 10s for completed/failed requests. Sends rich embed to the originating channel (falls back to DM).

---

## Supported Upscale Models

| Model | Architecture | Speed | Quality | Use Case |
|-------|-------------|-------|---------|----------|
| `RealESRGAN_x4plus_anime_6B` | RRDBNet (6 blocks) | Medium | **Best** | Manga/webtoon (default) |
| `realesr-animevideov3` | SRVGGNetCompact | **Fast** (5-10x) | Good | Quick preview |
| `RealESRGAN_x4plus` | RRDBNet (23 blocks) | Slow | Good | General purpose |

Select model via the interactive menu in `start.sh` or by editing `UPSCALE_MODEL` in `.env`.

---

## Database Schema (setup.sql)

| Table | Purpose |
|-------|---------|
| `users` | Discord user IDs, credits, total usage |
| `requests` | Upscale requests with status tracking (`pending` -> `processing` -> `completed`/`failed`) |
| `transaction_log` | Credit add/deduct history with balance snapshots |
| `settings` | Key-value config (`compression_quality`, `estimated_processing_time_per_chapter_seconds`, etc.) |

---

## Folder Structure

```
Upscale bot/
├── app/
│   ├── api/
│   │   └── main.py              # FastAPI app, endpoints, lifespan, logging
│   ├── models/
│   │   ├── api.py               # Pydantic request/response models
│   │   └── database.py          # DB schema type hints
│   ├── services/
│   │   ├── cleanup.py           # Periodic Drive folder cleanup (30min expiry)
│   │   ├── drive_service.py     # Google Drive OAuth2 client (raw aiohttp)
│   │   ├── image_pipeline.py    # Split -> Merge -> Compress pipeline
│   │   ├── supabase_service.py  # All Supabase CRUD operations
│   │   └── upscale_service.py   # Real-ESRGAN singleton, GPU, model download
│   └── workers/
│       ├── queue_worker.py      # Main processing worker (polls DB, orchestrates pipeline)
│       └── task_worker.py       # Legacy worker (unused)
├── discord_bot/
│   ├── bot.js                   # Discord bot, slash commands, Supabase poller
│   └── package.json
├── logs/                        # Daily rotating log files (server_YYYY-MM-DD.log)
├── weights/                     # Model weights (auto-downloaded)
├── .env                         # Environment variables (secrets)
├── requirements.txt             # Python dependencies
├── setup.sql                    # Full DB schema + seed data
├── start.sh                     # Interactive setup + launch script
├── stop.sh                      # Process cleanup script
└── sync_to_server.ps1           # One-way local -> server sync
```

---

## Logging

**Terminal** (console): Only warnings + key milestones (`Step 1/5`, `COMPLETED`, etc.)

**File** (`logs/server_YYYY-MM-DD.log`): Full DEBUG output with timestamps, every request, every image operation.

The `_important_only` filter in `main.py` controls which INFO messages appear in terminal.

---

## Troubleshooting

### CUDA not available
```bash
nvidia-smi  # Check drivers
pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu124
```

### OOM errors
Reduce tile size in `.env`:
```
UPSCALE_TILE=200
```

### Model not downloading
```bash
wget -P weights/ https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth
```

### WebP size limit (16383px)
Automatically handled -- oversized images are split into sequential numbered chunks. No action needed.

### Clean restart
```bash
./stop.sh && ./start.sh
```

---

## Future Improvements: Text-Aware Upscaling for Webtoons

**Problem**: Current anime models (RealESRGAN, AnimeSharp, 4x-AnimeSharp) are trained to *remove noise/artifacts*. They classify text/speech bubbles as "noise" and blur/smudge them.

**Solution: Two-Stage Text-Aware Pipeline**

### Phase 1: Add Text-Safe Models (DONE - in v5)
- `realesr-general-x4v3` — supports `-dn` denoise strength (0.0 = no denoising, preserves text)
- `4x-UltraSharp` — general-purpose, better edge preservation than anime models
- Both selectable in `start.sh` menu (options 4 & 5)

### Phase 2: Automatic Text Region Detection & Selective Processing
```
Input Image
    │
    ▼
┌───────────────────┐
│ Text Detection    │  ← manga-ocr / easyocr / CRAFT / edge detection
│ (bubble masks)    │
└───────────────────┘
    │
    ├──────────────────┬──────────────────┐
    ▼                  ▼                  ▼
┌─────────┐      ┌─────────────┐    ┌────────────┐
│ Text    │      │ Art/Background│   │ Borders/   │
│ Regions │      │ Regions       │   │ Panels     │
└────┬────┘      └──────┬────────┘    └─────┬──────┘
     │                  │                   │
     ▼                  ▼                   ▼
realesr-general-x4v3  4x-AnimeSharp     realesr-general-x4v3
(-dn 0.0)             (full quality)     (-dn 0.3)
     │                  │                   │
     └──────────────────┴───────────────────┘
                    │
                    ▼
            Composite (alpha-blend masks)
                    │
                    ▼
            Output Image
```

### Implementation Plan

1. **Text Detection Service** (`app/services/text_detector.py`)
   - Use `manga-ocr` or `easyocr` for bubble detection
   - Fallback: Canny edge detection + contour filtering for speech bubbles
   - Output: binary mask (1=text, 0=art) per image

2. **Selective Upscaler** (`app/services/selective_upscaler.py`)
   - Split image into text/art regions using mask
   - Upscale text regions with `realesr-general-x4v3` (-dn 0.0)
   - Upscale art regions with `4x-AnimeSharp` or `4x-UltraSharp`
   - Blend using feathered mask edges (avoid seams)

3. **Pipeline Integration** (`app/services/image_pipeline.py`)
   - Add Step 4: "Text Detection" after split, before upscale
   - Toggle via `.env`: `TEXT_AWARE_UPSCALE=true/false`
   - Fallback to single-model if detection fails

4. **Model Management**
   - Pre-download `realesr-general-x4v3` and `4x-UltraSharp` in `start.sh`
   - Cache upsamplers in `UpscaleService` (already singleton)

### Models to Add

| Model | Architecture | Size | Use Case |
|-------|-------------|------|----------|
| `realesr-general-x4v3` | RRDBNet (6 block) | 17MB | Text regions (with -dn 0.0) |
| `4x-UltraSharp` | RRDBNet (23 block) | 67MB | Art regions / general |
| `4x-AnimeSharp` | RRDBNet (23 block) | 29MB | Art regions (current best) |

### Configuration (`.env`)
```env
TEXT_AWARE_UPSCALE=true
TEXT_MODEL=realesr-general-x4v3
ART_MODEL=4x-AnimeSharp
TEXT_DENOISE=0.0
ART_DENOISE=0.3
TEXT_DETECTOR=manga-ocr  # or easyocr, craft
```

### Performance Impact
- +1-2s per image for text detection
- ~2x upscale time (two models instead of one)
- GPU VRAM: +50% (load two models, or swap)
- **Quality**: Massive improvement on text/bubbles

---

## Tech Stack

- **Python**: FastAPI, Uvicorn, Pillow, NumPy, OpenCV, PyTorch, Real-ESRGAN, Loguru, aiohttp
- **Node.js**: Discord.js v14, @supabase/supabase-js, Axios
- **Infrastructure**: CUDA 12.4, PyTorch 2.5.1, bare metal Linux (Vast.ai)
