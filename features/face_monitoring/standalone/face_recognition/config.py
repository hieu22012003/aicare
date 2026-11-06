"""
Configuration management for the face recognition system.
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, Optional


class Config:
    """Configuration manager with support for JSON config files and environment variables."""
    
    DEFAULT_CONFIG = {
        "database": {
            "path": "face_database.pkl",
            "unknown_faces_dir": "unknown_faces",
            "backup_enabled": True,
            "backup_interval": 3600  # seconds
        },
        "models": {
            "face_detector": {
                "proto": "models/opencv_face_detector.pbtxt",
                "model": "models/opencv_face_detector_uint8.pb"
            },
            "age_detector": {
                "proto": "models/age_deploy.prototxt", 
                "model": "models/age_net.caffemodel"
            },
            "gender_detector": {
                "proto": "models/gender_deploy.prototxt",
                "model": "models/gender_net.caffemodel"
            }
        },
        "detection": {
            "confidence_threshold": 0.7,
            "padding": 20,
            "face_size": (200, 200),
            "recognition_threshold": 80
        },
        "ui": {
            "window_name": "Face Recognition System",
            "display_confidence": True,
            "display_age_gender": True,
            "save_frames_dir": "saved_frames"
        },
        "logging": {
            "level": "INFO",
            "file": "face_recognition.log",
            "max_size": 10485760,  # 10MB
            "backup_count": 5
        }
    }
    
    def __init__(self, config_file: Optional[str] = None):
        """Initialize configuration with optional config file override."""
        self._config = self.DEFAULT_CONFIG.copy()
        self.config_file = config_file or "config.json"
        self.load_config()
    
    def load_config(self):
        """Load configuration from file if it exists."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    user_config = json.load(f)
                    self._merge_config(self._config, user_config)
                print(f"Loaded configuration from {self.config_file}")
            except Exception as e:
                print(f"Warning: Failed to load config file {self.config_file}: {e}")
                print("Using default configuration.")
    
    def save_config(self):
        """Save current configuration to file."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self._config, f, indent=2)
            print(f"Configuration saved to {self.config_file}")
        except Exception as e:
            print(f"Error saving config file: {e}")
    
    def _merge_config(self, base: Dict[str, Any], override: Dict[str, Any]):
        """Recursively merge override config into base config."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_config(base[key], value)
            else:
                base[key] = value
    
    def get(self, key_path: str, default=None):
        """Get configuration value using dot notation (e.g., 'database.path')."""
        keys = key_path.split('.')
        value = self._config
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        
        # Check environment variable override
        env_key = f"FR_{key_path.upper().replace('.', '_')}"
        env_value = os.getenv(env_key)
        if env_value is not None:
            # Try to convert to appropriate type
            if isinstance(value, bool):
                return env_value.lower() in ('true', '1', 'yes', 'on')
            elif isinstance(value, int):
                try:
                    return int(env_value)
                except ValueError:
                    pass
            elif isinstance(value, float):
                try:
                    return float(env_value)
                except ValueError:
                    pass
            return env_value
        
        return value
    
    def set(self, key_path: str, value):
        """Set configuration value using dot notation."""
        keys = key_path.split('.')
        config = self._config
        
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        
        config[keys[-1]] = value
    
    @property
    def database_path(self) -> str:
        return self.get('database.path')
    
    @property
    def unknown_faces_dir(self) -> Path:
        return Path(self.get('database.unknown_faces_dir'))
    
    @property
    def confidence_threshold(self) -> float:
        return self.get('detection.confidence_threshold')
    
    @property
    def padding(self) -> int:
        return self.get('detection.padding')
    
    @property
    def face_size(self) -> tuple:
        return tuple(self.get('detection.face_size'))
    
    @property
    def recognition_threshold(self) -> int:
        return self.get('detection.recognition_threshold')
