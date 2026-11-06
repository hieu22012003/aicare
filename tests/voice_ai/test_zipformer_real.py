"""Opt-in real model checks. No synthetic tone is treated as Vietnamese speech."""
import os
from pathlib import Path
import numpy as np
import pytest
from features.voice_assistant.local_voice.config import VoiceConfig
from features.voice_assistant.local_voice.engine import LocalSttEngine


@pytest.fixture(scope='module')
def engine():
    path = os.getenv('LOCAL_STT_TEST_MODEL')
    if not path:
        pytest.skip('Set LOCAL_STT_TEST_MODEL to an explicitly prepared local Vietnamese Zipformer model')
    return LocalSttEngine(VoiceConfig(path))


def test_real_model_silence_and_invalid_audio(engine):
    assert engine.transcribe(np.zeros(16000, dtype=np.float32))['transcript'] == ''
    with pytest.raises(Exception):
        engine.transcribe(b'not audio')
    with pytest.raises(ValueError):
        engine.transcribe(np.array([np.nan], dtype=np.float32))


def test_real_vietnamese_recording(engine):
    path, expected = os.getenv('LOCAL_STT_TEST_WAV'), os.getenv('LOCAL_STT_TEST_EXPECTED')
    if not path or not expected:
        pytest.skip('Requires consented Vietnamese speech and ground-truth transcript')
    result = engine.transcribe(Path(path).read_bytes(), 'real-audio')
    assert expected.casefold() in result['transcript'].casefold()
    assert result['device'] == 'cpu'
    assert result['audio_duration_ms'] > 0
