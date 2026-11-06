"""
Face database management for the face recognition system.
"""

import os
import pickle
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import cv2
import numpy as np

from .config import Config
from .utils import safe_filename, validate_age, validate_gender


class FaceDatabase:
    """
    Manages face database operations including storage, retrieval, and recognition.
    
    Features:
    - Multiple face images per person for improved recognition
    - Automatic database migration from older versions
    - Backup and restore functionality
    - Statistics and analytics
    """
    
    def __init__(self, config: Optional[Config] = None):
        """Initialize the face database."""
        self.config = config or Config()
        self.db_path = self.config.database_path
        self.unknown_dir = self.config.unknown_faces_dir
        self.unknown_dir.mkdir(exist_ok=True)
        
        self.known_faces: Dict[str, Dict[str, Any]] = {}
        self.face_recognizer = cv2.face.LBPHFaceRecognizer_create()
        self.label_map: Dict[int, str] = {}
        
        # Load existing database
        self.load_database()
    
    def load_database(self):
        """Load face database from file with migration support."""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'rb') as f:
                    data = pickle.load(f)
                    self.known_faces = data.get('known_faces', {})
                
                # Migrate old format to new format
                self._migrate_database()
                
                if self.known_faces:
                    self.train_recognizer()
                    
                print(f"Loaded {len(self.known_faces)} known faces from database")
            except Exception as e:
                print(f"Error loading database: {e}")
                self.known_faces = {}
                # Create backup of corrupted database
                self._backup_corrupted_database()
        else:
            print("No existing database found. Starting fresh.")
    
    def _migrate_database(self):
        """Migrate database from old format to new format."""
        migrated = False
        
        for name, person_data in self.known_faces.items():
            # Migrate from single gray_face to multiple gray_faces
            if 'gray_face' in person_data and 'gray_faces' not in person_data:
                person_data['gray_faces'] = [person_data['gray_face']]
                person_data['image_count'] = 1
                del person_data['gray_face']
                migrated = True
            
            # Add missing fields with defaults
            if 'added_on' not in person_data:
                person_data['added_on'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                migrated = True
            
            if 'image_count' not in person_data:
                person_data['image_count'] = len(person_data.get('gray_faces', []))
                migrated = True
            
            # Validate and normalize data
            if 'gender' in person_data:
                try:
                    person_data['gender'] = validate_gender(person_data['gender'])
                    migrated = True
                except ValueError:
                    person_data['gender'] = 'Unknown'
            
            if 'age' in person_data:
                try:
                    person_data['age'] = int(person_data['age'])
                except (ValueError, TypeError):
                    person_data['age'] = 0
        
        if migrated:
            self.save_database()
            print("Database migrated to new format")
    
    def _backup_corrupted_database(self):
        """Create a backup of corrupted database file."""
        if os.path.exists(self.db_path):
            backup_path = f"{self.db_path}.corrupted.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                shutil.copy2(self.db_path, backup_path)
                print(f"Corrupted database backed up to: {backup_path}")
            except Exception as e:
                print(f"Failed to backup corrupted database: {e}")
    
    def save_database(self):
        """Save face database to file."""
        try:
            # Create backup before saving
            if os.path.exists(self.db_path):
                backup_path = f"{self.db_path}.backup"
                shutil.copy2(self.db_path, backup_path)
            
            with open(self.db_path, 'wb') as f:
                pickle.dump({'known_faces': self.known_faces}, f)
                
            print(f"Database saved with {len(self.known_faces)} people")
            
            # Clean old backups (keep only last 5)
            self._cleanup_backups()
            
        except Exception as e:
            print(f"Error saving database: {e}")
    
    def _cleanup_backups(self):
        """Clean old backup files, keeping only the most recent ones."""
        backup_pattern = f"{self.db_path}.backup*"
        backup_files = list(Path(os.path.dirname(self.db_path)).glob(os.path.basename(backup_pattern)))
        backup_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        
        # Keep only the 5 most recent backups
        for backup_file in backup_files[5:]:
            try:
                backup_file.unlink()
            except Exception as e:
                print(f"Failed to delete old backup {backup_file}: {e}")
    
    def train_recognizer(self):
        """Train the face recognizer with current database."""
        if not self.known_faces:
            return

        faces = []
        labels = []
        label_map = {}
        current_label = 0

        for name, data in self.known_faces.items():
            if 'gray_faces' in data and data['gray_faces']:
                for face_img in data['gray_faces']:
                    # Ensure face image is the right size
                    face_resized = cv2.resize(face_img, self.config.face_size)
                    faces.append(face_resized)
                    labels.append(current_label)
                label_map[current_label] = name
                current_label += 1

        if faces:
            try:
                self.face_recognizer.train(faces, np.array(labels))
                self.label_map = label_map
                print(f"Trained recognizer with {len(faces)} face images from {len(label_map)} people")
            except Exception as e:
                print(f"Error training recognizer: {e}")
    
    def add_face(self, image_path: str, name: str, gender: str, age: int, is_multi_image: bool = False) -> bool:
        """
        Add a face to the database.
        
        Args:
            image_path: Path to the image file
            name: Person's name
            gender: Person's gender
            age: Person's age
            is_multi_image: Whether this is an additional image for existing person
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Image not found: {image_path}")

            img = cv2.imread(image_path)
            if img is None:
                raise ValueError("Invalid image file")

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            faces = face_cascade.detectMultiScale(gray, 1.1, 4)

            if len(faces) == 0:
                raise ValueError("No face detected in the provided image")
            elif len(faces) > 1:
                if not is_multi_image:
                    raise ValueError("Multiple faces detected. Please provide an image with a single face.")
                # For multi-image, take the largest face
                faces = [max(faces, key=lambda y: y[2] * y[3])]

            x, y, w, h = faces[0]
            face_roi = gray[y:y+h, x:x+w]
            face_resized = cv2.resize(face_roi, self.config.face_size)

            # Validate and normalize inputs
            name = safe_filename(name).strip()
            if not name:
                raise ValueError("Name cannot be empty")
            
            gender = validate_gender(gender)
            age = validate_age(str(age))

            if name not in self.known_faces:
                self.known_faces[name] = {
                    'gray_faces': [],
                    'gender': gender,
                    'age': age,
                    'added_on': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'image_count': 0,
                    'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

            self.known_faces[name]['gray_faces'].append(face_resized)
            self.known_faces[name]['image_count'] += 1
            self.known_faces[name]['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            self.train_recognizer()
            self.save_database()
            
            print(f"Added image #{self.known_faces[name]['image_count']} for {name} to database!")
            return True
            
        except Exception as e:
            print(f"Error adding face: {e}")
            return False
    
    def recognize_face(self, face_gray: np.ndarray) -> Optional[Dict[str, Any]]:
        """
        Recognize a face from the grayscale image.
        
        Args:
            face_gray: Grayscale face image
            
        Returns:
            Dict with recognition results or None if not recognized
        """
        if not self.known_faces:
            return None

        try:
            face_resized = cv2.resize(face_gray, self.config.face_size)
            label, confidence = self.face_recognizer.predict(face_resized)

            if confidence < self.config.recognition_threshold:
                name = self.label_map.get(label, "Unknown")
                if name in self.known_faces:
                    return {
                        'name': name,
                        'gender': self.known_faces[name]['gender'],
                        'age': self.known_faces[name]['age'],
                        'confidence': confidence,
                        'image_count': self.known_faces[name]['image_count'],
                        'last_updated': self.known_faces[name].get('last_updated', 'Unknown')
                    }

        except Exception as e:
            print(f"Error during face recognition: {e}")

        return None
    
    def save_unknown_face(self, face_color: np.ndarray, gender: str, age_range: str) -> str:
        """
        Save an unknown face image with metadata.
        
        Args:
            face_color: Color face image
            gender: Detected gender
            age_range: Detected age range
            
        Returns:
            str: Path to saved image
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_gender = safe_filename(gender)
        safe_age_range = safe_filename(age_range.replace('(', '').replace(')', '').replace('-', '_'))
        filename = f"unknown_{timestamp}_{safe_gender}_{safe_age_range}.jpg"
        filepath = self.unknown_dir / filename

        try:
            cv2.imwrite(str(filepath), face_color)
            print(f"Saved unknown face: {filepath.name}")
            return str(filepath)
        except Exception as e:
            print(f"Error saving unknown face: {e}")
            return ""
    
    def remove_person(self, name: str) -> bool:
        """
        Remove a person from the database.
        
        Args:
            name: Name of the person to remove
            
        Returns:
            bool: True if removed, False if not found
        """
        if name in self.known_faces:
            del self.known_faces[name]
            self.train_recognizer()
            self.save_database()
            print(f"Removed {name} from database")
            return True
        return False
    
    def update_person_info(self, name: str, gender: Optional[str] = None, age: Optional[int] = None) -> bool:
        """
        Update person information in the database.
        
        Args:
            name: Name of the person
            gender: New gender (optional)
            age: New age (optional)
            
        Returns:
            bool: True if updated, False if not found
        """
        if name not in self.known_faces:
            return False
        
        updated = False
        if gender is not None:
            self.known_faces[name]['gender'] = validate_gender(gender)
            updated = True
        
        if age is not None:
            self.known_faces[name]['age'] = validate_age(str(age))
            updated = True
        
        if updated:
            self.known_faces[name]['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.save_database()
            print(f"Updated information for {name}")
        
        return updated
    
    def get_person_details(self, name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a person."""
        return self.known_faces.get(name)
    
    def list_people(self) -> List[str]:
        """Get list of all people in the database."""
        return list(self.known_faces.keys())
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get database statistics."""
        total_people = len(self.known_faces)
        total_images = sum(data.get('image_count', 1) for data in self.known_faces.values())
        
        if total_people == 0:
            return {
                'total_people': 0,
                'total_images': 0,
                'avg_images_per_person': 0,
                'gender_distribution': {},
                'age_distribution': {}
            }
        
        # Gender distribution
        gender_counts = {}
        for data in self.known_faces.values():
            gender = data.get('gender', 'Unknown')
            gender_counts[gender] = gender_counts.get(gender, 0) + 1
        
        # Age distribution
        age_groups = {
            '0-18': 0, '19-30': 0, '31-45': 0, '46-60': 0, '60+': 0
        }
        for data in self.known_faces.values():
            age = data.get('age', 0)
            if age <= 18:
                age_groups['0-18'] += 1
            elif age <= 30:
                age_groups['19-30'] += 1
            elif age <= 45:
                age_groups['31-45'] += 1
            elif age <= 60:
                age_groups['46-60'] += 1
            else:
                age_groups['60+'] += 1
        
        return {
            'total_people': total_people,
            'total_images': total_images,
            'avg_images_per_person': total_images / total_people,
            'gender_distribution': gender_counts,
            'age_distribution': age_groups,
            'database_size_mb': os.path.getsize(self.db_path) / (1024 * 1024) if os.path.exists(self.db_path) else 0
        }
    
    def export_database(self, export_path: str) -> bool:
        """Export database to a JSON file for inspection."""
        try:
            import json
            export_data = {
                'export_timestamp': datetime.now().isoformat(),
                'total_people': len(self.known_faces),
                'people': {}
            }
            
            for name, data in self.known_faces.items():
                # Convert numpy arrays to lists for JSON serialization
                person_data = data.copy()
                if 'gray_faces' in person_data:
                    person_data['gray_faces'] = [face.tolist() for face in person_data['gray_faces']]
                export_data['people'][name] = person_data
            
            with open(export_path, 'w') as f:
                json.dump(export_data, f, indent=2)
            
            print(f"Database exported to: {export_path}")
            return True
        except Exception as e:
            print(f"Error exporting database: {e}")
            return False
    
    def cleanup_unknown_faces(self, days_old: int = 30) -> int:
        """
        Clean up unknown face images older than specified days.
        
        Args:
            days_old: Age in days to delete files
            
        Returns:
            int: Number of files deleted
        """
        if not self.unknown_dir.exists():
            return 0
        
        deleted_count = 0
        cutoff_time = datetime.now().timestamp() - (days_old * 24 * 3600)
        
        for file_path in self.unknown_dir.glob("*.jpg"):
            if file_path.stat().st_mtime < cutoff_time:
                try:
                    file_path.unlink()
                    deleted_count += 1
                except Exception as e:
                    print(f"Error deleting {file_path}: {e}")
        
        if deleted_count > 0:
            print(f"Cleaned up {deleted_count} old unknown face images")
        
        return deleted_count
