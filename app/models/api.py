"""
API models for request/response
"""
from typing import Optional, List
from pydantic import BaseModel, HttpUrl


class UpscaleRequest(BaseModel):
    user_id: str
    google_drive_folder_url: HttpUrl
    channel_id: Optional[str] = None


class UpscaleResponse(BaseModel):
    success: bool
    request_id: Optional[int] = None
    message: str
    queue_position: Optional[int] = None
    estimated_time_minutes: Optional[int] = None


class RequestStatusResponse(BaseModel):
    id: int
    user_id: str
    discord_user_id: str
    folder_url: str
    status: str
    pixel_count: Optional[int] = None
    output_url: Optional[str] = None
    error_message: Optional[str] = None
    credits_deducted: Optional[int] = None
    created_at: str
    updated_at: str


class UserResponse(BaseModel):
    id: int
    discord_user_id: str
    credits: int
    total_credits_used: int
    created_at: str


class TransactionResponse(BaseModel):
    id: int
    discord_user_id: str
    transaction_type: str
    amount: int
    balance_before: int
    balance_after: int
    description: Optional[str] = None
    created_at: str


class AdminAddCreditsRequest(BaseModel):
    user_id: str
    amount: int
    reason: Optional[str] = "Admin bonus"


class AdminDeductCreditsRequest(BaseModel):
    user_id: str
    amount: int
    reason: Optional[str] = "Admin deduction"


class HealthResponse(BaseModel):
    status: str
    gpu_available: bool
    queue_size: int
    worker_running: bool