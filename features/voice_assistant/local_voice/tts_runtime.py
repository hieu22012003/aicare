"""Small TTS subprocess entry for frozen desktop releases."""
import sys


def speak(text):
    try:
        import pyttsx3
        engine = pyttsx3.init()
        for voice in engine.getProperty('voices'):
            if 'vi' in getattr(voice, 'languages', []) or 'vi' in voice.id.lower():
                engine.setProperty('voice', voice.id)
                break
        engine.setProperty('rate', 135)
        engine.setProperty('volume', 1.0)
        engine.say(text)
        engine.runAndWait()
        return 0
    except Exception:
        return 1


if __name__ == '__main__':
    sys.exit(speak(sys.argv[1]) if len(sys.argv) == 2 else 1)
