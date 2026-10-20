"""The parent's uploaded pictures (task 5b): the companion image and up to five background scenes.

Every upload is checked by what the bytes really are (Pillow opens and decodes it), never by the file name or the
type the browser claims. It is then re-encoded into a fresh image, which drops all metadata (camera, location,
embedded text), shrunk to at most MAX_EDGE pixels on its long side, and stored under <data dir>/uploads/ with a
random name. Nothing here is in git. No API calls.
"""

import io
import re
import secrets
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app import rewards, session_plan
from app.config import get_settings

MAX_BYTES = 5 * 1024 * 1024
MAX_SCENES = 5
MAX_PIXELS = 40_000_000          # refuse enormous pictures before decoding them (a 5 MB file can hold far more)
MAX_EDGE = 2400                  # long side after re-encoding; plenty for a background or an avatar
LABEL_MAX = 40
KINDS = ("companion", "scene")
FORMATS = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg"), "WEBP": (".webp", "image/webp")}
NAME_RE = re.compile(r"^[0-9a-f]{24}\.(png|jpg|webp)$")
MEDIA_TYPES = {ext: media for ext, media in FORMATS.values()}


class UploadError(Exception):
    """A picture that cannot be accepted; the message is shown to the parent."""


def upload_dir() -> Path:
    return Path(get_settings().data_dir) / "uploads"


def clean_image(data: bytes) -> tuple[bytes, str]:
    """Check an uploaded file and return (re-encoded bytes, extension). Raises UploadError if it is not a
    PNG, JPEG or WebP picture, is too big, or cannot be read."""
    if not data:
        raise UploadError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise UploadError("That picture is over 5 MB. Choose a smaller one.")
    try:
        probe = Image.open(io.BytesIO(data))
        fmt = probe.format
        if fmt not in FORMATS:
            raise UploadError("Only PNG, JPG or WebP pictures can be used.")
        if probe.width * probe.height > MAX_PIXELS:
            raise UploadError("That picture has too many pixels. Choose a smaller one.")
        probe.verify()                                    # checks the file's structure ...
        img = Image.open(io.BytesIO(data))
        img.load()                                        # ... and decodes every pixel (a truncated file fails here)
    except UploadError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise UploadError("That file is not a picture this app can read. Use PNG, JPG or WebP.") from None
    ext, _ = FORMATS[fmt]
    img.seek(0)                                           # an animated picture keeps only its first frame
    if fmt == "JPEG":
        img = img.convert("RGB")
    elif img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "transparency" in img.info or "A" in img.mode else "RGB")
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    clean = Image.new(img.mode, img.size)                 # a new image carries no metadata at all
    clean.paste(img)
    out = io.BytesIO()
    if fmt == "JPEG":
        clean.save(out, "JPEG", quality=88, optimize=True)
    elif fmt == "PNG":
        clean.save(out, "PNG", optimize=True)
    else:
        clean.save(out, "WEBP", quality=88)
    return out.getvalue(), ext


def list_images(conn, kind: str) -> list[dict]:
    return [dict(r, url=f"/uploads/{r['filename']}") for r in
            conn.execute("SELECT * FROM uploaded_images WHERE kind = ? ORDER BY id", (kind,))]


def _remove_file(filename: str) -> None:
    if NAME_RE.match(filename):
        (upload_dir() / filename).unlink(missing_ok=True)


def save_image(conn, kind: str, data: bytes, label: str = "") -> None:
    """Validate and store a picture. A new companion replaces the old one. Raises UploadError."""
    if kind not in KINDS:
        raise UploadError("Choose whether this is the companion or a scene.")
    if kind == "scene" and len(list_images(conn, "scene")) >= MAX_SCENES:
        raise UploadError(f"You already have {MAX_SCENES} scenes. Delete one first.")
    clean, ext = clean_image(data)
    name = secrets.token_hex(12) + ext
    folder = upload_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(clean)
    label = " ".join(label.split())[:LABEL_MAX]
    old = list_images(conn, "companion") if kind == "companion" else []
    conn.execute("INSERT INTO uploaded_images (kind, filename, label, created_at) VALUES (?, ?, ?, ?)",
                 (kind, name, label, session_plan.stamp(session_plan.utc_now_dt())))
    if kind == "companion":
        conn.execute("DELETE FROM uploaded_images WHERE kind = 'companion' AND filename != ?", (name,))
        rewards.set_meta(conn, "companion_image", f"/uploads/{name}")     # commits
        for row in old:
            _remove_file(row["filename"])
    else:
        conn.commit()


def delete_image(conn, image_id) -> bool:
    """Delete a picture and its file. A deleted companion goes back to the original art; a deleted scene that was in
    use is taken off."""
    row = conn.execute("SELECT * FROM uploaded_images WHERE id = ?", (image_id,)).fetchone() if str(image_id).isdigit() else None
    if row is None:
        return False
    conn.execute("DELETE FROM uploaded_images WHERE id = ?", (row["id"],))
    if row["kind"] == "companion":
        conn.execute("DELETE FROM reward_meta WHERE key = 'companion_image' AND value = ?", (f"/uploads/{row['filename']}",))
    else:
        conn.execute("DELETE FROM reward_equipped WHERE slot = 'scene' AND item = ?", (f"{rewards.UPLOAD_PREFIX}{row['id']}",))
    conn.commit()
    _remove_file(row["filename"])
    return True


def file_for(name: str):
    """(path, media type) of a stored picture, or None for a bad name or a missing file."""
    if not NAME_RE.match(name):
        return None
    path = upload_dir() / name
    return (path, MEDIA_TYPES[path.suffix]) if path.is_file() else None
