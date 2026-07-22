"""
FastAPI application for Upscale Bot
"""
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from loguru import logger

from app.config import settings
from app.services.supabase_service import get_supabase_service
from app.workers.queue_worker import get_queue_worker

# Configure logging
logger.remove()  # Remove default handler

# Console output - warnings+errors always visible
logger.add(sys.stderr, level="WARNING", format="{time:HH:mm:ss} | {level} | {message}")

# Console output - important INFO messages only (step summaries, completion)
def _important_only(record):
    msg = record["message"]
    keywords = [
        "REQUEST #", "Step ", "✅ Split", "✅ Upscale",
        "✅ Output ready", "GPU detected",
        "Worker started", "Upscale Bot API", "Cleanup task",
        "exceeds WebP", "Splitting into chunks", "All images will use",
        "COMPLETED",
    ]
    return any(kw in msg for kw in keywords)

logger.add(sys.stderr, level="INFO", filter=_important_only,
           format="{time:HH:mm:ss} | {level} | {message}")

# Create logs directory
logs_dir = Path("./logs")
logs_dir.mkdir(exist_ok=True)

# Server log file - full debug logs
logger.add(
    logs_dir / "server_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="7 days",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {module}:{function}:{line} | {message}"
)


# Request/Response models
class UpscaleRequest(BaseModel):
    user_id: str
    google_drive_folder_url: str
    channel_id: Optional[str] = None


class UpscaleResponse(BaseModel):
    success: bool
    request_id: Optional[int] = None
    message: str
    queue_position: Optional[int] = None
    estimated_time_minutes: Optional[int] = None


class RequestStatusResponse(BaseModel):
    id: int
    discord_user_id: str
    folder_url: str
    status: str
    pixel_count: Optional[int] = None
    output_url: Optional[str] = None
    error_message: Optional[str] = None
    credits_deducted: Optional[int] = None
    notified: Optional[bool] = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    discord_user_id: str
    credits: int
    total_credits_used: int
    created_at: Optional[str] = None


class TransactionResponse(BaseModel):
    id: int
    discord_user_id: str
    transaction_type: str
    amount: int
    balance_before: int
    balance_after: int
    description: Optional[str] = None
    created_at: Optional[str] = None


class AdminCreditsRequest(BaseModel):
    user_id: str
    amount: int
    reason: Optional[str] = "Admin bonus"


class HealthResponse(BaseModel):
    status: str
    gpu_available: bool
    worker_running: bool


# Lifespan manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Upscale Bot API...")
    
    # Verify CUDA availability
    import torch
    cuda_available = torch.cuda.is_available()
    if cuda_available:
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        logger.info(f"🚀 GPU detected: {gpu_name} ({gpu_mem:.1f} GB) | CUDA {torch.version.cuda}")
    else:
        logger.error("❌ CUDA NOT AVAILABLE! Running on CPU (very slow!)")
        logger.error("   Run: pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu124")
    
    worker = get_queue_worker()
    
    # Start worker in background
    import asyncio
    worker_task = asyncio.create_task(worker.start())
    
    # Start cleanup task
    from app.services.cleanup import start_cleanup_task_async
    cleanup_task = asyncio.create_task(start_cleanup_task_async())
    logger.info(f"🧹 Cleanup task started (every {settings.cleanup_interval}s, expires after {settings.expiration_time}min)")
    
    yield
    
    # Shutdown
    worker.stop()
    worker_task.cancel()
    cleanup_task.cancel()
    logger.info("Upscale Bot API stopped")


# Create FastAPI app
app = FastAPI(
    title="Upscale Bot API",
    description="API for webtoon/manhwa image upscaling",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def verify_api_key(authorization: str = Header(None)):
    """Verify API key from Authorization header"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    
    api_key = authorization.replace("Bearer ", "")
    if api_key != settings.discord_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return api_key


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    worker = get_queue_worker()
    upscale = worker.upscale
    gpu_info = upscale.get_gpu_info()
    
    return HealthResponse(
        status="healthy",
        gpu_available=gpu_info.get("available", False),
        worker_running=worker.running
    )


@app.post("/upscale", response_model=UpscaleResponse)
async def create_upscale_request(
    request: UpscaleRequest,
    api_key: str = Depends(verify_api_key)
):
    """Create a new upscale request"""
    supabase = get_supabase_service()
    
    # Ensure user exists
    user = supabase.ensure_user(request.user_id)
    
    # Create request
    request_id = supabase.create_request(
        discord_user_id=request.user_id,
        folder_url=request.google_drive_folder_url,
        channel_id=request.channel_id
    )
    
    if not request_id:
        raise HTTPException(status_code=500, detail="Failed to create request")
    
    # Count pending/processing requests ahead in queue
    queue_position = supabase.get_queue_position(request_id)
    
    return UpscaleResponse(
        success=True,
        request_id=request_id,
        message="Request created successfully",
        queue_position=queue_position,
        estimated_time_minutes=None
    )


@app.get("/request/{request_id}", response_model=RequestStatusResponse)
async def get_request_status(
    request_id: int,
    api_key: str = Depends(verify_api_key)
):
    """Get request status"""
    supabase = get_supabase_service()
    request = supabase.get_request(request_id)
    
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    return RequestStatusResponse(**request)


@app.get("/user/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    api_key: str = Depends(verify_api_key)
):
    """Get user info - auto-create if not exists"""
    supabase = get_supabase_service()
    user = supabase.get_user(user_id)
    
    # Auto-create user if not exists
    if not user:
        user = supabase.ensure_user(user_id)
        if not user:
            raise HTTPException(status_code=500, detail="Failed to create user")
    
    return UserResponse(**user)


@app.get("/user/{user_id}/transactions", response_model=List[TransactionResponse])
async def get_user_transactions(
    user_id: str,
    limit: int = 10,
    api_key: str = Depends(verify_api_key)
):
    """Get user transactions"""
    supabase = get_supabase_service()
    transactions = supabase.get_transactions(user_id, limit)
    return [TransactionResponse(**t) for t in transactions]


@app.post("/admin/add-credits")
async def add_credits(
    request: AdminCreditsRequest,
    api_key: str = Depends(verify_api_key)
):
    """Add credits to user (admin only)"""
    supabase = get_supabase_service()
    
    user = supabase.get_user(request.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    new_balance = user["credits"] + request.amount
    supabase.update_credits(request.user_id, new_balance)
    
    supabase.log_transaction(
        request.user_id,
        "bonus",
        request.amount,
        user["credits"],
        new_balance,
        request.reason
    )
    
    return {"success": True, "new_balance": new_balance}


@app.post("/admin/deduct-credits")
async def deduct_credits(
    request: AdminCreditsRequest,
    api_key: str = Depends(verify_api_key)
):
    """Deduct credits from user (admin only)"""
    supabase = get_supabase_service()
    
    user = supabase.get_user(request.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    new_balance = max(0, user["credits"] - request.amount)
    supabase.update_credits(request.user_id, new_balance)
    
    supabase.log_transaction(
        request.user_id,
        "deduct",
        request.amount,
        user["credits"],
        new_balance,
        request.reason
    )
    
    return {"success": True, "new_balance": new_balance}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug
    )