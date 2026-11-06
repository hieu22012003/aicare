"""
User interface module for the face recognition system.
"""

import os
import sys
import cv2
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from .config import Config
from .database import FaceDatabase
from .utils import validate_age, validate_gender, get_file_age_in_days


class UIManager:
    """
    Manages all user interface interactions including menus and displays.
    
    Features:
    - Interactive menu system
    - Database management interface
    - Unknown face review system
    - Real-time display controls
    """
    
    def __init__(self, config: Optional[Config] = None):
        """Initialize the UI manager."""
        self.config = config or Config()
    
    def interactive_menu(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Display interactive menu for input source selection.
        
        Returns:
            Tuple of (input_source, mode) where mode can be 'database' or 'unknown_review'
        """
        print("\n" + "="*60)
        print("FACE RECOGNITION SYSTEM - INPUT SOURCE SELECTION")
        print("="*60)
        print("No input source specified. Please choose an option:")
        print("1. Use default webcam (camera index 0)")
        print("2. Use specific camera index")
        print("3. Load image file")
        print("4. Load video file")
        print("5. Manage face database (add/remove faces)")
        print("6. Review & label unknown faces")
        print("7. Exit application")
        print("8. System settings and diagnostics")

        while True:
            try:
                choice = input("\nEnter your choice (1-8): ").strip()

                if choice == '1':
                    return '0', None

                elif choice == '2':
                    while True:
                        cam_index = input("Enter camera index (e.g., 0, 1, 2): ").strip()
                        if cam_index.isdigit() and int(cam_index) >= 0:
                            return cam_index, None
                        print("Error: Camera index must be a non-negative integer.")

                elif choice in ['3', '4']:
                    file_type = "image" if choice == '3' else "video"
                    while True:
                        file_path = input(f"Enter path to {file_type} file: ").strip()
                        if os.path.exists(file_path) and os.path.isfile(file_path):
                            return file_path, None
                        print(f"Error: File '{file_path}' not found or not accessible.")

                elif choice == '5':
                    return None, 'database'

                elif choice == '6':
                    return None, 'unknown_review'

                elif choice == '7':
                    print("Exiting application. Goodbye!")
                    sys.exit(0)

                elif choice == '8':
                    self._show_system_menu()
                    continue

                else:
                    print("Invalid choice. Please enter a number between 1 and 8.")

            except KeyboardInterrupt:
                print("\nOperation cancelled by user. Exiting.")
                sys.exit(0)
    
    def _show_system_menu(self):
        """Display system settings and diagnostics menu."""
        print("\n" + "="*50)
        print("SYSTEM SETTINGS AND DIAGNOSTICS")
        print("="*50)
        print("1. View current configuration")
        print("2. Database statistics and health")
        print("3. Clean up old unknown faces")
        print("4. Export database")
        print("5. Test camera devices")
        print("6. Back to main menu")
        
        choice = input("\nEnter choice (1-6): ").strip()
        
        if choice == '1':
            self._show_configuration()
        elif choice == '2':
            self._show_database_diagnostics()
        elif choice == '3':
            self._cleanup_unknown_faces()
        elif choice == '4':
            self._export_database_dialog()
        elif choice == '5':
            self._test_cameras()
        
        input("\nPress Enter to continue...")
    
    def _show_configuration(self):
        """Display current configuration settings."""
        print("\n" + "="*50)
        print("CURRENT CONFIGURATION")
        print("="*50)
        
        config_items = [
            ("Database Path", self.config.database_path),
            ("Unknown Faces Directory", str(self.config.unknown_faces_dir)),
            ("Confidence Threshold", self.config.confidence_threshold),
            ("Face Padding", self.config.padding),
            ("Face Size", self.config.face_size),
            ("Recognition Threshold", self.config.recognition_threshold),
        ]
        
        for key, value in config_items:
            print(f"{key}: {value}")
    
    def _show_database_diagnostics(self):
        """Show database statistics and health information."""
        database = FaceDatabase(self.config)
        stats = database.get_statistics()
        
        print("\n" + "="*50)
        print("DATABASE DIAGNOSTICS")
        print("="*50)
        print(f"Total people: {stats['total_people']}")
        print(f"Total images: {stats['total_images']}")
        print(f"Average images per person: {stats['avg_images_per_person']:.1f}")
        print(f"Database size: {stats['database_size_mb']:.2f} MB")
        
        if stats['gender_distribution']:
            print("\nGender Distribution:")
            for gender, count in stats['gender_distribution'].items():
                print(f"  {gender}: {count}")
        
        if stats['age_distribution']:
            print("\nAge Distribution:")
            for age_range, count in stats['age_distribution'].items():
                print(f"  {age_range}: {count}")
        
        # Check unknown faces
        unknown_dir = self.config.unknown_faces_dir
        if unknown_dir.exists():
            unknown_files = list(unknown_dir.glob("*.jpg"))
            print(f"\nUnknown faces: {len(unknown_files)}")
            
            if unknown_files:
                # Show age of unknown faces
                old_files = sum(1 for f in unknown_files if get_file_age_in_days(str(f)) > 30)
                if old_files > 0:
                    print(f"  {old_files} files older than 30 days")
    
    def _cleanup_unknown_faces(self):
        """Clean up old unknown face files."""
        try:
            days = input("Enter age in days for files to delete (default 30): ").strip()
            days = int(days) if days.isdigit() else 30
            
            database = FaceDatabase(self.config)
            deleted = database.cleanup_unknown_faces(days)
            print(f"Deleted {deleted} old unknown face files")
        except Exception as e:
            print(f"Error during cleanup: {e}")
    
    def _export_database_dialog(self):
        """Export database to JSON file."""
        try:
            export_path = input("Enter export file path (default: database_export.json): ").strip()
            if not export_path:
                export_path = "database_export.json"
            
            database = FaceDatabase(self.config)
            if database.export_database(export_path):
                print(f"Database exported successfully to {export_path}")
            else:
                print("Export failed")
        except Exception as e:
            print(f"Error during export: {e}")
    
    def _test_cameras(self):
        """Test available camera devices."""
        print("\nTesting camera devices...")
        
        for i in range(5):  # Test first 5 camera indices
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret:
                    print(f"Camera {i}: Available ({frame.shape[1]}x{frame.shape[0]})")
                else:
                    print(f"Camera {i}: Available but cannot read frame")
                cap.release()
            else:
                print(f"Camera {i}: Not available")
    
    def manage_database(self) -> bool:
        """
        Interactive database management interface.
        
        Returns:
            bool: True to continue application, False to exit
        """
        database = FaceDatabase(self.config)

        while True:
            stats = database.get_statistics()
            self._show_database_menu(stats)
            
            choice = input("\nEnter choice (1-8): ").strip()

            if choice == '1':
                self._add_new_person(database)
            elif choice == '2':
                self._add_more_images(database)
            elif choice == '3':
                self._list_people(database)
            elif choice == '4':
                self._remove_person(database)
            elif choice == '5':
                self._update_person_info(database)
            elif choice == '6':
                self._show_detailed_stats(database)
            elif choice == '7':
                self._batch_add_images(database)
            elif choice == '8':
                return True
            else:
                print("Invalid choice!")
    
    def _show_database_menu(self, stats: Dict[str, Any]):
        """Display the database management menu."""
        print("\n" + "="*50)
        print("FACE DATABASE MANAGEMENT")
        print("="*50)
        print(f"Total people: {stats['total_people']}")
        print(f"Total images: {stats['total_images']}")
        if stats['total_people'] > 0:
            print(f"Average images per person: {stats['avg_images_per_person']:.1f}")
        print("\n1. Add new person (single image)")
        print("2. Add more images for existing person")
        print("3. List all people with image counts")
        print("4. Remove person")
        print("5. Update person information")
        print("6. View detailed statistics")
        print("7. Batch add images from directory")
        print("8. Exit database management")
    
    def _add_new_person(self, database: FaceDatabase):
        """Add a new person to the database."""
        try:
            img_path = input("Enter path to face image (single face only): ").strip()
            name = input("Enter person's name: ").strip()
            gender = input("Enter gender (Male/Female): ").strip()
            age_str = input("Enter age (number): ").strip()

            # Validate inputs
            age = validate_age(age_str)
            gender = validate_gender(gender)

            if database.add_face(img_path, name, gender, age):
                print(f"Successfully added {name} to database!")
            else:
                print("Failed to add face to database")

        except Exception as e:
            print(f"Error adding face: {e}")
    
    def _add_more_images(self, database: FaceDatabase):
        """Add more images for an existing person."""
        if not database.known_faces:
            print("Database is empty! Add a person first.")
            return

        print("\nExisting people:")
        for name, data in database.known_faces.items():
            count = data.get('image_count', 1)
            print(f"  - {name} ({count} images)")

        name = input("\nEnter name to add more images for: ").strip()
        if name not in database.known_faces:
            print(f"Person '{name}' not found in database!")
            return

        try:
            img_path = input("Enter path to additional face image: ").strip()
            gender = database.known_faces[name]['gender']
            age = database.known_faces[name]['age']
            
            if database.add_face(img_path, name, gender, age, is_multi_image=True):
                print(f"Successfully added additional image for {name}!")
            else:
                print("Failed to add additional image")
                
        except Exception as e:
            print(f"Error adding additional image: {e}")
    
    def _list_people(self, database: FaceDatabase):
        """List all people in the database with details."""
        if not database.known_faces:
            print("\nDatabase is empty!")
            return

        print("\n" + "="*50)
        print("KNOWN PEOPLE IN DATABASE")
        print("="*50)
        
        for idx, (name, data) in enumerate(database.known_faces.items(), 1):
            count = data.get('image_count', 1)
            added = data.get('added_on', 'Unknown')
            updated = data.get('last_updated', added)
            print(f"{idx}. {name}")
            print(f"   Gender: {data['gender']}, Age: {data['age']} years")
            print(f"   Images: {count}")
            print(f"   Added: {added}")
            print(f"   Last Updated: {updated}")
            print()
        
        input("\nPress Enter to continue...")
    
    def _remove_person(self, database: FaceDatabase):
        """Remove a person from the database."""
        if not database.known_faces:
            print("Database is empty!")
            return

        print("\nCurrent people:")
        for name in database.known_faces.keys():
            print(f"  - {name}")

        name = input("\nEnter name to remove: ").strip()
        confirm = input(f"Are you sure you want to remove {name}? (y/N): ").strip().lower()
        
        if confirm == 'y':
            if database.remove_person(name):
                print(f"Removed {name} from database")
            else:
                print(f"Name '{name}' not found in database")
        else:
            print("Operation cancelled")
    
    def _update_person_info(self, database: FaceDatabase):
        """Update information for a person in the database."""
        if not database.known_faces:
            print("Database is empty!")
            return

        print("\nCurrent people:")
        for name in database.known_faces.keys():
            print(f"  - {name}")

        name = input("\nEnter name to update: ").strip()
        if name not in database.known_faces:
            print(f"Person '{name}' not found in database!")
            return

        print(f"\nCurrent info for {name}:")
        data = database.known_faces[name]
        print(f"Gender: {data['gender']}")
        print(f"Age: {data['age']}")

        gender = input(f"Enter new gender (current: {data['gender']}, press Enter to keep): ").strip()
        age_str = input(f"Enter new age (current: {data['age']}, press Enter to keep): ").strip()

        # Only update if new values provided
        update_gender = validate_gender(gender) if gender else None
        update_age = validate_age(age_str) if age_str else None

        if database.update_person_info(name, update_gender, update_age):
            print(f"Updated information for {name}")
        else:
            print("No updates made")
    
    def _show_detailed_stats(self, database: FaceDatabase):
        """Show detailed database statistics."""
        stats = database.get_statistics()
        
        print("\n" + "="*50)
        print("DETAILED DATABASE STATISTICS")
        print("="*50)
        print(f"Total people: {stats['total_people']}")
        print(f"Total images: {stats['total_images']}")
        print(f"Average images per person: {stats['avg_images_per_person']:.1f}")
        print(f"Database size: {stats['database_size_mb']:.2f} MB")

        if stats['total_people'] > 0:
            max_images = max((data.get('image_count', 1) for data in database.known_faces.values()))
            min_images = min((data.get('image_count', 1) for data in database.known_faces.values()))
            print(f"Max images for one person: {max_images}")
            print(f"Min images for one person: {min_images}")

            print("\nImage distribution:")
            for name, data in database.known_faces.items():
                count = data.get('image_count', 1)
                bar = '█' * min(count, 20)  # Limit bar length
                print(f"  {name:20s} {bar} ({count})")
        
        print()
        input("Press Enter to continue...")
    
    def _batch_add_images(self, database: FaceDatabase):
        """Batch add images from a directory."""
        try:
            dir_path = input("Enter directory path containing face images: ").strip()
            if not os.path.exists(dir_path):
                print("Directory not found!")
                return
            
            name = input("Enter person's name: ").strip()
            gender = input("Enter gender (Male/Female): ").strip()
            age_str = input("Enter age (number): ").strip()
            
            age = validate_age(age_str)
            gender = validate_gender(gender)
            
            # Find image files
            image_extensions = {'.jpg', '.jpeg', '.png', '.bmp'}
            image_files = []
            
            for ext in image_extensions:
                image_files.extend(Path(dir_path).glob(f"*{ext}"))
                image_files.extend(Path(dir_path).glob(f"*{ext.upper()}"))
            
            if not image_files:
                print("No image files found in directory!")
                return
            
            print(f"\nFound {len(image_files)} image files")
            confirm = input(f"Add all images for {name}? (y/N): ").strip().lower()
            
            if confirm == 'y':
                success_count = 0
                for img_file in image_files:
                    if database.add_face(str(img_file), name, gender, age, is_multi_image=True):
                        success_count += 1
                
                print(f"Successfully added {success_count}/{len(image_files)} images for {name}")
            else:
                print("Operation cancelled")
                
        except Exception as e:
            print(f"Error during batch add: {e}")
    
    def review_unknown_faces(self) -> bool:
        """
        Interactive unknown face review interface.
        
        Returns:
            bool: True to continue application, False to exit
        """
        unknown_dir = self.config.unknown_faces_dir
        if not unknown_dir.exists() or not any(unknown_dir.iterdir()):
            print("No unknown faces to review!")
            return True

        unknown_files = sorted(unknown_dir.glob("*.jpg"), key=os.path.getmtime)
        print(f"\nFound {len(unknown_files)} unknown faces to review")

        database = FaceDatabase(self.config)

        for i, filepath in enumerate(unknown_files, 1):
            if not self._review_single_unknown_face(filepath, database, i, len(unknown_files)):
                break  # User chose to stop reviewing

        return True
    
    def _review_single_unknown_face(self, filepath: Path, database: FaceDatabase, 
                                   current: int, total: int) -> bool:
        """Review a single unknown face file."""
        print(f"\n--- Reviewing {current}/{total}: {filepath.name} ---")

        img = cv2.imread(str(filepath))
        if img is not None:
            cv2.imshow("Unknown Face - Press any key to continue", img)
            cv2.waitKey(0)
            cv2.destroyAllWindows()

        print("\nOptions:")
        print("1. Add to database as new person")
        print("2. Add to existing person")
        print("3. Skip (keep for later)")
        print("4. Delete this unknown face")
        print("5. Stop reviewing")

        action = input("Choose action (1-5): ").strip()

        if action == '1':
            self._add_unknown_as_new(filepath, database)
        elif action == '2':
            self._add_unknown_to_existing(filepath, database)
        elif action == '3':
            print("Keeping for later review")
        elif action == '4':
            filepath.unlink()
            print("Deleted unknown face")
        elif action == '5':
            print("Stopping review")
            return False
        else:
            print("Invalid choice, keeping for later")

        return True
    
    def _add_unknown_as_new(self, filepath: Path, database: FaceDatabase):
        """Add unknown face as a new person."""
        try:
            name = input("Enter person's name: ").strip()
            gender = input("Enter gender (Male/Female): ").strip()
            age_str = input("Enter age (number): ").strip()

            age = validate_age(age_str)
            gender = validate_gender(gender)

            if database.add_face(str(filepath), name, gender, age):
                delete_choice = input("Delete original unknown file? (Y/N): ").strip().lower()
                if delete_choice == 'y':
                    filepath.unlink()
                    print("Deleted unknown file")
        except Exception as e:
            print(f"Error adding to database: {e}")
    
    def _add_unknown_to_existing(self, filepath: Path, database: FaceDatabase):
        """Add unknown face to existing person."""
        if not database.known_faces:
            print("Database is empty! Use option 1 to add as new person.")
            return

        print("\nExisting people:")
        for name, data in database.known_faces.items():
            count = data.get('image_count', 1)
            print(f"  - {name} ({count} images)")

        name = input("\nEnter name to add this face to: ").strip()
        if name not in database.known_faces:
            print(f"Person '{name}' not found in database!")
            return

        try:
            gender = database.known_faces[name]['gender']
            age = database.known_faces[name]['age']
            
            if database.add_face(str(filepath), name, gender, age, is_multi_image=True):
                delete_choice = input("Delete original unknown file? (Y/N): ").strip().lower()
                if delete_choice == 'y':
                    filepath.unlink()
                    print("Deleted unknown file")
        except Exception as e:
            print(f"Error adding to database: {e}")
    
    def show_controls_help(self, is_camera: bool = False):
        """Display control help for video processing."""
        print("\nControls during video playback:")
        print("  SPACE - Pause/resume")
        print("  Q/ESC - Quit application")
        print("  S     - Save current frame")
        print("  D     - Open database management")
        print("  U     - Save ALL unknown faces in current frame")
        print("  R     - Review unknown faces")
        if is_camera:
            print("  C     - Cycle through cameras")
        print("  H     - Show this help")
    
    def display_frame(self, frame: any, window_name: str):
        """Display a frame in the specified window."""
        cv2.imshow(window_name, frame)
    
    def cleanup_windows(self):
        """Clean up all OpenCV windows."""
        cv2.destroyAllWindows()
