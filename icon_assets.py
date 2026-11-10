"""Shared app icon generation for 扣舷-数字节律."""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return Path(__file__).resolve().parent


ROOT = _bundle_root()
SOURCE_ICON_PATH = ROOT / "assets" / "kouxian-icon-source.png"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZES = (16, 24, 32, 48, 64, 96, 128, 192, 256, 512)
DATA_ICO_NAMES = ("扣舷-数字节律.ico", "扣舷.ico", "叩舷.ico", "交互节律.ico")


def _resample_filter():
    try:
        return Image.Resampling.LANCZOS
    except AttributeError:
        return Image.LANCZOS


RESAMPLE = _resample_filter()


def _root(root: str | Path | None = None) -> Path:
    return Path(root).resolve() if root else ROOT


def _source(source: str | Path | None = None) -> Path:
    return Path(source).expanduser().resolve() if source else SOURCE_ICON_PATH


def _crop_square(image: Image.Image) -> Image.Image:
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return image.crop((left, top, left + side, top + side))


def load_source_icon(source: str | Path | None = None) -> Image.Image:
    path = _source(source)
    if not path.is_file():
        raise FileNotFoundError(f"missing icon source: {path}")
    with Image.open(path) as image:
        return _crop_square(image.convert("RGBA"))


def make_icon_image(size: int = 256, source: str | Path | None = None) -> Image.Image:
    image = load_source_icon(source).resize((size, size), RESAMPLE)
    if size <= 64:
        image = image.filter(ImageFilter.UnsharpMask(radius=0.6, percent=125, threshold=1))
    return image


def write_png(path: str | Path, size: int, source: str | Path | None = None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    make_icon_image(size, source).save(target, "PNG")
    return target


def write_ico(path: str | Path, source: str | Path | None = None, sizes: tuple[int, ...] = ICO_SIZES) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    images = [make_icon_image(size, source) for size in sizes]
    images[-1].save(
        target,
        format="ICO",
        append_images=images[:-1],
        sizes=[(size, size) for size in sizes],
    )
    return target


def generate_icon_assets(
    source: str | Path | None = None,
    root: str | Path | None = None,
    include_current_dist: bool = False,
) -> list[Path]:
    project_root = _root(root)
    asset_dir = project_root / "assets"
    static_dir = project_root / "static"
    data_dir = project_root / "data"
    source_path = _source(source)
    canonical_source = asset_dir / SOURCE_ICON_PATH.name
    written: list[Path] = []

    asset_dir.mkdir(parents=True, exist_ok=True)
    if source_path != canonical_source.resolve():
        shutil.copyfile(source_path, canonical_source)
        written.append(canonical_source)
        source_path = canonical_source

    written.append(write_ico(project_root / "app.ico", source_path))
    for size in PNG_SIZES:
        written.append(write_png(asset_dir / f"kouxian-icon-{size}.png", size, source_path))

    written.append(write_png(static_dir / "icon-192.png", 192, source_path))
    written.append(write_png(static_dir / "icon-512.png", 512, source_path))

    for name in DATA_ICO_NAMES:
        written.append(write_ico(data_dir / name, source_path))
    written.append(write_png(data_dir / "interaction-rhythm-title.png", 96, source_path))
    written.append(write_png(data_dir / "interaction-rhythm-title-preview.png", 96, source_path))
    written.append(write_png(data_dir / "interaction-rhythm-icon-preview.png", 256, source_path))
    written.append(write_ico(Path(tempfile.gettempdir()) / "kouxian-tray.ico", source_path))

    if include_current_dist:
        written.extend(_write_current_dist_assets(project_root, source_path))

    return written


def _write_current_dist_assets(project_root: Path, source: Path) -> list[Path]:
    current_dir = project_root / "dist" / "current" / "InteractionRhythm"
    if not current_dir.exists():
        return []

    written = [
        write_ico(current_dir / "InteractionRhythm.ico", source),
        write_png(current_dir / "_internal" / "static" / "icon-192.png", 192, source),
        write_png(current_dir / "_internal" / "static" / "icon-512.png", 512, source),
        write_png(current_dir / "data" / "interaction-rhythm-title.png", 96, source),
    ]
    for name in DATA_ICO_NAMES:
        written.append(write_ico(current_dir / "data" / name, source))
    return written
