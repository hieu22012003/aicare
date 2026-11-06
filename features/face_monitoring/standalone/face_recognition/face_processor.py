"""
Face detection and processing module for the face recognition system.
"""

import cv2
import numpy as np
from typing import List, Tuple, Dict, Any, Optional

from .config import Config
from .database import FaceDatabase


class FaceProcessor:
    """
    Handles face detection, recognition, and attribute estimation.
    
    Features:
    - Deep learning based face detection
    - Age and gender estimation
    - Real-time processing with configurable parameters
    - Multiple face handling in single frame
    """
    
    def __init__(self, config: Optional[Config] = None):
        """Initialize the face processor with neural networks."""
        self.config = config or Config()
        
        # Load neural networks
        self.face_net = self._load_face_detector()
        self.age_net = self._load_age_detector()
        self.gender_net = self._load_gender_detector()
        
        # Age and gender labels
        self.age_labels = ['(0-2)', '(4-6)', '(8-12)', '(15-20)', '(25-32)', 
                          '(38-43)', '(48-53)', '(60-100)']
        self.gender_labels = ['Male', 'Female']
    
    def _load_face_detector(self):
        """Load the face detection neural network."""
        try:
            model_path = self.config.get('models.face_detector.model')
            proto_path = self.config.get('models.face_detector.proto')
            net = cv2.dnn.readNet(model_path, proto_path)
            return net
        except Exception as e:
            print(f"Error loading face detector: {e}")
            print("Falling back to Haar cascade detector")
            return None
    
    def _load_age_detector(self):
        """Load the age estimation neural network."""
        try:
            model_path = self.config.get('models.age_detector.model')
            proto_path = self.config.get('models.age_detector.proto')
            net = cv2.dnn.readNet(model_path, proto_path)
            return net
        except Exception as e:
            print(f"Error loading age detector: {e}")
            return None
    
    def _load_gender_detector(self):
        """Load the gender estimation neural network."""
        try:
            model_path = self.config.get('models.gender_detector.model')
            proto_path = self.config.get('models.gender_detector.proto')
            net = cv2.dnn.readNet(model_path, proto_path)
            return net
        except Exception as e:
            print(f"Error loading gender detector: {e}")
            return None
    
    def detect_faces(self, frame: np.ndarray, confidence_threshold: Optional[float] = None) -> Tuple[np.ndarray, List[List[int]]]:
        """
        Detect faces in the given frame.
        
        Args:
            frame: Input image frame
            confidence_threshold: Detection confidence threshold
            
        Returns:
            Tuple of (annotated_frame, face_boxes)
        """
        if confidence_threshold is None:
            confidence_threshold = self.config.confidence_threshold
        
        frame_copy = frame.copy()
        height, width = frame_copy.shape[:2]
        face_boxes = []
        
        if self.face_net is not None:
            # Use DNN-based face detection
            try:
                blob = cv2.dnn.blobFromImage(frame_copy, 1.0, (300, 300), 
                                           [104, 117, 123], True, False)
                self.face_net.setInput(blob)
                detections = self.face_net.forward()
                
                for i in range(detections.shape[2]):
                    confidence = detections[0, 0, i, 2]
                    if confidence > confidence_threshold:
                        x1 = int(detections[0, 0, i, 3] * width)
                        y1 = int(detections[0, 0, i, 4] * height)
                        x2 = int(detections[0, 0, i, 5] * width)
                        y2 = int(detections[0, 0, i, 6] * height)
                        
                        # Ensure coordinates are within frame bounds
                        x1, y1 = max(0, x1), max(0, y1)
                        x2, y2 = min(width-1, x2), min(height-1, y2)
                        
                        if x2 > x1 and y2 > y1:  # Valid box
                            face_boxes.append([x1, y1, x2, y2])
                            cv2.rectangle(frame_copy, (x1, y1), (x2, y2), 
                                        (0, 255, 0), 2, cv2.LINE_AA)
            except Exception as e:
                print(f"Error in DNN face detection: {e}")
                # Fall back to Haar cascade
                face_boxes = self._detect_faces_haar(frame_copy, confidence_threshold)
        else:
            # Use Haar cascade as fallback
            face_boxes = self._detect_faces_haar(frame_copy, confidence_threshold)
        
        return frame_copy, face_boxes
    
    def _detect_faces_haar(self, frame: np.ndarray, confidence_threshold: float) -> List[List[int]]:
        """Detect faces using Haar cascade classifier."""
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            faces = face_cascade.detectMultiScale(gray, 1.1, 4)
            
            face_boxes = []
            for (x, y, w, h) in faces:
                face_boxes.append([x, y, x+w, y+h])
                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2, cv2.LINE_AA)
            
            return face_boxes
        except Exception as e:
            print(f"Error in Haar face detection: {e}")
            return []
    
    def estimate_age_gender(self, face_img: np.ndarray) -> Tuple[str, str]:
        """
        Estimate age and gender for a face image.
        
        Args:
            face_img: Face image (color)
            
        Returns:
            Tuple of (gender, age_range)
        """
        if self.age_net is None or self.gender_net is None:
            return "Unknown", "Unknown"
        
        try:
            # Prepare input blob
            blob = cv2.dnn.blobFromImage(face_img, 1.0, (227, 227),
                                        (78.4263377603, 87.7689143744, 114.895847746), 
                                        swapRB=False)
            
            # Predict gender
            self.gender_net.setInput(blob)
            gender_preds = self.gender_net.forward()
            gender = self.gender_labels[gender_preds[0].argmax()]
            
            # Predict age
            self.age_net.setInput(blob)
            age_preds = self.age_net.forward()
            age_range = self.age_labels[age_preds[0].argmax()]
            
            return gender, age_range
            
        except Exception as e:
            print(f"Error in age/gender estimation: {e}")
            return "Unknown", "Unknown"
    
    def process_frame(self, frame: np.ndarray, database: FaceDatabase, 
                     save_unknown: bool = False, confidence_threshold: Optional[float] = None,
                     padding: Optional[int] = None) -> np.ndarray:
        """
        Process a frame for face detection and recognition.
        
        Args:
            frame: Input frame
            database: Face database for recognition
            save_unknown: Whether to save unknown faces
            confidence_threshold: Face detection confidence threshold
            padding: Padding around detected faces
            
        Returns:
            Processed frame with annotations
        """
        if confidence_threshold is None:
            confidence_threshold = self.config.confidence_threshold
        if padding is None:
            padding = self.config.padding
        
        # Detect faces
        result_frame, face_boxes = self.detect_faces(frame, confidence_threshold)
        
        if not face_boxes:
            return result_frame
        
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        unknown_faces_info = []
        
        for face_box in face_boxes:
            x1, y1, x2, y2 = face_box
            
            # Extract face region with padding
            face_color = frame[max(0, y1-padding):min(y2+padding, frame.shape[0]),
                              max(0, x1-padding):min(x2+padding, frame.shape[1])]
            face_gray = gray_frame[max(0, y1-padding):min(y2+padding, frame.shape[0]),
                                 max(0, x1-padding):min(x2+padding, frame.shape[1])]
            
            if face_color.size == 0 or face_gray.size == 0:
                continue
            
            # Try to recognize face
            recognized = database.recognize_face(face_gray)
            
            if recognized:
                # Known face - display recognition info
                label = self._format_recognition_label(recognized)
                color = (0, 255, 0)  # Green for known faces
            else:
                # Unknown face - estimate age and gender
                gender, age_range = self.estimate_age_gender(face_color)
                label = f"Unknown, {gender}, {age_range}"
                color = (0, 255, 255)  # Yellow for unknown faces
                
                if save_unknown:
                    unknown_faces_info.append({
                        'face_color': face_color.copy(),
                        'gender': gender,
                        'age_range': age_range
                    })
            
            # Draw label on frame with accented Vietnamese support
            try:
                from shared.camera.drawing_utils import draw_vietnamese_text
                draw_vietnamese_text(result_frame, label, (x1, max(5, y1 - 25)), font_size=16, color=color, stroke_color=(0, 0, 0), stroke_width=2)
            except Exception:
                cv2.putText(result_frame, label, (x1, max(15, y1-10)),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2, cv2.LINE_AA)
        
        # Save unknown faces if requested
        if save_unknown and unknown_faces_info:
            for info in unknown_faces_info:
                database.save_unknown_face(info['face_color'], 
                                         info['gender'], info['age_range'])
        
        return result_frame
    
    def _format_recognition_label(self, recognition_result: Dict[str, Any]) -> str:
        """Format the recognition result for display."""
        name = recognition_result['name']
        gender = recognition_result['gender']
        age = recognition_result['age']
        confidence = int(recognition_result['confidence'])
        
        if self.config.get('ui.display_confidence', True):
            return f"{name}, {gender}, {age} ({confidence}%)"
        else:
            return f"{name}, {gender}, {age}"
    
    def extract_face_roi(self, frame: np.ndarray, face_box: List[int], 
                        padding: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Extract face region of interest with optional padding.
        
        Args:
            frame: Input frame
            face_box: Face bounding box [x1, y1, x2, y2]
            padding: Padding around the face
            
        Returns:
            Tuple of (face_color, face_gray)
        """
        if padding is None:
            padding = self.config.padding
        
        x1, y1, x2, y2 = face_box
        height, width = frame.shape[:2]
        
        # Apply padding and ensure bounds
        x1_pad = max(0, x1 - padding)
        y1_pad = max(0, y1 - padding)
        x2_pad = min(width, x2 + padding)
        y2_pad = min(height, y2 + padding)
        
        # Extract ROIs
        face_color = frame[y1_pad:y2_pad, x1_pad:x2_pad]
        face_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)[y1_pad:y2_pad, x1_pad:x2_pad]
        
        return face_color, face_gray
    
    def validate_face_quality(self, face_img: np.ndarray) -> Dict[str, Any]:
        """
        Validate the quality of a detected face.
        
        Args:
            face_img: Face image (grayscale)
            
        Returns:
            Dictionary with quality metrics
        """
        if face_img.size == 0:
            return {'valid': False, 'reason': 'Empty face image'}
        
        # Basic quality checks
        height, width = face_img.shape
        
        # Size check
        if height < 50 or width < 50:
            return {'valid': False, 'reason': 'Face too small'}
        
        # Blur check
        laplacian_var = cv2.Laplacian(face_img, cv2.CV_64F).var()
        if laplacian_var < 100:
            return {'valid': False, 'reason': 'Face too blurry'}
        
        # Brightness check
        mean_brightness = np.mean(face_img)
        if mean_brightness < 30 or mean_brightness > 225:
            return {'valid': False, 'reason': 'Poor lighting'}
        
        return {
            'valid': True,
            'size': (width, height),
            'sharpness': laplacian_var,
            'brightness': mean_brightness
        }
    
    def get_face_embeddings(self, face_img: np.ndarray) -> Optional[np.ndarray]:
        """
        Extract face embeddings for advanced recognition (placeholder).
        
        This method can be extended to use more sophisticated face recognition
        models like FaceNet, ArcFace, etc.
        
        Args:
            face_img: Face image
            
        Returns:
            Face embeddings or None if extraction fails
        """
        # This is a placeholder for future enhancement
        # Could integrate with deep learning based face recognition models
        return None
    
    def benchmark_performance(self, test_frames: List[np.ndarray]) -> Dict[str, float]:
        """
        Benchmark the face processing performance.
        
        Args:
            test_frames: List of test frames
            
        Returns:
            Performance metrics
        """
        if not test_frames:
            return {}
        
        import time
        
        total_time = 0
        total_faces = 0
        
        for frame in test_frames:
            start_time = time.time()
            _, face_boxes = self.detect_faces(frame)
            end_time = time.time()
            
            total_time += (end_time - start_time)
            total_faces += len(face_boxes)
        
        avg_time_per_frame = total_time / len(test_frames)
        fps = 1.0 / avg_time_per_frame if avg_time_per_frame > 0 else 0
        
        return {
            'avg_time_per_frame': avg_time_per_frame,
            'fps': fps,
            'total_faces_detected': total_faces,
            'avg_faces_per_frame': total_faces / len(test_frames)
        }
