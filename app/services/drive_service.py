"""
Google Drive service for downloading and uploading files
"""
import os
import json
import aiohttp
from pathlib import Path
from typing import Optional, List, Tuple
from loguru import logger

from app.config import settings


class DriveService:
    """Handles Google Drive operations using OAuth2"""
    
    def __init__(self):
        self.client_id = settings.google_client_id
        self.client_secret = settings.google_client_secret
        self.refresh_token = settings.google_refresh_token
        self.output_folder_id = settings.google_output_folder_id
        self.access_token: Optional[str] = None
        
    async def _get_access_token(self) -> str:
        """Get or refresh access token"""
        if self.access_token:
            return self.access_token
            
        async with aiohttp.ClientSession() as session:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token"
            }
            
            async with session.post("https://oauth2.googleapis.com/token", data=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    self.access_token = result["access_token"]
                    return self.access_token
                else:
                    error = await resp.text()
                    raise Exception(f"Failed to get access token: {error}")
    
    def _extract_folder_id(self, url: str) -> Optional[str]:
        """Extract folder ID from Google Drive URL"""
        # Handle different URL formats
        if "folders/" in url:
            return url.split("folders/")[1].split("?")[0]
        elif "/d/" in url:
            return url.split("/d/")[1].split("?")[0]
        return None
    
    def _extract_file_id(self, url: str) -> Optional[str]:
        """Extract file ID from Google Drive URL"""
        if "/d/" in url:
            return url.split("/d/")[1].split("?")[0]
        return None
    
    async def list_folder_contents(self, folder_url: str) -> List[dict]:
        """List all files in a Google Drive folder"""
        folder_id = self._extract_folder_id(folder_url)
        if not folder_id:
            raise ValueError("Invalid folder URL")
        
        access_token = await self._get_access_token()
        files = []
        page_token = None
        
        async with aiohttp.ClientSession() as session:
            while True:
                params = {
                    "q": f"'{folder_id}' in parents and trashed = false and (mimeType contains 'image/' or mimeType contains 'application/zip' or mimeType contains 'application/x-rar-compressed')",
                    "fields": "nextPageToken, files(id, name, mimeType, size)",
                }
                
                if page_token:
                    params["pageToken"] = page_token
                
                headers = {"Authorization": f"Bearer {access_token}"}
                
                async with session.get(
                    "https://www.googleapis.com/drive/v3/files",
                    params=params,
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        files.extend(result.get("files", []))
                        page_token = result.get("nextPageToken")
                        if not page_token:
                            break
                    else:
                        error = await resp.text()
                        raise Exception(f"Failed to list folder: {error}")
        
        return files
    
    async def download_file(self, file_id: str, output_path: Path) -> Path:
        """Download a file from Google Drive"""
        access_token = await self._get_access_token()
        
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {access_token}"}
            
            # Get file info first
            async with session.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                params={"fields": "name, mimeType"},
                headers=headers
            ) as resp:
                if resp.status == 200:
                    file_info = await resp.json()
                    filename = file_info["name"]
                else:
                    raise Exception(f"Failed to get file info: {resp.status}")
            
            # Download the file
            async with session.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                params={"alt": "media"},
                headers=headers
            ) as resp:
                if resp.status == 200:
                    output_file = output_path / filename
                    with open(output_file, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)
                    return output_file
                else:
                    raise Exception(f"Failed to download file: {resp.status}")
    
    async def upload_file(self, file_path: Path, folder_id: Optional[str] = None) -> str:
        """Upload a file to Google Drive and return the shareable link"""
        import json
        import io
        access_token = await self._get_access_token()
        
        # Use default output folder if no folder_id provided
        if not folder_id:
            folder_id = self.output_folder_id
        
        file_metadata = {
            "name": file_path.name,
        }
        
        if folder_id:
            file_metadata["parents"] = [folder_id]
        
        # Build multipart request body
        boundary = "----UpscaleBotBoundary"
        metadata_json = json.dumps(file_metadata)
        file_bytes = file_path.read_bytes()
        
        # Detect content type
        content_type_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".bmp": "image/bmp"}
        file_content_type = content_type_map.get(file_path.suffix.lower(), "application/octet-stream")
        
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{metadata_json}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {file_content_type}\r\n\r\n"
        ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
        
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": f"multipart/related; boundary={boundary}"
            }
            
            # Upload with metadata
            async with session.post(
                "https://www.googleapis.com/upload/drive/v3/files",
                params={"uploadType": "multipart"},
                headers=headers,
                data=body
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    file_id = result["id"]
                else:
                    error = await resp.text()
                    raise Exception(f"Failed to upload file: {resp.status} - {error}")
            
            # Make file shareable
            permission = {"type": "anyone", "role": "reader"}
            async with session.post(
                f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
                json=permission,
                headers=headers
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to make file shareable: {resp.status}")
            
            # Get shareable link
            async with session.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                params={"fields": "webViewLink, webContentLink"},
                headers=headers
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("webContentLink", result.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view"))
                else:
                    return f"https://drive.google.com/file/d/{file_id}/view"
    
    async def create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        """Create a folder in Google Drive and return its ID"""
        access_token = await self._get_access_token()
        
        if not parent_id:
            parent_id = self.output_folder_id
        
        folder_metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id]
        }
        
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {access_token}"}
            
            async with session.post(
                "https://www.googleapis.com/drive/v3/files",
                json=folder_metadata,
                headers=headers
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    folder_id = result["id"]
                    logger.info(f"📁 Created folder: {name} ({folder_id})")
                    return folder_id
                else:
                    error = await resp.text()
                    raise Exception(f"Failed to create folder: {error}")
    
    async def make_folder_public(self, folder_id: str) -> bool:
        """Make a folder publicly accessible"""
        access_token = await self._get_access_token()
        
        permission = {"type": "anyone", "role": "reader"}
        
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {access_token}"}
            
            async with session.post(
                f"https://www.googleapis.com/drive/v3/files/{folder_id}/permissions",
                json=permission,
                headers=headers
            ) as resp:
                if resp.status == 200:
                    logger.info(f"🔓 Made folder public: {folder_id}")
                    return True
                else:
                    logger.warning(f"⚠️ Failed to make folder public: {resp.status}")
                    return False
    
    async def delete_file(self, file_id: str) -> bool:
        """Delete a file or folder from Google Drive"""
        access_token = await self._get_access_token()
        
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {access_token}"}
            
            async with session.delete(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                headers=headers
            ) as resp:
                if resp.status == 204:
                    logger.info(f"🗑️ Deleted: {file_id}")
                    return True
                else:
                    logger.warning(f"⚠️ Failed to delete {file_id}: {resp.status}")
                    return False
    
    async def get_file_info(self, file_id: str) -> dict:
        """Get metadata for a single file from Google Drive"""
        access_token = await self._get_access_token()
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {access_token}"}
            async with session.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                params={"fields": "id, name, mimeType, size"},
                headers=headers
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    error = await resp.text()
                    raise Exception(f"Failed to get file info: {resp.status} - {error}")

    async def list_files_in_folder(self, folder_id: str) -> List[str]:
        """List all file names in a Google Drive folder"""
        access_token = await self._get_access_token()
        
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {access_token}"}
            params = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "files(name)",
            }
            
            async with session.get(
                "https://www.googleapis.com/drive/v3/files",
                params=params,
                headers=headers
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return [f["name"] for f in result.get("files", [])]
                else:
                    logger.warning(f"⚠️ Failed to list files in folder: {resp.status}")
                    return []


# Singleton instance
_drive_service: Optional[DriveService] = None


def get_drive_service() -> DriveService:
    global _drive_service
    if _drive_service is None:
        _drive_service = DriveService()
    return _drive_service