"""Small TTS subprocess entry for frozen desktop releases (``AICare.exe --local-tts "text"``)."""
import sys


def speak(text):
    try:
        from features.voice_assistant.voice_ai.audio.vi_tts import speak as vi_speak

        return vi_speak(text)
    except Exception:
        return 1


if __name__ == '__main__':
    sys.exit(speak(sys.argv[1]) if len(sys.argv) == 2 else 1)
