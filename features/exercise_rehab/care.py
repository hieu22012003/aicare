import cv2
import mediapipe as mp
import numpy as np
import time

mp_pose = mp.solutions.pose
cap = cv2.VideoCapture(0)
#  Performance Boost
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

def calculate_angle(a, b, c):
    a = np.array([a.x, a.y])
    b = np.array([b.x, b.y])
    c = np.array([c.x, c.y])

    radians = np.arctan2(c[1]-b[1], c[0]-b[0]) - \
              np.arctan2(a[1]-b[1], a[0]-b[0])
    angle = np.abs(radians * 180.0 / np.pi)

    if angle > 180:
        angle = 360 - angle
    return angle


sit_reps = 0
arm_reps = 0
march_steps = 0

sit_stage = "up"
arm_stage = "down"
march_stage = None

arm_hold_start = 0

with mp_pose.Pose(
        model_complexity=0,
        min_detection_confidence=0.6,
        min_tracking_confidence=0.6) as pose:

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(image)
        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        h, w, _ = image.shape

        if results.pose_landmarks:
            lm = results.pose_landmarks.landmark

            # -------- Landmarks --------
            ls = lm[mp_pose.PoseLandmark.LEFT_SHOULDER]
            rs = lm[mp_pose.PoseLandmark.RIGHT_SHOULDER]
            le = lm[mp_pose.PoseLandmark.LEFT_ELBOW]
            re = lm[mp_pose.PoseLandmark.RIGHT_ELBOW]
            lw = lm[mp_pose.PoseLandmark.LEFT_WRIST]
            rw = lm[mp_pose.PoseLandmark.RIGHT_WRIST]

            lh = lm[mp_pose.PoseLandmark.LEFT_HIP]   # Used internally
            rh = lm[mp_pose.PoseLandmark.RIGHT_HIP]  # Used internally

            lk = lm[mp_pose.PoseLandmark.LEFT_KNEE]
            rk = lm[mp_pose.PoseLandmark.RIGHT_KNEE]
            la = lm[mp_pose.PoseLandmark.LEFT_ANKLE]
            ra = lm[mp_pose.PoseLandmark.RIGHT_ANKLE]

            def px(p):
                return int(p.x * w), int(p.y * h)

            # -------- Draw Clean Joint Points  --------
            joint_points = [ls, rs, le, re, lw, rw, lk, rk, la, ra]

            for point in joint_points:
                cv2.circle(image, px(point), 6, (0, 255, 255), -1)

            # ==============================
            # 1) SIT TO STAND
            # ==============================
            knee_angle = calculate_angle(lh, lk, la)

            if knee_angle < 95:
                sit_stage = "down"

            if knee_angle > 165 and sit_stage == "down":
                sit_reps += 1
                sit_stage = "up"

            # ==============================
            # 2) ARM RAISE 
            # ==============================
            left_shoulder_angle = calculate_angle(lh, ls, lw)
            right_shoulder_angle = calculate_angle(rh, rs, rw)

            current_time = time.time()

            left_up = left_shoulder_angle > 150
            right_up = right_shoulder_angle > 150

            if (left_up or right_up) and arm_stage == "down":
                arm_hold_start = current_time
                arm_stage = "raising"

            if (left_up or right_up) and arm_stage == "raising":
                if current_time - arm_hold_start > 0.5:
                    arm_stage = "up"

            if not (left_up or right_up) and arm_stage == "up":
                arm_reps += 1
                arm_stage = "down"

            # ==============================
            # 3) MARCH
            # ==============================
            if lk.y < rk.y - 0.05:
                if march_stage != "left":
                    march_steps += 1
                    march_stage = "left"

            elif rk.y < lk.y - 0.05:
                if march_stage != "right":
                    march_steps += 1
                    march_stage = "right"

            # ==============================
            # DISPLAY
            # ==============================
            cv2.putText(image, f"Sit-to-Stand: {sit_reps}",
                        (20,40), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, (0,255,0), 2)

            cv2.putText(image, f"Arm Raises: {arm_reps}",
                        (20,80), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, (255,0,0), 2)

            cv2.putText(image, f"March Steps: {march_steps}",
                        (20,120), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, (0,0,255), 2)

        cv2.imshow("AfterCare Functional Monitor", image)

        if cv2.waitKey(1) & 0xFF == 27:
            break

cap.release()
cv2.destroyAllWindows()
