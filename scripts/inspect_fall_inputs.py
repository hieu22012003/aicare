"""Inspect supplied clips without treating screen recording time as source PTS."""
import cv2
import hashlib
import json
from pathlib import Path

out = Path('docs/test-reports/fall_detection/input_inspection')
out.mkdir(parents=True, exist_ok=True)
files = [Path(r'C:\Users\USER\Downloads\Elderly Couple Slips and Falls Outside Front Door - 1498633 - RM Videos (720p, h264).mp4'), Path('Test_Fall.mp4'), Path('TestUI.mp4')]
items = []
for n, path in enumerate(files):
    cap = cv2.VideoCapture(str(path))
    item = dict(filename=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), fps=cap.get(cv2.CAP_PROP_FPS), metadata_frames=cap.get(cv2.CAP_PROP_FRAME_COUNT))
    count, first, last = 0, None, None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        pts = cap.get(cv2.CAP_PROP_POS_MSEC)/1000
        if first is None:
            first = pts
        last = pts
        if count == (400 if n == 0 else 60):
            cv2.imwrite(str(out / f'{n}.jpg'), frame)
        count += 1
    item.update(decoded_frames=count, first_pts=first, last_pts=last, frame_count_status='PASS' if abs(count-item['metadata_frames'])<=1 else 'FAIL')
    cap.release()
    items.append(item)
    print(item, flush=True)
(out / 'inputs.json').write_text(json.dumps(items, indent=2), encoding='utf-8')
