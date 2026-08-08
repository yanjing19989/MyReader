import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("MYREADER_DATA_DIR", ROOT / "data")).expanduser().resolve()
DB_PATH = Path(os.getenv("MYREADER_DB_PATH", DATA_DIR / "myreader.db")).expanduser().resolve()
CACHE_DIR = Path(os.getenv("MYREADER_CACHE_DIR", DATA_DIR / "cache")).expanduser().resolve()
THUMB_DIR = CACHE_DIR / "thumbs"
UPLOAD_DIR = DATA_DIR / "covers"
VIEWER_PATH = Path(
    os.getenv("MYREADER_VIEWER_PATH", r"D:\myprogram\BandiView\BandiView.exe")
).expanduser()


def ensure_dirs() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

