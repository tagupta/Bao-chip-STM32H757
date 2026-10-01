"""Synthesise the narration with macOS `say`, and derive the video timeline from it.

Writes narration.wav (one continuous track) and beats.js (window.BEATS with t0/dur per beat).
Usage: python build_audio.py <ffmpeg-binary>
"""
import json
import os
import subprocess
import sys
import wave

FF = sys.argv[1]
VOICE = os.environ.get("VOICE", "Samantha")
WPM = os.environ.get("WPM", "172")
RATE = 44100
LEAD, TAIL = 0.35, 0.8

beats = json.load(open("beats.src.json"))
os.makedirs("audio", exist_ok=True)

t = 0.0
chunks, out = [], []
for i, b in enumerate(beats):
    aiff, wav = f"audio/{i:02d}.aiff", f"audio/{i:02d}.wav"
    subprocess.run(["say", "-v", VOICE, "-r", WPM, "-o", aiff, b["say"]], check=True)
    subprocess.run([FF, "-y", "-loglevel", "error", "-i", aiff, "-ar", str(RATE), "-ac", "1",
                    "-c:a", "pcm_s16le", wav], check=True)
    with wave.open(wav) as w:
        n = w.getnframes()
        data = w.readframes(n)
    dur = max(b.get("min", 0), LEAD + n / RATE + TAIL)
    total = int(round(dur * RATE))
    seg = b"\x00\x00" * int(LEAD * RATE) + data
    seg = (seg + b"\x00\x00" * total)[: total * 2]
    chunks.append(seg)
    out.append({**b, "t0": round(t, 4), "dur": round(total / RATE, 4)})
    t += total / RATE

with wave.open("narration.wav", "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(RATE)
    w.writeframes(b"".join(chunks))

with open("beats.js", "w") as f:
    f.write("window.BEATS = " + json.dumps(out, ensure_ascii=False, indent=1) + ";\n")
print(f"{len(out)} beats, total {t:.1f}s")
