# Train the "Kee" wake-word model

Phase 2 voice uses [openWakeWord](https://github.com/dscripka/openWakeWord). You've already recorded the 50 positive samples (`scripts/record_wake_word.py`). This document explains the three paths to actually train the model.

## TL;DR — fastest viable path

1. Install training extras locally (~2 GB of deps):
   ```powershell
   .\.venv\Scripts\pip.exe install openwakeword[training]
   ```
2. Run the training notebook on your machine:
   ```powershell
   .\.venv\Scripts\jupyter.exe notebook
   # open: D:\Kee\.venv\Lib\site-packages\openwakeword\notebooks\automatic_model_training_simple.ipynb
   ```
3. In the notebook, point `target_phrase` at "Kee" and `clip_dir` at `D:/Kee/models/wakeword/samples/positive`. Run all cells. ~30-90 minutes on the RTX 5050.
4. Copy the resulting `*.onnx` to `D:\Kee\models\wakeword\kee.onnx`.
5. Restart Kee — `kee/perception/voice.py` auto-detects that file and switches from the `hey_jarvis_v0.1` placeholder.

## Path A — Colab (free GPU, zero local install)

Best when you don't want to occupy your machine for an hour:

1. Upload your `D:\Kee\models\wakeword\samples\positive\*.wav` to a Google Drive folder.
2. Open https://github.com/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training_simple.ipynb in Colab (file → "Open in Colab").
3. Switch runtime to GPU (Runtime → Change runtime type → T4).
4. Set `target_phrase = "Kee"`, point `clip_dir` at your Drive folder.
5. Run all cells. About 30 minutes wall-clock.
6. Download the trained `*.onnx`, drop it at `D:\Kee\models\wakeword\kee.onnx`.

## Path B — local (your RTX 5050)

The training notebook generates ~10 000 synthetic positive variations from your 50 clips (using a small TTS model) and pulls a few GB of background noise for negatives. Disk footprint: ~5-10 GB during training, ~50 MB final model.

The notebook is documented inline. Skim it once before running. Key knobs:

| Knob | Default | When to change |
|------|---------|----------------|
| `target_phrase` | `"hey jarvis"` | Set to `"Kee"`. |
| `n_synthetic_clips` | 10000 | Lower to 2000 for a fast (~10 min) trial run. |
| `n_epochs` | 50 | Lower to 10 for trial; raise to 100 for final. |
| `clip_dir` | (notebook default) | Point at `D:/Kee/models/wakeword/samples/positive`. |

## Path C — keep the placeholder

The voice pipeline runs today with `hey_jarvis_v0.1` as the wake word. If "say Hey Jarvis to Kee" is acceptable while you do other things, skip training entirely. You can train whenever — it's fully reversible.

## After training

```powershell
# 1. Drop the trained model in the canonical spot
copy <wherever>.onnx D:\Kee\models\wakeword\kee.onnx

# 2. Optional — explicit override (not needed if you used the canonical path)
[Environment]::SetEnvironmentVariable("KEE_WAKE_WORD", "D:\Kee\models\wakeword\kee.onnx", "User")

# 3. Run the voice surface
.\.venv\Scripts\python.exe -m kee.main voice
```

You should see `Wake model loaded: D:\Kee\models\wakeword\kee.onnx (key=kee, threshold=0.50)` in the log on startup. Say "Kee, qué hora es?" and you're in business.

## Tuning the trigger

If the model misses you (false negatives), drop `WAKE_THRESHOLD` in `kee/perception/voice.py` from 0.5 to 0.4 or 0.3. If it triggers on noise (false positives), raise it to 0.6 or 0.7. Re-train with more or more-varied positive samples for the proper fix.
