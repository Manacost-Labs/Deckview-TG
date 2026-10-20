"""Fail-open, versioned disk cache for rendered deck JPEGs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image
from image_creator.output_resolution import normalize_image_resolution

from deckview.config import DECKVIEW_REDIS_URL, HSJSON_BUILD, HSJSON_LOCALE, WEB_DATABASE_PATH

try:
    from redis import Redis
except Exception:  # pragma: no cover - cache remains fail-open without Redis.
    Redis = None


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_KEY_VERSION = 2
_redis_lock = threading.Lock()
_redis_client = None
_hot_cache_lock = threading.RLock()
_hot_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_PUBLIC_CACHE_DIR_MODE = 0o755
_PUBLIC_CACHE_FILE_MODE = 0o644
_PREVIEW_VERSION = "preview-v1"


def _preview_max_side() -> int:
    try:
        return max(320, min(1024, int(os.getenv("DECKVIEW_RENDER_PREVIEW_MAX_SIDE", "720"))))
    except (TypeError, ValueError):
        return 720


def _preview_quality() -> int:
    try:
        return max(40, min(90, int(os.getenv("DECKVIEW_RENDER_PREVIEW_QUALITY", "76"))))
    except (TypeError, ValueError):
        return 76


def _preview_method() -> int:
    try:
        return max(0, min(6, int(os.getenv("DECKVIEW_RENDER_PREVIEW_METHOD", "2"))))
    except (TypeError, ValueError):
        return 2


def _hot_cache_limit() -> int:
    try:
        return max(32, min(4096, int(os.getenv("DECKVIEW_RENDER_HOT_CACHE_MAX", "512"))))
    except (TypeError, ValueError):
        return 512


def _hot_cache_token(cache_key: str) -> str:
    # Tests and multi-instance deployments can use different cache roots in
    # the same process. Include both paths so one cache cannot leak into the
    # other even when the logical render key is identical.
    return f"{_database_path()}|{_cache_root()}|{cache_key}"


def _hot_cache_get(cache_key: str) -> dict[str, Any] | None:
    token = _hot_cache_token(cache_key)
    with _hot_cache_lock:
        entry = _hot_cache.get(token)
        if entry is None:
            return None
        try:
            expires_at = datetime.fromisoformat(str(entry["expires_at"]))
            if expires_at <= datetime.now(timezone.utc):
                _hot_cache.pop(token, None)
                return None
            artifact = Path(str(entry["artifact_path"]))
            if not artifact.is_file() or artifact.stat().st_size <= 0:
                _hot_cache.pop(token, None)
                return None
            _ensure_public_artifact_path(artifact)
        except Exception:
            _hot_cache.pop(token, None)
            return None
        _hot_cache.move_to_end(token)
        return dict(entry)


def _hot_cache_put(cache_key: str, entry: dict[str, Any]) -> None:
    token = _hot_cache_token(cache_key)
    with _hot_cache_lock:
        _hot_cache[token] = dict(entry)
        _hot_cache.move_to_end(token)
        while len(_hot_cache) > _hot_cache_limit():
            _hot_cache.popitem(last=False)


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def render_cache_read_enabled(scope: str | None = None) -> bool:
    if scope:
        scoped_name = f"DECKVIEW_RENDER_CACHE_READ_{scope.strip().upper()}"
        if os.getenv(scoped_name) is not None:
            return _env_bool(scoped_name)
    return _env_bool("DECKVIEW_RENDER_CACHE_READ")


def render_cache_write_enabled() -> bool:
    return _env_bool("DECKVIEW_RENDER_CACHE_WRITE")


def _resolve_project_path(raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else _PROJECT_ROOT / path


def _database_path() -> Path:
    return _resolve_project_path(os.getenv("WEB_DATABASE_PATH", WEB_DATABASE_PATH))


def _cache_root() -> Path:
    return _resolve_project_path(
        os.getenv("DECKVIEW_RENDER_CACHE_ROOT", "static/generated/render-cache")
    )


def _ensure_public_artifact_path(target: Path) -> None:
    """Keep nginx traversal/read permissions stable under the service umask."""
    cache_root = _cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_root.chmod(_PUBLIC_CACHE_DIR_MODE)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(_PUBLIC_CACHE_DIR_MODE)
    if target.is_file():
        target.chmod(_PUBLIC_CACHE_FILE_MODE)


def _versions() -> dict[str, Any]:
    return {
        "key_v": _KEY_VERSION,
        "renderer": os.getenv("DECKVIEW_RENDERER_VERSION", "python-v1").strip(),
        "template": os.getenv(
            "DECKVIEW_TEMPLATE_VERSION",
            "deckview-2026-08-card-grid-v6",
        ).strip(),
        "card_data": os.getenv(
            "DECKVIEW_CARD_DATA_VERSION",
            f"hsjson-{HSJSON_BUILD}",
        ).strip(),
        "card_assets": os.getenv(
            "DECKVIEW_CARD_ASSET_VERSION",
            "arena-image-v2",
        ).strip(),
        "locale": os.getenv(
            "DECKVIEW_RENDER_LOCALE",
            f"ru_RU/{HSJSON_LOCALE}",
        ).strip(),
        "artifact": os.getenv(
            "DECKVIEW_ARTIFACT_VERSION",
            "jpeg-q92-opt-v1",
        ).strip(),
    }


def _normalized_image_style(image_style: str | None) -> str:
    value = str(image_style or "classic").strip().lower()
    if value in {"classic", "parchment"} or value.startswith(
        ("classic:", "parchment:", "custom:")
    ):
        return value
    return "classic"


def build_render_cache_key(
    deck_code: str,
    deck_name: str | None,
    image_style: str = "classic",
    image_resolution: str = "standard",
) -> str:
    payload = {
        **_versions(),
        "deck_code": (deck_code or "").strip(),
        "deck_name": (deck_name or "").strip(),
    }
    style = _normalized_image_style(image_style)
    if style != "classic":
        payload["image_style"] = style
    resolution = normalize_image_resolution(image_resolution)
    if resolution != "standard":
        payload["image_resolution"] = resolution
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def acquire_render_lock(
    deck_code: str,
    deck_name: str | None,
    image_style: str = "classic",
    image_resolution: str = "standard",
):
    """Serialize identical renders across all workers; fail open on Redis errors."""
    global _redis_client
    if Redis is None or not render_cache_write_enabled():
        return None
    try:
        if _redis_client is None:
            with _redis_lock:
                if _redis_client is None:
                    _redis_client = Redis.from_url(
                        DECKVIEW_REDIS_URL,
                        socket_connect_timeout=1,
                        socket_timeout=2,
                    )
        cache_key = build_render_cache_key(deck_code, deck_name, image_style, image_resolution)
        lock = _redis_client.lock(
            f"deckview:render:{cache_key}",
            timeout=max(30.0, float(os.getenv("DECKVIEW_RENDER_LOCK_TTL", "120"))),
            blocking_timeout=max(
                1.0,
                float(os.getenv("DECKVIEW_RENDER_LOCK_WAIT", "45")),
            ),
            thread_local=False,
        )
        return lock if lock.acquire(blocking=True) else None
    except Exception as exc:
        print(f"[Deckview Render Cache] lock fallback: {type(exc).__name__}: {exc}")
        return None


def release_render_lock(lock) -> None:
    if lock is None:
        return
    try:
        lock.release()
    except Exception as exc:
        print(f"[Deckview Render Cache] unlock fallback: {type(exc).__name__}: {exc}")


def _deck_identity_hash(
    deck_code: str,
    deck_name: str | None,
    image_style: str = "classic",
    image_resolution: str = "standard",
) -> str:
    identity_payload = {
        "deck_code": (deck_code or "").strip(),
        "deck_name": (deck_name or "").strip(),
    }
    style = _normalized_image_style(image_style)
    if style != "classic":
        identity_payload["image_style"] = style
    resolution = normalize_image_resolution(image_resolution)
    if resolution != "standard":
        identity_payload["image_resolution"] = resolution
    identity = json.dumps(
        identity_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_render_cache_schema() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS render_cache (
                cache_key TEXT PRIMARY KEY,
                key_version INTEGER NOT NULL,
                deck_identity_hash TEXT NOT NULL,
                renderer_version TEXT NOT NULL,
                template_version TEXT NOT NULL,
                card_data_version TEXT NOT NULL,
                locale TEXT NOT NULL,
                artifact_version TEXT NOT NULL,
                artifact_relpath TEXT NOT NULL UNIQUE,
                artifact_sha256 TEXT NOT NULL,
                artifact_size_bytes INTEGER NOT NULL,
                cost INTEGER NOT NULL,
                deck_class TEXT,
                deck_mode TEXT,
                card_dbf_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_render_cache_expires
                ON render_cache(expires_at);
            CREATE INDEX IF NOT EXISTS idx_render_cache_identity
                ON render_cache(deck_identity_hash);
            """
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_path(cache_key: str) -> tuple[Path, str]:
    relpath = Path(cache_key[:2]) / f"{cache_key}.jpg"
    return _cache_root() / relpath, (Path("render-cache") / relpath).as_posix()


