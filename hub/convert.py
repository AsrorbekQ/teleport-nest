"""Document conversion through Calibre's ebook-convert."""

from __future__ import annotations

import subprocess
from pathlib import Path

CONVERTIBLE = {".pdf", ".docx", ".doc", ".rtf", ".odt", ".mobi", ".azw", ".azw3", ".fb2", ".html", ".htm", ".md",
               ".txt", ".txtz", ".lit", ".pdb", ".djvu", ".cbz", ".cbr"}


class ConversionError(Exception):
    pass


def convert_to_epub(source: Path, destination: Path, calibre: str, timeout: int = 900) -> Path:
    calibre_path = Path(calibre)
    if not calibre_path.exists():
        raise ConversionError(f"Calibre not found at {calibre}. Install Calibre or set paths.calibre in config.toml")
    if source.suffix.lower() == ".epub":
        destination.write_bytes(source.read_bytes())
        return destination
    args = [str(calibre_path), str(source), str(destination), "--output-profile", "generic_eink", "--enable-heuristics"]
    if source.suffix.lower() == ".pdf":
        args += ["--pdf-engine", "pdftohtml"]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ConversionError(f"Calibre timed out after {timeout}s") from e
    if proc.returncode != 0 or not destination.exists():
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        raise ConversionError("Calibre failed: " + " | ".join(tail))
    return destination
