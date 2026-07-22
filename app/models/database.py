"""
Database models for Supabase
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class User(BaseModel):
    id: Optional[int] = None
    discord_user_id: str
    credits: int = 0
    total_credits_used: int = 0
    created_at: Optional[datetime] = None


class Request(BaseModel):
    id: Optional[int] = None
    user_id: str
    discord_user_id: str
    folder_url: str
    status: str = "pending"
    pixel_count: Optional[int] = None
    output_url: Optional[str] = None
    error_message: Optional[str] = None
    credits_deducted: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TransactionLog(BaseModel):
    id: Optional[int] = None
    discord_user_id: str
    transaction_type: str  # "deduct", "refund", "bonus"
    amount: int
    balance_before: int
    balance_after: int
    description: Optional[str] = None
    created_at: Optional[datetime] = None


class Setting(BaseModel):
    id: Optional[int] = None
    key: str
    value: str
    description: Optional[str] = None