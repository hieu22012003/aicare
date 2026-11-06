"""
Logging configuration for the face recognition system.
"""

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Optional


class FaceRecognitionLogger:
    """Centralized logging configuration for the face recognition system."""
    
    def __init__(self, log_file: Optional[str] = None, log_level: str = "INFO"):
        """
        Initialize the logger.
        
        Args:
            log_file: Path to log file (default: face_recognition.log)
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        self.log_file = log_file or "face_recognition.log"
        self.log_level = getattr(logging, log_level.upper(), logging.INFO)
        self.logger = self._setup_logger()
    
    def _setup_logger(self) -> logging.Logger:
        """Set up the logger with file and console handlers."""
        logger = logging.getLogger("face_recognition")
        logger.setLevel(self.log_level)
        
        # Clear existing handlers to avoid duplicates
        logger.handlers.clear()
        
        # Create formatters
        detailed_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
        )
        simple_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        )
        
        # File handler with rotation
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                self.log_file,
                maxBytes=10*1024*1024,  # 10MB
                backupCount=5,
                encoding='utf-8'
            )
            file_handler.setLevel(self.log_level)
            file_handler.setFormatter(detailed_formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"Warning: Could not create file log handler: {e}")
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)  # Only warnings and errors to console
        console_handler.setFormatter(simple_formatter)
        logger.addHandler(console_handler)
        
        return logger
    
    def get_logger(self, name: Optional[str] = None) -> logging.Logger:
        """
        Get a logger instance.
        
        Args:
            name: Logger name (optional)
            
        Returns:
            Logger instance
        """
        if name:
            return logging.getLogger(f"face_recognition.{name}")
        return self.logger
    
    def set_level(self, level: str):
        """Change the logging level."""
        self.log_level = getattr(logging, level.upper(), logging.INFO)
        self.logger.setLevel(self.log_level)
        
        # Update all handlers
        for handler in self.logger.handlers:
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                handler.setLevel(self.log_level)
    
    def log_system_info(self):
        """Log system information for debugging."""
        import sys
        import platform
        import cv2
        import numpy as np
        
        self.logger.info("=== Face Recognition System Startup ===")
        self.logger.info(f"Python version: {sys.version}")
        self.logger.info(f"Platform: {platform.platform()}")
        self.logger.info(f"OpenCV version: {cv2.__version__}")
        self.logger.info(f"NumPy version: {np.__version__}")
        self.logger.info(f"Working directory: {os.getcwd()}")
        self.logger.info("=== System Information Complete ===")
    
    def log_error_with_context(self, error: Exception, context: str = ""):
        """
        Log an error with additional context.
        
        Args:
            error: Exception that occurred
            context: Additional context information
        """
        if context:
            self.logger.error(f"Error in {context}: {type(error).__name__}: {str(error)}")
        else:
            self.logger.error(f"Error: {type(error).__name__}: {str(error)}")
        
        # Log stack trace for debugging
        self.logger.debug("Stack trace:", exc_info=True)
    
    def log_performance_metric(self, operation: str, duration: float, 
                             additional_info: Optional[dict] = None):
        """
        Log performance metrics.
        
        Args:
            operation: Name of the operation
            duration: Duration in seconds
            additional_info: Additional metrics
        """
        message = f"Performance - {operation}: {duration:.3f}s"
        if additional_info:
            info_str = ", ".join(f"{k}={v}" for k, v in additional_info.items())
            message += f" ({info_str})"
        
        self.logger.info(message)
    
    def cleanup_old_logs(self, days_to_keep: int = 30):
        """
        Clean up old log files.
        
        Args:
            days_to_keep: Number of days to keep log files
        """
        try:
            log_dir = Path(self.log_file).parent
            log_pattern = Path(self.log_file).stem
            
            for log_file in log_dir.glob(f"{log_pattern}*.log*"):
                if log_file.name != Path(self.log_file).name:
                    # Check file age
                    import time
                    file_age = (time.time() - log_file.stat().st_mtime) / (24 * 3600)
                    if file_age > days_to_keep:
                        log_file.unlink()
                        self.logger.info(f"Deleted old log file: {log_file}")
        except Exception as e:
            self.logger.warning(f"Error cleaning up old logs: {e}")


# Global logger instance
_global_logger = None


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Get a logger instance.
    
    Args:
        name: Logger name (optional)
        
    Returns:
        Logger instance
    """
    global _global_logger
    if _global_logger is None:
        _global_logger = FaceRecognitionLogger()
    
    return _global_logger.get_logger(name)


def setup_logging(log_file: Optional[str] = None, log_level: str = "INFO") -> FaceRecognitionLogger:
    """
    Set up global logging configuration.
    
    Args:
        log_file: Path to log file
        log_level: Logging level
        
    Returns:
        FaceRecognitionLogger instance
    """
    global _global_logger
    _global_logger = FaceRecognitionLogger(log_file, log_level)
    return _global_logger


def log_exception(func):
    """
    Decorator to automatically log exceptions in functions.
    
    Args:
        func: Function to decorate
        
    Returns:
        Decorated function
    """
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger = get_logger(func.__module__)
            logger.log_error_with_context(e, f"function {func.__name__}")
            raise
    return wrapper


class PerformanceLogger:
    """Context manager for timing operations."""
    
    def __init__(self, operation_name: str, logger: Optional[logging.Logger] = None,
                 additional_info: Optional[dict] = None):
        """
        Initialize performance logger.
        
        Args:
            operation_name: Name of the operation being timed
            logger: Logger instance (optional)
            additional_info: Additional information to log
        """
        self.operation_name = operation_name
        self.logger = logger or get_logger("performance")
        self.additional_info = additional_info or {}
        self.start_time = None
    
    def __enter__(self):
        """Start timing."""
        import time
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """End timing and log result."""
        import time
        if self.start_time is not None:
            duration = time.time() - self.start_time
            # Use the global logger with performance logging
            logger = get_logger("performance")
            logger.info(f"Performance - {self.operation_name}: {duration:.3f}s")
            if self.additional_info:
                info_str = ", ".join(f"{k}={v}" for k, v in self.additional_info.items())
                logger.info(f"Performance - {self.operation_name} details: ({info_str})")
