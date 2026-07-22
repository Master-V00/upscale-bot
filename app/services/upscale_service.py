"""
Upscaling service using Real-ESRGAN
"""
import logging
import os
import sys
import types
import subprocess
from pathlib import Path
from typing import Optional
from loguru import logger
from PIL import Image

# Suppress Real-ESRGAN verbose tile logging
logging.getLogger("realesrgan").setLevel(logging.WARNING)
logging.getLogger("basicsr").setLevel(logging.WARNING)

from app.config import settings

# Monkey-patch: torchvision.transforms.functional_tensor was removed in 0.17+
# realesrgan still imports it, so we provide a compatibility shim
if "torchvision.transforms.functional_tensor" not in sys.modules:
    try:
        import torchvision.transforms.functional_tensor as _ft
    except ImportError:
        _dummy = types.ModuleType("torchvision.transforms.functional_tensor")
        try:
            from torchvision.transforms.functional import rgb_to_grayscale
            _dummy.rgb_to_grayscale = rgb_to_grayscale
        except ImportError:
            _dummy.rgb_to_grayscale = None
        sys.modules["torchvision.transforms.functional_tensor"] = _dummy

# Monkey-patch: suppress DecompressionBombWarning globally
import PIL.Image
PIL.Image.MAX_IMAGE_PIXELS = 500_000_000

# Monkey-patch: torch.load to use weights_only=True by default
import torch
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs.setdefault('weights_only', True)
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load


class UpscaleService:
    """Handles image upscaling using Real-ESRGAN"""
    
    def __init__(self):
        self.model_name = settings.upscale_model
        self.scale = settings.upscale_scale
        self.tile = settings.upscale_tile
        self.output_format = settings.upscale_format
        self.weights_dir = settings.weights_dir
        self._upsampler = None
        self._device = None
        
    def _get_device(self):
        """Get the best available device (CUDA GPU preferred) with capability profiling"""
        if self._device is not None:
            return self._device
            
        import torch
        if torch.cuda.is_available():
            self._device = torch.device('cuda')
            props = torch.cuda.get_device_properties(0)
            gpu_name = props.name
            gpu_mem_gb = props.total_memory / (1024**3)
            compute_cap = f"{props.major}.{props.minor}"
            multiprocessor_count = props.multi_processor_count
            
            logger.info(f"🚀 GPU: {gpu_name}")
            logger.info(f"   VRAM: {gpu_mem_gb:.1f} GB | Compute: {compute_cap} | SMs: {multiprocessor_count}")
            
            if self.tile == 0:
                if gpu_mem_gb >= 12:
                    self.tile = 1024
                elif gpu_mem_gb >= 8:
                    self.tile = 512
                else:
                    self.tile = 256
                logger.info(f"   Auto tile size: {self.tile} (based on VRAM)")
            else:
                logger.info(f"   Tile size: {self.tile} (from config)")
        else:
            self._device = torch.device('cpu')
            logger.warning("⚠️ CUDA not available, using CPU (SLOW!)")
            
        return self._device
    
    def _get_model_path(self) -> Path:
        """Get the model file path"""
        model_file = f"{self.model_name}.pth"
        return self.weights_dir / model_file
    
    def _ensure_model_exists(self):
        """Download model if it doesn't exist"""
        model_path = self._get_model_path()
        
        if not model_path.exists():
            logger.info(f"Downloading model {self.model_name}...")
            
            model_urls = {
                "RealESRGAN_x4plus_anime_6B": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
                "realesr-animevideov3": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth",
                "RealESRGAN_x4plus": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
            }
            
            url = model_urls.get(self.model_name)
            if not url:
                raise ValueError(f"Unknown model: {self.model_name}")
            
            try:
                subprocess.run(
                    ["wget", "-O", str(model_path), url],
                    check=True,
                    capture_output=True
                )
            except FileNotFoundError:
                subprocess.run(
                    ["curl", "-L", "-o", str(model_path), url],
                    check=True,
                    capture_output=True
                )
            
            logger.info(f"Model downloaded to {model_path}")
    
    def _get_upsampler(self):
        """Get or create the upsampler (singleton - loaded once)"""
        if self._upsampler is not None:
            return self._upsampler
            
        import torch
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer
        
        self._ensure_model_exists()
        model_path = self._get_model_path()
        device = self._get_device()
        
        # Create model based on architecture
        if self.model_name == "realesr-animevideov3":
            # SRVGGNetCompact - much faster, fewer parameters
            from basicsr.archs.srvgg_arch import SRVGGNetCompact
            model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=16, upscale=4, act_type='prelu')
        elif self.model_name == "RealESRGAN_x4plus_anime_6B":
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=6, num_grow_ch=32, scale=4)
        else:
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
        
        # Determine half precision (only works on GPU)
        half = device.type == 'cuda'
        
        # Create upsampler with explicit device
        self._upsampler = RealESRGANer(
            scale=self.scale,
            model_path=str(model_path),
            model=model,
            tile=self.tile,
            half=half,
            device=device
        )
        
        logger.info(f"✅ Upsampler ready: model={self.model_name}, tile={self.tile}, half={half}")
        return self._upsampler
    
    def upscale_image(self, input_path: Path, output_path: Path) -> Path:
        """Upscale a single image using Real-ESRGAN"""
        import numpy as np
        
        upsampler = self._get_upsampler()
        
        image = Image.open(input_path)
        img_array = np.array(image)
        output, _ = upsampler.enhance(img_array, outscale=self.scale)
        
        result = Image.fromarray(output)
        result.save(output_path)
        
        logger.info(f"Upscale completed: {output_path}")
        return output_path
    
    def upscale_image_pil(self, image: Image.Image) -> Image.Image:
        """Upscale a PIL Image directly"""
        import torch
        import numpy as np
        
        upsampler = self._get_upsampler()
        
        img_array = np.array(image)
        
        # Suppress stdout + stderr to hide tile progress logs from realesrgan
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')
        try:
            output, _ = upsampler.enhance(img_array, outscale=self.scale)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            output, _ = upsampler.enhance(img_array, outscale=self.scale)
        finally:
            sys.stdout.close()
            sys.stderr.close()
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        
        return Image.fromarray(output)
    
    def get_gpu_info(self) -> dict:
        """Get GPU information"""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                parts = result.stdout.strip().split(", ")
                return {
                    "name": parts[0],
                    "memory_total": int(parts[1]),
                    "memory_free": int(parts[2]),
                    "utilization": int(parts[3]),
                    "available": True
                }
        except Exception as e:
            logger.warning(f"Failed to get GPU info: {e}")
        
        return {"available": False}


# Singleton instance
_upscale_service: Optional[UpscaleService] = None


def get_upscale_service() -> UpscaleService:
    global _upscale_service
    if _upscale_service is None:
        _upscale_service = UpscaleService()
    return _upscale_service