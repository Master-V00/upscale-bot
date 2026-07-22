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
import requests
import torch
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
# We do NOT set weights_only=True because RealESRGAN model files
# may use pickle formats incompatible with weights_only
import torch
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs.setdefault('weights_only', False)
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load


# ── RRDBNet key conversion (old Sequential format → current named format) ──
_RRDB_TOP_MAP = {
    0: "conv_first",
    3: "conv_up1",
    6: "conv_up2",
    8: "conv_hr",
    10: "conv_last",
}


def _convert_rrdbnet_keys(state_dict: dict) -> dict:
    """Convert old Sequential-based RRDBNet keys to the current named format.

    Old format:  model.0.weight
                 model.1.sub.{i}.RDB{j}.conv{k}.0.weight
                 model.3.weight, model.6.weight, ...
    New format:  conv_first.weight
                 body.{i}.rdb{j}.conv{k}.weight
                 conv_body.weight, conv_up1.weight, ...
    """
    import re
    logger.debug(f"_convert_rrdbnet_keys: input keys ({len(state_dict)}): {list(state_dict.keys())[:10]}...")
    new_dict = {}
    rdb_pattern = re.compile(r"^model\.1\.sub\.(\d+)\.RDB(\d+)\.conv(\d+)\.0\.(.+)$")
    body_extra_pattern = re.compile(r"^model\.1\.sub\.(\d+)\.(.+)$")
    top_pattern = re.compile(r"^model\.(\d+)\.(.+)$")

    for key, value in state_dict.items():
        # Try RRDB body block pattern first (e.g. model.1.sub.0.RDB1.conv1.0.weight)
        m = rdb_pattern.match(key)
        if m:
            block_idx = int(m.group(1))   # RRDB block index (0-22)
            rdb_idx = int(m.group(2))     # RDB 1, 2, or 3
            conv_idx = int(m.group(3))    # conv 1-5
            param = m.group(4)            # weight or bias
            new_key = f"body.{block_idx}.rdb{rdb_idx}.conv{conv_idx}.{param}"
            new_dict[new_key] = value
            continue

        # Try body extra layers (e.g. model.1.sub.23.weight → conv_body.weight)
        m = body_extra_pattern.match(key)
        if m:
            sub_idx = int(m.group(1))
            rest = m.group(2)
            if sub_idx == 23:
                # 24th element inside body Sequential = conv_body (Conv2d)
                new_key = f"conv_body.{rest}"
                new_dict[new_key] = value
            else:
                logger.debug(f"  Skipping unknown body sub key: {key}")
            continue

        # Try top-level layer pattern (e.g. model.0.weight, model.3.weight, ...)
        m = top_pattern.match(key)
        if m:
            idx = int(m.group(1))
            param = m.group(2)
            layer_name = _RRDB_TOP_MAP.get(idx)
            if layer_name:
                new_key = f"{layer_name}.{param}"
                new_dict[new_key] = value
            else:
                # Skip non-param layers (activations, interpolate, etc.)
                logger.debug(f"  Skipping unknown top-level key: {key}")
            continue

        # Pass through any key we don't recognise
        new_dict[key] = value

    logger.debug(f"_convert_rrdbnet_keys: output keys ({len(new_dict)}): {list(new_dict.keys())[:10]}...")
    return new_dict


