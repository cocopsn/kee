# Train "Kee" wake-word on Google Colab — actually-works guide (2026)

The earlier draft pointed at `requirements-training.txt` and `train_simple.ipynb` — those don't exist in the current openWakeWord repo. Here's the path that actually works.

**Total time:** ~30-50 min wall clock on a free Colab T4.

**Output:** `kee.onnx` (~50 KB). Drop it at `D:\Kee\models\wakeword\kee.onnx` and Kee auto-detects (no code change).

> **Important — your 50 recordings are NOT used by the auto-pipeline.** openWakeWord's automatic training generates synthetic positives via TTS (piper-sample-generator) covering many voices and prosodies — usually higher-quality than 50 hand-recorded clips. Your `kee_001.wav … kee_050.wav` are kept around as your personal validation set in case you want to test the trained model later (or do refinement training).

## Step 1 — Open the official notebook in Colab

One click:

> **https://colab.research.google.com/github/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb**

Then: **Runtime → Change runtime type → T4 GPU → Save**.

## Step 2 — Run cells 1-12 unchanged

These are environment setup, dataset downloads (MIT RIRs, AudioSet sample, Free Music Archive sample, pre-computed ACAV100M features). They take ~10 min total. Just hit **Run** on each, top to bottom.

## Step 3 — In cell 14, change two lines

The cell starts with `# Modify values in the config and save a new version`. Replace its content with:

```python
config["target_phrase"] = ["kee", "ki", "key"]
config["model_name"] = "kee"
config["n_samples"] = 2000
config["n_samples_val"] = 500
config["steps"] = 10000
config["target_accuracy"] = 0.65
config["target_recall"] = 0.30

config["background_paths"] = ['./audioset_16k', './fma']
config["false_positive_validation_data_path"] = "validation_set_features.npy"
config["feature_data_files"] = {
    "ACAV100M_sample": "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"
}

with open('my_model.yaml', 'w') as f:
    yaml.dump(config, f)
print("Wrote my_model.yaml. target_phrase =", config["target_phrase"])
```

Why three phonetic variants (`kee`, `ki`, `key`): the synthetic TTS pronounces each slightly differently, giving the model a wider acoustic target. Improves recall without hurting precision much.

## Step 4 — Run cells 17, 18, 19 (training itself)

These are three sequential `train.py` invocations. Total ~20-30 min on T4:

- Cell 17: `--generate_clips` (~10 min)
- Cell 18: `--augment_clips` (~5 min)
- Cell 19: `--train_model` (~10 min)

You'll see live training metrics. Stop conditions: `target_accuracy=0.65` and `target_recall=0.30` — early stops once both are hit. If training plateaus below those thresholds, raise `steps` to 20000 or lower the targets.

## Step 5 — Download the trained model

Run this cell at the end:

```python
import glob
candidates = glob.glob("./my_custom_model/**/*.onnx", recursive=True) \
           + glob.glob("./*.onnx", recursive=True) \
           + glob.glob("./openwakeword/**/*.onnx", recursive=True)
candidates = [c for c in candidates if "kee" in c.lower() or "my_model" in c.lower()]
print("Candidates:", candidates)

if candidates:
    from google.colab import files
    files.download(candidates[0])
else:
    print("Nothing matched. Run: !find / -name '*.onnx' 2>/dev/null | grep -v site-packages")
```

## Step 6 — Drop into Kee on your Windows box

```powershell
Move-Item -Path "$env:USERPROFILE\Downloads\kee.onnx" `
          -Destination "D:\Kee\models\wakeword\kee.onnx" -Force
.\.venv\Scripts\python.exe -m kee.main voice
```

You should see:

```
Wake model loaded: D:\Kee\models\wakeword\kee.onnx (key=kee, threshold=0.50)
```

Say "Kee" — should trigger reliably.

## Tuning

| Symptom | Fix |
|---------|-----|
| Doesn't trigger when you say "Kee" | Edit `kee/perception/voice.py` → `WAKE_THRESHOLD = 0.5` to `0.4` or `0.35`. |
| Triggers on background talk / TV | Raise `WAKE_THRESHOLD` to `0.6` or `0.7`. |
| Both — bad model | Re-train with `n_samples=5000` and `steps=20000`. |
| Triggers on the word "qué" or "que" | Add "que" to `target_phrase` is the WRONG fix; lower threshold then re-train with longer recordings (1.8s instead of 1.5s). |

## If the notebook breaks

The openWakeWord notebook can drift. If a cell fails:

1. **`piper-sample-generator` install fails** — happens occasionally because it needs Linux build tools. Run `!apt-get install -y build-essential espeak-ng` first.
2. **`onnx_tf` errors** — that's the optional tflite conversion. Skip cell 20; the .onnx is what Kee needs.
3. **AudioSet download 404** — pick a different shard: change `bal_train09.tar` in cell 9 to `bal_train01.tar` or any other `bal_train??.tar`.
4. **Out of disk on free Colab** — drop `n_hours = 1` to `n_hours = 0.25` in cell 9.

## Cost

Free Colab tier — $0. Burns about 25-45 GPU-minutes of your free quota.
