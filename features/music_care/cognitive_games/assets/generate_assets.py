"""
Utility script to generate royalty-free synthesized audio tracks (.wav) and asset manifest.
Generates:
1. trk-relax-chimes.wav (Gentle pentatonic chime tones)
2. trk-peaceful-stream.wav (Soft soothing meditation harmonic chords)
3. trk-meditation-harmonics.wav (Deep warm calm drone)
4. trk-binaural-alpha-10hz.wav (Pure stereo binaural beats: Left 440 Hz, Right 450 Hz -> 10 Hz beat)
All tracks are 100% self-generated with pure mathematics under CC0 / Public Domain.
"""

from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path


def generate_wav_file(file_path: Path, duration_s: float, left_fn, right_fn, sample_rate: int = 44100) -> None:
    num_samples = int(duration_s * sample_rate)
    with wave.open(str(file_path), "w") as wav_file:
        # 2 channels (Stereo), 2 bytes per sample (16-bit), sample_rate
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        frames = bytearray()
        for i in range(num_samples):
            t = i / sample_rate
            # Smooth fade in / fade out envelope
            fade = 1.0
            if t < 1.0:
                fade = t
            elif t > duration_s - 1.0:
                fade = duration_s - t

            l_val = max(-1.0, min(1.0, left_fn(t) * fade))
            r_val = max(-1.0, min(1.0, right_fn(t) * fade))

            l_int = int(l_val * 32767 * 0.4)  # 40% gentle volume
            r_int = int(r_val * 32767 * 0.4)

            frames.extend(struct.pack("<hh", l_int, r_int))

        wav_file.writeframes(frames)


