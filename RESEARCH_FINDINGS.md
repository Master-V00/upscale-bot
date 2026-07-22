# Research Findings: RealESRGAN Alternatives & Webtoon Upscaling Artifacts

---

## TOPIC 1: Alternative Upscaling Models for Anime/Webtoon Images

### Current Models — Summary Assessment

| Model | Architecture | Size | Strengths | Weaknesses |
|-------|------------|------|-----------|------------|
| `realesr-animevideov3` | SRVGGNetCompact (16 conv) | 2.4 MB | Fast, low VRAM, good for video | Too lightweight for detail/text; smears fine lines |
| `RealESRGAN_x4plus_anime_6B` | RRDBNet (6 blocks) | 17 MB | Best balance of quality/size for anime | Still bad at text; distorts speech bubbles; struggles with glow effects |

---

### Alternative Model #1: 4x-AnimeSharp (Kim2091) — ★ TOP RECOMMENDATION

| Property | Value |
|----------|-------|
| **Source** | https://huggingface.co/Kim2091/AnimeSharp (PTH) / https://civitai.com/models/1017531 (ZIP) |
| **Architecture** | ESRGAN (RRDBNet, 64nf 23nb) |
| **Scale** | 4x |
| **File size** | ~29.5 MB (PTH), ~64 MB (full) |
| **Speed** | Medium (similar to 6B anime model) |
| **Text handling** | **Good** — interpolation between 4x-UltraSharp and 4x-TextSharp-v0.5; explicitly designed to handle both anime AND text |
| **Speech bubbles / glow effects** | Better than RealESRGAN anime models; less artifact on smooth gradients |
| **Compatibility with RealESRGAN** | ✅ YES — standard ESRGAN `.pth` format; works with `basicsr`/`realesrgan` inference code. Load via `RRDBNet(num_block=23, ...)` in RealESRGANer |
| **License** | CC BY-NC-SA 4.0 |

**Notes:** This is an interpolation between UltraSharp (photorealistic detail) and TextSharp (text preservation). It is the closest match for webtoon needs because webtoons contain both artwork AND text. Users report it "preserves sharpness without introducing many artifacts" and works "alot cleaner than Remacri."

**Lite variant available** (4x-AnimeSharp-lite, 19.2 MB, 32nf 12nb) if speed is critical.

---

### Alternative Model #2: APISR (CVPR 2024) — ★ BEST FOR HAND-DRAWN LINES

| Property | Value |
|----------|-------|
| **Source** | https://huggingface.co/HikariDawn/APISR / https://github.com/Kiteretsu77/APISR |
| **Architecture** | Custom (prediction-oriented compression + balanced twin perceptual loss) |
| **Scale** | 4x |
| **File size** | ~5-10 MB (multiple model variants) |
| **Speed** | Medium-Fast (only 1.03M parameters — very efficient) |
| **Text handling** | Good — specifically designed to handle distorted/faint hand-drawn lines |
| **Speech bubbles / glow effects** | **Excellent** — addresses "unwanted color artifacts" explicitly via balanced twin perceptual loss (anime + photorealistic features); this directly attacks the "solar bubble" artifact problem |
| **Compatibility with RealESRGAN** | ⚠️ Separate codebase (uses its own inference scripts); NOT directly compatible with `basicsr`/`realesrgan` loader. Requires APISR-specific inference pipeline. |

**Why this matters:** APISR was published at CVPR 2024 specifically to solve anime-specific artifacts that RealESRGAN cannot handle:
1. **Distorted/faint hand-drawn lines** — uses prediction-oriented compression module
2. **Unwanted color artifacts** (the "solar/sun bubble" problem) — uses balanced twin perceptual loss
3. **Video compression artifacts** — explicitly modeled in degradation pipeline

Outperforms Real-ESRGAN, BSRGAN, AnimeSR, and VQD-SR on the AVC-RealLQ benchmark.

---

### Alternative Model #3: Real-CUGAN (Bilibili AI Lab)

| Property | Value |
|----------|-------|
| **Source** | https://github.com/bilibili/ailab/tree/main/Real-CUGAN |
| **Architecture** | Cascaded U-Net (same as Waifu2x-CUNet, but retrained + frequency-domain supervision) |
| **Scale** | 2x, 3x, 4x (separate models) |
| **File size** | ~5-6 MB per model (11 variants total for different denoise levels + scales) |
| **Speed** | **Fastest** — ~2.2x faster than RealESRGAN AnimeVideo-v3 on V100 |
| **Text handling** | Decent — better than generic RealESRGAN but not specialized |
| **Speech bubbles / glow effects** | Good line preservation, conservative denoise variant helps preserve bubble edges |
| **Compatibility with RealESRGAN** | ⚠️ NCNN Vulkan binary (separate executable); Python models have different architecture. NOT directly compatible with `basicsr` but can be integrated via its own PyTorch code. |

