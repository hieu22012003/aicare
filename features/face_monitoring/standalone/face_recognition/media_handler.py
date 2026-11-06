"""
Media handling module for the face recognition system.
"""

import os
import sys
from typing import Optional, Tuple, Any

import cv2

from .config import Config
from .utils import get_input_type, ensure_directory


class MediaHandler:
    """
    Handles media input/output operations for the face recognition system.
    
    Features:
    - Camera, image, and video file handling
    - Output recording capabilities
    - Frame saving functionality
    - Multi-camera support
    """
    
    def __init__(self, config: Optional[Config] = None):
        """Initialize the media handler."""
        self.config = config or Config()
        self.current_capture = None
        self.current_writer = None
        self.is_camera = False
        self.camera_index = 0
    
    def initialize_input(self, input_source: Optional[str]) -> Tuple[str, bool]:
        """
        Initialize input source (camera, image, or video).
        
        Args:
            input_source: Path to file or camera index
            
        Returns:
            Tuple of (input_type, is_camera)
        """
        try:
            input_type = get_input_type(input_source)
            
            if input_type == "camera":
                camera_index = 0 if input_source is None else int(input_source)
                self.current_capture = cv2.VideoCapture(camera_index)
                
                if not self.current_capture.isOpened():
                    raise ValueError(f"Failed to open camera index {camera_index}")
                
                self.is_camera = True
                self.camera_index = camera_index
                print(f"Using camera index: {camera_index}")
                
            elif input_type == "video":
                self.current_capture = cv2.VideoCapture(input_source)
                
                if not self.current_capture.isOpened():
                    raise ValueError(f"Failed to open video file '{input_source}'")
                
                self.is_camera = False
                print(f"Processing video: {input_source}")
                
            else:  # image
                self.current_capture = cv2.imread(input_source)
                if self.current_capture is None:
                    raise ValueError(f"Failed to read image '{input_source}'")
                
                self.is_camera = False
                print(f"Processing image: {input_source}")
            
            return input_type, self.is_camera
            
        except Exception as e:
            print(f"Error initializing input: {e}")
            sys.exit(1)
    
    def initialize_output(self, output_path: Optional[str], input_type: str) -> Optional[cv2.VideoWriter]:
        """
        Initialize output writer for video recording.
        
        Args:
            output_path: Path to output file
            input_type: Type of input media
            
        Returns:
            VideoWriter object or None
        """
        if not output_path or input_type == "image":
            return None
        
        try:
            # Get frame dimensions and FPS from input
            if self.is_camera:
                # Use default camera settings
                frame_width = 1280
                frame_height = 720
                fps = 30
            else:
                frame_width = int(self.current_capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                frame_height = int(self.current_capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = max(1, int(self.current_capture.get(cv2.CAP_PROP_FPS)))
            
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            writer = cv2.VideoWriter(output_path, fourcc, fps, (frame_width, frame_height))
            
            if not writer.isOpened():
                raise ValueError(f"Failed to open output writer: {output_path}")
            
            self.current_writer = writer
            print(f"Output will be saved to: {output_path}")
            return writer
            
        except Exception as e:
            print(f"Error initializing output: {e}")
            return None
    
    def read_frame(self) -> Tuple[bool, Any]:
        """
        Read a frame from the current input source.
        
        Returns:
            Tuple of (success, frame)
        """
        if self.is_camera or isinstance(self.current_capture, cv2.VideoCapture):
            return self.current_capture.read()
        else:
            # For images, return the same frame once
            if self.current_capture is not None:
                frame = self.current_capture.copy()
                self.current_capture = None  # Clear after first read
                return True, frame
            return False, None
    
    def write_frame(self, frame: Any):
        """Write a frame to the output writer."""
        if self.current_writer is not None:
            self.current_writer.write(frame)
    
    def save_frame(self, frame: Any, filename: Optional[str] = None) -> str:
        """
        Save a frame to disk.
        
        Args:
            frame: Frame to save
            filename: Optional filename (auto-generated if None)
            
        Returns:
            Path to saved file
        """
        if filename is None:
            import time
            timestamp = int(time.time())
            filename = f"frame_{timestamp}.jpg"
        
        # Ensure save directory exists
        save_dir = ensure_directory(self.config.get('ui.save_frames_dir', 'saved_frames'))
        filepath = save_dir / filename
        
        try:
            cv2.imwrite(str(filepath), frame)
            print(f"Frame saved as '{filepath}'")
            return str(filepath)
        except Exception as e:
            print(f"Error saving frame: {e}")
            return ""
    
    def get_frame_info(self) -> dict:
        """Get information about the current input source."""
        info = {
            'is_camera': self.is_camera,
            'camera_index': self.camera_index if self.is_camera else None
        }
        
        if self.is_camera or isinstance(self.current_capture, cv2.VideoCapture):
            if self.current_capture.isOpened():
                info.update({
                    'width': int(self.current_capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    'height': int(self.current_capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    'fps': self.current_capture.get(cv2.CAP_PROP_FPS),
                    'frame_count': int(self.current_capture.get(cv2.CAP_PROP_FRAME_COUNT))
                })
        else:
            if self.current_capture is not None:
                height, width = self.current_capture.shape[:2]
                info.update({
                    'width': width,
                    'height': height,
                    'fps': 0,
                    'frame_count': 1
                })
        
        return info
    
    def switch_camera(self, new_index: int) -> bool:
        """
        Switch to a different camera index.
        
        Args:
            new_index: New camera index
            
        Returns:
            bool: True if successful
        """
        if not self.is_camera:
            return False
        
        try:
            # Release current camera
            if self.current_capture is not None:
                self.current_capture.release()
            
            # Open new camera
            self.current_capture = cv2.VideoCapture(new_index)
            
            if self.current_capture.isOpened():
                self.camera_index = new_index
                print(f"Switched to camera {new_index}")
                return True
            else:
                # Try to restore previous camera
                self.current_capture = cv2.VideoCapture(self.camera_index)
                print(f"Failed to open camera {new_index}, reverted to {self.camera_index}")
                return False
                
        except Exception as e:
            print(f"Error switching camera: {e}")
            return False
    
    def cycle_camera(self) -> bool:
        """Cycle through available camera indices."""
        if not self.is_camera:
            return False
        
        # Try next 10 camera indices
        for i in range(1, 11):
            next_index = (self.camera_index + i) % 10
            if self.switch_camera(next_index):
                return True
        
        print("No other cameras found")
        return False
    
    def list_available_cameras(self, max_index: int = 10) -> list:
        """
        List available camera indices.
        
        Args:
            max_index: Maximum camera index to check
            
        Returns:
            List of available camera indices
        """
        available = []
        
        for i in range(max_index):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                # Try to read a frame to confirm it works
                ret, _ = cap.read()
                if ret:
                    available.append(i)
                cap.release()
        
        return available
    
    def create_video_writer(self, output_path: str, width: int, height: int, fps: float = 30.0) -> Optional[cv2.VideoWriter]:
        """
        Create a video writer with specified parameters.
        
        Args:
            output_path: Output file path
            width: Frame width
            height: Frame height
            fps: Frames per second
            
        Returns:
            VideoWriter object or None
        """
        try:
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
            
            if writer.isOpened():
                return writer
            else:
                print(f"Failed to create video writer: {output_path}")
                return None
                
        except Exception as e:
            print(f"Error creating video writer: {e}")
            return None
    
    def process_image_file(self, image_path: str, processor_func) -> Any:
        """
        Process a single image file.
        
        Args:
            image_path: Path to image file
            processor_func: Function to process the frame
            
        Returns:
            Processed frame
        """
        try:
            frame = cv2.imread(image_path)
            if frame is None:
                raise ValueError(f"Failed to read image: {image_path}")
            
            return processor_func(frame)
            
        except Exception as e:
            print(f"Error processing image: {e}")
            return None
    
    def process_video_file(self, video_path: str, processor_func, output_path: Optional[str] = None) -> bool:
        """
        Process a video file frame by frame.
        
        Args:
            video_path: Path to video file
            processor_func: Function to process each frame
            output_path: Optional output video path
            
        Returns:
            bool: True if successful
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"Failed to open video: {video_path}")
            return False
        
        writer = None
        if output_path:
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = max(1, int(cap.get(cv2.CAP_PROP_FPS)))
            writer = self.create_video_writer(output_path, width, height, fps)
        
        try:
            frame_count = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                
                processed_frame = processor_func(frame)
                
                if writer:
                    writer.write(processed_frame)
                
                frame_count += 1
                
                # Display progress
                if frame_count % 100 == 0:
                    print(f"Processed {frame_count} frames...")
            
            print(f"Video processing complete. Total frames: {frame_count}")
            return True
            
        except Exception as e:
            print(f"Error processing video: {e}")
            return False
        finally:
            cap.release()
            if writer:
                writer.release()
    
    def generate_frame_thumbnails(self, video_path: str, output_dir: str, 
                                num_thumbnails: int = 10) -> list:
        """
        Generate thumbnails from a video file.
        
        Args:
            video_path: Path to video file
            output_dir: Directory to save thumbnails
            num_thumbnails: Number of thumbnails to generate
            
        Returns:
            List of thumbnail file paths
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
        
        ensure_directory(output_dir)
        thumbnails = []
        
        try:
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total_frames <= 0:
                return []
            
            # Calculate frame intervals
            interval = max(1, total_frames // num_thumbnails)
            
            for i in range(num_thumbnails):
                frame_num = i * interval
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
                
                ret, frame = cap.read()
                if ret:
                    thumbnail_path = os.path.join(output_dir, f"thumbnail_{i+1:03d}.jpg")
                    cv2.imwrite(thumbnail_path, frame)
                    thumbnails.append(thumbnail_path)
            
            return thumbnails
            
        except Exception as e:
            print(f"Error generating thumbnails: {e}")
            return []
        finally:
            cap.release()
    
    def get_video_duration(self, video_path: str) -> float:
        """
        Get the duration of a video file in seconds.
        
        Args:
            video_path: Path to video file
            
        Returns:
            Duration in seconds
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return 0.0
        
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            
            if fps > 0:
                return frame_count / fps
            else:
                return 0.0
                
        except Exception as e:
            print(f"Error getting video duration: {e}")
            return 0.0
        finally:
            cap.release()
    
    def cleanup(self):
        """Clean up resources."""
        if self.current_capture is not None:
            if isinstance(self.current_capture, cv2.VideoCapture):
                self.current_capture.release()
            self.current_capture = None
        
        if self.current_writer is not None:
            self.current_writer.release()
            self.current_writer = None
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.cleanup()
