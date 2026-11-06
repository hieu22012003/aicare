"""Raw-frame annotation aid. No prediction boxes or detector output."""
import cv2
from pathlib import Path
import sys
cap=cv2.VideoCapture(sys.argv[1])
out=Path('docs/test-reports/fall_detection/input_inspection')
for pts in (14.8,15.6,16.0,16.5,17.2,17.7,18.2,18.7):
    cap.set(cv2.CAP_PROP_POS_MSEC,pts*1000)
    ok,frame=cap.read()
    if ok:
        # Lower scene is an annotation viewing crop, NOT a detector ROI.
        cv2.imwrite(str(out/f'annotation_{pts}.jpg'),frame[400:720,280:950])
cap.release()
