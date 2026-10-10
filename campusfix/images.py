"""Image handling: validation, EXIF stripping, resizing, and clean JPEG storage."""

from pathlib import Path
from PIL import Image, UnidentifiedImageError

from campusfix.config import MAX_IMAGE_MB, MAX_IMAGE_SIDE, ROOT


def save_clean_image(path_in, issue_id, base_dir=None):
    """Validate, resize to MAX_IMAGE_SIDE, strip EXIF metadata, and save as JPEG."""
    if not path_in:
        raise ValueError("No image file provided.")

    source_path = Path(path_in)
    if not source_path.exists() or not source_path.is_file():
        raise ValueError(f"Image file does not exist: {path_in}")

    file_size_bytes = source_path.stat().st_size
    max_bytes = MAX_IMAGE_MB * 1024 * 1024
    if file_size_bytes > max_bytes:
        raise ValueError(f"Image is too large. Maximum allowed size is {MAX_IMAGE_MB} MB.")

    try:
        with Image.open(source_path) as img:
            # Convert to RGB (handles RGBA, P, L, CMYK)
            rgb_img = img.convert("RGB")

            # Resize if necessary while preserving aspect ratio
            width, height = rgb_img.size
            longest_side = max(width, height)
            if longest_side > MAX_IMAGE_SIDE:
                scale = MAX_IMAGE_SIDE / float(longest_side)
                new_size = (int(width * scale), int(height * scale))
                resized_img = rgb_img.resize(new_size, Image.Resampling.LANCZOS)
            else:
                resized_img = rgb_img

            # Strip EXIF and metadata by creating a fresh canvas without metadata
            clean_canvas = Image.new("RGB", resized_img.size)
            clean_canvas.paste(resized_img)

            # Determine destination
            target_base = Path(base_dir) if base_dir else ROOT
            images_dir = target_base / "notes" / "images"
            images_dir.mkdir(parents=True, exist_ok=True)

            out_file = images_dir / f"{issue_id}.jpg"
            clean_canvas.save(out_file, format="JPEG", quality=85, optimize=True)

            # Return relative path
            return f"notes/images/{issue_id}.jpg"
    except (UnidentifiedImageError, OSError) as err:
        raise ValueError(f"Unable to process image. File is not a valid image: {err}")
