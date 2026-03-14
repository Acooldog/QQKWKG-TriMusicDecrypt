from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TIT2, TPE1
from mutagen.mp4 import MP4, MP4Cover
from mutagen.wave import WAVE

from src.Infrastructure.runtime_paths import RuntimePaths


logger = logging.getLogger("qkkdecrypt.infrastructure.cover_art")


@dataclass(slots=True)
class CoverArtResult:
    status: str
    message: str
    image_path: str | None = None
    source: str | None = None


@dataclass(slots=True)
class AlbumMetadataResult:
    status: str
    message: str
    source: str | None = None
    updated_fields: tuple[str, ...] = field(default_factory=tuple)


class CoverArtService:
    """Supplement cover art and album metadata with a local-first strategy."""

    SEARCH_ENDPOINT = "https://u.y.qq.com/cgi-bin/musicu.fcg"
    COVER_URL_TEMPLATE = "https://y.gtimg.cn/music/photo_new/T002R500x500M000{albummid}.jpg"
    IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
    SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac"}
    ALBUM_METADATA_EXTENSIONS = {".m4a", ".wav"}

    def __init__(self) -> None:
        self.paths = RuntimePaths.discover()
        self.cache_dir = self.paths.plugins_dir / "cover_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._search_cache: dict[str, dict[str, str] | None] = {}
        self._download_cache: dict[str, pathlib.Path | None] = {}

    def supplement_cover(
        self,
        audio_path: str | pathlib.Path,
        source_file_path: str | pathlib.Path,
        media_summary: dict[str, Any] | None = None,
    ) -> CoverArtResult:
        audio = pathlib.Path(audio_path)
        source = pathlib.Path(source_file_path)
        audio_ext = audio.suffix.lower()
        if audio_ext not in self.SUPPORTED_AUDIO_EXTENSIONS:
            return CoverArtResult(
                status="unsupported",
                message=f"cover embedding is not supported for {audio_ext or 'unknown'}",
            )

        if media_summary and bool(media_summary.get("has_cover") or media_summary.get("cover")):
            return CoverArtResult(status="already_present", message="cover art already present")

        title, artist, album = self._extract_music_identity(audio, source, media_summary or {})
        if not title and not artist:
            return CoverArtResult(status="missing_metadata", message="no usable title/artist for cover lookup")

        local_image = self._find_local_cover(source, audio, title, artist, album)
        if local_image:
            if self._embed_cover(audio, local_image):
                return CoverArtResult("embedded", "embedded cover from local file", str(local_image), "local")
            return CoverArtResult("embed_failed", "failed to embed local cover", str(local_image), "local")

        cache_key = self._cache_key(title, artist, album)
        cached_image = self._find_cached_cover(cache_key)
        if cached_image:
            if self._embed_cover(audio, cached_image):
                return CoverArtResult("embedded", "embedded cover from cache", str(cached_image), "cache")
            return CoverArtResult("embed_failed", "failed to embed cached cover", str(cached_image), "cache")

        search_result = self._search_cover_online(title, artist)
        if not search_result:
            return CoverArtResult("not_found", "cover art was not found locally or online")

        downloaded = self._download_cover_image(search_result["albummid"], cache_key)
        if not downloaded:
            return CoverArtResult("download_failed", "failed to download cover art")

        if self._embed_cover(audio, downloaded):
            return CoverArtResult("embedded", "embedded cover from QQ network fallback", str(downloaded), "network")
        return CoverArtResult("embed_failed", "failed to embed downloaded cover", str(downloaded), "network")

    def supplement_album_metadata(
        self,
        audio_path: str | pathlib.Path,
        source_file_path: str | pathlib.Path,
        media_summary: dict[str, Any] | None = None,
    ) -> AlbumMetadataResult:
        audio = pathlib.Path(audio_path)
        source = pathlib.Path(source_file_path)
        audio_ext = audio.suffix.lower()
        if audio_ext not in self.ALBUM_METADATA_EXTENSIONS:
            return AlbumMetadataResult(
                status="unsupported",
                message=f"album metadata supplementation is not supported for {audio_ext or 'unknown'}",
            )

        current = self._extract_music_identity(audio, source, media_summary or {})
        title, artist, album = current
        if title and artist and album:
            return AlbumMetadataResult(status="already_present", message="album metadata already present", source="local")

        search_result = self._search_cover_online(title, artist)
        if not search_result:
            return AlbumMetadataResult(status="not_found", message="album metadata was not found locally or online")

        target_title = title or str(search_result.get("title") or "").strip()
        target_artist = artist or str(search_result.get("artist") or "").strip()
        target_album = album or str(search_result.get("album") or "").strip()
        updated_fields = self._embed_album_metadata(
            audio_path=audio,
            title=target_title,
            artist=target_artist,
            album=target_album,
            current_title=title,
            current_artist=artist,
            current_album=album,
        )
        if updated_fields:
            return AlbumMetadataResult(
                status="embedded",
                message="album metadata supplemented",
                source="network" if not album else "local",
                updated_fields=tuple(updated_fields),
            )
        return AlbumMetadataResult(status="already_present", message="album metadata already present", source="local")

    def _extract_music_identity(
        self,
        audio_path: pathlib.Path,
        source_file_path: pathlib.Path,
        media_summary: dict[str, Any],
    ) -> tuple[str, str, str]:
        tags = media_summary.get("tags") if isinstance(media_summary.get("tags"), dict) else {}
        if not tags and isinstance(media_summary.get("metadata"), dict):
            tags = media_summary.get("metadata") or {}

        title = self._first_non_empty(tags.get("title"), tags.get("TITLE"))
        artist = self._first_non_empty(tags.get("artist"), tags.get("ARTIST"), tags.get("album_artist"))
        album = self._first_non_empty(tags.get("album"), tags.get("ALBUM"))
        if title or artist:
            return str(title or "").strip(), str(artist or "").strip(), str(album or "").strip()

        stem = source_file_path.stem if source_file_path else audio_path.stem
        stem = re.sub(r"_([A-Za-z0-9]{1,6})$", "", stem)
        if " - " in stem:
            artist_part, title_part = stem.split(" - ", 1)
            return title_part.strip(), artist_part.strip(), ""
        return stem.strip(), "", ""

    @staticmethod
    def _first_non_empty(*values: object) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _find_local_cover(
        self,
        source_file_path: pathlib.Path,
        audio_path: pathlib.Path,
        title: str,
        artist: str,
        album: str,
    ) -> pathlib.Path | None:
        candidates: list[pathlib.Path] = []
        for ext in self.IMAGE_EXTENSIONS:
            candidates.append(source_file_path.with_suffix(ext))
            candidates.append(audio_path.with_suffix(ext))

        for folder in {source_file_path.parent, audio_path.parent}:
            for common_name in ("cover", "folder", "album", "front"):
                for ext in self.IMAGE_EXTENSIONS:
                    candidates.append(folder / f"{common_name}{ext}")
            for basis in filter(None, {title, album, f"{artist} - {title}" if artist and title else ""}):
                safe = self._sanitize_file_name(basis)
                for ext in self.IMAGE_EXTENSIONS:
                    candidates.append(folder / f"{safe}{ext}")

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate

        cache_key = self._cache_key(title, artist, album)
        return self._find_cached_cover(cache_key)

    @staticmethod
    def _sanitize_file_name(value: str) -> str:
        sanitized = re.sub(r'[<>:"/\\\\|?*]', "_", value.strip())
        return re.sub(r"\s+", " ", sanitized)

    @staticmethod
    def _cache_key(title: str, artist: str, album: str) -> str:
        basis = "|".join([title.strip().lower(), artist.strip().lower(), album.strip().lower()])
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()

    def _find_cached_cover(self, cache_key: str) -> pathlib.Path | None:
        cached = self._download_cache.get(cache_key)
        if cached is not None and cached.exists():
            return cached
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            candidate = self.cache_dir / f"{cache_key}{ext}"
            if candidate.exists() and candidate.is_file():
                self._download_cache[cache_key] = candidate
                return candidate
        return None

    def _search_cover_online(self, title: str, artist: str) -> dict[str, str] | None:
        query = " ".join(part for part in (title, artist) if part).strip()
        if not query:
            return None

        query_key = self._cache_key(title, artist, "")
        if query_key in self._search_cache:
            return self._search_cache[query_key]

        payload = {
            "comm": {"ct": "19", "cv": "1859", "uin": "0"},
            "req": {
                "method": "DoSearchForQQMusicDesktop",
                "module": "music.search.SearchCgiService",
                "param": {"grp": 1, "num_per_page": 10, "page_num": 1, "query": query, "search_type": 0},
            },
        }
        request = urllib.request.Request(
            self.SEARCH_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json;charset=utf-8", "User-Agent": "Mozilla/5.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                data = json.load(response)
        except Exception:
            logger.exception("QQ cover search failed for query=%s", query)
            self._search_cache[query_key] = None
            return None

        song_list = (((data.get("req") or {}).get("data") or {}).get("body") or {}).get("song") or {}
        items = song_list.get("list") or []
        best: dict[str, Any] | None = None
        best_score = -1
        for item in items:
            score = self._score_search_item(item, title, artist)
            if score > best_score:
                best = item
                best_score = score
        if not best or best_score < 2:
            self._search_cache[query_key] = None
            return None

        album = best.get("album") or {}
        albummid = album.get("mid")
        if not albummid:
            self._search_cache[query_key] = None
            return None
        singers = best.get("singer") or []
        singer_names = " / ".join(
            str(singer.get("name") or "").strip()
            for singer in singers
            if isinstance(singer, dict) and str(singer.get("name") or "").strip()
        )
        result = {
            "albummid": str(albummid),
            "album": str(album.get("name") or "").strip(),
            "title": str(best.get("name") or "").strip(),
            "artist": singer_names,
        }
        self._search_cache[query_key] = result
        return result

    def _score_search_item(self, item: dict[str, Any], title: str, artist: str) -> int:
        item_title = self._normalize_compare_text(str(item.get("name") or ""))
        item_artists = [
            self._normalize_compare_text(str(singer.get("name") or ""))
            for singer in (item.get("singer") or [])
            if isinstance(singer, dict)
        ]
        title_norm = self._normalize_compare_text(title)
        artist_norm = self._normalize_compare_text(artist)
        score = 0
        if title_norm and item_title == title_norm:
            score += 4
        elif title_norm and title_norm in item_title:
            score += 2
        if artist_norm and any(artist_norm == singer or artist_norm in singer for singer in item_artists):
            score += 3
        return score

    @staticmethod
    def _normalize_compare_text(value: str) -> str:
        lowered = value.lower().strip()
        lowered = re.sub(r"\(.*?\)", "", lowered)
        lowered = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", lowered)
        return lowered

    def _download_cover_image(self, albummid: str, cache_key: str) -> pathlib.Path | None:
        cached = self._download_cache.get(cache_key)
        if cached is not None and cached.exists():
            return cached
        existing = self._find_cached_cover(cache_key)
        if existing is not None:
            return existing
        url = self.COVER_URL_TEMPLATE.format(albummid=albummid)
        cache_path = self.cache_dir / f"{cache_key}.jpg"
        try:
            with urllib.request.urlopen(url, timeout=12) as response:
                data = response.read()
            if not data:
                self._download_cache[cache_key] = None
                return None
            cache_path.write_bytes(data)
            self._download_cache[cache_key] = cache_path
            return cache_path
        except Exception:
            logger.exception("Failed to download cover art: %s", url)
            self._download_cache[cache_key] = None
            return None

    def _embed_cover(self, audio_path: pathlib.Path, image_path: pathlib.Path) -> bool:
        try:
            image_bytes = image_path.read_bytes()
            mime, picture_type = self._detect_image_format(image_bytes)
            if not mime:
                return False
            suffix = audio_path.suffix.lower()
            if suffix == ".mp3":
                return self._embed_mp3(audio_path, image_bytes, mime)
            if suffix == ".m4a":
                return self._embed_m4a_cover(audio_path, image_bytes, picture_type)
            if suffix == ".flac":
                return self._embed_flac(audio_path, image_bytes, mime)
            return False
        except Exception:
            logger.exception("Failed to embed cover art: %s", audio_path)
            return False

    @staticmethod
    def _detect_image_format(image_bytes: bytes) -> tuple[str | None, int | None]:
        if image_bytes.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", MP4Cover.FORMAT_JPEG
        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", MP4Cover.FORMAT_PNG
        return None, None

    @staticmethod
    def _embed_mp3(audio_path: pathlib.Path, image_bytes: bytes, mime: str) -> bool:
        try:
            tags = ID3(str(audio_path))
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_bytes))
        tags.save(str(audio_path), v2_version=3)
        return True

    @staticmethod
    def _embed_m4a_cover(audio_path: pathlib.Path, image_bytes: bytes, picture_type: int | None) -> bool:
        if picture_type is None:
            return False
        audio = MP4(str(audio_path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags["covr"] = [MP4Cover(image_bytes, imageformat=picture_type)]
        audio.save()
        return True

    @staticmethod
    def _embed_flac(audio_path: pathlib.Path, image_bytes: bytes, mime: str) -> bool:
        audio = FLAC(str(audio_path))
        picture = Picture()
        picture.type = 3
        picture.mime = mime
        picture.desc = "Cover"
        picture.data = image_bytes
        audio.clear_pictures()
        audio.add_picture(picture)
        audio.save()
        return True

    def _embed_album_metadata(
        self,
        *,
        audio_path: pathlib.Path,
        title: str,
        artist: str,
        album: str,
        current_title: str,
        current_artist: str,
        current_album: str,
    ) -> list[str]:
        suffix = audio_path.suffix.lower()
        updated_fields: list[str] = []
        if suffix == ".m4a":
            audio = MP4(str(audio_path))
            if audio.tags is None:
                audio.add_tags()
            if title and not current_title:
                audio.tags["\xa9nam"] = [title]
                updated_fields.append("title")
            if artist and not current_artist:
                audio.tags["\xa9ART"] = [artist]
                updated_fields.append("artist")
            if album and not current_album:
                audio.tags["\xa9alb"] = [album]
                updated_fields.append("album")
            if updated_fields:
                audio.save()
            return updated_fields
        if suffix == ".wav":
            audio = WAVE(str(audio_path))
            if audio.tags is None:
                audio.add_tags()
            if title and not current_title:
                audio.tags.delall("TIT2")
                audio.tags.add(TIT2(encoding=3, text=[title]))
                updated_fields.append("title")
            if artist and not current_artist:
                audio.tags.delall("TPE1")
                audio.tags.add(TPE1(encoding=3, text=[artist]))
                updated_fields.append("artist")
            if album and not current_album:
                audio.tags.delall("TALB")
                audio.tags.add(TALB(encoding=3, text=[album]))
                updated_fields.append("album")
            if updated_fields:
                audio.save()
            return updated_fields
        return updated_fields
