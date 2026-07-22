#!/bin/bash

# Start script for Upscale Bot
# Smart CUDA detection + auto model download + interactive setup

set -e

echo "========================================="
echo "   Upscale Bot - Setup & Launch"
echo "========================================="

# Check prerequisites
for cmd in python3 node npm; do
    if ! command -v $cmd &> /dev/null; then
        echo "Error: $cmd is not installed"
        exit 1
    fi
done

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "[1/8] Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# ============================================
# Smart CUDA detection + PyTorch install
# ============================================
echo "[2/8] Detecting GPU and CUDA version..."

detect_cuda_version() {
    if command -v nvidia-smi &> /dev/null; then
        CUDA_VER=$(nvidia-smi | grep -oP 'CUDA Version:\s+\K[0-9]+\.[0-9]+' | head -1)
        if [ -n "$CUDA_VER" ]; then echo "$CUDA_VER"; return; fi
    fi
    if command -v nvcc &> /dev/null; then
        CUDA_VER=$(nvcc --version | grep -oP 'release \K[0-9]+\.[0-9]+')
        if [ -n "$CUDA_VER" ]; then echo "$CUDA_VER"; return; fi
    fi
    echo ""
}

detect_gpu_name() {
    if command -v nvidia-smi &> /dev/null; then
        nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1
    fi
}

GPU_NAME=$(detect_gpu_name)
CUDA_VERSION=$(detect_cuda_version)

echo "  GPU: ${GPU_NAME:-Unknown}"
echo "  CUDA: ${CUDA_VERSION:-Unknown}"

get_torch_index_url() {
    local cuda_major=$(echo "$1" | cut -d. -f1)
    local cuda_minor=$(echo "$1" | cut -d. -f2)
    
    if [ -z "$1" ]; then echo "https://download.pytorch.org/whl/cpu"; return; fi
    
    if [ "$cuda_major" -ge 12 ]; then
        echo "https://download.pytorch.org/whl/cu124"
    elif [ "$cuda_major" -eq 11 ] && [ "$cuda_minor" -ge 8 ]; then
        echo "https://download.pytorch.org/whl/cu118"
    else
        echo "https://download.pytorch.org/whl/cu118"
    fi
}

TORCH_INDEX_URL=$(get_torch_index_url "$CUDA_VERSION")
echo "  PyTorch index: $TORCH_INDEX_URL"

CURRENT_TORCH=$(python -c "import torch; print(f'{torch.__version__}|{torch.cuda.is_available()}')" 2>/dev/null || echo "")
DESIRED_TORCH="2.5.1"
DESIRED_TV="0.20.1"

if [[ "$CURRENT_TORCH" == "${DESIRED_TORCH}+"* ]] && [[ "$CURRENT_TORCH" == *"|True" ]]; then
    echo "  PyTorch $DESIRED_TORCH with CUDA already installed, skipping..."
else
    echo "[3/8] Installing PyTorch $DESIRED_TORCH + torchvision $DESIRED_TV..."
    pip install --force-reinstall \
        torch==${DESIRED_TORCH} \
        torchvision==${DESIRED_TV} \
        --index-url "$TORCH_INDEX_URL" \
        2>&1 | tail -5
    
    VERIFY=$(python -c "import torch; print(f'torch={torch.__version__}, cuda={torch.version.cuda}, available={torch.cuda.is_available()}')" 2>&1)
    echo "  Verified: $VERIFY"
fi

# Install Python dependencies
echo "[4/8] Installing Python dependencies..."
pip install -r requirements.txt 2>&1 | tail -10

# Install Node.js dependencies
echo "[5/8] Installing Node.js dependencies..."
cd discord_bot
npm install --silent 2>&1 | tail -3
cd ..

# Create necessary directories
mkdir -p temp output logs weights

echo ""
echo "========================================="
echo "   Setup Menu"
echo "========================================="
echo ""
echo "  [1] Validate credentials (test Supabase, Google Drive, Discord)"
echo "  [2] Select upscaling model"
echo "  [3] Start services"
echo "  [0] Exit"
echo ""
read -p "  Choose an option: " MENU_CHOICE

case "$MENU_CHOICE" in
    1)
        echo ""
        echo "-----------------------------------------"
        echo "  Validating credentials..."
        echo "-----------------------------------------"
        
        # Test Supabase
        echo -n "  Supabase... "
        python -c "
from app.config import settings
from app.services.supabase_service import get_supabase_service
try:
    sb = get_supabase_service()
    sb.client.table('users').select('id').limit(1).execute()
    print('OK')
except Exception as e:
    print(f'FAILED: {e}')
" 2>&1
        
        # Test Google Drive
        echo -n "  Google Drive... "
        python -c "
import asyncio
from app.services.drive_service import get_drive_service
async def test():
    try:
        ds = get_drive_service()
        token = await ds._get_access_token()
        print('OK')
    except Exception as e:
        print(f'FAILED: {e}')
asyncio.run(test())
" 2>&1
        
        # Test Discord Bot Token
        echo -n "  Discord Bot... "
        python -c "
import requests
from app.config import settings
r = requests.get('https://discord.com/api/v10/users/@me', headers={'Authorization': f'Bot {settings.discord_token}'})
print('OK' if r.status_code == 200 else f'FAILED (status {r.status_code})')
" 2>&1
        
        # Test GPU
        echo -n "  GPU/CUDA... "
        python -c "
