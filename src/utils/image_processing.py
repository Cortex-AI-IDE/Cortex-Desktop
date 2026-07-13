"""
Image processing pipeline for Cortex AI.

Ported from Claude Code's TypeScript implementation:
- imageResizer.ts -> Progressive compression & resizing
- imageValidation.ts -> API boundary validation
- imagePaste.ts -> Format detection & normalization
- attachments.ts -> Content block building for API

Provides image validation, resizing, compression, and content block
building for multimodal API calls (Mistral vision).
"""

import base64
import io
import logging
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple

log = logging.getLogger("image_processing")

# ==================== CONSTANTS (from imageResizer.ts) ====================

# Target raw size for images before base64 encoding (~3.75MB)
IMAGE_TARGET_RAW_SIZE = 3_932_160  # 3.75 * 1024 * 1024

# Maximum base64 size accepted by API (5MB)
API_IMAGE_MAX_BASE64_SIZE = 5_242_880  # 5 * 1024 * 1024

# Maximum dimensions (from Claude Code - Anthropic limits)
IMAGE_MAX_WIDTH = 1568
IMAGE_MAX_HEIGHT = 1568

# Minimum dimensions - skip tiny images
IMAGE_MIN_WIDTH = 10
IMAGE_MIN_HEIGHT = 10

# JPEG quality levels for progressive compression
JPEG_QUALITY_LEVELS = [80, 60, 40, 20]

# Maximum images per message
MAX_IMAGES_PER_MESSAGE = 5

# Supported media types
SUPPORTED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


class ImageFormat(Enum):
    """Detected image format."""
    JPEG = "image/jpeg"
    PNG = "image/png"
    GIF = "image/gif"
    WEBP = "image/webp"
    BMP = "image/bmp"
    UNKNOWN = "unknown"


@dataclass
class ImageDimensions:
    """Image dimensions."""
    width: int
    height: int


@dataclass
class ProcessedImage:
    """Result of image processing pipeline."""
    base64_data: str           # Base64-encoded image data
    media_type: str            # MIME type (image/jpeg, image/png, etc.)
    dimensions: ImageDimensions  # Final dimensions
    original_size: int         # Original size in bytes
    processed_size: int        # Final size in bytes  
    was_resized: bool          # Whether the image was resized
    was_compressed: bool       # Whether the image was compressed
    metadata_text: str = ""    # Coordinate mapping metadata for resized images


@dataclass
class ImageValidationResult:
    """Result of image validation."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ==================== FORMAT DETECTION (from imageResizer.ts) ====================

def detect_image_format(data: bytes) -> ImageFormat:
    """Detect image format from magic bytes.
    
    Ported from Claude Code's detectImageFormatFromBuffer().
    """
    if len(data) < 4:
        return ImageFormat.UNKNOWN
    
    # JPEG: FF D8 FF
    if data[0] == 0xFF and data[1] == 0xD8 and data[2] == 0xFF:
        return ImageFormat.JPEG
    
    # PNG: 89 50 4E 47
    if data[0] == 0x89 and data[1] == 0x50 and data[2] == 0x4E and data[3] == 0x47:
        return ImageFormat.PNG
    
    # GIF: 47 49 46 38
    if data[0] == 0x47 and data[1] == 0x49 and data[2] == 0x46 and data[3] == 0x38:
        return ImageFormat.GIF
    
    # WebP: RIFF....WEBP
    if len(data) >= 12 and data[0:4] == b'RIFF' and data[8:12] == b'WEBP':
        return ImageFormat.WEBP
    
    # BMP: 42 4D
    if data[0] == 0x42 and data[1] == 0x4D:
        return ImageFormat.BMP
    
    return ImageFormat.UNKNOWN


def get_image_dimensions_from_bytes(data: bytes, fmt: ImageFormat) -> Optional[ImageDimensions]:
    """Extract image dimensions without full decode (fast path).
    
    Falls back to Pillow if fast path fails.
    """
    try:
        if fmt == ImageFormat.PNG and len(data) >= 24:
            # PNG IHDR chunk: width at offset 16, height at offset 20
            w = struct.unpack('>I', data[16:20])[0]
            h = struct.unpack('>I', data[20:24])[0]
            return ImageDimensions(width=w, height=h)
        
        if fmt == ImageFormat.JPEG:
            # JPEG: scan for SOF0/SOF2 markers
            i = 2
            while i < len(data) - 9:
                if data[i] == 0xFF:
                    marker = data[i + 1]
                    if marker in (0xC0, 0xC2):  # SOF0, SOF2
                        h = struct.unpack('>H', data[i+5:i+7])[0]
                        w = struct.unpack('>H', data[i+7:i+9])[0]
                        return ImageDimensions(width=w, height=h)
                    elif marker == 0xD9:  # EOI
                        break
                    else:
                        if i + 3 < len(data):
                            seg_len = struct.unpack('>H', data[i+2:i+4])[0]
                            i += 2 + seg_len
                        else:
                            break
                else:
                    i += 1
        
        if fmt == ImageFormat.GIF and len(data) >= 10:
            w = struct.unpack('<H', data[6:8])[0]
            h = struct.unpack('<H', data[8:10])[0]
            return ImageDimensions(width=w, height=h)
        
        # Fallback: use Pillow
        return _get_dimensions_pillow(data)
    except Exception:
        return _get_dimensions_pillow(data)


def _get_dimensions_pillow(data: bytes) -> Optional[ImageDimensions]:
    """Get dimensions using Pillow (fallback)."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        return ImageDimensions(width=img.width, height=img.height)
    except Exception:
        return None


