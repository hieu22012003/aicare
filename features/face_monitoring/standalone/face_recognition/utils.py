"""
Utility functions for the face recognition system.
"""

import logging
import os
from pathlib import Path
from typing import Optional


def setup_logging(log_file: str = "face_recognition.log", level: str = "INFO"):
    """Setup logging configuration."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)


def get_input_type(input_src: Optional[str]) -> str:
    """
    Determine the type of input source.
    
    Args:
        input_src: Input source (camera index, image path, or video path)
        
    Returns:
        str: Type of input ('camera', 'image', or 'video')
    """
    if input_src is None:
        return "camera"

    if input_src.isdigit():
        return "camera"

    if not os.path.exists(input_src):
        raise FileNotFoundError(f"Input file '{input_src}' not found")

    ext = os.path.splitext(input_src.lower())[1]
    if ext in ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp']:
        return "image"
    elif ext in ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv']:
        return "video"
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def validate_image_path(image_path: str) -> Path:
    """
    Validate that an image file exists and is readable.
    
    Args:
        image_path: Path to the image file
        
    Returns:
        Path: Validated Path object
        
    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file is not a valid image
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")
    
    if not path.is_file():
        raise ValueError(f"Path is not a file: {image_path}")
    
    valid_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp'}
    if path.suffix.lower() not in valid_extensions:
        raise ValueError(f"Invalid image format: {path.suffix}")
    
    return path


def ensure_directory(dir_path: str) -> Path:
    """
    Ensure a directory exists, create if it doesn't.
    
    Args:
        dir_path: Path to the directory
        
    Returns:
        Path: Path object of the directory
    """
    path = Path(dir_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_file_age_in_days(file_path: str) -> float:
    """
    Get the age of a file in days.
    
    Args:
        file_path: Path to the file
        
    Returns:
        float: Age in days
    """
    if not os.path.exists(file_path):
        return float('inf')
    
    mtime = os.path.getmtime(file_path)
    import time
    return (time.time() - mtime) / (24 * 3600)


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        str: Formatted size string
    """
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    
    return f"{size_bytes:.1f} {size_names[i]}"


def safe_filename(filename: str) -> str:
    """
    Create a safe filename by removing/replacing invalid characters.
    
    Args:
        filename: Original filename
        
    Returns:
        str: Safe filename
    """
    import re
    # Remove invalid characters
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove leading/trailing spaces and dots
    safe_name = safe_name.strip('. ')
    # Ensure it's not empty
    if not safe_name:
        safe_name = "unnamed"
    return safe_name


def validate_age(age_str: str) -> int:
    """
    Validate and convert age string to integer.
    
    Args:
        age_str: Age as string
        
    Returns:
        int: Validated age
        
    Raises:
        ValueError: If age is invalid
    """
    try:
        age = int(age_str)
        if age < 0 or age > 150:
            raise ValueError("Age must be between 0 and 150")
        return age
    except ValueError as e:
        if "invalid literal" in str(e):
            raise ValueError("Age must be a valid number")
        raise


def validate_gender(gender_str: str) -> str:
    """
    Validate and normalize gender string.
    
    Args:
        gender_str: Gender as string
        
    Returns:
        str: Normalized gender ('Male' or 'Female')
        
    Raises:
        ValueError: If gender is invalid
    """
    gender = gender_str.strip().lower()
    if gender in ['male', 'm', 'man', 'men']:
        return 'Male'
    elif gender in ['female', 'f', 'woman', 'women']:
        return 'Female'
    else:
        raise ValueError("Gender must be 'Male' or 'Female'")


class ProgressTracker:
    """Simple progress tracking utility."""
    
    def __init__(self, total: int, description: str = "Processing"):
        self.total = total
        self.current = 0
        self.description = description
    
    def update(self, increment: int = 1):
        """Update progress by increment."""
        self.current += increment
        percentage = (self.current / self.total) * 100
        print(f"\r{self.description}: {self.current}/{self.total} ({percentage:.1f}%)", end='', flush=True)
        
        if self.current >= self.total:
            print()
    
    def finish(self):
        """Mark progress as complete."""
        self.current = self.total
        self.update(0)
