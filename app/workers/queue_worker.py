"""
Background worker for processing upscale requests
Stage-first processing with disk-based temp directories
"""
import asyncio
import gc
import shutil
import tempfile
import time
import traceback
import uuid
import zipfile
import rarfile
from pathlib import Path
from typing import Optional, List
import aiohttp
from loguru import logger

from app.config import settings
from app.services.supabase_service import get_supabase_service
from app.services.drive_service import get_drive_service
from app.services.upscale_service import get_upscale_service
from app.services.image_pipeline import get_image_pipeline


class ImageProcessor:
    """Handle image processing tasks"""
    
    def calculate_total_height(self, image_paths: list) -> int:
        """Calculate total height from images (sum of all page heights)"""
        total_height = 0
        try:
            from PIL import Image
            for image_path in image_paths:
                img = Image.open(image_path)
                width, height = img.size
                img.close()
                logger.debug(f"  📄 {image_path.name}: {width}x{height}")
                total_height += height
            logger.info(f"📊 Total height count: {total_height}")
            return total_height
        except Exception as e:
            logger.error(f"❌ Failed to calculate pixel count: {e}")
            return 0
    
    def calculate_credits_needed(self, total_height: int) -> int:
        """Calculate credits needed based on total height"""
        if total_height <= 200000:
            return 1
        elif total_height <= 300000:
            return 2
        elif total_height <= 400000:
            return 3
        elif total_height <= 500000:
            return 4
        else:
            return 5


