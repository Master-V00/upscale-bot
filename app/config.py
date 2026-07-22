"""
Configuration loader for Upscale Bot
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Settings:
    def __init__(self):
        # Supabase
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_KEY")
        
        # Discord
        self.discord_token = os.getenv("DISCORD_TOKEN")
        self.discord_client_id = os.getenv("DISCORD_CLIENT_ID")
        self.discord_api_key = os.getenv("DISCORD_API_KEY")
        self.admin_user_id = os.getenv("ADMIN_USER_ID")
        
        # Google Drive
        self.google_client_id = os.getenv("GOOGLE_CLIENT_ID")
        self.google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        self.google_refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
        self.google_output_folder_id = os.getenv("GOOGLE_OUTPUT_FOLDER_ID")
        
        # API
        self.api_host = os.getenv("API_HOST", "0.0.0.0")
        self.api_port = int(os.getenv("API_PORT", 10100))
        self.debug = os.getenv("DEBUG", "false").lower() == "true"
        
        # Upscaling
        self.upscale_model = os.getenv("UPSCALE_MODEL", "RealESRGAN_x4plus_anime_6B")
        self.upscale_scale = int(os.getenv("UPSCALE_SCALE", 4))
        self.upscale_tile = int(os.getenv("UPSCALE_TILE", 0))
        self.upscale_format = os.getenv("UPSCALE_FORMAT", "png")
        self.compression_quality = int(os.getenv("COMPRESSION_QUALITY", 95))
        
        # Paths
        self.temp_dir = Path(os.getenv("TEMP_DIR", "./temp"))
        self.output_dir = Path(os.getenv("OUTPUT_DIR", "./output"))
        self.logs_dir = Path(os.getenv("LOGS_DIR", "./logs"))
        self.weights_dir = Path(os.getenv("WEIGHTS_DIR", "./weights"))
        
        # Cleanup
        self.cleanup_interval = int(os.getenv("CLEANUP_INTERVAL_SECONDS", 60))
        self.expiration_time = int(os.getenv("EXPIRATION_TIME_MINUTES", 30))


settings = Settings()


def get_settings() -> Settings:
    return settings