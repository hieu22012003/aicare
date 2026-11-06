#  AfterCare – Pose Estimation Functional Monitor
##  Overview

**AfterCare** is an AI-powered camera-based recovery monitoring system designed for post-hospital discharge patients.

Using **MediaPipe Pose** and computer vision, the system analyzes patient movements in real time to detect early signs of:

- Functional decline  
- Movement asymmetry  
- Reduced lower-body strength  
- Increased fall risk  

It provides objective recovery monitoring using only a standard webcam — no wearable devices required.

---

##  Problem Statement

After discharge from hospital, patients recover at home without continuous supervision.

Common risks include:

- Gradual lower-limb weakness  
- Poor rehabilitation performance  
- Movement imbalance  
- Increased fall risk  
- Delayed functional recovery  

Doctors often rely on self-reporting or occasional follow-ups.

AfterCare enables structured, AI-based movement monitoring at home.

---

##  Solution

AfterCare uses AI-based pose detection to monitor rehabilitation exercises and evaluate movement quality.

The system detects and counts:

1.  Sit-to-Stand repetitions  
2.  Arm Raises (above 150° shoulder elevation)  
3.  Marching steps  

It ensures only properly performed movements are counted.

---

##  What the System Detects

The system tracks key body joints:

- Shoulders  
- Elbows  
- Wrists  
- Knees  
- Ankles  

Using these landmarks, it performs:

###  Sit-to-Stand Detection
- Measures knee angle  
- Detects full sitting → full standing movement  
- Counts only complete repetitions  

###  Arm Raise Detection
- Calculates shoulder angle (Waist → Shoulder → Wrist)  
- Requires angle > 150°  
- Requires 0.5 second hold  
- Detects left arm, right arm, or both  

###  March Detection
- Compares left and right knee height  
- Counts alternating raised knees  
- Uses threshold to prevent false detection  

---

##  Real-Time Output

The system displays:

- Sit-to-Stand rep count  
- Arm Raise rep count  
- March step count  

These metrics can later be extended into:

- Functional Recovery Score  
- Stability Index  
- Fall Risk Level

<img width="500" height="500" alt="image" src="https://github.com/user-attachments/assets/6a18d6ca-e331-4684-aced-10ed28d9c818" />



---

##  Tech Stack

- Python  
- OpenCV  
- MediaPipe   
- NumPy  

---

##  Installation

Install dependencies:

```bash
pip install mediapipe opencv-python numpy
```
Press Esc to EXIT
