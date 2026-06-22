"""Record positive wake-word samples for openWakeWord training.

USAGE
    .\.venv\Scripts\python.exe scripts\record_wake_word.py

Defaults: 50 samples of "Kee", 1.5 s each, 16 kHz mono, written as
WAV to `models\wakeword\samples\positive\kee_NNN.wav`. After each
clip the script plays it back and asks `[k]eep / [r]etry / [q]uit`.
At the end it prints a summary you paste back to me.

Tips for good samples:
  * Say "Kee" naturally, not exaggerated.
  * Vary the surroundings: speak close, far, sitting, standing,
    with light background noise (typing, ambient room).
  * Vary your tone: confident, asking, tired, urgent.
  * Don't whisper unless you'd plausibly trigger it whispering.
  * Leave ~150 ms of silence at the start and end of each clip.

Each sample takes ~5 seconds (record + playback + decide). Plan for
~5 minutes total for 50 samples.

REQUIREMENTS
    The script needs `sounddevice` and `scipy` (already pulled when
    installing Kee). No openWakeWord install required — that comes
    later, when we train.

OUTPUT FORMAT
    16-bit PCM, 16000 Hz, mono, ~1.5s WAV.
    Compatible with the openWakeWord training pipeline at
    https://github.com/dscripka/openWakeWord
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from scipy.io import wavfile

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "models" / "wakeword" / "samples" / "positive"
SR = 16000           # openWakeWord training sample rate
DURATION_S = 1.5      # one shot
CHANNELS = 1
DTYPE = "int16"


def beep(freq_hz: int = 880, ms: int = 120) -> None:
    """Tiny tone to signal record-now."""
    n = int(SR * ms / 1000)
    t = np.linspace(0, ms / 1000, n, endpoint=False)
    tone = (np.sin(2 * np.pi * freq_hz * t) * 0.3 * 32767).astype(np.int16)
    sd.play(tone, samplerate=SR, blocking=True)


def record_clip() -> np.ndarray:
    audio = sd.rec(int(SR * DURATION_S), samplerate=SR, channels=CHANNELS, dtype=DTYPE)
    sd.wait()
    return audio.flatten()


def playback(clip: np.ndarray) -> None:
    sd.play(clip, samplerate=SR, blocking=True)


def main() -> int:
    p = argparse.ArgumentParser(description="Record wake-word samples for openWakeWord.")
    p.add_argument("--word", default="kee", help="Wake word (used in filename).")
    p.add_argument("--count", type=int, default=50, help="How many samples to record.")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output directory.")
    p.add_argument("--start-from", type=int, default=None,
                   help="Start numbering at N (default: continue past existing files).")
    args = p.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    existing = sorted(args.out.glob(f"{args.word}_*.wav"))
    next_idx = args.start_from if args.start_from is not None else (len(existing) + 1)
    target_total = next_idx + args.count - 1

    print()
    print("=" * 60)
    print(f"Recording wake-word: '{args.word.upper()}'")
    print(f"Output directory:   {args.out}")
    print(f"Existing samples:   {len(existing)}")
    print(f"This run will record samples #{next_idx} .. #{target_total}")
    print(f"Format: 16-bit PCM mono, {SR} Hz, {DURATION_S}s clips")
    print("=" * 60)
    print()
    print("Controls between recordings:")
    print("  [Enter] keep this take and continue")
    print("  r      retry this sample (re-record)")
    print("  p      replay last take")
    print("  q      quit and save what you have")
    print()
    input("Press Enter when ready to start... ")
    print()

    saved = 0
    quit_requested = False

    while next_idx <= target_total and not quit_requested:
        retry = True
        clip: np.ndarray | None = None
        while retry:
            print(f"\n#{next_idx:03d}  ...beep, then say '{args.word.upper()}' once.")
            time.sleep(0.6)
            beep()
            time.sleep(0.05)
            clip = record_clip()
            print("       Playing back...")
            playback(clip)
            choice = input(
                "       [Enter]=keep  r=retry  p=replay  q=quit  > "
            ).strip().lower()

            if choice == "r":
                continue
            if choice == "p":
                playback(clip)
                # ask again
                choice2 = input(
                    "       [Enter]=keep  r=retry  q=quit  > "
                ).strip().lower()
                if choice2 == "r":
                    continue
                if choice2 == "q":
                    quit_requested = True
                retry = False
            elif choice == "q":
                quit_requested = True
                retry = False
            else:
                retry = False

        if clip is not None and not quit_requested:
            path = args.out / f"{args.word}_{next_idx:03d}.wav"
            wavfile.write(str(path), SR, clip)
            saved += 1
            next_idx += 1

    final_count = len(list(args.out.glob(f"{args.word}_*.wav")))
    print()
    print("=" * 60)
    print(f"Saved {saved} new sample(s) this run.")
    print(f"Total samples on disk: {final_count}")
    print(f"Location: {args.out}")
    print("=" * 60)
    print()
    print("Tell Kee or me:")
    print(f"  'Tengo {final_count} samples grabados de \"{args.word}\".'")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Samples saved up to last keep are intact.")
        sys.exit(130)