class UpscaleService:
    """Handles image upscaling using Real-ESRGAN"""
    
    MODEL_URLS = {
        "RealESRGAN_x4plus_anime_6B": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
        "realesr-animevideov3": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth",
        "RealESRGAN_x4plus": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        "4x-AnimeSharp": "https://huggingface.co/Kim2091/AnimeSharp/resolve/main/4x-AnimeSharp.pth",
        "4x-UltraSharp": "https://huggingface.co/lokCX/4x-Ultrasharp/resolve/main/4x-UltraSharp.pth",
        "realesr-general-x4v3": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
        "realesr-general-wdn-x4v3": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-wdn-x4v3.pth",
    }
    
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
        """Download model if it doesn't exist or is corrupted"""
        model_path = self._get_model_path()

        # Check if file exists and has valid size
        if model_path.exists():
            file_size = model_path.stat().st_size
            if file_size < 1_000_000:
                logger.warning(f"  ⚠️ Model file too small ({file_size} bytes), re-downloading...")
                model_path.unlink()
            else:
                try:
                    ckpt = torch.load(model_path, map_location='cpu', weights_only=False)
                    if 'params' not in ckpt and 'params_ema' not in ckpt:
                        logger.warning(f"  ⚠️ Model file missing 'params' key, re-downloading...")
                        model_path.unlink()
                    else:
                        # Validate keys inside params/params_ema
                        params_key = 'params' if 'params' in ckpt else 'params_ema'
                        inner_keys = list(ckpt[params_key].keys())
                        if inner_keys and inner_keys[0].startswith('model.'):
                            logger.warning(f"  ⚠️ Model file has old key format, re-downloading for conversion...")
                            model_path.unlink()
                        else:
                            return
                except Exception as e:
                    logger.warning(f"  ⚠️ Model file corrupted ({e}), re-downloading...")
                    model_path.unlink()

        # Download the model
        logger.info(f"Downloading model {self.model_name}...")

        url = self.MODEL_URLS.get(self.model_name)
        if not url:
            raise ValueError(f"Unknown model: {self.model_name}")

        # Download with requests for reliable error handling
        logger.info(f"  Downloading {url}...")
        try:
            resp = requests.get(url, stream=True, timeout=120)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(model_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
        except requests.RequestException as e:
            if model_path.exists():
                model_path.unlink()
            raise ValueError(f"Download failed for {self.model_name}: {e}") from e

        # Validate the downloaded file
        file_size = model_path.stat().st_size
        if file_size < 1_000_000:
            model_path.unlink()
            raise ValueError(
                f"Downloaded model file is too small ({file_size} bytes). "
                f"The server may have returned an error page instead of the model."
            )

        try:
            ckpt = torch.load(model_path, map_location='cpu', weights_only=False)

            if 'params' in ckpt or 'params_ema' in ckpt:
                pass  # Format expected by RealESRGANer
            elif any(k.endswith('.weight') for k in ckpt.keys()):
                # Likely a direct state_dict — check if it needs key conversion
                first_key = next(iter(ckpt.keys()))
                if first_key.startswith('model.'):
                    # Old RRDBNet format (Sequential-based keys) → convert to current format
                    logger.info(f"  Converting RRDBNet keys from old format to current format...")
                    ckpt = _convert_rrdbnet_keys(ckpt)
                ckpt = {'params': ckpt}
                torch.save(ckpt, model_path)
            else:
                model_path.unlink()
                raise ValueError(
                    f"Downloaded model file has unexpected keys (first 5: {list(ckpt.keys())[:5]}). "
                    f"The server may have returned an error page instead of the model."
                )
        except Exception as e:
            if model_path.exists():
                model_path.unlink()
            raise ValueError(
                f"Downloaded model file is corrupted or invalid: {e}"
            ) from e

        logger.info(f"Model downloaded and validated: {model_path} ({file_size / 1024 / 1024:.1f} MB)")

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
        elif self.model_name == "4x-AnimeSharp":
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
        elif self.model_name == "4x-UltraSharp":
            # UltraSharp uses RRDBNet 23 blocks like AnimeSharp
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
        elif self.model_name == "realesr-general-x4v3":
            # General model with denoise control - SRVGGNetCompact (32 conv layers)
            from basicsr.archs.srvgg_arch import SRVGGNetCompact
            model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=32, upscale=4, act_type='prelu')
        else:
            # Default: standard RealESRGAN (23 blocks)
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)

        # Determine half precision (only works on GPU)
        half = device.type == 'cuda'

        # Create upsampler with explicit device
        # realesr-general-x4v3 supports denoise via DNI (Deep Network Interpolation)
        # Requires TWO models: realesr-general-x4v3 + realesr-general-wdn-x4v3
        if self.model_name == "realesr-general-x4v3":
            # Get denoise strength from settings (0.0 = no denoise/text preserved, 1.0 = max denoise)
            # Default to 0.3 for balanced text preservation
            denoise_strength = getattr(settings, 'upscale_denoise_strength', 0.3)
            
            # Need BOTH model files for DNI
            model_path_main = self._get_model_path()
            model_path_wdn = model_path_main.parent / "realesr-general-wdn-x4v3.pth"
            
            # Download WDN model if not present
            if not model_path_wdn.exists():
                wdn_url = self.MODEL_URLS.get("realesr-general-wdn-x4v3")
                if wdn_url:
                    logger.info(f"  Downloading WDN model for denoise control...")
                    try:
                        resp = requests.get(wdn_url, stream=True, timeout=120)
                        resp.raise_for_status()
                        with open(model_path_wdn, "wb") as f:
                            for chunk in resp.iter_content(chunk_size=8192):
                                f.write(chunk)
                    except requests.RequestException as e:
                        logger.warning(f"  Failed to download WDN model: {e}")
            
            # Use DNI with both models if WDN exists
            if model_path_wdn.exists():
                model_path = [str(model_path_main), str(model_path_wdn)]
                dni_weight = [denoise_strength, 1 - denoise_strength]
                logger.info(f"  Using DNI denoise strength: {denoise_strength}")
            else:
                model_path = str(model_path_main)
                dni_weight = None
                logger.warning(f"  WDN model not found, using base model only (no denoise control)")
            
            self._upsampler = RealESRGANer(
                scale=self.scale,
                model_path=model_path,
                model=model,
                tile=self.tile,
                half=half,
                device=device,
                dni_weight=dni_weight
            )
        else:
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