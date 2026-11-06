"""
Face Recognition System

A modular face recognition system with database management, 
real-time processing, and unknown face collection capabilities.
"""

__version__ = "1.0.0"
__author__ = "Shaun Dsouza"

from .database import FaceDatabase
from .face_processor import FaceProcessor
from .media_handler import MediaHandler
from .ui import UIManager
from .config import Config
from .utils import get_input_type
from .logging_config import setup_logging, get_logger

__all__ = [
    'FaceDatabase',
    'FaceProcessor', 
    'MediaHandler',
    'UIManager',
    'Config',
    'get_input_type',
    'setup_logging',
    'get_logger'
]
