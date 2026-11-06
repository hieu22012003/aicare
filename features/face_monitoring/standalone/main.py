"""
Face Recognition System - Main Entry Point

A modular face recognition system with database management, 
real-time processing, and unknown face collection capabilities.

Usage:
    python main.py [--input SOURCE] [--output OUTPUT] [--confidence THRESHOLD] [--padding PADDING]
    python main.py --database
    python main.py --review-unknown

Examples:
    python main.py                           # Interactive mode
    python main.py --input 0                # Use default camera
    python main.py --input video.mp4        # Process video file
    python main.py --input image.jpg        # Process single image
    python main.py --database               # Database management mode
    python main.py --review-unknown         # Review unknown faces
"""

import argparse
import sys
import cv2
from pathlib import Path

from face_recognition import (
    FaceDatabase, FaceProcessor, MediaHandler, UIManager, Config,
    get_input_type
)
from face_recognition.logging_config import setup_logging, get_logger, PerformanceLogger


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Advanced Face Recognition System with Unknown Face Collection',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    # Input/Output options
    parser.add_argument('--input', type=str, default=None,
                        help='Path to image/video file or camera index')
    parser.add_argument('--output', type=str, default=None,
                        help='Path to save output (image/video)')
    
    # Processing options
    parser.add_argument('--confidence', type=float, default=None,
                        help='Face detection confidence threshold')
    parser.add_argument('--padding', type=int, default=None,
                        help='Padding around detected faces')
    parser.add_argument('--save-unknown', action='store_true',
                        help='Automatically save unknown faces')
    
    # Configuration options
    parser.add_argument('--config', type=str, default=None,
                        help='Path to configuration file')
    parser.add_argument('--log-level', type=str, default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        help='Logging level')
    
    # Mode options
    parser.add_argument('--database', action='store_true',
                        help='Manage face database instead of processing video')
    parser.add_argument('--review-unknown', action='store_true',
                        help='Review and label unknown faces')
    
    return parser.parse_args()


def process_single_image(image_path: str, processor: FaceProcessor, database: FaceDatabase,
                        output_path: str = None, save_unknown: bool = False,
                        confidence_threshold: float = None, padding: int = None) -> bool:
    """
    Process a single image file.
    
    Args:
        image_path: Path to image file
        processor: Face processor instance
        database: Face database instance
        output_path: Optional output path
        save_unknown: Whether to save unknown faces
        confidence_threshold: Face detection confidence threshold
        padding: Padding around faces
        
    Returns:
        bool: True if successful
    """
    try:
        frame = cv2.imread(image_path)
        if frame is None:
            print(f"Error: Failed to read image '{image_path}'")
            return False
        
        print(f"Processing image: {image_path}")
        
        with PerformanceLogger("image_processing"):
            result_frame = processor.process_frame(
                frame, database, save_unknown=save_unknown,
                confidence_threshold=confidence_threshold,
                padding=padding
            )
        
        # Display result
        window_name = "Face Recognition - Press any key to exit"
        cv2.imshow(window_name, result_frame)
        
        # Save output if specified
        if output_path:
            cv2.imwrite(output_path, result_frame)
            print(f"Result saved to: {output_path}")
        
        cv2.waitKey(0)
        cv2.destroyAllWindows()
        return True
        
    except Exception as e:
        from face_recognition.logging_config import FaceRecognitionLogger
        log_manager = FaceRecognitionLogger()
        log_manager.log_error_with_context(e, f"processing image {image_path}")
        return False


