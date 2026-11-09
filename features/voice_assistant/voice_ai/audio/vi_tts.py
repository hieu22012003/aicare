"""Offline Vietnamese text-to-speech (sherpa-onnx + Piper VITS voice vi_VN-vais1000-medium).

Field report 09/11: the medication reminder was unintelligible. pyttsx3 uses the Windows SAPI voices and
the old voice picker matched any voice id containing "vi" — "Microsoft DAVID" (English) matches — so the
Vietnamese sentence was read with English pronunciation. Windows has no Vietnamese voice unless the
language pack is installed.

Order: 1. Piper Vietnamese voice bundled in data/models (works on any Windows, no language pack)
       2. a real Vietnamese SAPI voice (id/name/language says vi-VN / Vietnamese)
       3. nothing intelligible available: a system beep, never Vietnamese read by an English voice.

Run as a process (``python -m features.voice_assistant.voice_ai.audio.vi_tts "text"``) so the coordinator
can kill it for barge-in.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import wave
from pathlib import Path

log = logging.getLogger("voice_ai.audio.vi_tts")

VOICE_DIRNAME = "vits-piper-vi_VN-vais1000-medium"
VOICE_ONNX = "vi_VN-vais1000-medium.onnx"
SPEED = float(os.getenv("AICARE_TTS_SPEED", "0.9"))  # < 1 = slower, easier for elderly listeners

_DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
_UNITS = {
    "mg": "mi li gam", "g": "gam", "kg": "ki lô gam", "mcg": "mi crô gam", "µg": "mi crô gam", "ml": "mi li lít",
    "l": "lít", "ui": "đơn vị", "iu": "đơn vị", "mmhg": "mi li mét thủy ngân", "bpm": "nhịp mỗi phút",
    "%": "phần trăm",
}


def _model_dir() -> Path | None:
    candidates = []
    try:
        from shared.config import resource_path

        candidates.append(Path(resource_path(f"data/models/{VOICE_DIRNAME}")))
    except Exception:
        pass
    candidates.append(Path(__file__).resolve().parents[4] / "data" / "models" / VOICE_DIRNAME)
    for c in candidates:
        if (c / VOICE_ONNX).is_file() and (c / "tokens.txt").is_file():
            return c
    return None


# --------------------------------------------------------------------------- text normalisation
def _read_under_1000(n: int, full: bool) -> str:
    h, rest = divmod(n, 100)
    t, u = divmod(rest, 10)
    words = []
    if h or full:
        words += [_DIGITS[h], "trăm"]
    if t == 0:
        if u:
            words += (["lẻ"] if (h or full) else []) + [_DIGITS[u]]
    elif t == 1:
        words.append("mười")
        if u:
            words.append("lăm" if u == 5 else _DIGITS[u])
    else:
        words += [_DIGITS[t], "mươi"]
        if u == 1:
            words.append("mốt")
        elif u == 4:
            words.append("tư")
        elif u == 5:
            words.append("lăm")
        elif u:
            words.append(_DIGITS[u])
    return " ".join(words)


def read_number(n: int) -> str:
    """Vietnamese reading of a non-negative integer (up to billions)."""
    if n == 0:
        return "không"
    parts, scales, i = [], ["", "nghìn", "triệu", "tỷ"], 0
    groups = []
    while n:
        n, g = divmod(n, 1000)
        groups.append(g)
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        full = i < len(groups) - 1  # inner groups read "không trăm ..."
        parts.append(_read_under_1000(g, full))
        if scales[i]:
            parts.append(scales[i])
    return " ".join(parts)


def normalize(text: str) -> str:
    """Numbers, times and dose units in words, so the voice reads "5mg" as "năm mi li gam"."""
    s = " ".join((text or "").split())
    s = re.sub(r"(\d{1,2}):(\d{2})", lambda m: f"{read_number(int(m.group(1)))} giờ"
               + ("" if m.group(2) == "00" else f" {read_number(int(m.group(2)))} phút"), s)
    s = re.sub(r"(\d)\s*/\s*(\d)", r"\1 trên \2", s)  # 135/85
    s = re.sub(r"(\d+)[.,]0\b", r"\1", s)  # "1.0 viên" -> "1 viên"
    s = re.sub(r"(\d+)[.,](\d+)", lambda m: f"{read_number(int(m.group(1)))} phẩy {read_number(int(m.group(2)))}", s)

    def unit(m):
        u = m.group(2)
        return f"{read_number(int(m.group(1)))} {_UNITS.get(u.lower(), u)}"

    s = re.sub(r"(\d+)\s*(mg|mcg|µg|ml|kg|g|l|UI|IU|mmHg|bpm|%)(?![\wÀ-ỹ])", unit, s, flags=re.IGNORECASE)
    s = re.sub(r"(?<![A-Za-z])\d+", lambda m: read_number(int(m.group(0))), s)  # not "SpO2"
    return s.replace("–", ",").replace("—", ",")


# --------------------------------------------------------------------------- engines
_tts = None


def _piper():
    global _tts
    if _tts is None:
        d = _model_dir()
        if d is None:
            return None
        import sherpa_onnx

        vits = sherpa_onnx.OfflineTtsVitsModelConfig(model=str(d / VOICE_ONNX), tokens=str(d / "tokens.txt"),
                                                     data_dir=str(d / "espeak-ng-data"))
        _tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=2)))
    return _tts


def synthesize(text: str, path: str) -> bool:
    """Write `text` as a 16-bit WAV file. False when the bundled voice is not available."""
    try:
        tts = _piper()
    except Exception as e:
        log.warning("Vietnamese voice unavailable: %s", e)
        return False
    if tts is None:
        return False
    import numpy as np

    audio = tts.generate(normalize(text), sid=0, speed=SPEED)
    samples = np.clip(np.asarray(audio.samples, dtype=np.float32) * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(audio.sample_rate)
        w.writeframes(samples.tobytes())
    return True


def play_wav(path: str) -> None:
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(path, winsound.SND_FILENAME)
        return
    import shutil
    import subprocess

    for player in (["afplay", path], ["aplay", "-q", path], ["paplay", path]):
        if shutil.which(player[0]):
            subprocess.run(player, check=False)
            return


def is_vietnamese_voice(voice) -> bool:
    """A SAPI/eSpeak voice that really speaks Vietnamese (not "DAVID", which merely contains "vi")."""
    langs = []
    for lang in getattr(voice, "languages", None) or []:
        langs.append(lang.decode(errors="ignore") if isinstance(lang, bytes) else str(lang))
    text = " ".join([getattr(voice, "id", "") or "", getattr(voice, "name", "") or ""] + langs).lower()
    return bool(re.search(r"(?<![a-z])vi([-_]vn)?(?![a-z])|vietnam|tiếng việt|\bvi-vn\b|_vi-vn_", text))


def speak_sapi(text: str) -> bool:
    try:
        import pyttsx3

        engine = pyttsx3.init()
        voice = next((v for v in engine.getProperty("voices") if is_vietnamese_voice(v)), None)
        if voice is None:
            log.warning("No Vietnamese voice installed in Windows and the bundled voice is missing")
            return False
        engine.setProperty("voice", voice.id)
        engine.setProperty("rate", 135)
        engine.setProperty("volume", 1.0)
        engine.say(normalize(text))
        engine.runAndWait()
        return True
    except Exception as e:
        log.warning("SAPI speech failed: %s", e)
        return False


CHIME_PREFIX = "\a"  # text starting with BEL: play the reminder chime first (medication due)


def write_chime(path: str, rate: int = 22050) -> None:
    """Soft three-note chime (~1.6 s), generated so no audio asset is needed."""
    import numpy as np

    notes = ((659.25, 0.0), (783.99, 0.38), (1046.5, 0.76))  # E5 G5 C6
    total = int(rate * 1.6)
    t = np.arange(total) / rate
    out = np.zeros(total, dtype=np.float64)
    for freq, start in notes:
        env = np.where(t >= start, np.exp(-(t - start) * 3.2), 0.0)
        out += env * (np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(4 * np.pi * freq * t))
    out = (out / np.max(np.abs(out)) * 0.7 * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(out.tobytes())


def play_chime() -> None:
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="aicare_chime_")
    os.close(fd)
    try:
        write_chime(path)
        play_wav(path)
    except Exception as e:
        log.warning("Chime failed: %s", e)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def speak(text: str) -> int:
    """Blocking. 0 = spoken, 1 = only a beep was possible. A leading BEL plays the chime first."""
    if text.startswith(CHIME_PREFIX):
        play_chime()
        text = text.lstrip(CHIME_PREFIX).strip()
        if not text:
            return 0
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="aicare_tts_")
    os.close(fd)
    try:
        if synthesize(text, path):
            play_wav(path)
            return 0
    except Exception as e:
        log.warning("Vietnamese voice failed: %s", e)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if speak_sapi(text):
        return 0
    if sys.platform == "win32":
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
    return 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--chime":
        sys.exit(speak(CHIME_PREFIX + (args[1] if len(args) > 1 else "")))
    sys.exit(speak(args[0]) if len(args) == 1 else 2)
