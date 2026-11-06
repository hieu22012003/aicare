"""Hardware-only bounded mono recording. Called exclusively in worker process."""
import threading
import numpy as np
from scipy.signal import resample_poly
from math import gcd


class AudioRecorder:
    def __init__(self, max_seconds=15):
        self.max_seconds = max_seconds
        self.stream = None
        self.lock = threading.Lock()
        self.chunks = []
        self.frames = 0
        self.error = None

    def start(self):
        try:
            import sounddevice as sd
        except OSError as exc:
            if 'PortAudio' in str(exc):
                raise RuntimeError('Thiếu thư viện âm thanh PortAudio. Cần cài đặt trên máy để sử dụng micro.') from None
            raise
        if self.stream is not None:
            raise RuntimeError('Micro đang được sử dụng')
        device = sd.query_devices(kind='input')
        self.rate = int(device['default_samplerate'])
        sd.check_input_settings(channels=1, dtype='float32', samplerate=self.rate)
        self.chunks, self.frames, self.error = [], 0, None
        def capture(indata, frames, timing, status):
            with self.lock:
                if status:
                    self.error = 'Micro overflow/thiết bị lỗi: ' + str(status)
                remaining = int(self.max_seconds * self.rate) - self.frames
                chunk = indata[:max(0, remaining), 0].copy()
                self.chunks.append(chunk) if len(chunk) else None
                self.frames += len(chunk)
        self.stream = sd.InputStream(channels=1, dtype='float32', samplerate=self.rate,
                                     callback=capture)
        try:
            self.stream.start()
        except Exception:
            self.close()
            raise

    def close(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def stop(self):
        self.close()  # Never stop/join a callback while holding its lock.
        with self.lock:
            chunks, self.chunks = self.chunks, []
            error = self.error
        if error:
            raise RuntimeError(error)
        if not chunks:
            raise ValueError('Không thu được âm thanh từ micro')
        audio = np.concatenate(chunks)
        if len(audio) < self.rate * .2 or not np.isfinite(audio).all():
            raise ValueError('Bản ghi quá ngắn hoặc không hợp lệ')
        if np.any(np.abs(audio) >= .9999):
            raise ValueError('Micro bị clipping; hãy giảm âm lượng đầu vào')
        if self.rate != 16000:
            divisor = gcd(self.rate, 16000)
            audio = resample_poly(audio, 16000 // divisor, self.rate // divisor)
        return np.asarray(audio[:int(self.max_seconds * 16000)], dtype=np.float32)
