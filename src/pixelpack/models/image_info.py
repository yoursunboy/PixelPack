"""Description of the files found on disk by :mod:`pixelpack.core.scanner`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class SourceFile:
    """One file discovered inside the source folder.

    ``rel_path`` always uses ``/`` as the separator and is relative to the
    scanned root; it becomes the entry name inside the produced ZIP.
    ``output_name`` is the entry name the optimiser will use, which differs from
    ``rel_path`` when a BMP/TIFF has to be re-encoded as PNG.
    """

    path: Path
    rel_path: str
    size_bytes: int
    is_image: bool = False
    width: int = 0
    height: int = 0
    format: str = ""
    has_alpha: bool = False
    output_name: str = ""
    error: str | None = None

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()

    @property
    def long_edge(self) -> int:
        return max(self.width, self.height)

    @property
    def short_edge(self) -> int:
        return min(self.width, self.height)

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def megapixels(self) -> float:
        return self.pixels / 1_000_000

    @property
    def dimensions(self) -> str:
        if self.width <= 0 or self.height <= 0:
            return "—"
        return f"{self.width}×{self.height}"

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.rel_path} ({self.dimensions}, {self.size_bytes} B)"


@dataclass(slots=True)
class ScanResult:
    """The complete inventory of a source folder."""

    root: Path
    files: list[SourceFile] = field(default_factory=list)

    @property
    def image_files(self) -> list[SourceFile]:
        return [f for f in self.files if f.is_image]

    @property
    def non_image_files(self) -> list[SourceFile]:
        return [f for f in self.files if not f.is_image]

    @property
    def image_count(self) -> int:
        return sum(1 for f in self.files if f.is_image)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)

    @property
    def image_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files if f.is_image)

    @property
    def non_image_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files if not f.is_image)

    @property
    def max_long_edge(self) -> int:
        return max((f.long_edge for f in self.files if f.is_image), default=0)

    @property
    def unreadable_files(self) -> list[SourceFile]:
        return [f for f in self.files if f.is_image and f.error]
