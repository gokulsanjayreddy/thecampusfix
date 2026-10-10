"""Tests for campusfix/images.py upload cleaning, EXIF stripping, and validation."""

from pathlib import Path
from PIL import Image
import pytest

from campusfix.config import MAX_IMAGE_SIDE
from campusfix.images import save_clean_image


def test_save_clean_image_valid(tmp_path):
    img_path = tmp_path / "test_input.png"
    img = Image.new("RGB", (600, 400), color="blue")
    img.save(img_path, format="PNG")

    rel_path = save_clean_image(img_path, "abc123", base_dir=tmp_path)
    assert rel_path == "notes/images/abc123.jpg"

    saved_file = tmp_path / "notes" / "images" / "abc123.jpg"
    assert saved_file.exists()
    with Image.open(saved_file) as out_img:
        assert out_img.format == "JPEG"
        assert out_img.size == (600, 400)


def test_save_clean_image_resizes_large_dimensions(tmp_path):
    img_path = tmp_path / "large_input.png"
    # Create an image with 2560x1440
    img = Image.new("RGB", (2560, 1440), color="green")
    img.save(img_path, format="PNG")

    save_clean_image(img_path, "big456", base_dir=tmp_path)
    saved_file = tmp_path / "notes" / "images" / "big456.jpg"

    with Image.open(saved_file) as out_img:
        w, h = out_img.size
        assert max(w, h) == MAX_IMAGE_SIDE
        assert w == 1280
        assert h == 720


def test_save_clean_image_strips_exif(tmp_path):
    img_path = tmp_path / "exif_input.jpg"
    img = Image.new("RGB", (300, 200), color="red")

    # Set mock EXIF using Pillow's native Exif
    exif = img.getexif()
    exif[0x010E] = "Test Campus Image"  # ImageDescription tag
    img.save(img_path, format="JPEG", exif=exif)

    # Verify input has EXIF
    with Image.open(img_path) as verify_in:
        assert verify_in.getexif().get(0x010E) == "Test Campus Image"

    save_clean_image(img_path, "noexif", base_dir=tmp_path)
    saved_file = tmp_path / "notes" / "images" / "noexif.jpg"

    with Image.open(saved_file) as out_img:
        exif_data = out_img.getexif()
        assert len(exif_data) == 0


def test_save_clean_image_rejects_non_image(tmp_path):
    txt_path = tmp_path / "not_an_image.txt"
    txt_path.write_text("Hello, this is just plain text, not an image.", encoding="utf-8")

    with pytest.raises(ValueError, match="not a valid image"):
        save_clean_image(txt_path, "badimg", base_dir=tmp_path)


def test_save_clean_image_rejects_oversized_file(tmp_path, monkeypatch):
    from campusfix import images
    monkeypatch.setattr(images, "MAX_IMAGE_MB", 0.001)  # ~1 KB limit

    img_path = tmp_path / "too_big.png"
    img = Image.new("RGB", (500, 500), color="purple")
    img.save(img_path, format="PNG")

    with pytest.raises(ValueError, match="Image is too large"):
        save_clean_image(img_path, "huge", base_dir=tmp_path)


def test_save_clean_image_nonexistent_file(tmp_path):
    fake_path = tmp_path / "does_not_exist.jpg"
    with pytest.raises(ValueError, match="does not exist"):
        save_clean_image(fake_path, "missing", base_dir=tmp_path)