def build_all_assets() -> None:
    base_dir = Path(__file__).parent
    audio_dir = base_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # 1. Relax Chimes (Gentle harmonic bell tones at 528 Hz, 660 Hz, 792 Hz)
    def chime_l(t: float) -> float:
        return 0.5 * math.sin(2 * math.pi * 528 * t) * math.exp(-((t % 2.5) * 1.5)) + \
               0.3 * math.sin(2 * math.pi * 792 * t) * math.exp(-(((t + 1.2) % 3.0) * 1.8))

    def chime_r(t: float) -> float:
        return 0.5 * math.sin(2 * math.pi * 660 * t) * math.exp(-(((t + 0.8) % 2.5) * 1.5)) + \
               0.3 * math.sin(2 * math.pi * 528 * t) * math.exp(-(((t + 2.1) % 3.0) * 1.8))

    chimes_path = audio_dir / "trk-relax-chimes.wav"
    generate_wav_file(chimes_path, 10.0, chime_l, chime_r)

    # 2. Peaceful Stream / Flute tone (Soft 432 Hz warm meditation)
    def stream_l(t: float) -> float:
        return 0.4 * math.sin(2 * math.pi * 432 * t) + \
               0.2 * math.sin(2 * math.pi * 216 * t) + \
               0.1 * math.sin(2 * math.pi * 864 * t)

    def stream_r(t: float) -> float:
        return 0.4 * math.sin(2 * math.pi * 432 * t + 0.4) + \
               0.2 * math.sin(2 * math.pi * 216 * t + 0.2) + \
               0.1 * math.sin(2 * math.pi * 864 * t + 0.5)

    stream_path = audio_dir / "trk-peaceful-stream.wav"
    generate_wav_file(stream_path, 10.0, stream_l, stream_r)

    # 3. Meditation Harmonics (Warm C Major triad chord: 261.63, 329.63, 392.00 Hz)
    def drone_l(t: float) -> float:
        return 0.35 * math.sin(2 * math.pi * 261.63 * t) + \
               0.25 * math.sin(2 * math.pi * 329.63 * t)

    def drone_r(t: float) -> float:
        return 0.35 * math.sin(2 * math.pi * 261.63 * t + 0.3) + \
               0.25 * math.sin(2 * math.pi * 392.00 * t)

    drone_path = audio_dir / "trk-meditation-harmonics.wav"
    generate_wav_file(drone_path, 10.0, drone_l, drone_r)

    # 4. Stereo Binaural Beats 10 Hz (Alpha trial):
    # Left = 440 Hz, Right = 450 Hz -> Beat = 10 Hz
    def bin_l(t: float) -> float:
        return math.sin(2 * math.pi * 440.0 * t)

    def bin_r(t: float) -> float:
        return math.sin(2 * math.pi * 450.0 * t)

    binaural_path = audio_dir / "trk-binaural-alpha-10hz.wav"
    generate_wav_file(binaural_path, 10.0, bin_l, bin_r)

    # Write Manifest JSON
    manifest = {
        "module": "cognitive_games",
        "version": "1.0.0",
        "content_packs": [
            {
                "pack_id": "pack-household",
                "title": "Đồ Dùng Thân Thuộc",
                "theme": "household",
                "license": "CC0-1.0 (Public Domain)",
                "items": [
                    {"id": "it-clock", "name": "Đồng hồ", "icon": "⏰"},
                    {"id": "it-teacup", "name": "Tách trà", "icon": "🍵"},
                    {"id": "it-fan", "name": "Chiếc quạt", "icon": "🪭"},
                    {"id": "it-lamp", "name": "Đèn bàn", "icon": "💡"},
                    {"id": "it-book", "name": "Quyển sách", "icon": "📖"},
                    {"id": "it-hat", "name": "Chiếc nón", "icon": "👒"},
                    {"id": "it-kettle", "name": "Ấm nước", "icon": "🫖"},
                    {"id": "it-glasses", "name": "Kính lão", "icon": "👓"},
                    {"id": "it-chair", "name": "Ghế tựa", "icon": "🪑"},
                    {"id": "it-bed", "name": "Chiếc giường", "icon": "🛏"},
                    {"id": "it-umbrella", "name": "Chiếc ô", "icon": "☂️"},
                    {"id": "it-mirror", "name": "Chiếc gương", "icon": "🪞"}
                ]
            },
            {
                "pack_id": "pack-nature",
                "title": "Hoa Cỏ Thanh Bình",
                "theme": "nature",
                "license": "CC0-1.0 (Public Domain)",
                "items": [
                    {"id": "it-lotus", "name": "Hoa sen", "icon": "🪷"},
                    {"id": "it-peach", "name": "Hoa đào", "icon": "🌸"},
                    {"id": "it-sunflower", "name": "Hoa hướng dương", "icon": "🌻"},
                    {"id": "it-leaf", "name": "Lá xanh", "icon": "🍃"},
                    {"id": "it-rice", "name": "Bông lúa", "icon": "🌾"},
                    {"id": "it-bamboo", "name": "Cây tre", "icon": "🎋"},
                    {"id": "it-tree", "name": "Cây xanh", "icon": "🌳"},
                    {"id": "it-sun", "name": "Mặt trời", "icon": "☀️"},
                    {"id": "it-cloud", "name": "Đám mây", "icon": "☁️"},
                    {"id": "it-rainbow", "name": "Cầu vồng", "icon": "🌈"},
                    {"id": "it-star", "name": "Ngôi sao", "icon": "⭐"},
                    {"id": "it-hibiscus", "name": "Hoa đỏ", "icon": "🌺"}
                ]
            },
            {
                "pack_id": "pack-fruits",
                "title": "Trái Cây Làng Quê",
                "theme": "fruits",
                "license": "CC0-1.0 (Public Domain)",
                "items": [
                    {"id": "it-banana", "name": "Quả chuối", "icon": "🍌"},
                    {"id": "it-apple", "name": "Quả táo", "icon": "🍎"},
                    {"id": "it-orange", "name": "Quả cam", "icon": "🍊"},
                    {"id": "it-watermelon", "name": "Dưa hấu", "icon": "🍉"},
                    {"id": "it-grapes", "name": "Chùm nho", "icon": "🍇"},
                    {"id": "it-mango", "name": "Quả xoài", "icon": "🥭"},
                    {"id": "it-coconut", "name": "Quả dừa", "icon": "🥥"},
                    {"id": "it-corn", "name": "Bắp ngô", "icon": "🌽"},
                    {"id": "it-strawberry", "name": "Quả dâu", "icon": "🍓"},
                    {"id": "it-pineapple", "name": "Quả dứa", "icon": "🍍"},
                    {"id": "it-peach-fruit", "name": "Quả đào", "icon": "🍑"},
                    {"id": "it-cherry", "name": "Anh đào", "icon": "🍒"}
                ]
            }
        ],
        "audio_tracks": [
            {
                "track_id": "trk-relax-chimes",
                "title": "Tiếng Chuông Gió Thanh Tịnh",
                "artist_attribution": "AI Cognitive Sound Lab",
                "asset_uri": str(chimes_path),
                "license": "CC0-1.0 (Public Domain - Mathematically Synthesized)",
                "audio_mode": "ordinary",
                "duration_seconds": 10,
                "description": "Giai điệu chuông gió êm dịu, âm lượng nhẹ nhàng giúp thư thái tinh thần."
            },
            {
                "track_id": "trk-peaceful-stream",
                "title": "Suối Reo Rừng Chiều",
                "artist_attribution": "AI Cognitive Sound Lab",
                "asset_uri": str(stream_path),
                "license": "CC0-1.0 (Public Domain - Mathematically Synthesized)",
                "audio_mode": "ordinary",
                "duration_seconds": 10,
                "description": "Hòa âm tần số 432 Hz tự nhiên, êm tai và dễ chịu."
            },
            {
                "track_id": "trk-meditation-harmonics",
                "title": "Giai Điệu Bình An",
                "artist_attribution": "AI Cognitive Sound Lab",
                "asset_uri": str(drone_path),
                "license": "CC0-1.0 (Public Domain - Mathematically Synthesized)",
                "audio_mode": "ordinary",
                "duration_seconds": 10,
                "description": "Hợp âm trưởng ấm áp xua tan căng thẳng sau giờ sinh hoạt."
            },
            {
                "track_id": "trk-binaural-alpha-10hz",
                "title": "Âm Thanh Thử Nghiệm 10Hz (Stereo Alpha Beat)",
                "artist_attribution": "AI Cognitive Sound Lab",
                "asset_uri": str(binaural_path),
                "license": "CC0-1.0 (Public Domain - Mathematically Synthesized)",
                "audio_mode": "binaural_trial",
                "duration_seconds": 10,
                "description": "Thử nghiệm âm thanh nhịp hai tai 10 Hz (Alpha). Yêu cầu tai nghe stereo. Tắt mặc định. Không dùng làm chẩn đoán y tế."
            }
        ]
    }

    manifest_path = base_dir / "asset_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Assets generated successfully at:", manifest_path)


if __name__ == "__main__":
    build_all_assets()
