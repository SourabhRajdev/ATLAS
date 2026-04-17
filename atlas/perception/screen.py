"""Screen capture + OCR pipeline. On-demand only, never continuous.

Capture path: front-window only via CGWindowListCreateImage, downscaled.
OCR: rapidocr-onnxruntime if available (fast on M-series), else skipped.
Outputs are scrubbed for secrets via privacy.scrub_ocr() before storage.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from atlas.perception.privacy import scrub_ocr, is_app_blacklisted

logger = logging.getLogger("atlas.perception.screen")

DEFAULT_DIR = Path("~/.atlas/screenshots/rolling").expanduser()
RETENTION_HOURS = 24


class ScreenPipeline:
    def __init__(self, output_dir: Path | None = None) -> None:
        self.dir = output_dir or DEFAULT_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self._ocr_engine = None  # lazy

    # ---------- capture ----------

    def capture_front_window(self, app_name: str | None = None) -> Path | None:
        """Capture front window only. Returns path or None if blocked/failed."""
        if app_name and is_app_blacklisted(app_name):
            logger.info("blocked by privacy: %s", app_name)
            return None
        try:
            import Quartz  # type: ignore
            window_list = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID,
            )
            if not window_list:
                return None
            front = window_list[0]
            window_id = front.get("kCGWindowNumber")
            if window_id is None:
                return None
            image = Quartz.CGWindowListCreateImage(
                Quartz.CGRectNull,
                Quartz.kCGWindowListOptionIncludingWindow,
                window_id,
                Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageNominalResolution,
            )
            if image is None:
                return None
            return self._save_image(image)
        except Exception as e:
            logger.debug("capture failed: %s", e)
            return None

    def _save_image(self, cg_image) -> Path | None:
        try:
            import Quartz  # type: ignore
            ts = int(time.time() * 1000)
            path = self.dir / f"win-{ts}.png"
            url = Quartz.CFURLCreateWithFileSystemPath(
                None, str(path), Quartz.kCFURLPOSIXPathStyle, False,
            )
            dest = Quartz.CGImageDestinationCreateWithURL(
                url, "public.png", 1, None,
            )
            if dest is None:
                return None
            Quartz.CGImageDestinationAddImage(dest, cg_image, None)
            Quartz.CGImageDestinationFinalize(dest)
            return path
        except Exception as e:
            logger.debug("save image failed: %s", e)
            return None

    # ---------- OCR ----------

    def ocr(self, image_path: Path) -> str:
        if not image_path or not image_path.exists():
            return ""
        engine = self._get_ocr()
        if engine is None:
            return ""
        try:
            result, _ = engine(str(image_path))
            if not result:
                return ""
            text = "\n".join(line[1] for line in result if len(line) >= 2)
            return scrub_ocr(text)
        except Exception as e:
            logger.debug("ocr failed: %s", e)
            return ""

    def _get_ocr(self):
        if self._ocr_engine is not None:
            return self._ocr_engine
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
            self._ocr_engine = RapidOCR()
            return self._ocr_engine
        except Exception as e:
            logger.warning("rapidocr unavailable: %s — install rapidocr-onnxruntime", e)
            self._ocr_engine = False
            return None

    # ---------- maintenance ----------

    def prune_old(self) -> int:
        """Delete screenshots older than retention window. Returns count deleted."""
        cutoff = time.time() - RETENTION_HOURS * 3600
        n = 0
        for p in self.dir.glob("*.png"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    n += 1
            except OSError:
                pass
        return n

    @staticmethod
    def hash_path(p: Path) -> str:
        try:
            return hashlib.sha1(p.read_bytes()).hexdigest()[:16]
        except OSError:
            return ""
