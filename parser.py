import json
import logging
import hashlib
import imagehash
from pathlib import Path
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk
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
    """
    Simple container for displaying duplicate image for user to see
    """

    def __init__(self, background_colour = (240, 240, 240), padding = 5,
                max_height = 500, label_h = 20,label_colour = (0, 0, 0)):

        self.background_colour = background_colour
        self.padding = padding
        self.max_height = max_height
        self.label_h = label_h
        self.label_colour = label_colour

    # Scale image with fix height, maintain ratio
    def scale_image(self, image: Image.Image, max_height: int) -> Image.Image:
        scale = max_height / image.height

        # Lanczos filter ensure image up/downscaling quality
        # int() convert float value
        return image.resize(
            (int(image.width * scale), max_height), 
            Image.LANCZOS
        )

    # Disply duplicated images found
    def display_image(self, image1: Path, image2: Path) -> Image.Image:
        """
        Rotates & scales images, place them side by side on black canva,
        display corresponding image name.
        """

        # Rotate image based on its EXIF orientation
        img1_horizontal = ImageOps.exif_transpose(Image.open(image1))
        img2_horizontal = ImageOps.exif_transpose(Image.open(image2))

        img1_scaled = self.scale_image(img1_horizontal.convert("RGB"), self.max_height)
        img2_scaled = self.scale_image(img2_horizontal.convert("RGB"), self.max_height)

        total_w = img1_scaled.width + img2_scaled.width + self.padding
        total_h = self.label_h + self.max_height

        canva = Image.new("RGB", (total_w, total_h), self.background_colour)

        # Paste new images below the label row
        # Box argument (second parameter): (left, upper, right, and lower pixel coordinate)
        canva.paste(img1_scaled, (0, self.label_h))
        canva.paste(img2_scaled, (img1_scaled.width + self.padding, self.label_h))

        # Display file name
        ImageDraw.Draw(canva).text(
            (20, 1), 
            Path(image1).name, 
            fill = self.label_colour, 
            font = ImageFont.truetype("arial.ttf", 14)
        )
        ImageDraw.Draw(canva).text((
            img1_scaled.width + self.padding + 20, 1),
            Path(image2).name,
            fill = self.label_colour,
            font = ImageFont.truetype("arial.ttf", 14)
        )

        return canva

    # Hold the window
    def keep_display(self, image: Image.Image,
                    title: str = "Duplicate images found") -> None:
        """
        Displays image using Tkinter in the center, blocks execution 
        until the user closes it. Display one by one.
        """

        # Create main window
        root = tk.Tk()
        root.title(title)
        root.resizable(False, False)

        # Load & read the image
        img = ImageTk.PhotoImage(image)
        label = tk.Label(root, image = img)
        label.pack()

        # Center the window
        root.update_idletasks()
        window_w = root.winfo_reqwidth()
        window_h = root.winfo_reqheight()        
        x_coord = int(root.winfo_screenwidth()/2 - window_w/2)
        y_coord = int(root.winfo_screenheight()/2 - window_h/2)
        root.geometry(f'{window_w}x{window_h}+{x_coord}+{y_coord}')

        # Keep the window open
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
                
                print(f"{number}) ❌ {image.name} -> {result.original_filename}")
                data["duplicate_of"] = result.original_filename
                data["duplicate_reason"] = result.reason
                self.duplicates.append(data)
                
                combined_img = displayer.display_image(image, Path(result.original_filename))

                title = f"Duplicate images found: {image.name}  <-->  {Path(result.original_filename).name}"
                displayer.keep_display(combined_img, title)

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