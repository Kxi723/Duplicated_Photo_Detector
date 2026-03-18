import json
import logging
import hashlib
import imagehash
import time
from pathlib import Path
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk
from PIL.ExifTags import TAGS, GPSTAGS

# Paths
SCRIPT_DIR = Path(__file__).parent
IMAGE_DIR = SCRIPT_DIR / "image"
INFO_DIR = SCRIPT_DIR / "result.json"

# Logging configuration
logging.basicConfig(
    filename = SCRIPT_DIR  / "activity.log",
    level = logging.INFO,
    format = '%(asctime)s %(levelname)s: %(message)s',
    filemode = 'w'
)

# -------------------------------------------------
# Configuration Constants
# -------------------------------------------------

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
PHASH_THRESHOLD = 9

# =============================================================================
# Classes & Functions
# =============================================================================

class ImageDisplayer:
    """
    Simple container for displaying duplicate images for user to see
    """
    def __init__(self, background_colour = (240, 240, 240), padding = 5,
                max_height = 500, label_h = 20,label_colour = (0, 0, 0)):

        self.background_colour = background_colour
        self.padding = padding
        self.max_height = max_height
        self.label_h = label_h
        self.label_colour = label_colour

    def scale_image(self, image: Image.Image, max_height: int) -> Image.Image:
        """
        Scales image to a fixed height while maintaining aspect ratio.
        """
        scale = max_height / image.height

        # Lanczos filter ensure high quality image up/downscaling
        # int() convert float value
        return image.resize(
            (int(image.width * scale), max_height), 
            Image.LANCZOS
        )

    def display_image(self, image1: Path, image2: Path) -> Image.Image:
        """
        Rotates and scales images based on EXIF, places them side-by-side 
        on canva and displays the corresponding file names.
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

    def keep_display(self, image: Image.Image,
                    title: str = "Duplicate images found") -> None:
        """
        Displays the image canvas using Tkinter in the center of the screen, 
        and blocks execution until the user closes it.
        """

        # Create main window
        root = tk.Tk()
        root.title(title)
        root.resizable(False, False)
        # Display at top
        root.attributes(topmost=True)

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
        logging.info("Window displayed")
        root.mainloop()

        logging.info("Window closed")        


class ReportEncoder(json.JSONEncoder):
    """
    Custom JSON encoder to handle ImageHash objects.
    """
    def default(self, obj):
        # Convert ImageHash object to string
        if isinstance(obj, imagehash.ImageHash):
            return str(obj)
        # Otherwise, use default encoder
        return super().default(obj)


class DuplicateResult:
    """
    A class for storing the result of duplicate check.
    """
    def __init__(self, is_duplicate: bool, reason: str, 
                file_name: str = None, file_path: Path = None):
        self.is_duplicate = is_duplicate
        self.reason = reason
        self.file_name = file_name
        self.file_path = file_path


class PhotoScanner:
    """
    1. Reads images
    2. Calculate hash value and extract metadata
    3. Stores images data
    4. Compare and check duplicate
    5. Displays and export results
    """

    def __init__(self, dir_path: Path = IMAGE_DIR, 
                phash_threshold: int = PHASH_THRESHOLD):
        self.dir_path = dir_path
        self.phash_threshold = phash_threshold
        self.database = []
        self.duplicates = []

    def read_image_path(self) -> list[Path]:
        """
        Scans target directory and return sorted list of image paths.
        """

        if not self.dir_path.exists():
            raise FileNotFoundError(f"Directory not found: {self.dir_path}")
        
        logging.info(f"Scanning directory: {self.dir_path}")

        return sorted(
            path for path in self.dir_path.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )


    def compute_md5(self, path: Path) -> str:
        """
        Computes MD5 hash to get a 32-character hexadecimal string
        for identifying exact byte-for-byte duplicates.
        """
        with open(path, "rb") as file:
            return hashlib.md5(file.read()).hexdigest()


    def compute_phash(self, path: Path) -> imagehash.ImageHash | None:
        """
        Computes the perceptual hash (pHash) for identifying 
        visually similar images.
        """
        try:
            image = ImageOps.exif_transpose(Image.open(path))
            return imagehash.phash(image)

        except Exception as e:
            logging.error(f"Failed to compute pHash for {path.name} | {e}")
            return None


    def extract_metadata(self, path: Path) -> dict:
        """
        Extracts EXIF metadata including date taken, camera make/model, 
        resolution, and GPS info.
        """
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
        """
        Compiles file path, MD5 hash, pHash, and metadata into a record.
        """
        return {
            "filename": path.name,
            "filepath": str(path),
            "md5": self.compute_md5(path),
            "phash": self.compute_phash(path),
            "metadata": self.extract_metadata(path),
        }


    def check_duplicate(self, new_data: dict) -> DuplicateResult:
        """
        Compares new image data against the accumulated database to 
        check for duplicates based on MD5, pHash, and EXIF metadata.
        """
        image_file = new_data["filename"]

        for historical_data in self.database:
            # Check for exact byte match
            if new_data["md5"] == historical_data["md5"]:
                logging.info(f"{image_file} identified as a duplicate of {historical_data['filename']} (MD5 match)")

                return DuplicateResult(
                    True,
                    f"Exact copy of '{historical_data['filename']}' (MD5)", 
                    historical_data["filename"],
                    historical_data["filepath"]
                )

        for historical_data in self.database:
            # Check visual similarity
            if new_data["phash"] is not None and historical_data["phash"] is not None:  

                # Ensure the value is positive
                distance = abs(new_data["phash"] - historical_data["phash"])

                if distance <= self.phash_threshold:
                    logging.info(f"{image_file} identified as a duplicate of {historical_data['filename']} (pHash distance: {distance})")

                    return DuplicateResult(
                        True,
                        f"visually similar to {historical_data['filename']} (pHash distance = {distance})",
                        historical_data["filename"],
                        historical_data["filepath"]
                    )

        new_meta = new_data["metadata"]
        new_data_set = {
            new_meta.get("date_time"), 
            new_meta.get("date_original"), 
            new_meta.get("date_digitized")
        }
        new_data_set.discard(None) # Clear 'None' value

        # If date was modified, prompt a warning
        # Set() is used to remove duplicated dates, set()>2 means two different date
        if len(new_data_set) > 1:
            logging.warning(f"{image_file} has mismatched EXIF dates")

        for historical_data in self.database:
            historical_meta = historical_data["metadata"]

            historical_set = {
                historical_meta.get("date_time"), 
                historical_meta.get("date_original"), 
                historical_meta.get("date_digitized")
            }
            historical_set.discard(None)

            # Check if ANY timestamp and phone model matches
            if new_data_set and historical_set and new_data_set.intersection(historical_set):
  
                if new_meta["model"] and new_meta["model"] == historical_meta.get("model"):
                    logging.info(f"'{image_file}' identified as a duplicate of {historical_data['filename']} (Metadata match)")

                    return DuplicateResult(
                        True,
                        f"same timestamp & device as {historical_data['filename']}",
                        historical_data["filename"],
                        historical_data["filepath"]
                    )

        # No duplicate found
        logging.info(f"{image_file} is verified as a unique new photo")
        return DuplicateResult(False, "photo is unique")


    def start(self) -> None:
        """
        Main method to start the duplicate detection process.
        """

        image_list = self.read_image_path()
        logging.info(f"Total {len(image_list)} images found for processing")

        displayer = ImageDisplayer()

        # Start matching to find duplicate image
        for number, image in enumerate(image_list, start = 1):
            data = self.build_record(image)
            result = self.check_duplicate(data)

            # Display duplicate image to user
            if result.is_duplicate:
                print(f"{number}) ❌ {image.name} -> {result.file_name}")
                title = f"Duplicate found: {image.name}  <-->  {result.file_name}"
                
                window = displayer.display_image(image, Path(result.file_path))
                displayer.keep_display(window, title)

                data["duplicate_reason"] = result.reason
                self.duplicates.append(data)

            else:
                print(f"{number}) ✅ {image.name}")
                self.database.append(data)

        print("─" * 40)
        print(f"Duplicates found: {len(self.duplicates)} images")
        print(f"Unique photos: {len(self.database)} images")


    def export_report(self, output_json: Path = INFO_DIR) -> None:
        """
        Exports records of unique and duplicate photos into JSON file.
        """
        export_data = {
            "unique_photos": self.database,
            "duplicates": self.duplicates
        }

        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=4, cls=ReportEncoder)
        
        print(f"Detailed JSON report exported to '{output_json.name}'")
        logging.info(f"Detailed results have been successfully exported to {output_json.name}")

# -------------------------------------------------
# Main Entry Point
# -------------------------------------------------

if __name__ == "__main__":
    logging.info("Program started")
    logging.info(f"pHash threshold: {PHASH_THRESHOLD}")

    try:
        scanner = PhotoScanner()
        scanner.start()
        scanner.export_report()

    except FileNotFoundError as e:
        logging.error(e)
        print(e)

    except Exception as e:
        logging.error(e)
        print(e)

    finally:
        logging.info("Program ended")