# ==================== VALIDATION (from imageValidation.ts) ====================

def validate_images_for_api(images: List[Dict[str, Any]]) -> ImageValidationResult:
    """Validate images before sending to API.
    
    Ported from Claude Code's validateImagesForAPI().
    Checks all base64 image blocks against size limits.
    
    Args:
        images: List of image dicts with 'data' key (base64 string)
    
    Returns:
        ImageValidationResult with valid flag and any errors/warnings
    """
    result = ImageValidationResult(valid=True)
    
    if not images:
        return result
    
    if len(images) > MAX_IMAGES_PER_MESSAGE:
        result.warnings.append(
            f"Too many images ({len(images)}). Maximum is {MAX_IMAGES_PER_MESSAGE}. "
            f"Extra images will be dropped."
        )
    
    for i, img in enumerate(images[:MAX_IMAGES_PER_MESSAGE]):
        img_data = img.get('data', '')
        
        # Strip data URI prefix if present
        if 'base64,' in img_data:
            img_data = img_data.split('base64,', 1)[1]
        
        # Check base64 size
        b64_size = len(img_data)
        if b64_size > API_IMAGE_MAX_BASE64_SIZE:
            result.errors.append(
                f"Image {i+1} is too large ({b64_size / 1_048_576:.1f}MB). "
                f"Maximum is {API_IMAGE_MAX_BASE64_SIZE / 1_048_576:.1f}MB. "
                f"Image will be compressed automatically."
            )
            # Don't mark as invalid - we'll compress it
        
        if b64_size == 0:
            result.errors.append(f"Image {i+1} is empty.")
            result.valid = False
    
    return result


