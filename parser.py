import json
import logging
import hashlib
import imagehash
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps 
from PIL.ExifTags import TAGS, GPSTAGS

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
PHASH_THRESHOLD = 5


SCRIPT_DIR = Path(__file__).parent
IMAGE_DIR = SCRIPT_DIR / "image"

logging.basicConfig(
    filename = SCRIPT_DIR  / "activity.log",
    level = logging.INFO,
    format = '%(asctime)s %(levelname)s: %(message)s',
    filemode = 'w'
)


class ImageDisplayer:
    def __init__(self, max_height=600, padding=20, label_h=36, bg_color=(30, 30, 30), label_color=(200, 200, 200)):
        self.max_height = max_height
        self.padding = padding
        self.label_h = label_h
        self.bg_color = bg_color
        self.label_color = label_color

    def resize_to_height(self, img: Image.Image, target_h: int) -> Image.Image:
        """Scale image to target height, maintaining aspect ratio."""
        scale = target_h / img.height
        return img.resize((int(img.width * scale), target_h), Image.LANCZOS)

    def compare(self, path1: Path, path2: Path) -> Image.Image:
        """
        Place two images side by side on a dark canvas.
        Returns the combined Image.
        """
        # Automatically rotate image based on its EXIF orientation if needed
        img1_raw = ImageOps.exif_transpose(Image.open(path1))
        img2_raw = ImageOps.exif_transpose(Image.open(path2))

        img1 = self.resize_to_height(img1_raw.convert("RGB"), self.max_height)
        img2 = self.resize_to_height(img2_raw.convert("RGB"), self.max_height)
    
        total_w = img1.width + self.padding + img2.width
        total_h = self.label_h + self.max_height
    
        canvas = Image.new("RGB", (total_w, total_h), self.bg_color)
    
        # Paste images below the label row
        canvas.paste(img1, (0, self.label_h))
        canvas.paste(img2, (img1.width + self.padding, self.label_h))
    
        # Draw filename labels
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("arial.ttf", 14)
        except OSError:
            font = ImageFont.load_default()
    
        draw.text((4, 8), Path(path1).name, fill=self.label_color, font=font)
        draw.text((img1.width + self.padding + 4, 8), Path(path2).name, fill=self.label_color, font=font)
    
        return canvas

    def show_and_wait(self, img: Image.Image, title: str = "Duplicate Comparison"):
        """Displays image using Tkinter and blocks execution until the user closes it."""
        import tkinter as tk
        from PIL import ImageTk
        
        root = tk.Tk()
        root.title(title)
        root.resizable(False, False)
        
        # Bring window to the front
        root.lift()
        root.attributes('-topmost', True)
        root.after_idle(root.attributes, '-topmost', False)
        
        photo_img = ImageTk.PhotoImage(img)
        label = tk.Label(root, image=photo_img)
        label.pack()
        
        # Center the window
        root.update_idletasks()
        w = root.winfo_reqwidth()
        h = root.winfo_reqheight()
        ws = root.winfo_screenwidth()
        hs = root.winfo_screenheight()
        x = int(ws/2 - w/2)
        y = int(hs/2 - h/2)
        root.geometry(f'{w}x{h}+{x}+{y}')
        
        # Block until closed
        root.mainloop()

class ReportEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, imagehash.ImageHash):
            return str(obj)
        return super().default(obj)
            

class DuplicateResult:
    """
    Simple container so callers can check status without parsing strings.
    """

    def __init__(self, is_duplicate: bool, reason: str, original_filename: str = None):
        self.is_duplicate = is_duplicate
        self.reason = reason
        self.original_filename = original_filename

    def __str__(self) -> str:
        prefix = "DUPLICATE" if self.is_duplicate else "UNIQUE"
        return f"{prefix} — {self.reason}"


