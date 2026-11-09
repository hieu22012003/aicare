"""Hardware-only bounded mono recording. Called exclusively in worker process."""
import threading
import numpy as np
from scipy.signal import resample_poly
from math import gcd


# Automatic end of utterance (field 09/11: "chưa thấy nó nhận được lời nói" — the old flow recorded until
# the elder pressed "Dừng", up to 30 s, so nothing seemed to happen after speaking).
END_SILENCE_S = 0.9     # stop this long after the voice stops
NO_SPEECH_S = 7.0       # stop when nobody speaks at all
MIN_SPEECH_S = 0.25     # voiced time needed before an utterance counts
CALIBRATE_S = 0.25      # first part of the recording estimates the room noise


class AudioRecorder:
    def __init__(self, max_seconds=15):
        self.max_seconds = max_seconds
        self.stream = None
        self.lock = threading.Lock()
        self.chunks = []
        self.frames = 0
        self.error = None
        self.device_name = ''
        self._reset_vad()

    def _reset_vad(self):
        self.level = 0.0
        self._noise = []
        self._floor = None
        self._voiced = 0.0
        self._speech_started = False
        self._last_voice = 0.0
        self._elapsed = 0.0

    def _vad(self, chunk):
        """Energy VAD with an adaptive noise floor; runs in the audio callback (cheap)."""
        n = len(chunk)
        if not n:
            return
        dt = n / float(self.rate)
        rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
        self.level = min(1.0, rms / 0.1)
        self._elapsed += dt
        if self._floor is None:
            self._noise.append(rms)
            if self._elapsed >= CALIBRATE_S:
                self._floor = float(np.median(self._noise))
            return
        threshold = max(0.008, self._floor * 3.0)
        if rms > threshold:
            self._voiced += dt
            self._last_voice = self._elapsed
            if self._voiced >= MIN_SPEECH_S:
                self._speech_started = True
        elif not self._speech_started:
            self._floor = 0.95 * self._floor + 0.05 * rms  # follow slow background changes

    def auto_stop(self):
        """None while listening; 'end' after the utterance; 'no_speech' when nobody spoke."""
        with self.lock:
            if self._speech_started and self._elapsed - self._last_voice >= END_SILENCE_S:
                return 'end'
            if not self._speech_started and self._elapsed >= NO_SPEECH_S:
                return 'no_speech'
            return None

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
        self.device_name = str(device.get('name', ''))
        self.rate = int(device['default_samplerate'])
        sd.check_input_settings(channels=1, dtype='float32', samplerate=self.rate)
        self.chunks, self.frames, self.error = [], 0, None
        self._reset_vad()
        def capture(indata, frames, timing, status):
            with self.lock:
                if status:
                    self.error = 'Micro overflow/thiết bị lỗi: ' + str(status)
                remaining = int(self.max_seconds * self.rate) - self.frames
                chunk = indata[:max(0, remaining), 0].copy()
                self.chunks.append(chunk) if len(chunk) else None
                self.frames += len(chunk)
                self._vad(chunk)
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
        # A few clipped samples (a cough, a tap on the table) must not throw the utterance away.
        if np.mean(np.abs(audio) >= .9999) > 0.02:
            raise ValueError('Micro quá to (bị rè); hãy giảm âm lượng micro trong Windows')
        audio = np.clip(audio, -0.999, 0.999)
        if self.rate != 16000:
            divisor = gcd(self.rate, 16000)
            audio = resample_poly(audio, 16000 // divisor, self.rate // divisor)
        return np.asarray(audio[:int(self.max_seconds * 16000)], dtype=np.float32)