def _preview_artifact_path(cache_key: str) -> tuple[Path, str]:
    relpath = Path(cache_key[:2]) / f"{cache_key}.{_PREVIEW_VERSION}.webp"
    return _cache_root() / relpath, (Path("render-cache") / relpath).as_posix()


def _valid_webp(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 16:
            return False
        with path.open("rb") as stream:
            header = stream.read(12)
        return header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    except OSError:
        return False


def lookup_render_cache_by_key(cache_key: str) -> dict[str, Any] | None:
    """Resolve an existing immutable render artifact for Telegram downloads.

    Unlike a normal render lookup, this intentionally keeps the exact historic
    renderer/style revision addressed by the button. That lets an old message
    download the same image it displayed even after the active renderer changes.
    """
    normalized_key = str(cache_key or "").strip().lower()
    if len(normalized_key) != 64 or any(
        character not in "0123456789abcdef" for character in normalized_key
    ):
        return None

    try:
        hot = _hot_cache_get(normalized_key)
        if hot is not None:
            hot["cache_layer"] = "memory"
            return hot

        ensure_render_cache_schema()
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM render_cache WHERE cache_key = ? AND expires_at > ?",
                (normalized_key, datetime.now(timezone.utc).isoformat()),
            ).fetchone()
        if row is None:
            return None

        target, expected_relpath = _artifact_path(normalized_key)
        if row["artifact_relpath"] != expected_relpath:
            return None
        if not target.is_file() or target.stat().st_size != row["artifact_size_bytes"]:
            return None
        _ensure_public_artifact_path(target)
        entry = {
            "cache_key": normalized_key,
            "filename": expected_relpath,
            "artifact_path": str(target),
            "cost": row["cost"],
            "deck_class": row["deck_class"],
            "deck_mode": row["deck_mode"],
            "card_dbf_ids": json.loads(row["card_dbf_ids_json"]),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "cache_layer": "disk",
        }
        _hot_cache_put(normalized_key, entry)
        return entry
    except Exception as exc:
        print(
            "[Deckview Render Cache] download lookup miss: "
            f"{type(exc).__name__}: {exc}"
        )
        return None


