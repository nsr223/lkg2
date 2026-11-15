"""Download the ready-made search database (search.db) for the RAG scenario from the GitHub release.

    python scripts/get_search_db.py

About 700 MB to download, 1.1 GB once unpacked. Uses only the Python standard library.
To build it yourself instead, see scripts/build_search.py.
"""
import gzip
import pathlib
import shutil
import sys
import urllib.request

TAG = "search-db-2026-11-15"
URL = f"https://github.com/nsr223/lkg2/releases/download/{TAG}/search.db.gz"

root = pathlib.Path(__file__).resolve().parent.parent
gz, out = root / "search.db.gz", root / "search.db"

print(f"Downloading {URL}")
with urllib.request.urlopen(URL) as r, open(gz, "wb") as f:
    total = int(r.headers.get("Content-Length", 0)); done = 0
    while chunk := r.read(1 << 20):
        f.write(chunk); done += len(chunk)
        if total:
            print(f"\r  {done / 1e6:.0f} / {total / 1e6:.0f} MB", end="", flush=True)
print()
print("Unpacking…")
try:
    with gzip.open(gz, "rb") as src, open(out, "wb") as dst:   # gzip's own check catches a damaged download
        shutil.copyfileobj(src, dst, 1 << 20)
except (OSError, EOFError):
    out.unlink(missing_ok=True); gz.unlink()
    sys.exit("The download is incomplete or damaged. Please try again.")
gz.unlink()
print(f"Done: {out} ({out.stat().st_size / 1e9:.1f} GB). The Q&A tab can now use 'With LKG 2.0'; reload the page.")