**Key advantage:** Multiple denoise levels (conservative, no-denoise, denoise1x, denoise2x, denoise3x). The "conservative" variant applies minimal changes, making it safer for text and bubbles. Also has an "up2x" model useful as a first-stage before a stronger 2x pass.

---

### Alternative Model #4: MangaJaNai (the-database) — ★ BEST FOR MANGA/WEBTOON TEXT

| Property | Value |
|----------|-------|
| **Source** | https://github.com/the-database/MangaJaNai |
| **Architecture** | ESRGAN-based (custom training) |
| **Scale** | 4x (7 variants optimized for different input heights: 1200p, 1300p, 1400p, 1500p, 1600p, 1920p, 2048p) |
| **File size** | Varies by variant |
| **Speed** | Medium (ESRGAN-based) |
| **Text handling** | **Excellent** — specifically trained for Japanese/English manga text; JPEG/moiré artifact removal; halftone dot restoration |
| **Speech bubbles / glow effects** | Very good (trained on manga pages with actual bubbles) |
| **Compatibility with RealESRGAN** | ⚠️ Uses its own GUI (MangaJaNaiConverterGui) or `traiNNer-redux` inference; models likely compatible with basicsr format but need verification |

**Also has IllustrationJaNai** for color images (covers, color pages), which is more relevant for webtoons than the B&W MangaJaNai models.

**Limitation:** Optimized for print-height images (1200-2048px). Webtoons are typically taller (scroll format) which may require pre-segmentation.

---

### Alternative Model #5: SwinIR / Swin2SR

| Property | Value |
|----------|-------|
| **Source** | https://github.com/JingyunLiang/SwinIR / https://huggingface.co/caidas/swinIR_classicalSR_x4_64 |
| **Architecture** | Swin Transformer (shifted window attention) |
| **Scale** | 4x (also 2x, 3x variants) |
| **File size** | ~15-30 MB (varies by variant) |
| **Speed** | **Slow** (3-5x slower than RealESRGAN) |
| **Text handling** | Good — transformer attention preserves high-frequency details better than CNN-based models |
| **Speech bubbles / glow effects** | Good — less hallucination than GAN-based models; more faithful reconstruction |
| **Compatibility with RealESRGAN** | ❌ Separate architecture (Transformer, not RRDBNet). Different inference pipeline. Not loadable via `basicsr`. |

**Why consider it:** SwinIR produces more faithful upscaling with less "invented" detail than GAN-based models. This means:
- Less hallucination of artifacts in speech bubbles
- More conservative handling of glow effects
- Better preservation of original text shapes

The trade-off is speed and lack of RealESRGAN framework compatibility.

---

### Alternative Model #6: Waifu2x (Classic)

| Property | Value |
|----------|-------|
| **Source** | https://github.com/nihui/waifu2x-ncnn-vulkan |
| **Architecture** | SRCNN / VGG (multiple variants including CUNet, UpConv, VGG) |
| **Scale** | 2x (most common) |
| **File size** | ~1-5 MB per model |
| **Speed** | Fast |
| **Text handling** | Poor (was never designed for text; introduces noise) |
| **Speech bubbles / glow effects** | Poor (tends to introduce noise artifacts in smooth regions) |
| **Compatibility with RealESRGAN** | ❌ Separate binary (ncnn Vulkan). Different architecture entirely. |

**Not recommended** as primary model for webtoons. Useful only as a very fast noise-reduction pre-process step before another upscaler.

---

### Alternative Model #7: 4x-UltraMix Balanced (Kim2091)

| Property | Value |
|----------|-------|
| **Source** | https://civitai.com/models/147806/ultramix |
| **Architecture** | ESRGAN (RRDBNet) |
| **Scale** | 4x |
| **File size** | ~30 MB |
| **Speed** | Medium |
| **Text handling** | Good (balanced variant) |
| **Speech bubbles / glow effects** | Better than RealESRGAN anime models |
| **Compatibility with RealESRGAN** | ✅ YES — standard ESRGAN `.pth` format, compatible with `basicsr`/`realesrgan` |

Has three variants: Smooth, Balanced, and Restore. The **Smooth** variant would be safest for webtoons (less artifact risk). The **Restore** variant adds more detail but might increase artifact risk on bubbles.

---

### Compatibility Summary with RealESRGAN (`basicsr`/`realesrgan`) Framework

