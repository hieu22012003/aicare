"""Local-only engine. Zipformer loading and decoding belong to the persistent worker."""
import io
import os
import time
from pathlib import Path
import numpy as np
import unicodedata


MODEL_FILES = ('encoder.int8.onnx', 'decoder.onnx', 'joiner.int8.onnx', 'tokens.txt')


def decode_audio(data):
    import av
    chunks = []
    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format='fltp', layout='mono', rate=16000)
        for frame in container.decode(audio=0):
            chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
        chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    return np.concatenate(chunks).astype(np.float32) if chunks else np.array([], dtype=np.float32)


def prepare_audio(audio):
    """Remove DC and gently amplify quiet speech, without gates that cut syllables."""
    audio = np.asarray(audio, dtype=np.float32)
    centered = audio - np.mean(audio)
    peak = float(np.max(np.abs(centered)))
    rms = float(np.sqrt(np.mean(centered.astype(np.float64) ** 2)))
    gain = min(4.0, 0.08 / max(rms, 1e-8), 0.95 / max(peak, 1e-8))
    # Preserve normal input level; attenuate only when removing DC would clip.
    gain = max(min(1.0, 0.95 / max(peak, 1e-8)), gain)
    return np.ascontiguousarray(centered * gain, dtype=np.float32)


class LocalSttEngine:
    def __init__(self, config):
        path = Path(config.model_path)
        if not all((path / f).is_file() for f in MODEL_FILES):
            raise RuntimeError('Chưa có dữ liệu model Zipformer. Chạy python scripts/prepare_local_stt.py; kiểm tra VOICE_MODEL_PATH.')
        if config.device != 'cpu':
            raise ValueError('Zipformer INT8 dùng CPU. Đặt VOICE_DEVICE=cpu.')
        import sherpa_onnx
        # sherpa's native tokens reader on Windows does not reliably open UTF-8
        # absolute paths. A portable package can have a Vietnamese parent path;
        # its model paths relative to the apps/desktop/package CWD are still ASCII.
        native_path = path
        if os.name == 'nt' and not str(path).isascii():
            try:
                relative = Path(os.path.relpath(path.resolve(), Path.cwd()))
                if str(relative).isascii():
                    native_path = relative
            except ValueError:  # Different drive; retain the supplied path.
                pass
        self.config = config
        self.device = 'cpu'
        self.model = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(native_path / MODEL_FILES[0]), decoder=str(native_path / MODEL_FILES[1]),
            joiner=str(native_path / MODEL_FILES[2]), tokens=str(native_path / MODEL_FILES[3]),
            num_threads=config.cpu_threads, sample_rate=16000, feature_dim=80,
            decoding_method='greedy_search' if config.beam_size == 1 else 'modified_beam_search',
            max_active_paths=config.beam_size, provider='cpu', model_type='transducer')

    def transcribe(self, audio, session_id=''):
        started = time.perf_counter()
        if isinstance(audio, bytes):
            audio = decode_audio(audio)
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim != 1 or not len(audio) or not np.isfinite(audio).all():
            raise ValueError('Audio rỗng hoặc không hợp lệ')
        if len(audio) > int(self.config.max_seconds * 16000) or np.max(np.abs(audio)) > 1.001:
            raise ValueError('Audio vượt giới hạn hoặc bị clipping')
        audio = prepare_audio(audio)
        if np.max(np.abs(audio)) < 1e-6:
            transcript = ''
            speech_ms = 0
        else:
            stream = self.model.create_stream()
            stream.accept_waveform(16000, audio)
            self.model.decode_stream(stream)
            # Preserve Vietnamese accents and negations without autocorrection.
            transcript = unicodedata.normalize('NFC', stream.result.text.strip())
            # Offline Zipformer does not report VAD speech duration.
            speech_ms = None
        return dict(session_id=session_id, status='completed', transcript=transcript,
                    language='vi', audio_duration_ms=round(len(audio) / 16),
                    inference_ms=round((time.perf_counter() - started) * 1000),
                    device=self.device, model=Path(self.config.model_path).name,
                    speech_duration_ms=speech_ms, error_code=None)
