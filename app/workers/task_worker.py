"""
Background Worker for Processing Requests
Polls Supabase queue and processes upscaling tasks
"""
import os
import shutil
import asyncio
import uuid
from pathlib import Path
from typing import Optional
from loguru import logger

from app.config import settings
from app.services.supabase_service import get_supabase_service
from app.services.drive_service import get_drive_service
from app.services.image_pipeline import get_image_pipeline
from app.services.upscale_service import get_upscale_service


class TaskWorker:
    """Main worker for processing image upscaling requests"""
    
    def __init__(self):
        self.supabase = get_supabase_service()
        self.drive = get_drive_service()
        self.pipeline = get_image_pipeline()
        self.upscale = get_upscale_service()
        self._running = False
    
    async def initialize(self):
        """Initialize services"""
        logger.info("🔧 Initializing worker services...")
        await self.upscale.initialize()
        self._running = True
        logger.info("✅ Worker initialized")
    
    def stop(self):
        """Stop the worker"""
        self._running = False
        logger.info("🛑 Worker stopped")
    
    async def process_queue(self):
        """Main queue processing loop"""
        logger.info("📥 Worker started: polling for pending requests...")
        
        while self._running:
            try:
                request = self.supabase.get_next_pending_request()
                
                if request:
                    request_id = request["id"]
                    logger.info(f"🚀 Processing request #{request_id}...")
                    
                    await self._process_request(request)
                    logger.info(f"✅ Request #{request_id} fully completed")
                else:
                    await asyncio.sleep(5)
                    
            except Exception as e:
                logger.error(f"❌ Queue processing error: {e}")
                await asyncio.sleep(5)
    
    async def _process_request(self, request: dict):
        """Process a single request"""
        request_id = request["id"]
        user_id = request["user_id"]
        discord_user_id = request["discord_user_id"]
        folder_url = request["folder_url"]
        
        temp_dir = None
        output_dir = None
        drive_folder_id = None
        
        try:
            # Update status to processing
            self.supabase.update_request(request_id, {"status": "processing"})
            
            # Create temp directories
            temp_dir = Path(settings.temp_chapters_dir) / str(uuid.uuid4())
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            output_dir = temp_dir / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # Extract folder ID from URL
            folder_id = self.drive.get_folder_id_from_url(folder_url)
            if not folder_id:
                raise ValueError("Invalid Google Drive folder URL")
            
            # Download images from Google Drive
            logger.info(f"⬇️ Downloading from Google Drive: {folder_id}")
            download_ok = await self._retry_async(
                lambda: asyncio.to_thread(self.drive.download_folder, folder_id, str(temp_dir / "input")),
                max_retries=3,
                label="Google Drive download"
            )
            
            if not download_ok:
                raise ValueError("Failed to download images from Google Drive")
            
            input_dir = temp_dir / "input"
            if not input_dir.exists():
                # Check for nested folders
                subdirs = list(temp_dir.glob("**/input"))
                if subdirs:
                    input_dir = subdirs[0]
                else:
                    input_dir = temp_dir / "input"
            
            # Find images
            image_files = list(input_dir.glob("*"))
            image_files = [f for f in image_files if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp', '.bmp')]
            
            if not image_files:
                raise ValueError("No images found in the downloaded folder")
            
            # Calculate pixel count for credits
            pixel_count = self.pipeline.get_total_pixels([str(f) for f in image_files])
            credits_needed = self._calculate_credits(pixel_count)
            
            if credits_needed == -1:
                raise ValueError("Chapter exceeds maximum pixel limit")
            
            # Check user credits
            user = self.supabase.get_user(discord_user_id)
            if not user:
                raise ValueError("User not found")
            
            if user["credits"] < credits_needed:
                raise ValueError(f"Insufficient credits. Need {credits_needed}, have {user['credits']}")
            
            # Process images through pipeline
            logger.info(f"🎨 Processing {len(image_files)} images...")
            output_paths = await self.pipeline.process_chapter(str(input_dir), str(output_dir))
            
            if not output_paths:
                raise ValueError("No images were processed successfully")
            
            # Create output folder on Google Drive
            master_folder = settings.google_output_folder_id
            output_folder_name = f"Upscaled_{request_id}"
            
            logger.info(f"📁 Creating output folder: {output_folder_name}")
            drive_folder_id = await self._retry_async(
                lambda: asyncio.to_thread(self.drive.create_folder, output_folder_name, master_folder),
                max_retries=3,
                label="Create Drive folder"
            )
            
            if not drive_folder_id:
                raise ValueError("Failed to create output folder on Google Drive")
            
            # Make folder public
            await asyncio.to_thread(self.drive.make_folder_public, drive_folder_id)
            
            # Upload processed images
            logger.info(f"⬆️ Uploading {len(output_paths)} images...")
            upload_ok = await self._retry_async(
                lambda: asyncio.to_thread(self.drive.upload_folder, str(output_dir), drive_folder_id),
                max_retries=3,
                label="Google Drive upload"
            )
            
            if not upload_ok:
                raise ValueError("Failed to upload images to Google Drive")
            
            output_url = f"https://drive.google.com/drive/folders/{drive_folder_id}"
            logger.info(f"🎯 Upload complete: {output_url}")
            
            # Deduct credits and log transaction
            self.supabase.deduct_credits(discord_user_id, credits_needed)
            self.supabase.add_total_credits_used(discord_user_id, credits_needed)
            
            new_balance = user["credits"] - credits_needed
            self.supabase.log_transaction(
                discord_user_id=discord_user_id,
                transaction_type="deduct",
                amount=credits_needed,
                balance_before=user["credits"],
                balance_after=new_balance,
                description=f"Upscale request #{request_id}"
            )
            
            # Update request as completed
            self.supabase.update_request(request_id, {
                "status": "completed",
                "pixel_count": pixel_count,
                "output_url": output_url,
                "credits_deducted": credits_needed
            })
            
            logger.info(f"✅ Request #{request_id} completed successfully!")
            
        except Exception as e:
            logger.error(f"❌ Error processing request #{request_id}: {e}")
            self.supabase.update_request(request_id, {
                "status": "failed",
                "error_message": str(e)
            })
            
        finally:
            # Cleanup temp directories
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                    logger.debug(f"🧹 Cleaned up: {temp_dir}")
                except Exception as e:
                    logger.warning(f"⚠️ Cleanup failed: {e}")
            
            # Clean up drive folder on failure
            if drive_folder_id and "failed" in str(e).lower():
                try:
                    await asyncio.to_thread(self.drive.delete_folder, drive_folder_id)
                except:
                    pass
    
    def _calculate_credits(self, pixel_count: int) -> int:
        """Calculate credits needed based on pixel count"""
        if pixel_count > 500000:
            return -1  # Too large
        elif pixel_count <= 200000:
            return 1
        elif pixel_count <= 300000:
            return 2
        elif pixel_count <= 400000:
            return 3
        elif pixel_count <= 500000:
            return 4
        return 5
    
    async def _retry_async(self, fn, max_retries: int = 3, base_delay: float = 2.0, label: str = "operation"):
        """Retry an async function with exponential backoff"""
        for attempt in range(1, max_retries + 1):
            try:
                return await fn()
            except Exception as e:
                if attempt == max_retries:
                    raise
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(f"⚠️ {label} failed (attempt {attempt}/{max_retries}): {e}. Retrying in {delay:.0f}s...")
                await asyncio.sleep(delay)


# Global worker instance
_worker = TaskWorker()


def get_worker() -> TaskWorker:
    return _worker