| Model | Directly Loadable? | How to Use |
|-------|-------------------|------------|
| **4x-AnimeSharp** | ✅ YES | Drop `.pth` in weights/, load with `RRDBNet(num_block=23)` in `RealESRGANer` |
| **4x-AnimeSharp-lite** | ✅ YES | Same, load with `RRDBNet(num_block=12, num_feat=32)` |
| **4x-UltraMix** | ✅ YES | Standard ESRGAN `.pth`, works with RealESRGANer |
| **APISR** | ❌ NO | Requires APISR-specific inference code (separate repo) |
| **Real-CUGAN** | ⚠️ Partial | NCNN Vulkan binary OR custom PyTorch node; not basicsr-compatible |
| **MangaJaNai** | ⚠️ Unknown | Likely basicsr-compatible `.pth` (ESRGAN-based), verify format first |
| **SwinIR** | ❌ NO | Different architecture, separate inference pipeline |
| **Waifu2x** | ❌ NO | Separate binary (ncnn Vulkan) |
| **4x-UltraMix** | ✅ YES | Standard ESRGAN `.pth` |

---

## TOPIC 2: "الفقاعات الشمسيه" and Webtoon Upscaling Artifacts

### What Is "الفقاعات الشمسيه" (Solar Bubbles / Sun Bubbles)?

The Arabic phrase "الفقاعات الشمسيه" literally translates to "solar bubbles" or "sun bubbles." In the context of webtoon/manhwa/manhua, this refers to **speech bubbles or thought bubbles rendered with a glow/shine effect** — specifically bubbles that have:

1. A **white or semi-transparent fill** with a soft glow around the edges
2. A **gradient or sunburst-like shine** across the bubble surface
3. A **translucent overlay effect** (the background art shows through the bubble)
4. **Light ray effects** emanating from the center or top of the bubble

These are extremely common in webtoon art (especially Korean manhwa and Chinese manhua), where creators use semi-transparent glowing speech bubbles as a stylistic element. The "solar/sun" descriptor comes from the appearance — the bubbles look like they have a soft sun-like glow or are backlit.

### Why RealESRGAN Models Struggle with Webtoon Images

#### 1. **Text Distortion Problem**

RealESRGAN models (especially anime-trained variants) were trained primarily on **Japanese anime frames**, not webtoon/manhwa panels. Webtoon text has distinct characteristics:

- **Font rendering differences**: Webtoons use digital fonts optimized for screen reading, often with thinner strokes than anime titles. RealESRGAN perceives these thin strokes as "noise" and either thickens, thins, or breaks them.
- **Arabic text complications**: Arabic script is cursive with connected letters, varying baseline positions, and diacritical marks. GAN-based upscalers were never trained on Arabic text, so they introduce:
  - Broken letter connections (medial/initial/final forms confused)
  - Distorted diacritics (tashkeel) that look like noise
  - Baseline drift within words
  - Added or removed dots (i'jam) that change meaning
  
#### 2. **Speech Bubble / Glow Effect (Solar Bubble) Artifacts**

The solar/sun bubble effect creates numerous problems for GAN-based upscalers:

- **High-contrast-to-glow transition**: The sharp edge of the bubble outline transitioning into a soft glow gradient confuses the model. It doesn't know whether to sharpen or smooth this edge.
- **Gradient hallucination**: GANs are poor at preserving smooth gradients. They introduce "banding" artifacts — visible color steps where the original had a smooth gradient — because the discriminator penalizes smooth regions as "fake."
- **Chromatic aberration injection**: The model may add false color fringing at the edges of bubbles, especially where white bubble meets colored background.
- **Glow amplification/attenuation**: The upscaler either over-amplifies the glow (making it bloom unnaturally) or removes it entirely (making the bubble look flat and pasted-on).
- **Over-sharpening of bubble edges**: RealESRGAN adds sharpness to the bubble outline, creating a harsh "cut-out" look that destroys the intended soft transition.

From the RealESRGAN paper itself (Section on sinc filters): *"If sinc filters are not employed during training, the restored results will amplify the ringing and overshoot artifacts that existed in the input images, especially around the text and lines."* This is exactly what causes the solar bubble issue — ringing artifacts around high-contrast edges like bubble outlines.

#### 3. **Thin Artistic Lines**

Webtoon art often features very thin line art for features like:
- Hair strands
- Clothing folds
- Background details
- Facial features

GAN-based models exhibit **thresholding behavior**: lines thinner than the model's receptive field resolution are either removed (smoothed over) or thickened beyond recognition. The SRVGGNetCompact model (animevideov3) is especially bad at this due to its shallow architecture (only 16 conv layers).

#### 4. **Gradients and Light Effects**

Webtoons use digital painting techniques including:
- Cell shading with soft gradients
- Light rays and sunburst effects
- Glow layers and screen blending modes

GAN upscalers were trained primarily on photorealistic textures (grass, skin, brick, etc.) and handle smooth color gradients poorly. The discriminator was trained to detect "real" textures, so smooth regions look "fake" to it, and the generator adds false texture.

### Mitigation Strategies

#### A. Pre-Processing Techniques

1. **Image segmentation + selective upscaling**: Split image into text, bubble, and art regions; upscale each region with a different model or parameters:
   - Art region: Anime-optimized model (4x-AnimeSharp)
   - Bubble regions: Conservative upscaling or even just Lanczos interpolation (bypass AI entirely)
   
2. **Pre-denoise**: Apply very light pre-denoising before upscaling to reduce the chance of the GAN amplifying JPEG artifacts:
   ```python
   from PIL import Image, ImageFilter
   img.filter(ImageFilter.SMOOTH)  # Very mild
   ```

3. **Downscale then upscale**: Webtoon source images that are already at reasonable resolution (e.g., 800px+ wide) may look worse when upscaled with AI. Consider:
   - If input ≥ 800px wide: Use 2x upscale instead of 4x
   - If input ≥ 1200px wide: Skip AI upscaling entirely, use Lanczos for minor size adjustment

4. **Tile-based processing with overlap**: Reduce edge artifacts by using small overlapping tiles with careful blending at seams.

#### B. Model Selection Strategies

1. **Use 4x-AnimeSharp as primary model** — handles text better, fewer gradient artifacts
2. **Use 2x scale instead of 4x** when source is already decent quality (less artifact amplification)
3. **Try Real-CUGAN conservative mode** — the "conservative" variant applies minimal changes, safer for bubbles
4. **Two-stage pipeline**: Upscale with 4x-AnimeSharp first, then detect and fix bubble regions
5. **APISR for heavily compressed sources** — if source has visible JPEG artifacts, APISR handles them best

#### C. Post-Processing Techniques

1. **Bubble region refinement**: After upscaling, downscale bubble regions and re-upscale with more conservative parameters
2. **Gradient smoothing**: Apply selective Gaussian blur to regions identified as gradients (preserves line art but smooths banding)
3. **Text inpainting**: For badly distorted text, consider OCR + re-rendering approach:
   - Run OCR on original text
   - Use BubbleBlaster or similar to clean the bubble
   - Re-render text at higher resolution

#### D. Recommended Hybrid Pipeline

Based on all findings, the optimal webtoon upscaling pipeline:

```
Input Image
    │
    ├── [Pre-processing] Check input resolution
    │   ├── <600px wide → 4x upscale
    │   ├── 600-1200px wide → 2x upscale
    │   └── >1200px wide → Lanczos resize only
    │
    ├── [Model: 4x-AnimeSharp] Primary upscale
    │   (Best balance of text + art quality)
    │
    ├── [Post-processing] Detect bubble regions
    │   └── Apply gradient smoothing to bubble interiors
    │
    └── [Optional] If text still distorted
        └── OCR + re-render text at target resolution
```

### Why the Problem Exists at a Technical Level

The root cause is that **GAN-based upscalers are optimized for perceptual quality, not pixel fidelity**. The generator is trained to fool a discriminator that was trained on "real" images. When the input contains elements the discriminator associates with "fake" images (smooth gradients, thin text, glow effects), the generator tries to "fix" them by adding texture and sharpness — making speech bubbles look distorted and text unreadable.

**Non-GAN models (SwinIR, HAT)** don't have this problem because they optimize for reconstruction loss (PSNR/SSIM) rather than adversarial loss. They produce less visually "sharp" results but preserve the original structure more faithfully. This is why chaining SwinIR after RealESRGAN (or vice versa) can produce better results — each compensates for the other's weaknesses.

### Key References

1. RealESRGAN Paper: https://arxiv.org/abs/2107.10833 (Section on sinc filters and ringing artifacts around text)
2. APISR Paper (CVPR 2024): https://openaccess.thecvf.com/content/CVPR2024/html/Wang_APISR_Anime_Production_Inspired_Real-World_Anime_Super-Resolution_CVPR_2024_paper.html
3. 4x-AnimeSharp on OpenModelDB: https://openmodeldb.info/models/4x-AnimeSharp
4. Real-CUGAN: https://github.com/bilibili/ailab/tree/main/Real-CUGAN
5. MangaJaNai: https://github.com/the-database/MangaJaNai