class PhotoScanner:

    def __init__(self, dir_path: Path = IMAGE_DIR, phash_threshold: int = PHASH_THRESHOLD):
        self.dir_path = dir_path
        self.phash_threshold = phash_threshold
        self.database = []
        self.duplicates = []

    # Read folder (subfolder included) and return a list of image paths
    def read_image_path(self) -> list[Path]:

        if not self.dir_path.exists():
            raise FileNotFoundError(f"Folder not found {self.dir_path}")
        
        logging.info(f"Scanning folder {self.dir_path}")

        return sorted(
            path for path in self.dir_path.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )

    # Implement MD5 algorithm to get 32-bits hexa value
    def compute_md5(self, path: Path) -> str:
        # Read binary
        with open(path, "rb") as file:
            return hashlib.md5(file.read()).hexdigest()

    # Implement perception hashing to get 16-bits hexa value
    def compute_phash(self, path: Path) -> imagehash.ImageHash | None:
        try:
            return imagehash.phash(Image.open(path))

        except Exception as e:
            logging.error(f"{path}: {e}")
            return None

    # Extract EXIF metadata: date taken, phone model, camera settings, and GPS info.
    def extract_metadata(self, path: Path) -> dict:
        metadata = {
            "date_time": None,
            "date_original": None,
            "date_digitized": None,
            "make": None,
            "model": None,
            "resolution": None,
            "gps": None
        }

        try:
            with Image.open(path) as img:
                metadata["resolution"] = f"{img.width}x{img.height}"
                exif = img._getexif()

                if not exif:
                    return metadata

                # EXIF data are stored using numeric ID
                for tag_id, value in exif.items():
                    # Map numeric ID to human-readable tag name
                    tag = TAGS.get(tag_id, tag_id)

                    if tag == "DateTime":
                        metadata["date_time"] = str(value)

                    elif tag == "DateTimeOriginal":
                        metadata["date_original"] = str(value)

                    elif tag == "DateTimeDigitized":
                        metadata["date_digitized"] = str(value)

                    elif tag == "Make":
                        metadata["make"] = str(value).replace('\x00', '').strip()

                    elif tag == "Model":
                        metadata["model"] = str(value).replace('\x00', '').strip()

                    elif tag == "GPSInfo":
                        metadata["gps"] = {GPSTAGS.get(t, t): str(value[t]) for t in value}

        except Exception:
            pass

        return metadata


    def build_record(self, path: Path) -> dict:
        return {
            "filename": path.name,
            "filepath": str(path),
            "md5": self.compute_md5(path),
            "phash": self.compute_phash(path),
            "metadata": self.extract_metadata(path),
        }


    def check_duplicate(self, new_data: dict) -> DuplicateResult:
        image_file = new_data["filename"]

        for historical_data in self.database:
            # Check exact byte matched
            if new_data["md5"] == historical_data["md5"]:
                logging.info(f"{image_file} is identified as a duplicate by MD5 algorithm")

                return DuplicateResult(
                    True,
                    f"Exact copy of '{historical_data['filename']}' (MD5)", 
                    historical_data["filepath"]
                )

        for historical_data in self.database:
            # Check visual similarity
            if new_data["phash"] is not None and historical_data["phash"] is not None:  

                # Ensure the value is positive
                distance = abs(new_data["phash"] - historical_data["phash"])

                if distance <= self.phash_threshold:
                    logging.info(f"{image_file} is identified as duplicate by pHash algorithm")

                    return DuplicateResult(
                        True,
                        f"visually similar to '{historical_data['filename']}' (pHash distance={distance})",
                        historical_data["filepath"]
                    )

        new_meta = new_data["metadata"]
        new_dates_set = {
            new_meta.get("date_time"), 
            new_meta.get("date_original"), 
            new_meta.get("date_digitized")
        }
        new_dates_set.discard(None)

        # If date was modified, prompt a warning
        # Set() is used to remove duplicated dates, set()>2 means two different date
        if len(new_dates_set) > 1:
            logging.info(f"{image_file} is identified as mismatched EXIF data (date modified)")

        for historical_data in self.database:
            historical_meta = historical_data["metadata"]
            
            # Collect available dates from historical image
            hist_dates_set = {
                historical_meta.get("date_time"), 
                historical_meta.get("date_original"), 
                historical_meta.get("date_digitized")
            }
            hist_dates_set.discard(None)
            
            # 2) Check if ANY timestamp and phone model matches
            if new_dates_set and hist_dates_set and new_dates_set.intersection(hist_dates_set):
                
                if new_meta["model"] and new_meta["model"] == historical_meta.get("model"):
                    logging.info(f"{image_file} is identified as duplicate due to same metadata")

                    return DuplicateResult(
                        True,
                        f"same timestamp & device as '{historical_data['filename']}'",
                        historical_data["filepath"]
                    )

        # No duplicated found
        logging.info(f"{image_file} is verified as new photo")
        return DuplicateResult(False, "photo is unique")


    def start(self) -> None:
        image_list = self.read_image_path()
        logging.info(f"{len(image_list)} images found")

        print("─" * 40)
        displayer = ImageDisplayer()

        for number, image in enumerate(image_list, start = 1):
            data = self.build_record(image)
            result = self.check_duplicate(data)

            if result.is_duplicate:
                combined_img = displayer.compare(image, Path(result.original_filename))
                
                print(f"{number}) ❌ {image.name} -> {result.original_filename}")
                data["duplicate_of"] = result.original_filename
                data["duplicate_reason"] = result.reason
                self.duplicates.append(data)
                
                # Show the image window and block until the user closes it
                title = f"Duplicate: {image.name} <-> {Path(result.original_filename).name}"
                displayer.show_and_wait(combined_img, title)

            else:
                print(f"{number}) ✅ {image.name}")
                self.database.append(data)

        print("─" * 40)
        print(f"Duplicates found: {len(self.duplicates)}")
        print(f"Unique photos: {len(self.database)}")


    def export_report(self, output_json: Path = SCRIPT_DIR / "report.json") -> None:
        export_data = {
            "unique_photos": self.database,
            "duplicates": self.duplicates
        }

        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=4, cls=ReportEncoder)
        
        print(f"Detailed JSON report exported to {output_json}")


if __name__ == "__main__":
    logging.info("Program started")

    try:
        scanner = PhotoScanner()
        scanner.start()
        scanner.export_report()

    except FileNotFoundError as e:
        logging.error(e)

    finally:
        logging.info("Program ended")