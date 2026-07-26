from __future__ import annotations

from pathlib import Path

from polis.store.engine import StoreError


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise StoreError(f"blob key escapes root: {key!r}")
        return path

    async def put(self, key: str, data: bytes, *, content_type: str = "application/json") -> str:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self.uri(key)

    async def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    async def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def uri(self, key: str) -> str:
        return self._path(key).as_uri()


def open_blobs(url: str) -> LocalBlobStore:
    if not url.startswith("file://"):
        raise StoreError("this milestone supports file:// blob storage; S3 lands in M6")
    raw = url.removeprefix("file://")
    return LocalBlobStore(Path(raw))