import torch
if torch.cuda.is_available():
    print(f'OK ({torch.cuda.get_device_name(0)})')
else:
    print('FAILED: CUDA not available')
" 2>&1
        
        echo ""
        echo "  Press Enter to continue..."
        read -r
        exec "$0"
        ;;
    2)
        echo ""
        echo "-----------------------------------------"
        echo "  Select Upscaling Model"
        echo "-----------------------------------------"
        echo ""
        echo "  [1] realesr-animevideov3   (FAST - 5-10x faster, 2.4MB, excellent anime quality)"
        echo "  [2] RealESRGAN_x4plus_anime_6B  (BEST QUALITY - 17MB, slower, sharper details)"
        echo ""
        read -p "  Choose model [1-2]: " MODEL_CHOICE
        
        case "$MODEL_CHOICE" in
            1)
                SELECTED_MODEL="realesr-animevideov3"
                ;;
            2)
                SELECTED_MODEL="RealESRGAN_x4plus_anime_6B"
                ;;
            *)
                echo "  Invalid choice, using default (realesr-animevideov3)"
                SELECTED_MODEL="realesr-animevideov3"
                ;;
        esac
        
        # Update .env file
        if [ -f ".env" ]; then
            sed -i "s/^UPSCALE_MODEL=.*/UPSCALE_MODEL=$SELECTED_MODEL/" .env
            echo "  Updated .env: UPSCALE_MODEL=$SELECTED_MODEL"
        else
            echo "  WARNING: .env file not found!"
        fi
        
        # Download model weights if not present
        MODEL_FILE="weights/${SELECTED_MODEL}.pth"
        if [ ! -f "$MODEL_FILE" ]; then
            echo "  Downloading $SELECTED_MODEL weights..."
            case "$SELECTED_MODEL" in
                "realesr-animevideov3")
                    wget -q --show-progress -O "$MODEL_FILE" \
                        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth" 2>/dev/null || \
                    curl -L --progress-bar -o "$MODEL_FILE" \
                        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth"
                    ;;
                "RealESRGAN_x4plus_anime_6B")
                    wget -q --show-progress -O "$MODEL_FILE" \
                        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth" 2>/dev/null || \
                    curl -L --progress-bar -o "$MODEL_FILE" \
                        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth"
                    ;;
            esac
            echo "  Model downloaded: $(du -h $MODEL_FILE | cut -f1)"
        else
            echo "  Model already downloaded: $MODEL_FILE"
        fi
        
        echo ""
        echo "  Press Enter to continue..."
        read -r
        exec "$0"
        ;;
    3)
        # Continue to start services
        ;;
    0)
        echo "  Exiting."
        exit 0
        ;;
    *)
        # Default: skip menu, start services
        ;;
esac

# ============================================
# Download all model weights if not present
# ============================================
echo "[6/8] Checking model weights..."
for MODEL in "RealESRGAN_x4plus_anime_6B" "realesr-animevideov3"; do
    MODEL_FILE="weights/${MODEL}.pth"
    if [ ! -f "$MODEL_FILE" ]; then
        echo "  Downloading $MODEL..."
        case "$MODEL" in
            "realesr-animevideov3")
                wget -q --show-progress -O "$MODEL_FILE" \
                    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth" 2>/dev/null || \
                curl -L --progress-bar -o "$MODEL_FILE" \
                    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth"
                ;;
            "RealESRGAN_x4plus_anime_6B")
                wget -q --show-progress -O "$MODEL_FILE" \
                    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth" 2>/dev/null || \
                curl -L --progress-bar -o "$MODEL_FILE" \
                    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth"
                ;;
        esac
        echo "  Downloaded: $(du -h $MODEL_FILE | cut -f1)"
    fi
done

echo "[7/8] Starting services..."

# Kill any existing processes
if [ -f api.pid ]; then kill $(cat api.pid) 2>/dev/null || true; rm -f api.pid; fi
if [ -f bot.pid ]; then kill $(cat bot.pid) 2>/dev/null || true; rm -f bot.pid; fi
pkill -f "uvicorn app.api.main" 2>/dev/null || true
pkill -f "node bot.js" 2>/dev/null || true
sleep 1

export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

echo "  Starting API server on port 10100..."
python -m uvicorn app.api.main:app --host 0.0.0.0 --port 10100 2>&1 | tee -a logs/server.log &
API_PID=$!
echo $API_PID > api.pid

sleep 3

echo "  Starting Discord bot..."
cd discord_bot
node bot.js 2>&1 | tee -a ../logs/bot.log &
BOT_PID=$!
echo $BOT_PID > ../bot.pid
cd ..

echo ""
echo "========================================="
echo "   Upscale Bot is running!"
echo "========================================="
echo "  API Server PID: $API_PID"
echo "  Discord Bot PID: $BOT_PID"
echo "  Logs: logs/"
echo ""
echo "  Press Ctrl+C to stop all services"

cleanup() {
    echo ""
    echo "Stopping services..."
    kill $API_PID 2>/dev/null
    kill $BOT_PID 2>/dev/null
    echo "Services stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM
wait
