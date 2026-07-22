"""
Image processing pipeline: Split long images, upscale, merge, compress
Stage-first processing: all images through each stage before moving to next
All files stored on disk (not in memory)
Parallel processing for split, merge, and compress
"""
import gc
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from math import ceil
from pathlib import Path
from typing import Optional, List, Dict
from loguru import logger
from PIL import Image
import numpy as np

from app.config import settings

# Increase PIL decompression bomb limit for large webtoon images
Image.MAX_IMAGE_PIXELS = 5_000_000_000

_CPU_COUNT = os.cpu_count() or 4


def natural_sort_key(text: str):
    """Natural sort key for strings with numbers (e.g., '1.jpg', '2.jpg', '10.jpg')"""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', text)]


class ImagePipeline:
    """Stage-first pipeline with safe memory management"""
    
    def __init__(self):
        self.preferred_height = 3000
        self.tolerance = 500
        self.split_workers = _CPU_COUNT
        self.merge_workers = _CPU_COUNT
        self.compress_workers = _CPU_COUNT
        
    @staticmethod
    def _pil_format_for_ext(ext: str) -> str:
        clean = ext.lower().lstrip(".")
        format_map = {
            "jpg": "JPEG", "jpeg": "JPEG",
            "png": "PNG",
            "webp": "WEBP",
            "bmp": "BMP",
            "tiff": "TIFF", "tif": "TIFF",
            "gif": "GIF",
        }
        return format_map.get(clean, "PNG")
    
    @staticmethod
    def _ensure_mode_compatible(image: Image.Image, pil_format: str) -> Image.Image:
        if pil_format in ("JPEG", "BMP"):
            if image.mode in ("RGBA", "P", "LA", "1"):
                return image.convert("RGB")
        elif pil_format in ("WEBP",):
            if image.mode == "P":
                if "transparency" in image.info:
                    return image.convert("RGBA")
                return image.convert("RGB")
        return image
    
    def calculate_best_splits(self, image_path: Path) -> List[int]:
        """Calculate optimal split points using luminance percentile + contiguous region detection"""
        image = Image.open(image_path)
        width, height = image.size
        
        if height <= self.preferred_height:
            image.close()
            return []
        
        num_strips = ceil(height / self.preferred_height) - 1
        actual_height = height // (num_strips + 1)
        splits = [actual_height * i for i in range(1, num_strips + 1)]
        
        tolerance = min(self.tolerance, actual_height // 2)
        if tolerance == 0:
            image.close()
            return splits
        
        image_luminance = image.convert("L")
        luminance = np.array(image_luminance)
        image_luminance.close()
        image.close()
        
        for index, split in enumerate(splits):
            search_start = max(0, split - tolerance)
            search_end = min(height, split + tolerance)
            
            search_range = luminance[search_start:search_end, :]
            horizontal_changes = np.sum(np.square(np.diff(search_range, axis=1)), axis=1)
            
            if len(horizontal_changes) == 0:
                continue
            
            threshold = np.percentile(horizontal_changes, 10)
            horizontal_changes_bitmap = (horizontal_changes > threshold).astype(np.uint8)
            
            small_value_indices = np.where(horizontal_changes_bitmap == 0)[0]
            
            if len(small_value_indices) > 0:
                diff_indices = np.diff(small_value_indices)
                split_points = np.where(diff_indices > 1)[0]
                split_points = np.concatenate(([0], split_points + 1, [len(small_value_indices)]))
                
                best_region_length = 0
                best_region_start = 0
                for i in range(len(split_points) - 1):
                    start = split_points[i]
                    end = split_points[i + 1]
                    current_length = end - start
                    if current_length > best_region_length:
                        best_region_length = current_length
                        best_region_start = start
                
                best_split = small_value_indices[best_region_start + (best_region_length // 2)]
            else:
                best_split = np.argmin(horizontal_changes)
            
            splits[index] = search_start + best_split
        
        return splits
    
    def split_image_to_disk(self, image_path: Path, parts_dir: Path) -> List[Path]:
        """Split one image into parts on disk, preserving original DPI"""
        splits = self.calculate_best_splits(image_path)
        
        if not splits:
            import shutil
            dest = parts_dir / image_path.name
            if dest != image_path:
                shutil.copy2(image_path, dest)
            return [dest]
        
        image = Image.open(image_path)
        original_dpi = image.info.get("dpi", (96, 96))
        part_paths = []
        y1 = 0
        part_idx = 0
        
        for y2 in splits:
            part = image.crop((0, y1, image.width, y2))
            part_name = f"{image_path.stem}_part{part_idx:03d}{image_path.suffix}"
            part_path = parts_dir / part_name
            part.save(part_path, dpi=original_dpi)
            part.close()
            part_paths.append(part_path)
            y1 = y2
            part_idx += 1
        
        part = image.crop((0, y1, image.width, image.height))
        part_name = f"{image_path.stem}_part{part_idx:03d}{image_path.suffix}"
        part_path = parts_dir / part_name
        part.save(part_path, dpi=original_dpi)
        part.close()
        image.close()
        
        return part_paths
    
    def _split_single(self, img_file: Path, parts_dir: Path):
        parts = self.split_image_to_disk(img_file, parts_dir)
        return img_file.name, parts
    
    def split_all_images(self, input_dir: Path, parts_dir: Path) -> List[Path]:
        """STAGE 1: Split ALL images in parallel"""
        image_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.gif'}
        image_files = sorted([f for f in input_dir.iterdir() if f.suffix.lower() in image_extensions])
        
        all_part_paths = []
        
        with ThreadPoolExecutor(max_workers=self.split_workers) as executor:
            futures = {executor.submit(self._split_single, img_file, parts_dir): img_file for img_file in image_files}
            
            for future in as_completed(futures):
                name, parts = future.result()
                all_part_paths.extend(parts)
                logger.info(f"  ✂️ Split {name} → {len(parts)} parts")
        
        all_part_paths.sort()
        logger.info(f"  📂 Total parts: {len(all_part_paths)}")
        return all_part_paths
    

    
    def upscale_all_parts(self, part_paths: List[Path], upscaled_dir: Path, upscale_func) -> List[Path]:
        """STAGE 2: Upscale ALL parts (sequential - GPU bottleneck), preserving DPI"""
        upscaled_paths = []
        for i, part_path in enumerate(part_paths):
            logger.info(f"  🎨 Upscaling part {i+1}/{len(part_paths)}: {part_path.name}")
            
            image = Image.open(part_path)
            original_dpi = image.info.get("dpi", (96, 96))
            upscaled = upscale_func(image)
            image.close()
            
            out_path = upscaled_dir / part_path.name
            upscaled.save(out_path, dpi=original_dpi)
            upscaled.close()
            upscaled_paths.append(out_path)
        
        return upscaled_paths
    
    def _merge_single(self, stem: str, parts: List[Path], output_dir: Path):
        """Merge one image's parts - JPEG with chunking for oversized images, preserving DPI"""
        try:
            parts.sort(key=lambda p: p.name)
            
            first = Image.open(parts[0])
            part_width = first.width
            part_heights = [first.height]
            modes = [first.mode]
            original_dpi = first.info.get("dpi", (96, 96))
            first.close()
            
            for p in parts[1:]:
                img = Image.open(p)
                part_heights.append(img.height)
                modes.append(img.mode)
                img.close()
            
            total_height = sum(part_heights)
            
            mode_priority = ["1", "L", "P", "LA", "RGB", "RGBA", "CMYK", "YCbCr"]
            best_mode = max(modes, key=lambda m: mode_priority.index(m) if m in mode_priority else 0)
            
            if best_mode in ("RGBA", "P", "LA", "1"):
                best_mode = "RGB"
            
            merged = Image.new(best_mode, (part_width, total_height))
            y_offset = 0
            
            for p in parts:
                img = Image.open(p)
                if img.mode != best_mode:
                    converted = img.convert(best_mode)
                    img.close()
                    img = converted
                merged.paste(img, (0, y_offset))
                y_offset += img.size[1]
                img.close()
            
            results = []
            JPEG_MAX = 65500
            
            if part_width <= JPEG_MAX and total_height <= JPEG_MAX:
                out_path = output_dir / f"{stem}.jpg"
                merged.save(out_path, format="JPEG", quality=95, optimize=True, dpi=original_dpi)
                results.append((stem, len(parts), out_path.name))
            else:
                y = 0
                chunk_idx = 1
                while y < total_height:
                    chunk_height = min(JPEG_MAX, total_height - y)
                    chunk = merged.crop((0, y, part_width, y + chunk_height))
                    chunk_name = f"{stem}_p{chunk_idx:02d}.jpg"
                    chunk_path = output_dir / chunk_name
                    chunk.save(chunk_path, format="JPEG", quality=95, optimize=True, dpi=original_dpi)
                    chunk.close()
                    results.append((stem, len(parts), chunk_name))
                    y += chunk_height
                    chunk_idx += 1
                logger.info(f"    [MERGE] {stem}: {part_width}x{total_height} exceeds JPEG limit, split into {chunk_idx - 1} chunks")
            
            merged.close()
            del merged
            gc.collect()
            
            return results
            
        except Exception as e:
            import traceback
            logger.error(f"    [MERGE] FAILED for {stem}: {type(e).__name__}: {e}")
            logger.error(f"    [MERGE] Traceback: {traceback.format_exc()}")
            raise
    
    def merge_parts_from_disk(self, upscaled_paths: List[Path], original_images: List[Path], output_dir: Path) -> List[Path]:
        """STAGE 3: Merge parts back in parallel"""
        stem_order = [img_path.stem for img_path in original_images]
        
        stem_parts: Dict[str, List[Path]] = {}
        for part_path in upscaled_paths:
            for stem in stem_order:
                if part_path.name.startswith(stem):
                    if stem not in stem_parts:
                        stem_parts[stem] = []
                    stem_parts[stem].append(part_path)
                    break
        
        merged_dir = output_dir
        merged_dir.mkdir(parents=True, exist_ok=True)
        merged_paths = []
        
        def _do_merge(stem, parts):
            return self._merge_single(stem, parts, merged_dir)
        
        with ThreadPoolExecutor(max_workers=self.merge_workers) as executor:
            futures = {executor.submit(_do_merge, stem, parts): stem for stem, parts in stem_parts.items()}
            
            for future in as_completed(futures):
                results = future.result()
                stem = results[0][0]
                count = results[0][1]
                for _, _, fname in results:
                    merged_paths.append(merged_dir / fname)
                if len(results) == 1:
                    logger.info(f"  🔗 Merged {stem}: {count} parts → {results[0][2]}")
                else:
                    logger.info(f"  🔗 Merged {stem}: {count} parts → {len(results)} files")
        
        stem_chunk_map = {}
        for p in merged_paths:
            for stem in stem_order:
                if p.name.startswith(stem):
                    if stem not in stem_chunk_map:
                        stem_chunk_map[stem] = []
                    stem_chunk_map[stem].append(p)
                    break
        
        for stem in stem_chunk_map:
            stem_chunk_map[stem].sort(key=lambda p: natural_sort_key(p.name))
        
        sorted_paths = []
        for stem in stem_order:
            if stem in stem_chunk_map:
                sorted_paths.extend(stem_chunk_map[stem])
        
        renamed_paths = []
        pad = len(str(len(sorted_paths)))
        for i, path in enumerate(sorted_paths, 1):
            new_name = f"{i:0{pad}d}.jpg"
            new_path = merged_dir / new_name
            path.rename(new_path)
            renamed_paths.append(new_path)
        
        if len(renamed_paths) > len(original_images):
            logger.info(f"  🔗 Renamed: {len(renamed_paths)} files ({renamed_paths[0].name} - {renamed_paths[-1].name})")
        
        return renamed_paths
    
    def _compress_single(self, img_path: Path, quality: int):
        """Compress one image - JPEG only, already compressed in merge with quality"""
        try:
            return img_path.name, quality
        except Exception as e:
            import traceback
            logger.error(f"    [COMPRESS] FAILED for {img_path.name}: {type(e).__name__}: {e}")
            logger.error(f"    [COMPRESS] Traceback: {traceback.format_exc()}")
            raise
    
    def compress_images_on_disk(self, image_paths: List[Path], output_dir: Path, quality: int = None) -> List[Path]:
        """STAGE 4: Compress ALL images in parallel"""
        if quality is None:
            try:
                from app.services.supabase_service import get_supabase_service
                supabase = get_supabase_service()
                quality = supabase.get_setting_int("compression_quality", 95)
            except Exception:
                quality = settings.compression_quality
        
        compressed_paths = []
        
        with ThreadPoolExecutor(max_workers=self.compress_workers) as executor:
            futures = {executor.submit(self._compress_single, img_path, quality): img_path for img_path in image_paths}
            
            for future in as_completed(futures):
                name, q = future.result()
                compressed_paths.append(output_dir / name)
                logger.info(f"  🗜️ Compressed: {name} (quality={q})")
        
        return compressed_paths

    def pre_compress_images(self, input_dir: Path, quality: int = 85, max_dimension: int = 3000) -> List[Path]:
        """
        Pre-compress images in place to reduce file sizes before splitting.
        Original files are replaced with compressed JPEG versions.
        """
        image_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.gif'}
        image_files = sorted([f for f in input_dir.iterdir() if f.suffix.lower() in image_extensions])

        if not image_files:
            logger.info("  ℹ️ No images to pre-compress")
            return []

        logger.info(f"  🗜️ Pre-compressing {len(image_files)} images (quality={quality}, max_dim={max_dimension})")

        compressed_paths = []

        def _pre_compress_single(img_path: Path) -> Path:
            try:
                img = Image.open(img_path)
                original_size = img_path.stat().st_size
                original_width, original_height = img.size

                # Resize if too large (maintains aspect ratio)
                if original_width > max_dimension or original_height > max_dimension:
                    img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                    logger.info(f"    📏 Resized {img_path.name}: {original_width}x{original_height} → {img.width}x{img.height}")

                # Convert RGBA/P to RGB for JPEG
                if img.mode in ('RGBA', 'LA', 'P'):
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    if img.mode in ('RGBA', 'LA'):
                        bg.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                        img = bg
                    else:
                        img = img.convert('RGB')

                # Save as compressed JPEG to temp file, then replace original
                temp_path = input_dir / f".{img_path.name}.tmp.jpg"
                img.save(temp_path, format='JPEG', quality=quality, optimize=True, dpi=(96, 96))
                img.close()

                out_path = img_path.with_suffix('.jpg')
                if img_path != out_path:
                    img_path.unlink()
                temp_path.rename(out_path)

                compressed_size = out_path.stat().st_size
                reduction = (1 - compressed_size / original_size) * 100
                logger.info(f"    🗜️ {img_path.name}: {original_size/1024:.0f}KB → {compressed_size/1024:.0f}KB ({reduction:.1f}% reduction)")

                return out_path
            except Exception as e:
                import traceback
                logger.error(f"    [PRE-COMPRESS] FAILED for {img_path.name}: {type(e).__name__}: {e}")
                logger.error(f"    [PRE-COMPRESS] Traceback: {traceback.format_exc()}")
                raise

        with ThreadPoolExecutor(max_workers=self.compress_workers) as executor:
            futures = {executor.submit(_pre_compress_single, img_path): img_path for img_path in image_files}
            for future in as_completed(futures):
                compressed_paths.append(future.result())

        compressed_paths.sort(key=lambda p: natural_sort_key(p.name))
        logger.info(f"  ✅ Pre-compression complete: {len(compressed_paths)} images")
        return compressed_paths


# Singleton instance
_image_pipeline: Optional[ImagePipeline] = None


def get_image_pipeline() -> ImagePipeline:
    global _image_pipeline
    if _image_pipeline is None:
        _image_pipeline = ImagePipeline()
    return _image_pipeline
