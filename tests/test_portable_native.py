"""Fail before loading extensions when the portable CRT is incomplete."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest

MODULE = Path(__file__).resolve().parents[1]/'deploy/windows/packaging/windows_portable/portable_native.py'


def module():
    spec = importlib.util.spec_from_file_location('native_test', MODULE)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows loader')
@pytest.mark.parametrize('case', ['missing_manifest', 'missing_dll', 'corrupt_dll'])
def test_incomplete_crt_rejected_before_loading(case, tmp_path):
    native = module()
    if case != 'missing_manifest':
        (tmp_path/'vc-runtime.json').write_text(json.dumps({'files': {'msvcp140.dll': '0'*64}}))
    if case == 'corrupt_dll':
        (tmp_path/'msvcp140.dll').write_bytes(b'bad DLL')
    with patch.object(native.sys, 'executable', str(tmp_path/'python.exe')):
        with patch.object(native.ctypes, 'WinDLL') as load:
            with pytest.raises(RuntimeError, match='missing|damaged'):
                native.bootstrap()
            load.assert_not_called()


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows native paths')
def test_zipformer_uses_ascii_relative_paths_under_unicode_parent(tmp_path, monkeypatch):
    from features.voice_assistant.local_voice.engine import LocalSttEngine, MODEL_FILES
    app = tmp_path/'Máy mới có dấu'
    model = app/'data/models/zipformer'
    model.mkdir(parents=True)
    for name in MODEL_FILES:
        (model/name).touch()
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return object()
    monkeypatch.setitem(__import__('sys').modules, 'sherpa_onnx',
                        SimpleNamespace(OfflineRecognizer=SimpleNamespace(from_transducer=create)))
    monkeypatch.chdir(app)
    LocalSttEngine(SimpleNamespace(model_path=str(model), device='cpu', cpu_threads=2, beam_size=1))
    for key, filename in zip(('encoder','decoder','joiner','tokens'), MODEL_FILES):
        path = Path(captured[key])
        assert not path.is_absolute()
        assert str(path).isascii()
        assert path.resolve() == model/filename