def attach_render_preview(entry: dict[str, Any]) -> dict[str, Any]:
    """Attach an immutable, fail-open WebP derivative to a render-cache entry."""
    result = dict(entry)
    started_ns = time.perf_counter_ns()
    generated = False
    try:
        cache_key = str(result.get("cache_key") or "").strip().lower()
        if len(cache_key) != 64 or any(char not in "0123456789abcdef" for char in cache_key):
            return result
        source = Path(str(result["artifact_path"])).resolve(strict=True)
        cache_root = _cache_root().resolve(strict=True)
        if not source.is_relative_to(cache_root):
            raise ValueError("preview source escaped cache root")
        target, preview_relpath = _preview_artifact_path(cache_key)
        _ensure_public_artifact_path(target)

        if not _valid_webp(target):
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                with Image.open(source) as opened:
                    image = opened.convert("RGB")
                    image.thumbnail(
                        (_preview_max_side(), _preview_max_side()),
                        Image.Resampling.LANCZOS,
                    )
                    image.save(
                        temporary,
                        "WEBP",
                        quality=_preview_quality(),
                        method=_preview_method(),
                    )
                temporary.chmod(_PUBLIC_CACHE_FILE_MODE)
                # Multiple workers may prepare the same derivative. Publishing
                # with replace keeps every observable file complete.
                os.replace(temporary, target)
                generated = True
            finally:
                if temporary.exists():
                    temporary.unlink()
        _ensure_public_artifact_path(target)
        if not _valid_webp(target):
            return result
        result.update(
            {
                "preview_filename": preview_relpath,
                "preview_artifact_path": str(target),
                "preview_size_bytes": target.stat().st_size,
                "preview_generated": generated,
                "preview_prepare_ms": round(
                    (time.perf_counter_ns() - started_ns) / 1_000_000,
                    3,
                ),
            }
        )
    except Exception as exc:
        print(f"[Deckview Render Cache] preview miss: {type(exc).__name__}: {exc}")
    return result