def validate_single_image(base64_data: str) -> Tuple[bool, str]:
    """Quick validation for a single image.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not base64_data:
        return False, "Empty image data"
    
    # Strip data URI prefix
    raw_b64 = base64_data
    if 'base64,' in raw_b64:
        raw_b64 = raw_b64.split('base64,', 1)[1]
    
    if len(raw_b64) > API_IMAGE_MAX_BASE64_SIZE:
        return False, f"Image too large ({len(raw_b64) / 1_048_576:.1f}MB > 5MB limit)"
    
    # Try to decode to verify it's valid base64
    try:
        decoded = base64.b64decode(raw_b64[:100])  # Just check start
        if len(decoded) < 4:
            return False, "Image data too small"
    except Exception:
        return False, "Invalid base64 encoding"
    
    return True, ""


# ==================== COMPRESSION (from imageResizer.ts) ====================

def compress_image(
    data: bytes,
    target_size: int = IMAGE_TARGET_RAW_SIZE,
    max_width: int = IMAGE_MAX_WIDTH,
    max_height: int = IMAGE_MAX_HEIGHT
) -> Tuple[bytes, ImageFormat, bool]:
    """Progressive image compression pipeline.
    
    Ported from Claude Code's compressImageBuffer() and 
    maybeResizeAndDownsampleImageBuffer().
    
    Strategy (in order):
    1. If already under target_size, return as-is
    2. Try PNG optimization (for PNG images)
    3. Try JPEG at decreasing quality levels (80, 60, 40, 20)
    4. Resize dimensions if still too large
    5. Ultra-compressed JPEG as last resort
    
    Args:
        data: Raw image bytes
        target_size: Target size in bytes
        max_width: Maximum width in pixels
        max_height: Maximum height in pixels
    
    Returns:
        Tuple of (compressed_bytes, format, was_modified)
    """
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow not installed, skipping compression")
        fmt = detect_image_format(data)
        return data, fmt, False
    
    # Check if already under target
    if len(data) <= target_size:
        fmt = detect_image_format(data)
        # Still check dimensions
        dims = get_image_dimensions_from_bytes(data, fmt)
        if dims and dims.width <= max_width and dims.height <= max_height:
            return data, fmt, False
    
    # Open image
    img = Image.open(io.BytesIO(data))
    original_format = detect_image_format(data)
    was_modified = False
    
    # Convert BMP to PNG first (BMP is uncompressed)
    if original_format == ImageFormat.BMP:
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        data = buf.getvalue()
        original_format = ImageFormat.PNG
        was_modified = True
        if len(data) <= target_size:
            return data, ImageFormat.PNG, True
    
    # Step 1: Resize if dimensions exceed limits
    if img.width > max_width or img.height > max_height:
        ratio = min(max_width / img.width, max_height / img.height)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        was_modified = True
        log.info(f"Resized image: {img.width}x{img.height} -> {new_w}x{new_h}")
    
    # Step 2: Try PNG compression (for PNGs)
    if original_format == ImageFormat.PNG:
        buf = io.BytesIO()
        img.save(buf, format='PNG', optimize=True)
        compressed = buf.getvalue()
        if len(compressed) <= target_size:
            return compressed, ImageFormat.PNG, True
    
    # Step 3: Progressive JPEG compression
    # Convert to RGB if necessary (JPEG doesn't support alpha)
    if img.mode in ('RGBA', 'LA', 'P'):
        # Create white background for transparency
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        if img.mode in ('RGBA', 'LA'):
            background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    
    for quality in JPEG_QUALITY_LEVELS:
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality, optimize=True)
        compressed = buf.getvalue()
        if len(compressed) <= target_size:
            log.info(f"Compressed to JPEG quality={quality}, size={len(compressed)}")
            return compressed, ImageFormat.JPEG, True
    
    # Step 4: Aggressive resize + minimum JPEG quality
    # Reduce dimensions by 50% and try again
    new_w = img.width // 2
    new_h = img.height // 2
    if new_w >= IMAGE_MIN_WIDTH and new_h >= IMAGE_MIN_HEIGHT:
        img = img.resize((new_w, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=20, optimize=True)
        compressed = buf.getvalue()
        log.info(f"Ultra-compressed: {new_w}x{new_h} JPEG q=20, size={len(compressed)}")
        return compressed, ImageFormat.JPEG, True
    
    # Last resort: return the q=20 JPEG at current dimensions
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=20, optimize=True)
    return buf.getvalue(), ImageFormat.JPEG, True


def create_image_metadata_text(
    original_dims: ImageDimensions,
    new_dims: ImageDimensions
) -> str:
    """Generate coordinate mapping metadata for resized images.
    
    Ported from Claude Code's createImageMetadataText().
    Helps the model understand coordinate scaling when images are resized.
    """
    if original_dims.width == new_dims.width and original_dims.height == new_dims.height:
        return ""
    
    scale_x = original_dims.width / new_dims.width if new_dims.width > 0 else 1
    scale_y = original_dims.height / new_dims.height if new_dims.height > 0 else 1
    
    return (
        f"[Image was resized from {original_dims.width}x{original_dims.height} "
        f"to {new_dims.width}x{new_dims.height}. "
        f"Coordinate scale factors: x={scale_x:.2f}, y={scale_y:.2f}. "
        f"To map coordinates back to original: multiply x by {scale_x:.2f}, y by {scale_y:.2f}]"
    )


# ==================== CONTENT BLOCK BUILDING (from attachments.ts) ====================

def build_image_content_blocks(
    images: List[Dict[str, Any]],
    auto_compress: bool = True
) -> List[Dict[str, Any]]:
    """Build image content blocks for multimodal API calls.
    
    Ported from Claude Code's buildImageContentBlocks().
    Converts raw image data into properly formatted content blocks.
    
    Args:
        images: List of image dicts with 'data' key (base64, possibly with data URI prefix)
        auto_compress: Whether to auto-compress oversized images
    
    Returns:
        List of image_url content blocks ready for API
    """
    blocks = []
    metadata_texts = []
    
    for i, img in enumerate(images[:MAX_IMAGES_PER_MESSAGE]):
        img_data = img.get('data', '')
        if not img_data:
            continue
        
        # Extract base64 and media type from data URI
        media_type = "image/jpeg"  # default
        raw_b64 = img_data
        
        if img_data.startswith('data:'):
            # Parse data URI: data:image/png;base64,XXXX
            try:
                header, raw_b64 = img_data.split('base64,', 1)
                if 'image/' in header:
                    media_type = header.split('data:')[1].split(';')[0]
            except (ValueError, IndexError):
                pass
        
        # Decode to bytes for processing
        try:
            raw_bytes = base64.b64decode(raw_b64)
        except Exception as e:
            log.warning(f"Failed to decode image {i+1}: {e}")
            continue
        
        original_size = len(raw_bytes)
        original_dims = get_image_dimensions_from_bytes(raw_bytes, detect_image_format(raw_bytes))
        
        # Compress if needed
        was_compressed = False
        if auto_compress and (
            len(raw_bytes) > IMAGE_TARGET_RAW_SIZE or
            (original_dims and (original_dims.width > IMAGE_MAX_WIDTH or original_dims.height > IMAGE_MAX_HEIGHT))
        ):
            raw_bytes, new_fmt, was_compressed = compress_image(raw_bytes)
            if was_compressed:
                media_type = new_fmt.value
                raw_b64 = base64.b64encode(raw_bytes).decode('utf-8')
                log.info(f"Image {i+1} compressed: {original_size} -> {len(raw_bytes)} bytes")
                
                # Generate metadata for coordinate mapping
                if original_dims:
                    new_dims = get_image_dimensions_from_bytes(raw_bytes, new_fmt)
                    if new_dims:
                        meta = create_image_metadata_text(original_dims, new_dims)
                        if meta:
                            metadata_texts.append(meta)
        
        # Validate final size
        final_b64_size = len(raw_b64)
        if final_b64_size > API_IMAGE_MAX_BASE64_SIZE:
            log.warning(f"Image {i+1} still too large after compression ({final_b64_size / 1_048_576:.1f}MB). Skipping.")
            continue
        
        # Build data URI
        data_uri = f"data:{media_type};base64,{raw_b64}"
        
        # Build content block (Mistral/OpenAI format)
        blocks.append({
            "type": "image_url",
            "image_url": {
                "url": data_uri
            }
        })
        
        log.info(f"Image {i+1}: {media_type}, {len(raw_bytes)} bytes, compressed={was_compressed}")
    
    return blocks, metadata_texts


def build_vision_messages(
    text: str,
    images: List[Dict[str, Any]],
    system_prompt: str = None,
    auto_compress: bool = True
) -> List[Dict[str, str]]:
    """Build complete message array for vision API call.
    
    Combines system prompt, text, and image content blocks into
    a properly formatted message array for Mistral/OpenAI API.
    
    Args:
        text: User's text message
        images: List of image dicts with 'data' key
        system_prompt: Optional system prompt for vision context
        auto_compress: Whether to auto-compress images
    
    Returns:
        List of message dicts ready for API call
    """
    messages = []
    
    # System prompt
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    
    # Build image blocks
    image_blocks, metadata_texts = build_image_content_blocks(images, auto_compress)
    
    if image_blocks:
        # Build multimodal content array
        content = []
        
        # Add metadata text if images were resized
        full_text = text
        if metadata_texts:
            full_text = "\n".join(metadata_texts) + "\n\n" + text
        
        content.append({"type": "text", "text": full_text})
        content.extend(image_blocks)
        
        messages.append({"role": "user", "content": content})
    else:
        # No valid images, text only
        messages.append({"role": "user", "content": text})
    
    return messages


# ==================== FULL PIPELINE ====================

def process_images_for_api(
    images: List[Dict[str, Any]],
    text: str,
    provider: str = "mistral"
) -> Dict[str, Any]:
    """Full image processing pipeline.
    
    Runs validation -> compression -> content block building.
    
    Args:
        images: Raw image dicts from frontend
        text: User's text message
        provider: LLM provider name
    
    Returns:
        Dict with 'messages', 'warnings', 'errors' keys
    """
    result = {
        "messages": [],
        "warnings": [],
        "errors": [],
        "image_count": 0,
        "provider_supports_vision": False
    }
    
    # Check provider vision support
    vision_providers = {"mistral"}
    if provider.lower() not in vision_providers:
        result["errors"].append(
            f"Provider '{provider}' does not support vision/OCR. "
            f"Only Mistral supports image analysis. Images will be dropped."
        )
        result["messages"] = [{"role": "user", "content": text}]
        return result
    
    result["provider_supports_vision"] = True
    
    # Step 1: Validate
    validation = validate_images_for_api(images)
    result["warnings"].extend(validation.warnings)
    
    if not validation.valid:
        result["errors"].extend(validation.errors)
        result["messages"] = [{"role": "user", "content": text}]
        return result
    
    # Step 2: Build vision messages with auto-compression
    # CRITICAL: This is STRICTLY an OCR/description step. Mistral must ONLY
    # transcribe and describe what's visually in the image — it MUST NOT
    # provide solutions, write code, debug, or analyze beyond visual description.
    # The full solution will be handled by the coding agent (DeepSeek/MiMo).
    # NOTE: Must capture BOTH text AND visual/behavioral context (UI state,
    # highlights, errors, layout) so the coding model can understand the
    # screenshot as if it could see it directly.
    vision_system_prompt = (
        "You are a screenshot analysis tool. Your job is to extract BOTH the text "
        "content AND the visual/behavioral context from images.\n"
        "RULES:\n"
        "(1) Transcribe all visible text verbatim (code, labels, errors, filenames, line numbers).\n"
        "(2) Describe visual context: which tab/file is active, what's highlighted or selected, "
        "cursor position, error underlines, diff colors (green=added, red=removed), "
        "warning icons, status indicators, panel layout, scroll position.\n"
        "(3) Note anomalies: anything misaligned, truncated, overlapping, broken, or visually wrong.\n"
        "(4) DO NOT provide solutions, fixes, code, analysis, or advice.\n"
        "(5) DO NOT answer the user's question — only describe what is visible.\n"
        "(6) Be thorough on visual state — the downstream model cannot see the image "
        "and relies entirely on your description to understand context."
    )
    
    messages = build_vision_messages(
        text=text,
        images=images[:MAX_IMAGES_PER_MESSAGE],
        system_prompt=vision_system_prompt,
        auto_compress=True
    )
    
    result["messages"] = messages
    result["image_count"] = min(len(images), MAX_IMAGES_PER_MESSAGE)
    
    # Add warnings for dropped images
    if len(images) > MAX_IMAGES_PER_MESSAGE:
        result["warnings"].append(
            f"Only first {MAX_IMAGES_PER_MESSAGE} images were included. "
            f"{len(images) - MAX_IMAGES_PER_MESSAGE} images were dropped."
        )
    
    log.info(f"Image pipeline complete: {result['image_count']} images processed for {provider}")
    return result
