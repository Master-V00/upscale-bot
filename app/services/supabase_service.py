"""
Supabase database service
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from loguru import logger
from supabase import create_client, Client

from app.config import settings


class SupabaseService:
    """Handles all database operations with Supabase"""
    
    def __init__(self):
        self.client: Client = create_client(settings.supabase_url, settings.supabase_key)
    
    # ========== USER OPERATIONS ==========
    
    def get_user(self, discord_user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by Discord ID"""
        try:
            result = self.client.table("users").select("*").eq("discord_user_id", discord_user_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get user: {e}")
            return None
    
    def create_user(self, discord_user_id: str, credits: int = None) -> Optional[Dict[str, Any]]:
        """Create a new user with credits from settings"""
        if credits is None:
            credits = self.get_setting_int("default_credits", 100)
        
        try:
            result = self.client.table("users").insert({
                "discord_user_id": discord_user_id,
                "credits": credits,
                "total_credits_used": 0
            }).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            return None
    
    def ensure_user(self, discord_user_id: str) -> Dict[str, Any]:
        """Get or create user"""
        user = self.get_user(discord_user_id)
        if not user:
            user = self.create_user(discord_user_id)
            if user:
                logger.info(f"Created new user: {discord_user_id}")
        return user
    
    def update_credits(self, discord_user_id: str, new_credits: int) -> bool:
        """Update user credits"""
        try:
            self.client.table("users").update({
                "credits": new_credits
            }).eq("discord_user_id", discord_user_id).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to update credits: {e}")
            return False
    
    def increment_credits_used(self, discord_user_id: str, amount: int) -> bool:
        """Increment total credits used"""
        try:
            self.client.table("users").update({
                "total_credits_used": self.client.table("users").select("total_credits_used").eq("discord_user_id", discord_user_id).execute().data[0]["total_credits_used"] + amount
            }).eq("discord_user_id", discord_user_id).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to increment credits used: {e}")
            return False
    
    def add_total_credits_used(self, discord_user_id: str, amount: int) -> bool:
        """Add to total credits used"""
        try:
            user = self.get_user(discord_user_id)
            if user:
                new_total = user.get("total_credits_used", 0) + amount
                self.client.table("users").update({
                    "total_credits_used": new_total
                }).eq("discord_user_id", discord_user_id).execute()
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to add total credits used: {e}")
            return False
    
    # ========== NOTIFICATION OPERATIONS ==========
    
    def mark_request_notified(self, request_id: int) -> bool:
        """Mark request as notified"""
        try:
            self.client.table("requests").update({
                "notified": True
            }).eq("id", request_id).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to mark request notified: {e}")
            return False
    
    # ========== REQUEST OPERATIONS ==========
    
    def create_request(
        self,
        discord_user_id: str,
        folder_url: str,
        channel_id: Optional[str] = None
    ) -> Optional[int]:
        """Create a new upscale request"""
        try:
            result = self.client.table("requests").insert({
                "discord_user_id": discord_user_id,
                "folder_url": folder_url,
                "channel_id": channel_id,
                "status": "pending",
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }).execute()
            
            if result.data:
                return result.data[0]["id"]
            return None
        except Exception as e:
            logger.error(f"Failed to create request: {e}")
            return None
    
    def get_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        """Get request by ID"""
        try:
            result = self.client.table("requests").select("*").eq("id", request_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get request: {e}")
            return None
    
    def update_request_status(
        self,
        request_id: int,
        status: str,
        output_url: Optional[str] = None,
        error_message: Optional[str] = None,
        pixel_count: Optional[int] = None,
        credits_deducted: Optional[int] = None
    ) -> bool:
        """Update request status"""
        try:
            update_data = {
                "status": status,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            if output_url is not None:
                update_data["output_url"] = output_url
            if error_message is not None:
                update_data["error_message"] = error_message
            if pixel_count is not None:
                update_data["pixel_count"] = pixel_count
            if credits_deducted is not None:
                update_data["credits_deducted"] = credits_deducted
            
            self.client.table("requests").update(update_data).eq("id", request_id).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to update request: {e}")
            return False
    
    def get_pending_requests(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get pending requests"""
        try:
            result = self.client.table("requests")\
                .select("*")\
                .eq("status", "pending")\
                .order("created_at", desc=False)\
                .limit(limit)\
                .execute()
            return result.data
        except Exception as e:
            logger.error(f"Failed to get pending requests: {e}")
            return []
    
    def get_queue_position(self, request_id: int) -> int:
        """Get queue position for a request (count of pending/processing requests ahead)"""
        try:
            # Get the created_at of the current request
            current = self.client.table("requests").select("created_at").eq("id", request_id).execute()
            if not current.data:
                return 0
            
            current_created = current.data[0]["created_at"]
            
            # Count pending/processing requests created before this one
            result = self.client.table("requests")\
                .select("id", count="exact")\
                .in_("status", ["pending", "processing"])\
                .lt("created_at", current_created)\
                .execute()
            
            return (result.count or 0) + 1  # +1 because position is 1-indexed
        except Exception as e:
            logger.error(f"Failed to get queue position: {e}")
            return 0
    
    def get_user_requests(self, discord_user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get user's recent requests"""
        try:
            result = self.client.table("requests")\
                .select("*")\
                .eq("discord_user_id", discord_user_id)\
                .order("created_at", desc=True)\
                .limit(limit)\
                .execute()
            return result.data
        except Exception as e:
            logger.error(f"Failed to get user requests: {e}")
            return []
    
    # ========== TRANSACTION OPERATIONS ==========
    
    def log_transaction(
        self,
        discord_user_id: str,
        transaction_type: str,
        amount: int,
        balance_before: int,
        balance_after: int,
        description: Optional[str] = None
    ) -> Optional[int]:
        """Log a transaction"""
        try:
            result = self.client.table("transaction_log").insert({
                "discord_user_id": discord_user_id,
                "transaction_type": transaction_type,
                "amount": amount,
                "balance_before": balance_before,
                "balance_after": balance_after,
                "description": description,
                "created_at": datetime.utcnow().isoformat()
            }).execute()
            
            if result.data:
                return result.data[0]["id"]
            return None
        except Exception as e:
            logger.error(f"Failed to log transaction: {e}")
            return None
    
    def get_transactions(self, discord_user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get user's transactions"""
        try:
            result = self.client.table("transaction_log")\
                .select("*")\
                .eq("discord_user_id", discord_user_id)\
                .order("created_at", desc=True)\
                .limit(limit)\
                .execute()
            return result.data
        except Exception as e:
            logger.error(f"Failed to get transactions: {e}")
            return []
    
    # ========== SETTINGS OPERATIONS ==========
    
    def get_setting(self, key: str) -> Optional[str]:
        """Get a setting value"""
        try:
            result = self.client.table("settings").select("value").eq("key", key).execute()
            return result.data[0]["value"] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get setting: {e}")
            return None
    
    def get_all_settings(self) -> Dict[str, str]:
        """Get all settings as a dictionary"""
        try:
            result = self.client.table("settings").select("key, value").execute()
            return {item["key"]: item["value"] for item in result.data}
        except Exception as e:
            logger.error(f"Failed to get all settings: {e}")
            return {}
    
    def get_setting_int(self, key: str, default: int = 0) -> int:
        """Get a setting value as integer"""
        value = self.get_setting(key)
        if value is not None:
            try:
                return int(value)
            except ValueError:
                return default
        return default
    
    def get_setting_float(self, key: str, default: float = 0.0) -> float:
        """Get a setting value as float"""
        value = self.get_setting(key)
        if value is not None:
            try:
                return float(value)
            except ValueError:
                return default
        return default
    
    def set_setting(self, key: str, value: str, description: Optional[str] = None) -> bool:
        """Set a setting value"""
        try:
            # Upsert the setting
            self.client.table("settings").upsert({
                "key": key,
                "value": value,
                "description": description
            }).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to set setting: {e}")
            return False
    
    # ========== CLEANUP OPERATIONS ==========
    
    def get_expired_outputs(self, expiration_minutes: int = 20) -> List[Dict[str, Any]]:
        """Get outputs that have expired and should be deleted"""
        try:
            expiration_time = (datetime.now(timezone.utc) - timedelta(minutes=expiration_minutes)).isoformat()
            
            result = self.client.table("requests")\
                .select("*")\
                .eq("status", "completed")\
                .lt("updated_at", expiration_time)\
                .neq("gdrive_file_cleaned", True)\
                .execute()
            
            return result.data if result.data else []
        except Exception as e:
            logger.error(f"Failed to get expired outputs: {e}")
            return []
    
    def mark_output_cleaned(self, request_id: int) -> bool:
        """Mark output as cleaned from Google Drive"""
        try:
            self.client.table("requests").update({
                "gdrive_file_cleaned": True
            }).eq("id", request_id).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to mark output as cleaned: {e}")
            return False


# Singleton instance
_supabase_service: Optional[SupabaseService] = None


def get_supabase_service() -> SupabaseService:
    global _supabase_service
    if _supabase_service is None:
        _supabase_service = SupabaseService()
    return _supabase_service