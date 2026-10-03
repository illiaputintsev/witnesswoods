"""Demo voice-over with Gemini text-to-speech: one WAV per paragraph plus one combined file.

Needs GEMINI_API_KEY in .env (create one at https://aistudio.google.com/apikey).
Run: .venv/bin/python scripts/voiceover.py [--voice Charon] [--style "..."] [--only 3]
Output: out/voiceover/01.wav ... and out/voiceover/voiceover_full.wav
"""
import argparse
import base64
import io
import os
import sys
import wave
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
OUT = ROOT / "out" / "voiceover"
MODEL = "gemini-3.8-flash-tts"
STYLE = "calm, clear and matter-of-fact, like a documentary narrator; medium pace; no drama"
GAP_S = 0.7  # silence between paragraphs in the combined file

PARAGRAPHS = [
    "Every week, forest owners across Sweden tell the Swedish Forest Agency they plan to clear-cut. Felling can normally "
    "start six weeks later. That six-week window is the only time an ecologist can check a site. Nobody can check them all.",
    "This is Witness Woods. This week, nine hundred and seventy-four new sites were notified. About half of them have no "
    "recent species records close by.",
    "Our agent, Forest Witness, investigates the most urgent ones in every county. It reads the notification and checks "
    "whether the forest is already cut. It pulls species records from Sweden's national database, checks the Red List "
    "for threats from forestry, and asks one honest question: has anyone actually looked here?",
    "Here is a high-priority site. Two endangered fungi were recorded about thirty metres from the boundary, and forestry "
    "is among their threats. Every claim links to the real record. The dossier shows the date the window closes, and it "
    "says plainly what the evidence cannot tell you.",
    "A reviewer can ask follow-up questions. Here, it drafts a field-visit plan for this exact site.",
    "Where there are no records, it doesn't pretend. It says: under-surveyed.",
    "Everything the agent sees is open public data. Felling notifications and satellite-detected clear-cuts come from the "
    "Swedish Forest Agency. More than a hundred million species observations come from Artportalen, the national species "
    "database. Threats for each species come from the Swedish Red List twenty twenty-five. The agent also measures how "
    "much anyone has recorded around each site, so it can tell nothing is here apart from nobody has looked.",
    "How do we know it points to the right places? We hid the agency's map of known valuable forests, ranked every "
    "notification blind, and checked once. In a test fixed in advance, our top ten percent were almost twice as likely "
    "as random sites of the same size to lie next to a known valuable forest.",
    "This works because Sweden makes this information public: every felling notification, every species record, every "
    "Red List assessment. Most countries don't yet. Witness Woods shows what that openness makes possible, and we hope "
    "it helps make the case for other countries to open their own felling and biodiversity data.",
    "Witness Woods ranks new felling sites so reviewers know where to look first.",
]


def speak(client, text: str, voice: str, style: str) -> bytes:
    r = client.interactions.create(
        model=MODEL,
        input=[{"type": "user_input", "content": [{"type": "text", "text": text,
                                                   "annotations": [{"type": "speech_metadata", "style": style}]}]}],
        response_format={"type": "audio"},
        generation_config={"speech_config": [{"voice": voice}]},
    )
    return base64.b64decode(r.output_audio.data)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="Elio")
    ap.add_argument("--style", default=STYLE)
    ap.add_argument("--only", type=int, help="re-record just this paragraph number (1-based)")
    a = ap.parse_args()
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        sys.exit("Add GEMINI_API_KEY=... to .env first (create a key at https://aistudio.google.com/apikey).")
    from google import genai
    client = genai.Client()
    OUT.mkdir(parents=True, exist_ok=True)
    todo = [a.only] if a.only else range(1, len(PARAGRAPHS) + 1)
    for i in todo:
        f = OUT / f"{i:02d}.wav"
        f.write_bytes(speak(client, PARAGRAPHS[i - 1], a.voice, a.style))
        print(f"{f.name}: {PARAGRAPHS[i - 1][:60]}...", flush=True)
    # combined file: all paragraphs in order with a short silence between them
    frames, params = [], None
    for i in range(1, len(PARAGRAPHS) + 1):
        f = OUT / f"{i:02d}.wav"
        if not f.exists():
            continue
        with wave.open(io.BytesIO(f.read_bytes())) as w:
            params = params or w.getparams()
            frames.append(w.readframes(w.getnframes()))
    if params:
        silence = b"\x00" * int(params.framerate * GAP_S) * params.sampwidth * params.nchannels
        with wave.open(str(OUT / "voiceover_full.wav"), "wb") as w:
            w.setparams(params)
            w.writeframes(silence.join(frames))
        secs = sum(len(x) for x in frames) / (params.framerate * params.sampwidth * params.nchannels)
        print(f"voiceover_full.wav: {secs + GAP_S * (len(frames) - 1):.0f} s, voice {a.voice}")


if __name__ == "__main__":
    main()
