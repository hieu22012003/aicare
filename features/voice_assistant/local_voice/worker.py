"""One persistent process owns microphone/model; no per-utterance process/model."""
import queue
import time
from features.voice_assistant.local_voice.engine import LocalSttEngine
from features.voice_assistant.local_voice.recorder import AudioRecorder


def run_worker(config, commands, events):
    recorder = AudioRecorder(config.max_seconds)
    session = None
    deadline = None
    next_level = 0.0
    try:
        engine = LocalSttEngine(config)
        events.put(('ready', None, {'device': engine.device}))
        while True:
            try:
                command, token = commands.get(timeout=.05)
            except queue.Empty:
                reason = recorder.auto_stop() if session is not None else None
                if session is not None and time.monotonic() >= next_level:
                    next_level = time.monotonic() + .15
                    try:
                        events.put_nowait(('level', session, round(recorder.level, 3)))
                    except queue.Full:
                        pass
                if reason == 'no_speech':
                    recorder.close()
                    token, session, deadline = session, None, None
                    events.put(('error', token, 'Không nghe thấy tiếng nói. Bác nói to hơn, gần micro hơn'
                                + (f' (micro: {recorder.device_name})' if recorder.device_name else '')))
                    continue
                if reason == 'end' or (deadline and time.monotonic() >= deadline):
                    command, token = 'stop', session
                else:
                    continue
            if command == 'shutdown':
                break
            try:
                if command == 'record' and session is None:
                    recorder.start()
                    session, deadline = token, time.monotonic() + config.max_seconds
                    events.put(('recording', token, {'device': recorder.device_name}))
                elif command == 'cancel':
                    recorder.close()
                    session, deadline = None, None
                    recorder.chunks.clear()
                    events.put(('idle', token, None))
                elif command == 'stop' and session == token:
                    audio = recorder.stop()
                    deadline = None
                    events.put(('transcribing', token, None))
                    result = engine.transcribe(audio, token)
                    events.put(('result', token, result))
                    session = None
            except Exception as exc:
                recorder.close()
                session, deadline = None, None
                events.put(('error', token, str(exc)))
    except Exception as exc:
        events.put(('unavailable', None, str(exc)))
    finally:
        recorder.close()
