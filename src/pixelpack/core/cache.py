"""Bounded caches that keep repeated optimisation rounds affordable.

Two caches live here:

``encoded``
    Finished encoder output, keyed by ``(rel_path, width, height, quality,
    format)``. Images protected by ``min_long_edge`` produce identical
    dimensions at every scale, so this turns most rounds into a dictionary
    lookup for them.

``decoded``
    Source images that have already been decoded and rotated. Decoding is by
    far the most expensive step, so this is the single biggest win — but it is
    bounded, because PixelPack must never load a whole folder into memory.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Hashable

#: Upper bound for cached encoder output (~the size of a few archives).
DEFAULT_MAX_ENCODED_BYTES = 384 * 1024 * 1024

#: Upper bound for decoded source pixels. A 24 MP RGB image is ~72 MB, so this
#: holds roughly seven full-resolution photos.
DEFAULT_MAX_DECODED_PIXELS = 90_000_000

#: Never cache a single source image larger than this many pixels.
MAX_CACHEABLE_SOURCE_PIXELS = 80_000_000


class _LruCache:
    """A tiny thread-safe LRU keyed by an arbitrary hashable."""

    def __init__(self, max_weight: int) -> None:
        self._max_weight = max(0, int(max_weight))
        self._data: OrderedDict[Hashable, tuple[Any, int]] = OrderedDict()
        self._weight = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return entry[0]

    def put(self, key: Hashable, value: Any, weight: int) -> None:
        if self._max_weight <= 0 or weight > self._max_weight:
            return
        with self._lock:
            existing = self._data.pop(key, None)
            if existing is not None:
                self._weight -= existing[1]
            self._data[key] = (value, weight)
            self._weight += weight
            while self._weight > self._max_weight and len(self._data) > 1:
                _, (_, evicted_weight) = self._data.popitem(last=False)
                self._weight -= evicted_weight

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._weight = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class RenderCache:
    """Combined encoder-output and decoded-source cache."""

    def __init__(
        self,
        max_encoded_bytes: int = DEFAULT_MAX_ENCODED_BYTES,
        max_decoded_pixels: int = DEFAULT_MAX_DECODED_PIXELS,
    ) -> None:
        self.encoded = _LruCache(max_encoded_bytes)
        self.decoded = _LruCache(max_decoded_pixels)

    # -- decoded sources ---------------------------------------------------
    def get_decoded(self, path: Hashable) -> Any | None:
        return self.decoded.get(path)

    def put_decoded(self, path: Hashable, image: Any) -> None:
        width, height = getattr(image, "size", (0, 0))
        pixels = int(width) * int(height)
        if pixels <= 0 or pixels > MAX_CACHEABLE_SOURCE_PIXELS:
            return
        self.decoded.put(path, image, pixels)

    # -- encoder output ----------------------------------------------------
    def get_encoded(self, key: Hashable) -> Any | None:
        return self.encoded.get(key)

    def put_encoded(self, key: Hashable, value: Any, weight: int | None = None) -> None:
        """Cache encoder output.

        *value* is normally a :class:`~pixelpack.core.image_processor.RenderedImage`;
        its weight defaults to the size of the encoded payload.
        """
        if weight is None:
            weight = len(getattr(value, "data", value))
        self.encoded.put(key, value, weight)

    def clear(self) -> None:
        self.encoded.clear()
        self.decoded.clear()

    def stats(self) -> dict[str, int]:
        return {
            "encoded_entries": len(self.encoded),
            "decoded_entries": len(self.decoded),
            "encoded_hits": self.encoded.hits,
            "encoded_misses": self.encoded.misses,
            "decoded_hits": self.decoded.hits,
            "decoded_misses": self.decoded.misses,
        }