class QueueWorker:
    """Background worker that processes upscale requests"""
    
    def __init__(self):
        self.supabase = get_supabase_service()
        self.drive = get_drive_service()
        self.upscale = get_upscale_service()
        self.pipeline = get_image_pipeline()
        self.processor = ImageProcessor()
        self.running = False
        self.current_task = None
        self.temp_base = Path(settings.temp_dir)
        self.temp_base.mkdir(parents=True, exist_ok=True)
        
    async def start(self):
        """Start the worker loop"""
        self.running = True
        logger.info("📥 Worker started: polling database for pending requests...")
        
        while self.running:
            try:
                await self.process_next_request()
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"❌ Worker error: {e}")
                await asyncio.sleep(10)
    
    def stop(self):
        """Stop the worker"""
        self.running = False
        logger.info("🛑 Worker stopped")
    
    def _ensure_path(self, path, name="path"):
        """Ensure path is a Path object"""
        if not isinstance(path, Path):
            logger.warning(f"  ⚠️ {name} was not a Path: {type(path)} = {path}")
            return Path(path)
        return path

    def _create_chapter_dir(self) -> Path:
        """Create a unique temp directory on disk for this request"""
        # Ensure temp_base is a Path
        self.temp_base = self._ensure_path(self.temp_base, "temp_base")
        chapter_dir = self.temp_base / str(uuid.uuid4())
        chapter_dir.mkdir(parents=True, exist_ok=True)
        return chapter_dir
    
    def _cleanup_dir(self, path: Path):
        """Safely remove a directory"""
        path = self._ensure_path(path, "cleanup_path")
        try:
            if path and path.exists():
                shutil.rmtree(path, ignore_errors=True)
        except Exception as e:
            logger.warning(f"⚠️ Cleanup failed for {path}: {e}")
    
    async def _extract_archive(self, archive_path: Path, extract_to: Path):
        """Extract ZIP/RAR, handling subfolder-inside case, then delete archive"""
        try:
            if archive_path.suffix.lower() == ".zip":
                with zipfile.ZipFile(archive_path, 'r') as zf:
                    zf.extractall(extract_to)
            elif archive_path.suffix.lower() == ".rar":
                with rarfile.RarFile(archive_path) as rf:
                    rf.extractall(extract_to)
            else:
                logger.warning(f"  ⚠️ Unknown archive format: {archive_path.name}")
                return
            # If extraction created a single subfolder, move its contents up
            items = list(extract_to.iterdir())
            if len(items) == 1 and items[0].is_dir():
                sub = items[0]
                for child in list(sub.iterdir()):
                    shutil.move(str(child), str(extract_to / child.name))
                shutil.rmtree(sub, ignore_errors=True)
                logger.info(f"  📂 Moved files from subfolder '{sub.name}' to input")
        except Exception as e:
            logger.warning(f"  ⚠️ Archive extraction error for {archive_path.name}: {e}")
            raise
        finally:
            # Always clean up the archive file
            if archive_path.exists():
                archive_path.unlink()
    
    async def _verify_upload(self, folder_id: str, expected_files: List[str]):
        """Verify all files were uploaded to Google Drive folder"""
        import time
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                access_token = await self.drive._get_access_token()
                
                async with aiohttp.ClientSession() as session:
                    headers = {"Authorization": f"Bearer {access_token}"}
                    
                    # List files in the folder
                    params = {
                        "q": f"'{folder_id}' in parents and trashed = false",
                        "fields": "files(id, name)",
                    }
                    
                    async with session.get(
                        "https://www.googleapis.com/drive/v3/files",
                        params=params,
                        headers=headers
                    ) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            uploaded_files = {f["name"] for f in result.get("files", [])}
                            
                            missing = set(expected_files) - uploaded_files
                            if not missing:
                                logger.info(f"  ✅ All {len(expected_files)} files verified in Drive")
                                return
                            else:
                                logger.warning(f"  ⚠️ Attempt {attempt+1}: Missing files in Drive: {missing}")
                        else:
                            logger.warning(f"  ⚠️ Attempt {attempt+1}: Failed to list Drive files: {resp.status}")
            except Exception as e:
                logger.warning(f"  ⚠️ Attempt {attempt+1}: Verification error: {e}")
            
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
        
        # If we get here, verification failed
        missing = set(expected_files) - uploaded_files if 'uploaded_files' in dir() else set(expected_files)
        raise Exception(f"Upload verification failed after {max_retries} attempts. Missing files: {missing}")
    
    async def process_next_request(self):
        """Process the next pending request"""
        pending_requests = self.supabase.get_pending_requests(limit=1)
        
        if not pending_requests:
            return
        
        request = pending_requests[0]
        request_id = request["id"]
        discord_user_id = request["discord_user_id"]
        folder_url = request["folder_url"]
        channel_id = request.get("channel_id")
        
        logger.info(f"🚀 Processing request #{request_id} for user {discord_user_id}")
        logger.info(f"  📁 Folder: {folder_url}")
        logger.info(f"  💬 Channel: {channel_id}")
        
        sub_folder_id = None
        chapter_dir = None
        
        try:
            self.supabase.update_request_status(request_id, "processing")
            self.current_task = request_id
            import time as _time
            _request_start = _time.time()
            
            # Get user and check credits
            user = self.supabase.get_user(discord_user_id)
            if not user or user.get("credits", 0) <= 0:
                logger.warning(f"❌ User {discord_user_id} has insufficient credits")
                self.supabase.update_request_status(request_id, "failed", error_message="Insufficient credits")
                return
            
            logger.info(f"  💳 User has {user['credits']} credits")
            
            # Create disk-based temp directory
            chapter_dir = self._create_chapter_dir()
            input_dir = self._ensure_path(chapter_dir / "input", "input_dir")
            parts_dir = self._ensure_path(chapter_dir / "parts", "parts_dir")
            upscaled_dir = self._ensure_path(chapter_dir / "upscaled", "upscaled_dir")
            output_dir = self._ensure_path(chapter_dir / "output", "output_dir")
            input_dir.mkdir()
            parts_dir.mkdir()
            upscaled_dir.mkdir()
            output_dir.mkdir()
            
            logger.info(f"{'='*50}")
            logger.info(f"  📂 Working directory: {chapter_dir}")
            
            # ============================================
            # STEP 1: Download from Google Drive
            # ============================================
            logger.info("📥 Step 1/9: Downloading from Google Drive...")

            image_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}

            # Detect if URL is a single file or a folder
            file_id = self.drive._extract_file_id(folder_url)
            folder_id = self.drive._extract_folder_id(folder_url)

            if file_id and not folder_id:
                # ── Single file link ──
                logger.info("  🔗 Detected single file link")
                file_info = await self.drive.get_file_info(file_id)
                mime = file_info.get("mimeType", "")
                if not mime.startswith("image/"):
                    self.supabase.update_request_status(request_id, "failed", error_message="URL is not an image")
                    return
                logger.info(f"  📥 Downloading single image: {file_info['name']}")
                await self.drive.download_file(file_id, input_dir)
                is_single_file = True
            else:
                # ── Folder link ──
                files = await self.drive.list_folder_contents(folder_url)
                logger.info(f"  📂 Found {len(files)} files in folder")

                if not files:
                    self.supabase.update_request_status(request_id, "failed", error_message="No images found in folder")
                    return

                image_files = []
                for file in files:
                    if file["mimeType"].startswith("image/"):
                        image_files.append(file)
                    elif file["mimeType"] in ["application/zip", "application/x-rar-compressed"]:
                        logger.info(f"  📥 Downloading archive: {file['name']}")
                        downloaded = await self.drive.download_file(file["id"], chapter_dir)
                        await self._extract_archive(downloaded, input_dir)

                for file in image_files:
                    logger.info(f"  📥 Downloading: {file['name']}")
                    await self.drive.download_file(file["id"], input_dir)

                is_single_file = False

            # Collect all images from input_dir
            all_images = sorted([f for f in input_dir.iterdir() if f.suffix.lower() in image_extensions])
            logger.info(f"  📸 Found {len(all_images)} images to process")

            if not all_images:
                self.supabase.update_request_status(request_id, "failed", error_message="No valid images found")
                return

            # ============================================
            # STEP 2: Calculate credits
            # ============================================
            logger.info("📊 Step 2/9: Calculating credits...")
            total_height = self.processor.calculate_total_height(all_images)
            credits_needed = self.processor.calculate_credits_needed(total_height)
            logger.info(f"  💰 Credits needed: {credits_needed}")
            
            if user["credits"] < credits_needed:
                self.supabase.update_request_status(
                    request_id, "failed",
                    error_message=f"Insufficient credits. Need {credits_needed}, have {user['credits']}"
                )
                return
            
            # ============================================
            # STEP 3: Rename images sequentially (01, 02, 03...)
            # ============================================
            logger.info("🔢 Step 3/9: Renaming images sequentially...")
            image_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.gif'}
            all_images = sorted([f for f in input_dir.iterdir() if f.suffix.lower() in image_extensions])
            for idx, img in enumerate(all_images, 1):
                new_name = f"{idx:02d}{img.suffix}"
                img.rename(input_dir / new_name)
            logger.info(f"  ✅ Renamed {len(all_images)} images → 01, 02, 03...")

            # ============================================
            # STEP 4: Pre-compress images to reduce file sizes before splitting
            # ============================================
            logger.info("🗜️ Step 4/9: Pre-compressing images...")
            input_dir = Path(input_dir)
            await asyncio.to_thread(self.pipeline.pre_compress_images, input_dir, 85)
            logger.info(f"  ✅ Pre-compression complete")
            
            # Re-read images after compression
            image_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.gif'}
            all_images = sorted([f for f in input_dir.iterdir() if f.suffix.lower() in image_extensions])
            logger.info(f"  📸 Found {len(all_images)} images after compression")
            
            # ============================================
            # STEP 5: SPLIT ALL images → parts on disk
            # ============================================
            logger.info("✂️ Step 5/9: Splitting ALL images...")
            all_part_paths = await asyncio.to_thread(self.pipeline.split_all_images, input_dir, parts_dir)
            logger.info(f"  ✅ Split complete: {len(all_part_paths)} parts on disk")
            
            # Free memory after split
            gc.collect()
            
            # Track original images for merge (need their extensions)
            original_images = all_images
            
            # ============================================
            # STEP 6: UPSCALE ALL parts → upscaled on disk
            # ============================================
            logger.info("🎨 Step 6/9: Upscaling ALL parts...")
            def upscale_func(image):
                return self.upscale.upscale_image_pil(image)
            
            upscaled_paths = await asyncio.to_thread(self.pipeline.upscale_all_parts, all_part_paths, upscaled_dir, upscale_func)
            logger.info(f"  ✅ Upscale complete: {len(upscaled_paths)} upscaled parts on disk")
            
            # Free memory after upscale
            gc.collect()
            
            # Delete parts directory (no longer needed)
            await asyncio.to_thread(self._cleanup_dir, parts_dir)
            
            # ============================================
            # STEP 7: MERGE + COMPRESS → output on disk
            # ============================================
            logger.info("🔗 Step 7/9: Merging + compressing...")
            merged_paths = await asyncio.to_thread(self.pipeline.merge_parts_from_disk, upscaled_paths, original_images, output_dir)
            compressed_paths = await asyncio.to_thread(self.pipeline.compress_images_on_disk, merged_paths, output_dir)
            logger.info(f"  ✅ Output ready: {len(compressed_paths)} files")
            
            # Free memory after merge
            gc.collect()
            
            # Delete upscaled directory (no longer needed)
            await asyncio.to_thread(self._cleanup_dir, upscaled_dir)
            
            # ============================================
            # STEP 8: Upload to Google Drive
            # ============================================
            logger.info("⬆️ Step 8/9: Uploading to Google Drive...")
            subfolder_name = f"Upscaled_Order_{request_id}"
            sub_folder_id = await self.drive.create_folder(subfolder_name)
            await self.drive.make_folder_public(sub_folder_id)
            
            uploaded_files = []
            for compressed_file in compressed_paths:
                # Verify file exists before upload
                if not compressed_file.exists():
                    logger.error(f"  ❌ File not found: {compressed_file}")
                    raise FileNotFoundError(f"Output file missing: {compressed_file}")
                
                await self.drive.upload_file(compressed_file, sub_folder_id)
                logger.info(f"  📤 Uploaded: {compressed_file.name}")
                uploaded_files.append(compressed_file.name)
            
            # ============================================
            # STEP 9: Verify all files uploaded successfully
            # ============================================
            logger.info("✅ Step 9/9: Verifying upload...")
            await self._verify_upload(sub_folder_id, uploaded_files)
            logger.info(f"  ✅ Verification complete: All {len(uploaded_files)} files confirmed")
            
            # ✅ ONLY NOW deduct credits
            new_balance = user["credits"] - credits_needed
            self.supabase.update_credits(discord_user_id, new_balance)
            self.supabase.log_transaction(
                discord_user_id, "deduct", credits_needed,
                user["credits"], new_balance, f"Upscale request {request_id}"
            )
            self.supabase.add_total_credits_used(discord_user_id, credits_needed)
            logger.info(f"  ✅ Deducted {credits_needed} credits. New balance: {new_balance}")
            
            output_url = f"https://drive.google.com/drive/folders/{sub_folder_id}"
            
            self.supabase.update_request_status(
                request_id, "completed",
                output_url=output_url,
                pixel_count=total_height,
                credits_deducted=credits_needed
            )
            
            total_size_mb = sum(f.stat().st_size for f in compressed_paths) / (1024 * 1024)
            elapsed = _time.time() - _request_start
            summary = (
                f"  ✅ REQUEST #{request_id} COMPLETED SUCCESSFULLY\n"
                f"  📊 Processed: {len(all_images)} image(s) → {len(compressed_paths)} output file(s)\n"
                f"  📐 Total height: {total_height:,}px | Credits: {credits_needed}\n"
                f"  💾 Output size: {total_size_mb:.1f}MB total\n"
                f"  ⏱️ Processing time: {elapsed:.0f}s ({elapsed/60:.1f}min)\n"
                f"  🔗 {output_url}\n"
                f"{'='*50}"
            )
            logger.info(summary)
            
        except Exception as e:
            logger.error(f"❌ Error processing request #{request_id}: {e}")
            logger.error(f"  🔍 Traceback:\n{traceback.format_exc()}")
            self.supabase.update_request_status(request_id, "failed", error_message=str(e))
            if sub_folder_id:
                try:
                    await self.drive.delete_file(sub_folder_id)
                    logger.info(f"🗑️ Deleted empty Drive folder {sub_folder_id}")
                except Exception as del_err:
                    logger.warning(f"⚠️ Could not delete Drive folder {sub_folder_id}: {del_err}")
        finally:
            # Cleanup disk temp directory
            await asyncio.to_thread(self._cleanup_dir, chapter_dir)
            self.current_task = None


# Singleton instance
_queue_worker: Optional[QueueWorker] = None


def get_queue_worker() -> QueueWorker:
    global _queue_worker
    if _queue_worker is None:
        _queue_worker = QueueWorker()
    return _queue_worker
