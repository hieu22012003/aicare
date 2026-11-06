# OpenCV Face Recognition System

<div align="center">

![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)
![OpenCV](https://img.shields.io/badge/OpenCV-4.x-green?logo=opencv&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-blue.svg)
[![GitHub Stars](https://img.shields.io/github/stars/dsouza-shaun/opencv-face-recognition-system?style=social)](https://github.com/dsouza-shaun/opencv-face-recognition-system)

</div>
<div>

**A Python application for real-time face detection and recognition using OpenCV and LBPH algorithm.<br> 
It can identify known faces, estimate age and gender, and manage a persistent face database.**

</div>


---

## Table of Contents
- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

---

## Features

| Module              | Capabilities                                                             |
|---------------------|--------------------------------------------------------------------------|
| Face Detection      | DNN-based detector, multi-face support, adjustable confidence/padding    |
| Recognition         | LBPH algorithm, persistent `pickle` database, multiple images per person |
| Demographics        | Age range + gender prediction via pre-trained CNNs                       |
| Database Management | Add/remove/list users, view stats, auto-save unknown faces               |
| Input Flexibility   | Webcam, video files, images + output saving                              |

---

## Installation

Clone the repository:

```bash
git clone https://github.com/dsouza-shaun/opencv-face-recognition-system.git
cd opencv-face-recognition-system
```

Create a virtual environment (recommended):

```bash
python -m venv .venv
```

Activate it:

Linux / macOS

```bash
source .venv/bin/activate
```

Windows

```bash
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Model Files

The system requires several pre-trained model files inside the `models/` directory:

- `opencv_face_detector.pbtxt`
- `opencv_face_detector_uint8.pb`
- `age_deploy.prototxt`
- `age_net.caffemodel`
- `gender_deploy.prototxt`
- `gender_net.caffemodel`

These models are used for face detection and demographic prediction.

---

## Quick Start

Run the application in interactive mode:

```bash
python main.py
```

This opens the menu interface where you can start recognition, manage the database, or review unknown faces.

---

## Usage

### Use Webcam

```bash
python main.py --input 0
```

### Process a Video File

```bash
python main.py --input video.mp4 --output processed_video.avi
```

### Process an Image

```bash
python main.py --input image.jpg --output result.jpg
```

### Open Database Management

```bash
python main.py --database
```

### Review Unknown Faces

```bash
python main.py --review-unknown
```

---

## Command Line Arguments

```
--input           Path to image/video file or camera index
--output          Path to save processed output
--confidence      Face detection confidence threshold (default: 0.7)
--padding         Padding around detected faces (default: 20)
--database        Open database management interface
--review-unknown  Review saved unknown faces
```

---

## Interactive Controls

While processing video or camera feeds:

```
SPACE   Pause or resume processing
Q / ESC Quit the application
S       Save current frame
D       Open database management
U       Save unknown faces from current frame
R       Review unknown faces
C       Cycle through available cameras
```

---

## Database Management

The face database is stored in:

```
face_database.pkl
```

Supported operations include:

- Adding a new person
- Adding more images to an existing person
- Listing all registered individuals
- Removing people from the database
- Viewing database statistics

Adding multiple images per person significantly improves recognition accuracy.

---

## Configuration

The application can be configured using `config.json`.

Example configuration:

```json
{
  "database": {
    "path": "face_database.pkl",
    "unknown_faces_dir": "unknown_faces"
  },
  "detection": {
    "confidence_threshold": 0.7,
    "padding": 20,
    "recognition_threshold": 80
  },
  "logging": {
    "level": "INFO",
    "file": "face_recognition.log"
  }
}
```

Command line arguments override configuration values.

---

## Project Structure

A detailed breakdown of the repository is available in:

[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)

---

## Troubleshooting

**No face detected**

- Ensure the face is clearly visible
- Improve lighting conditions
- Try lowering the detection confidence threshold

**Poor recognition accuracy**

- Add more images per person
- Use images from different angles and lighting conditions

**Camera not opening**

- Try different camera indices (0, 1, 2)
- Ensure no other application is using the camera

**Model files missing**

- Verify that all required model files exist in the `models/` directory

---

## Acknowledgements

<div align="center">
  <img src="open-cv.png" alt="OpenCV" width="150" />
</div>

This project relies on the following open-source resources:

- **OpenCV** – Image/video processing, face detection & LBPH recognition  
- **Age/Gender Models** – Pre-trained CNNs by [Levi & Hassner (CVPR 2015)](https://ieeexplore.ieee.org/document/7301367)

---

## License

This project is licensed under the MIT License.  
See the `LICENSE` file for details.

---

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.
Show some ❤️ and star the repo to support the project

---

## Author

Shaun Dsouza - [@dsouza-shaun](https://github.com/dsouza-shaun)

---

## Support

If you encounter a bug or have a question, please open an issue on GitHub.