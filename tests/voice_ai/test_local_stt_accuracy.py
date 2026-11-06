from types import SimpleNamespace
from unittest.mock import Mock
import unicodedata
import numpy as np
import pytest
from features.voice_assistant.local_voice.config import VoiceConfig
from features.voice_assistant.local_voice.engine import LocalSttEngine, prepare_audio


def engine_with_segments(segments):
    engine=LocalSttEngine.__new__(LocalSttEngine)
    engine.config=VoiceConfig('unused')
    engine.device='cpu'
    engine.model=Mock()
    engine.stream=Mock()
    engine.stream.result=SimpleNamespace(text=' '.join(s.text for s in segments))
    engine.model.create_stream.return_value=engine.stream
    return engine


def segment(text,**overrides):
    return SimpleNamespace(**dict(dict(text=text,avg_logprob=-.2,no_speech_prob=.1,compression_ratio=1.1),**overrides))


def speech():
    return (.03*np.sin(np.arange(16000,dtype=np.float32)*.1)).astype(np.float32)


def test_accuracy_decoding_keeps_accents_negation_and_session():
    text=unicodedata.normalize('NFD','Tôi chưa uống thuốc, đừng đánh dấu đã uống.')
    engine=engine_with_segments([segment(text)])
    result=engine.transcribe(speech(),'session-123')
    assert result['transcript']=='Tôi chưa uống thuốc, đừng đánh dấu đã uống.'
    assert result['session_id']=='session-123'
    engine.stream.accept_waveform.assert_called_once()
    assert engine.stream.accept_waveform.call_args.args[0]==16000
    engine.model.decode_stream.assert_called_once_with(engine.stream)


def test_silence_and_dc_do_not_invoke_decoder():
    engine=engine_with_segments([])
    for audio in [np.zeros(16000,dtype=np.float32),np.full(16000,.02,dtype=np.float32)]:
        assert engine.transcribe(audio)['transcript']==''
    engine.model.decode_stream.assert_not_called()


def test_quiet_signal_amplification_bounded_without_changing_waveform():
    audio=speech()*.1+.02
    prepared=prepare_audio(audio)
    assert abs(float(prepared.mean()))<1e-6
    assert np.max(np.abs(prepared))==pytest.approx(.012,abs=1e-4)
    assert np.corrcoef(audio,prepared)[0,1]>.9999
    assert prepared.dtype==np.float32


@pytest.mark.parametrize('audio',[np.array([],dtype=np.float32),np.array([np.nan]),np.ones((2,2)),np.ones(480001),np.array([1.1])])
def test_invalid_audio_never_reaches_decoder(audio):
    engine=engine_with_segments([])
    with pytest.raises(ValueError): engine.transcribe(audio)
    engine.model.decode_stream.assert_not_called()


def test_thirty_second_limit_and_result_metadata():
    engine=engine_with_segments([segment('Tôi cần hỗ trợ.')])
    result=engine.transcribe(np.tile(speech(),30))
    assert result['audio_duration_ms']==30000
    assert result['speech_duration_ms'] is None
    assert VoiceConfig('x').beam_size==1
    assert VoiceConfig('x').max_seconds==30


def test_relative_model_path_is_app_relative(monkeypatch):
    from pathlib import Path
    monkeypatch.setenv('VOICE_MODEL_PATH','data/models/sherpa-onnx-zipformer-vi-30M-int8-2026-02-09')
    monkeypatch.setenv('VOICE_BEAM_SIZE','5')
    config=VoiceConfig.from_env()
    assert Path(config.model_path)==Path(__file__).resolve().parents[2]/'data/models/sherpa-onnx-zipformer-vi-30M-int8-2026-02-09'


@pytest.mark.parametrize('beam',[0,2,6,100])
def test_invalid_beam_rejected(beam):
    with pytest.raises(ValueError): VoiceConfig('x',beam_size=beam)


def test_missing_model_is_actionable(tmp_path):
    with pytest.raises(RuntimeError, match='prepare_local_stt'):
        LocalSttEngine(VoiceConfig(str(tmp_path)))


def test_model_loaded_with_cpu_and_decoder_configuration(tmp_path, monkeypatch):
    import sys
    from features.voice_assistant.local_voice.engine import MODEL_FILES
    for name in MODEL_FILES:
        (tmp_path/name).touch()
    factory=Mock()
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', SimpleNamespace(OfflineRecognizer=SimpleNamespace(from_transducer=factory)))
    LocalSttEngine(VoiceConfig(str(tmp_path)))
    assert factory.call_args.kwargs['provider']=='cpu'
    assert factory.call_args.kwargs['decoding_method']=='greedy_search'
    LocalSttEngine(VoiceConfig(str(tmp_path), beam_size=5))
    assert factory.call_args.kwargs['decoding_method']=='modified_beam_search'
    with pytest.raises(ValueError, match='VOICE_DEVICE'):
        LocalSttEngine(VoiceConfig(str(tmp_path), device='cuda'))
