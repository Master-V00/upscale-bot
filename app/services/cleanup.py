"""
Cleanup task for removing expired outputs from Google Drive
Run this periodically to maintain storage space
"""
import asyncio
from loguru import logger

from app.services.supabase_service import get_supabase_service
from app.services.drive_service import get_drive_service
from app.config import settings


class CleanupScheduler:
    """Scheduled cleanup for expired outputs"""

    def __init__(self, interval_seconds: int = None, expiration_minutes: int = None):
        self.interval_seconds = interval_seconds or settings.cleanup_interval
        self.expiration_minutes = expiration_minutes or settings.expiration_time

    def cleanup_expired_outputs(self):
        """Cleanup expired outputs from Google Drive"""
        supabase = get_supabase_service()
        drive = get_drive_service()
        
        try:
            logger.debug("Starting cleanup task...")

            expired_outputs = supabase.get_expired_outputs(self.expiration_minutes)

            if not expired_outputs:
                logger.debug("No expired outputs to clean")
                return

            logger.info(f"Found {len(expired_outputs)} expired outputs")

            deleted_count = 0
            skip_count = 0

            for output in expired_outputs:
                try:
                    request_id = output.get('id')
                    output_url = output.get('output_url', '')

                    # Extract folder ID from URL
                    folder_id = None
                    if output_url and 'folders/' in output_url:
                        folder_id = output_url.split('folders/')[-1].split('?')[0]
                    elif output_url and '/d/' in output_url:
                        folder_id = output_url.split('/d/')[-1].split('?')[0]

                    if folder_id:
                        import asyncio
                        loop = asyncio.new_event_loop()
                        try:
                            success = loop.run_until_complete(drive.delete_file(folder_id))
                            if success:
                                deleted_count += 1
                                logger.info(f"  🗑️ Deleted folder for request #{request_id}")
                            else:
                                # Folder might already be deleted - that's fine
                                skip_count += 1
                                logger.debug(f"  ⏭️ Folder for request #{request_id} already deleted or inaccessible")
                        finally:
                            loop.close()
                    else:
                        skip_count += 1

                    # Always mark as cleaned, even if folder was already gone
                    supabase.mark_output_cleaned(request_id)

                except Exception as e:
                    # Don't let errors in one request stop the whole cleanup
                    logger.debug(f"  ⏭️ Skipping request {output.get('id')}: {e}")
                    # Still mark as cleaned to avoid retrying
                    try:
                        supabase.mark_output_cleaned(output.get('id'))
                    except Exception:
                        pass

            logger.info(f"Cleanup completed - Deleted: {deleted_count}, Skipped: {skip_count}")

        except Exception as e:
            logger.error(f"Cleanup task failed: {e}")


async def start_cleanup_task_async(interval_seconds: int = None, expiration_minutes: int = None):
    """Start cleanup task as a periodic background coroutine."""
    scheduler = CleanupScheduler(interval_seconds, expiration_minutes)
    while True:
        scheduler.cleanup_expired_outputs()
        await asyncio.sleep(scheduler.interval_seconds)
