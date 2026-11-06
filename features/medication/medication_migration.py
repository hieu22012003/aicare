"""Idempotent archival snapshot. Never delete profiles, old events, or evidence."""
import json
import shutil
from pathlib import Path


def archive_legacy(data_dir, shared_dir):
    archive = Path(data_dir) / 'archive' / 'fall_detection'
    archive.mkdir(parents=True, exist_ok=True)
    # Existing archives are immutable; do not replace a pre-migration snapshot.
    for source, name in [(Path(shared_dir)/'state.json','state.json'),
                         (Path(shared_dir)/'results'/'fall.json','fall.json')]:
        target = archive/name
        if source.is_file() and not target.exists():
            shutil.copy2(source,target)
    marker = archive/'migration_v1.json'
    if not marker.exists():
        marker.write_text(json.dumps({
            'version':1, 'historical_only':True,
            'events_retained_at':str(Path(data_dir)/'fall_events'),
            'profiles_preserved':True, 'automatic_deletion':False,
        },indent=2),encoding='utf-8')
    return archive