def store_render_cache(
    *,
    deck_code: str,
    deck_name: str | None,
    source_path: str | os.PathLike[str],
    cost: int,
    deck_class: str | None,
    deck_mode: str | None,
    card_dbf_ids: Iterable[int],
    image_style: str = "classic",
    image_resolution: str = "standard",
    generate_preview: bool = False,
) -> dict[str, Any] | None:
    """Store an existing JPEG without re-encoding it. Any failure is a cache miss."""
    if not render_cache_write_enabled():
        return None

    try:
        source = Path(source_path).resolve(strict=True)
        cache_key = build_render_cache_key(deck_code, deck_name, image_style, image_resolution)
        target, artifact_relpath = _artifact_path(cache_key)
        _ensure_public_artifact_path(target)
        if not target.is_file():
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                try:
                    os.link(source, temporary)
                except OSError:
                    shutil.copy2(source, temporary)
                # Render-cache artifacts are addressed by an unguessable
                # content/version key and served as public deck previews.
                # Apply the serving mode before publication so nginx never
                # observes a cache entry it cannot read.
                temporary.chmod(_PUBLIC_CACHE_FILE_MODE)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        else:
            # Repair artifacts written by older workers under a restrictive
            # service umask when they are encountered again.
            target.chmod(_PUBLIC_CACHE_FILE_MODE)
        _ensure_public_artifact_path(target)

        size = target.stat().st_size
        if size <= 0:
            raise ValueError("cached artifact is empty")
        versions = _versions()
        now = datetime.now(timezone.utc)
        ttl_hours = max(
            1.0,
            float(os.getenv("DECKVIEW_RENDER_CACHE_TTL_HOURS", "504").replace(",", ".")),
        )
        expires_at = now + timedelta(hours=ttl_hours)
        card_ids = [int(value) for value in card_dbf_ids]

        ensure_render_cache_schema()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO render_cache (
                    cache_key, key_version, deck_identity_hash, renderer_version,
                    template_version, card_data_version, locale, artifact_version,
                    artifact_relpath, artifact_sha256, artifact_size_bytes, cost,
                    deck_class, deck_mode, card_dbf_ids_json, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    artifact_relpath=excluded.artifact_relpath,
                    artifact_sha256=excluded.artifact_sha256,
                    artifact_size_bytes=excluded.artifact_size_bytes,
                    cost=excluded.cost,
                    deck_class=excluded.deck_class,
                    deck_mode=excluded.deck_mode,
                    card_dbf_ids_json=excluded.card_dbf_ids_json,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at
                """,
                (
                    cache_key,
                    _KEY_VERSION,
                    _deck_identity_hash(deck_code, deck_name, image_style, image_resolution),
                    versions["renderer"],
                    versions["template"],
                    versions["card_data"],
                    versions["locale"],
                    versions["artifact"],
                    artifact_relpath,
                    _sha256_file(target),
                    size,
                    int(cost or 0),
                    (deck_class or "").strip() or None,
                    (deck_mode or "").strip() or None,
                    json.dumps(card_ids, separators=(",", ":")),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        entry = {
            "cache_key": cache_key,
            "deck_code": (deck_code or "").strip(),
            "deck_name": (deck_name or "").strip() or None,
            "image_style": _normalized_image_style(image_style),
            "image_resolution": normalize_image_resolution(image_resolution),
            "filename": artifact_relpath,
            "artifact_relpath": artifact_relpath,
            "artifact_path": str(target),
            "cost": int(cost or 0),
            "deck_class": (deck_class or "").strip() or None,
            "deck_mode": (deck_mode or "").strip() or None,
            "card_dbf_ids": card_ids,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        if generate_preview:
            entry = attach_render_preview(entry)
        _hot_cache_put(cache_key, entry)
        return entry
    except Exception as exc:
        print(f"[Deckview Render Cache] store miss: {type(exc).__name__}: {exc}")
        return None


def lookup_render_cache(
    deck_code: str,
    deck_name: str | None,
    *,
    scope: str | None = None,
    image_style: str = "classic",
    image_resolution: str = "standard",
) -> dict[str, Any] | None:
    """Return a valid current-version entry, otherwise fail open as a miss."""
    if not render_cache_read_enabled(scope):
        return None

    try:
        cache_key = build_render_cache_key(deck_code, deck_name, image_style, image_resolution)
        hot = _hot_cache_get(cache_key)
        if hot is not None:
            if scope == "api":
                hot = attach_render_preview(hot)
            _hot_cache_put(cache_key, hot)
            hot["cache_layer"] = "memory"
            return hot

        ensure_render_cache_schema()
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM render_cache WHERE cache_key = ? AND expires_at > ?",
                (cache_key, datetime.now(timezone.utc).isoformat()),
            ).fetchone()
        if row is None:
            return None

        target, expected_relpath = _artifact_path(cache_key)
        if row["artifact_relpath"] != expected_relpath:
            return None
        if not target.is_file() or target.stat().st_size != row["artifact_size_bytes"]:
            return None
        _ensure_public_artifact_path(target)
        entry = {
            "cache_key": cache_key,
            "deck_code": (deck_code or "").strip(),
            "deck_name": (deck_name or "").strip() or None,
            "image_style": _normalized_image_style(image_style),
            "image_resolution": normalize_image_resolution(image_resolution),
            "filename": expected_relpath,
            "artifact_path": str(target),
            "cost": row["cost"],
            "deck_class": row["deck_class"],
            "deck_mode": row["deck_mode"],
            "card_dbf_ids": json.loads(row["card_dbf_ids_json"]),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "cache_layer": "disk",
        }
        if scope == "api":
            entry = attach_render_preview(entry)
        _hot_cache_put(cache_key, entry)
        return entry
    except Exception as exc:
        print(f"[Deckview Render Cache] lookup miss: {type(exc).__name__}: {exc}")
        return None


def materialize_render_cache(
    entry: dict[str, Any],
    destination: str | os.PathLike[str],
) -> str | None:
    """Atomically hardlink/copy a trusted cache artifact to a request-local path."""
    try:
        source = Path(str(entry["artifact_path"])).resolve(strict=True)
        cache_root = _cache_root().resolve(strict=True)
        if not source.is_relative_to(cache_root):
            raise ValueError("cache artifact escaped cache root")

        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            try:
                os.link(source, temporary)
            except OSError:
                shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return str(target)
    except Exception as exc:
        print(f"[Deckview Render Cache] materialize miss: {type(exc).__name__}: {exc}")
        return None
