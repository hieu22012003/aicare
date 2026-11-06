"""Refresh every changed V3 member through the common native QA gate."""
from archive_v3 import archive

def refresh():
    return archive()

if __name__ == '__main__':
    refresh()