def process_video_stream(input_source: str, processor: FaceProcessor, database: FaceDatabase,
                        ui_manager: UIManager, media_handler: MediaHandler,
                        output_path: str = None, save_unknown: bool = False,
                        confidence_threshold: float = None, padding: int = None) -> bool:
    """
    Process video stream (camera or video file).
    
    Args:
        input_source: Input source path or camera index
        processor: Face processor instance
        database: Face database instance
        ui_manager: UI manager instance
        media_handler: Media handler instance
        output_path: Optional output video path
        save_unknown: Whether to save unknown faces
        confidence_threshold: Face detection confidence threshold
        padding: Padding around faces
        
    Returns:
        bool: True if successful
    """
    try:
        # Initialize input
        input_type, is_camera = media_handler.initialize_input(input_source)
        
        # Initialize output if specified
        writer = media_handler.initialize_output(output_path, input_type)
        
        # Display controls
        ui_manager.show_controls_help(is_camera)
        
        # Processing loop
        window_name = "Face Recognition System"
        paused = False
        frame_count = 0
        save_unknown_next = False
        current_frame = None
        
        logger = get_logger("main")
        logger.info(f"Starting video processing - Input: {input_source}, Type: {input_type}")
        
        while True:
            if not paused:
                ret, frame = media_handler.read_frame()
                if not ret:
                    if input_type == "video":
                        print("Video processing complete")
                    else:
                        print("Lost camera connection")
                    break
                
                frame_count += 1
                
                with PerformanceLogger("frame_processing"):
                    current_frame = processor.process_frame(
                        frame, database, save_unknown=save_unknown_next,
                        confidence_threshold=confidence_threshold,
                        padding=padding
                    )
                
                save_unknown_next = False
                
                # Write to output if specified
                if writer:
                    media_handler.write_frame(current_frame)
            
            # Display frame
            if current_frame is not None:
                ui_manager.display_frame(current_frame, window_name)
            
            # Handle keyboard input
            key = cv2.waitKey(1 if not paused else 30) & 0xFF
            
            if key == ord('q') or key == 27:  # Q or ESC
                break
            elif key == ord(' '):
                paused = not paused
                status = "PAUSED" if paused else "RUNNING"
                print(f"Processing {status}")
            elif key == ord('s'):
                media_handler.save_frame(current_frame, f"frame_{frame_count}.jpg")
            elif key == ord('u'):
                save_unknown_next = True
                print("Will save unknown faces in next frame")
            elif key == ord('r'):
                print("Opening unknown faces review...")
                ui_manager.cleanup_windows()
                ui_manager.review_unknown_faces()
                print("Returning to video stream...")
            elif key == ord('d'):
                print("Opening database management...")
                ui_manager.cleanup_windows()
                ui_manager.manage_database()
                print("Returning to video stream...")
            elif key == ord('c') and is_camera:
                media_handler.cycle_camera()
            elif key == ord('h'):
                ui_manager.show_controls_help(is_camera)
            elif cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                break
        
        # Cleanup
        media_handler.cleanup()
        ui_manager.cleanup_windows()
        
        # Save final frame if output specified
        if output_path and current_frame is not None:
            final_path = Path(output_path).with_suffix('.jpg')
            cv2.imwrite(str(final_path), current_frame)
            print(f"Final frame saved as '{final_path}'")
        
        logger.info(f"Video processing complete - Total frames: {frame_count}")
        return True
        
    except Exception as e:
        from face_recognition.logging_config import FaceRecognitionLogger
        log_manager = FaceRecognitionLogger()
        log_manager.log_error_with_context(e, f"processing video stream {input_source}")
        return False


def main():
    """Main entry point."""
    args = parse_arguments()
    config = Config(args.config)
    
    # Override config with command line arguments
    if args.confidence is not None:
        config.set('detection.confidence_threshold', args.confidence)
    if args.padding is not None:
        config.set('detection.padding', args.padding)
    
    # Setup logging
    log_manager = setup_logging(
        log_file=config.get('logging.file'),
        log_level=args.log_level
    )
    logger = get_logger("main")
    
    # Log system information
    log_manager.log_system_info()
    
    try:
        logger.info("Initializing face recognition system components...")
        
        database = FaceDatabase(config)
        processor = FaceProcessor(config)
        ui_manager = UIManager(config)
        media_handler = MediaHandler(config)
        
        logger.info("Components initialized successfully")
        
        # Handle special modes
        if args.database:
            logger.info("Starting database management mode")
            ui_manager.manage_database()
            return
        
        if args.review_unknown:
            logger.info("Starting unknown faces review mode")
            ui_manager.review_unknown_faces()
            return
        
        # Determine input source
        input_source = args.input
        if input_source is None and sys.stdin.isatty():
            input_source, mode = ui_manager.interactive_menu()
            
            if mode == 'database':
                ui_manager.manage_database()
                return
            elif mode == 'unknown_review':
                ui_manager.review_unknown_faces()
                return
        
        # Validate input source
        if input_source is None:
            print("No input source specified. Use --help for usage information.")
            return
        
        # Determine input type and process accordingly
        try:
            input_type = get_input_type(input_source)
            
            if input_type == "image":
                success = process_single_image(
                    input_source, processor, database, args.output,
                    args.save_unknown, config.confidence_threshold, config.padding
                )
            else:
                success = process_video_stream(
                    input_source, processor, database, ui_manager, media_handler,
                    args.output, args.save_unknown, config.confidence_threshold, config.padding
                )
            
            if success:
                logger.info("Processing completed successfully")
            else:
                logger.error("Processing failed")
                sys.exit(1)
                
        except Exception as e:
            from face_recognition.logging_config import FaceRecognitionLogger
            log_manager = FaceRecognitionLogger()
            log_manager.log_error_with_context(e, "determining input type")
            sys.exit(1)
        
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
        print("\nApplication interrupted by user")
    except Exception as e:
        from face_recognition.logging_config import FaceRecognitionLogger
        log_manager = FaceRecognitionLogger()
        log_manager.log_error_with_context(e, "main execution")
        print(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        log_manager.cleanup_old_logs()
        logger.info("Application terminated")


if __name__ == "__main__":
    main()