"""Stream decoded MP3/WAV PCM to PortAudio, independent of Windows media codecs."""
import threading
from PyQt5.QtCore import QObject, pyqtSignal, QCoreApplication


class PcmMusicPlayer(QObject):
    started = pyqtSignal()
    finished = pyqtSignal()
    failed = pyqtSignal(str)
    _event = pyqtSignal(int, str, str)

    def __init__(self, parent=None, output_factory=None):
        super().__init__(parent)
        self._path = ''
        self._volume = 0.35
        self._generation = 0
        self._cancel = threading.Event()
        self._resume = threading.Event()
        self._thread = None
        self._output_factory = output_factory
        self._event.connect(self._on_event)
        if QCoreApplication.instance():
            QCoreApplication.instance().aboutToQuit.connect(self.stop)

    def setMedia(self, media):
        self.stop()
        url = media.canonicalUrl() if hasattr(media, 'canonicalUrl') else media
        self._path = url.toLocalFile()

    def setVolume(self, volume):
        self._volume = max(0, min(100, volume)) / 100.0

    def play(self):
        if self._thread and self._thread.is_alive():
            self._resume.set()
            return
        self._generation += 1
        generation = self._generation
        self._cancel = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._thread = threading.Thread(target=self._run,
            args=(generation, self._path, self._cancel, self._resume), daemon=True,
            name='MusicPCM')
        self._thread.start()

    def pause(self):
        self._resume.clear()

    def stop(self):
        self._generation += 1
        self._cancel.set()
        self._resume.set()
        # PortAudio writes are bounded to short blocks; never wait for a whole song.
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=0.5)
        self._thread = None

    def _on_event(self, generation, kind, message):
        if generation != self._generation:
            return
        if kind == 'started':
            self.started.emit()
        elif kind == 'finished':
            self.finished.emit()
        else:
            self.failed.emit(message)

    def _run(self, generation, path, cancel, resume):
        try:
            import av
            import numpy as np
            import sounddevice as sd
            device = sd.query_devices(kind='output') if self._output_factory is None else {'max_output_channels': 2, 'default_samplerate': 48000}
            channels = min(2, int(device['max_output_channels']))
            if channels < 1:
                raise RuntimeError('Không tìm thấy loa/tai nghe để phát nhạc')
            rate = int(device['default_samplerate'])
            factory = self._output_factory or sd.OutputStream
            resampler = av.AudioResampler(format='flt', layout='stereo' if channels==2 else 'mono', rate=rate)
            with av.open(path) as source, factory(samplerate=rate, channels=channels, dtype='float32') as output:
                stream = next(s for s in source.streams if s.type=='audio')
                started = False
                for frame in source.decode(stream):
                    if cancel.is_set():
                        return
                    for pcm in resampler.resample(frame):
                        samples = pcm.to_ndarray().reshape(-1, channels)
                        # Small blocks keep cancellation and pause responsive.
                        for offset in range(0, len(samples), 1024):
                            while not resume.wait(0.05):
                                if cancel.is_set(): return
                            if cancel.is_set(): return
                            output.write(np.ascontiguousarray(samples[offset:offset+1024] * self._volume))
                            if not started:
                                started = True
                                self._event.emit(generation, 'started', '')
                for pcm in resampler.resample(None):
                    if cancel.is_set(): return
                    output.write(np.ascontiguousarray(pcm.to_ndarray().reshape(-1, channels) * self._volume))
                if not started:
                    raise RuntimeError('Tệp không chứa dữ liệu âm thanh phát được')
            if not cancel.is_set():
                self._event.emit(generation, 'finished', '')
        except Exception as exc:
            if not cancel.is_set():
                self._event.emit(generation, 'failed', str(exc))
