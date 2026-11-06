"""V3 uses the same native QA gate and complete verification as PT/HP."""
from pathlib import Path
from archive import archive as checked_archive
ROOT = Path(__file__).resolve().parents[4]
PACKAGE = ROOT/'releases/portable-v3/V3/AICare'

def archive():
    return checked_archive(PACKAGE)

if __name__ == '__main__':
    archive()
