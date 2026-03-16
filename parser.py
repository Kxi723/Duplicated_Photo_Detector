import json
import hashlib
import imagehash
from pathlib import Path
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"}
PHASH_THRESHOLD = 5


def read_photo_path(folder_path) -> list[Path]:
    """
    Walk through a folder (and all subfolders) and collect every image file.
    """

    folder = Path(folder_path)

    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")

    # Get photo accepted in directory & subdirectories
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


# Implement MD5 algorithm to get 32-bits hexa value
def compute_md5(path: Path) -> str:
    # Read binary
    with open(path, "rb") as file:
        return hashlib.md5(file.read()).hexdigest()


# Implement perception hashing to get 16-bits hexa value
def compute_phash(path: Path) -> imagehash:
    try:
        return imagehash.phash(Image.open(path))
    
    except Exception as e:
        print(f"[phash error] {path}: {e}")
        return None


# Extract EXIF metadata: date taken, phone model, camera settings, and GPS info.
def extract_metadata(path: Path) -> dict:
    metadata = {
        "file_size_bytes": path.stat().st_size,
        "date": None,
        "make": None,
        "model": None,
        "resolution": None,
        "camera_settings": {},
        "gps": None
    }

    try:
        with Image.open(path) as img:
            metadata["resolution"] = f"{img.width}x{img.height}"
            exif = img._getexif()

            if not exif:
                return metadata

            for tag_id, value in exif.items():
                tag = TAGS.get(tag_id, tag_id)

                if tag == "DateTimeOriginal":
                    metadata["date"] = str(value)
                elif tag == "Make":
                    metadata["make"] = str(value).replace('\x00', '').strip()
                elif tag == "Model":
                    metadata["model"] = str(value).replace('\x00', '').strip()
                elif tag in ["FNumber", "ExposureTime", "ISOSpeedRatings", "FocalLength", "LensModel"]:
                    metadata["camera_settings"][tag] = str(value)
                elif tag == "GPSInfo":
                    # Convert GPS values to string for JSON serialization
                    metadata["gps"] = {GPSTAGS.get(t, t): str(value[t]) for t in value}

    except Exception:
        pass
    return metadata


def build_record(path: Path) -> dict:
    """Compute and bundle all fingerprints for one image into a record dict."""
    return {
        "filename": path.name,
        "file":     str(path),
        "md5":      compute_md5(path),
        "phash":    compute_phash(path),
        "metadata": extract_metadata(path),
    }

# ── Step 3: Check one image against existing database ────────────────────────

class DuplicateResult:
    """Simple container so callers can check status without parsing strings."""
    def __init__(self, is_duplicate: bool, reason: str):
        self.is_duplicate = is_duplicate
        self.reason       = reason

    def __str__(self) -> str:
        prefix = "DUPLICATE" if self.is_duplicate else "UNIQUE"
        return f"{prefix} — {self.reason}"


def check_duplicate(record: dict, database: list[dict], phash_threshold: int) -> DuplicateResult:
    """
    Compare a pre-built record against the database.
    Checks in order: MD5 → pHash → metadata timestamp+model.
    Returns a DuplicateResult (never prints).
    """
    # 1. Exact byte match
    for entry in database:
        if record["md5"] == entry["md5"]:
            return DuplicateResult(True, f"exact copy of '{entry['filename']}' (MD5)")

    # 2. Visual similarity
    if record["phash"] is not None:
        for entry in database:
            if entry["phash"] is not None:
                distance = abs(record["phash"] - entry["phash"])
                if distance <= phash_threshold:
                    return DuplicateResult(
                        True,
                        f"visually similar to '{entry['filename']}' "
                        f"(pHash distance={distance}, threshold={phash_threshold})"
                    )

    # 3. Same timestamp + phone model (warning, not a hard duplicate)
    meta = record["metadata"]
    for entry in database:
        ex = entry["metadata"]
        if meta["date"] and meta["date"] == ex.get("date"):
            if meta["model"] and meta["model"] == ex.get("model"):
                return DuplicateResult(
                    True,
                    f"same timestamp & device as '{entry['filename']}'"
                )

    return DuplicateResult(False, "photo is unique")


# ── Step 4: Scan entire folder ────────────────────────────────────────────────

def scan_folder(folder_path: str | Path, phash_threshold: int = PHASH_THRESHOLD, output_json: str = "report.json") -> None:
    """
    Main entry point.
    Scans all images in a folder, detects duplicates, and prints/exports a JSON report.
    """
    photo_paths = read_photo_path(folder_path)
    total       = len(photo_paths)

    print(f"\nScanning : {folder_path}")
    print(f"Found    : {total} image(s)")
    print("─" * 60)

    database    = []   # unique images only
    all_results = []   # (filename, DuplicateResult) for every image

    for i, path in enumerate(photo_paths, 1):
        record = build_record(path)
        result = check_duplicate(record, database, phash_threshold)
        all_results.append((path.name, result))

        status = "DUP" if result.is_duplicate else "UNIQ"
        print(f"[{i}/{total}] {status} {path.name}")
        print(f"        {result.reason}")

        meta = record["metadata"]
        print(f"        Date       : {meta.get('date') or 'n/a'}")
        print(f"        Model      : {meta.get('model') or 'n/a'}")
        print(f"        Resolution : {meta.get('resolution') or 'n/a'}")
        print(f"        GPS        : {'present' if meta.get('gps') else 'n/a'}")
        print()

        if not result.is_duplicate:
            database.append(record)

    # ── Summary ───────────────────────────────────────────────────────────────
    duplicates = [r for r in all_results if r[1].is_duplicate]
    unique     = [r for r in all_results if not r[1].is_duplicate]

    print("=" * 60)
    print(f"  Total      : {total}")
    print(f"  Unique     : {len(unique)}")
    print(f"  Duplicates : {len(duplicates)}")
    print("=" * 60)

    if duplicates:
        print("\n  Flagged files:")
        for name, result in duplicates:
            print(f"    • {name}")
            print(f"      {result.reason}")
    else:
        print("\n  No duplicates found.")

    # ── Export to JSON ────────────────────────────────────────────────────────
    report_data = {
        "summary": {
            "folder_scanned": str(folder_path),
            "total_images": total,
            "unique_images": len(unique),
            "duplicate_images": len(duplicates),
        },
        "database": [
            {
                "file": r["file"],
                "md5": r["md5"],
                "phash": str(r["phash"]) if r["phash"] else None,
                "metadata": r["metadata"]
            }
            for r in database
        ],
        "duplicates": [
            {
                "file": name,
                "reason": result.reason
            }
            for name, result in duplicates
        ]
    }
    
    try:
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=4)
        print(f"\n  Report successfully saved to {output_json}")
    except Exception as e:
        print(f"\n  Failed to save JSON report: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    PHOTO_FOLDER = r"C:\Users\jason.kx.lai\Downloads\Jason_Lai\Code\photo_parser\photo"

    scan_folder(PHOTO_FOLDER)