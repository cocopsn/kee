#!/usr/bin/env bash
# Master script for training the Kee wake word inside WSL2 Ubuntu.
# Runs end-to-end: apt deps → pip env → datasets → train → copy .onnx to D:/.
# Logs to /mnt/d/Kee/data/wake_train.log (readable from Windows).
#
# Idempotent: re-running skips steps already done. Safe to interrupt + resume.

set -euo pipefail

LOG=/mnt/d/Kee/data/wake_train.log
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1

step() { echo ""; echo "===== [$(date '+%H:%M:%S')] $* ====="; }

WORK=$HOME/oww-train
VENV=$WORK/.venv
# Datasets live on D:/ — they're 17 GB+ and we don't want them in the
# WSL ext4.vhdx forever. Slower I/O but bounded write-once.
DATA=/mnt/d/Kee/wsl-training-data
OUT_ONNX=/mnt/d/Kee/models/wakeword/kee.onnx
mkdir -p "$WORK" "$DATA"

# ── 1. apt deps ───────────────────────────────────────────────────────────
step "apt: build tools + ffmpeg + espeak-ng (one-time)"
if ! dpkg -s python3.12-venv >/dev/null 2>&1; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        build-essential python3.12-venv python3-pip \
        ffmpeg espeak-ng git wget curl ca-certificates \
        libsndfile1 libgomp1
fi

# ── 2. python 3.11 + venv (piper-phonemize has no cp312 wheel) ───────────
step "ensure uv + python 3.11"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
    export PATH="$HOME/.local/bin:$PATH"
fi
uv python install 3.11 >/dev/null 2>&1 || true
PY311=$(uv python find 3.11)
echo "  using $PY311"

step "python 3.11 venv at $VENV"
if [ ! -d "$VENV" ]; then
    "$PY311" -m venv "$VENV"
fi
source "$VENV/bin/activate"
pip install --upgrade pip wheel setuptools >/dev/null

# ── 3. clone openwakeword + piper-sample-generator ────────────────────────
step "clone repos"
cd "$WORK"
[ -d openwakeword ] || git clone --depth 1 https://github.com/dscripka/openwakeword
[ -d piper-sample-generator ] || git clone --depth 1 https://github.com/rhasspy/piper-sample-generator

# ── 4. pip install heavy deps ─────────────────────────────────────────────
step "pip install training deps (~3-5 min, ~3 GB)"
# Install openwakeword in editable mode to expose train.py
pip install -e "$WORK/openwakeword" >/dev/null
pip install \
    'pyarrow<14.0' \
    piper-phonemize \
    webrtcvad \
    mutagen==1.47.0 \
    torchinfo==1.8.0 \
    torchmetrics==1.2.0 \
    audiomentations==0.33.0 \
    torch-audiomentations==0.13.0 \
    acoustics==0.2.6 \
    pronouncing==0.2.0 \
    datasets==2.14.6 \
    deep-phonemizer==0.0.19 \
    \
    \
    onnx onnx_tf scipy >/dev/null || {
        echo "WARN: some pinned versions failed; retrying without strict pins"
        pip install \
            piper-phonemize webrtcvad mutagen torchinfo torchmetrics \
            audiomentations torch-audiomentations acoustics pronouncing \
            datasets deep-phonemizer \
            onnx onnx_tf scipy >/dev/null
    }

# Speechbrain is large; install separately so a failure is recoverable
pip install speechbrain==0.5.14 >/dev/null || pip install speechbrain >/dev/null

# ── 5. piper TTS sample model (used to synthesise positive examples) ─────
step "download piper TTS model for synthetic positives"
mkdir -p piper-sample-generator/models
PIPER_MODEL=piper-sample-generator/models/en_US-libritts_r-medium.pt
if [ ! -f "$PIPER_MODEL" ]; then
    wget -q -O "$PIPER_MODEL" \
      'https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/en_US-libritts_r-medium.pt'
fi

# ── 6. openwakeword bundled feature extractors ────────────────────────────
step "openwakeword feature extractors (~5 MB)"
RES=$WORK/openwakeword/openwakeword/resources/models
mkdir -p "$RES"
for f in embedding_model.onnx embedding_model.tflite melspectrogram.onnx melspectrogram.tflite; do
    [ -f "$RES/$f" ] || wget -q -O "$RES/$f" "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/$f"
done

# ── 7. background datasets ────────────────────────────────────────────────
cd "$DATA"

step "MIT RIRs (~700 MB if not cached)"
if [ ! -d "$DATA/mit_rirs" ] || [ -z "$(ls -A "$DATA/mit_rirs" 2>/dev/null)" ]; then
    KEE_DATA="$DATA" python - <<'PY'
import os, datasets, scipy
from tqdm import tqdm
data = os.environ["KEE_DATA"]
os.makedirs(f"{data}/mit_rirs", exist_ok=True)
ds = datasets.load_dataset(
    "davidscripka/MIT_environmental_impulse_responses",
    split="train", streaming=True,
)
for row in tqdm(ds):
    name = row["audio"]["path"].split("/")[-1]
    scipy.io.wavfile.write(
        os.path.join(f"{data}/mit_rirs", name),
        16000, (row["audio"]["array"] * 32767).astype("int16"),
    )
PY
fi

step "AudioSet — SKIPPED (HF dataset re-organised to parquet, 404 on legacy tar)"
# Background noise variety still comes from FMA below + MIT RIRs above.
# If you want richer noise augmentation later, point background_paths at
# any folder of 16 kHz mono WAVs (UrbanSound8K, ESC-50, DEMAND all work).
mkdir -p "$DATA/audioset_16k"

step "FMA samples (~250 MB, 1 hour of music)"
mkdir -p "$DATA/fma"
if [ -z "$(ls -A "$DATA/fma" 2>/dev/null)" ]; then
    KEE_DATA="$DATA" python - <<'PY'
import os, datasets, scipy
from tqdm import tqdm
data = os.environ["KEE_DATA"]
ds = iter(datasets.load_dataset(
    "rudraml/fma", name="small", split="train", streaming=True,
).cast_column("audio", datasets.Audio(sampling_rate=16000)))
for i in tqdm(range(120)):  # ~1 hour of 30s clips
    try:
        row = next(ds)
        name = row["audio"]["path"].split("/")[-1].replace(".mp3", ".wav")
        scipy.io.wavfile.write(
            os.path.join(f"{data}/fma", name),
            16000, (row["audio"]["array"] * 32767).astype("int16"),
        )
    except StopIteration:
        break
PY
fi

step "openwakeword pre-computed features (~17 GB ACAV100M + ~50 MB validation)"
# wget -c resumes partial downloads; HF tends to throttle long pulls.
# Verify final size matches Content-Length so a half-broken file doesn't
# pass through to training.
ACAV_URL='https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/openwakeword_features_ACAV100M_2000_hrs_16bit.npy'
ACAV_PATH="$DATA/openwakeword_features_ACAV100M_2000_hrs_16bit.npy"
EXPECTED_ACAV_BYTES=17280000128

current=$(stat -c%s "$ACAV_PATH" 2>/dev/null || echo 0)
if [ "$current" -lt "$EXPECTED_ACAV_BYTES" ]; then
    while [ "$current" -lt "$EXPECTED_ACAV_BYTES" ]; do
        echo "  ACAV100M: $current / $EXPECTED_ACAV_BYTES bytes — resuming…"
        wget -c -q --tries=3 --timeout=60 -O "$ACAV_PATH" "$ACAV_URL" || true
        new=$(stat -c%s "$ACAV_PATH" 2>/dev/null || echo 0)
        if [ "$new" -le "$current" ]; then
            echo "  ACAV100M: stuck at $new — giving up after this attempt"
            break
        fi
        current=$new
    done
fi

[ -f "$DATA/validation_set_features.npy" ] || wget -q --tries=3 -O "$DATA/validation_set_features.npy" \
    'https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/validation_set_features.npy'

# ── 8. config ────────────────────────────────────────────────────────────
step "write training config (target_phrase=kee/ki/key)"
CONFIG="$WORK/kee_config.yaml"
TEMPLATE="$WORK/openwakeword/examples/custom_model.yml"
KEE_TEMPLATE="$TEMPLATE" KEE_OUT="$CONFIG" KEE_DATA="$DATA" KEE_WORK="$WORK" \
    python - <<'PY'
import os, yaml
template = os.environ["KEE_TEMPLATE"]
out = os.environ["KEE_OUT"]
data = os.environ["KEE_DATA"]
work = os.environ["KEE_WORK"]
cfg = yaml.safe_load(open(template))
cfg["target_phrase"] = ["kee", "ki", "key"]
cfg["model_name"] = "kee"
cfg["n_samples"] = 2000
cfg["n_samples_val"] = 500
cfg["steps"] = 7000
cfg["target_accuracy"] = 0.50
cfg["target_recall"] = 0.20
cfg["background_paths"] = [
    os.path.join(data, "fma"),
]
cfg["rir_paths"] = [os.path.join(data, "mit_rirs")]
cfg["false_positive_validation_data_path"] = os.path.join(data, "validation_set_features.npy")
cfg["feature_data_files"] = {
    "ACAV100M_sample": os.path.join(data, "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"),
}
cfg["piper_sample_generator_path"] = os.path.join(work, "piper-sample-generator")
cfg["output_dir"] = os.path.join(work, "kee_model")
os.makedirs(cfg["output_dir"], exist_ok=True)
yaml.safe_dump(cfg, open(out, "w"))
print(f"wrote {out}")
print("target_phrase:", cfg["target_phrase"])
PY

# ── 9. training: 3 steps ─────────────────────────────────────────────────
cd "$WORK"

step "STEP 1/3: generate synthetic clips (~10 min)"
python -m openwakeword.train --training_config "$CONFIG" --generate_clips

step "STEP 2/3: augment clips (~5 min)"
python -m openwakeword.train --training_config "$CONFIG" --augment_clips

step "STEP 3/3: train model (~10-20 min)"
python -m openwakeword.train --training_config "$CONFIG" --train_model

# ── 10. copy result back to Windows ──────────────────────────────────────
step "copy trained .onnx to Windows"
ONNX=$(find "$WORK/kee_model" -name "*.onnx" | head -1)
if [ -z "$ONNX" ]; then
    echo "ERROR: no .onnx produced. Inspect $WORK/kee_model and the log above."
    exit 1
fi
mkdir -p "$(dirname "$OUT_ONNX")"
cp -v "$ONNX" "$OUT_ONNX"

step "DONE — kee.onnx ready at D:\\Kee\\models\\wakeword\\kee.onnx"
ls -lh "$OUT_ONNX"
echo ""
echo "On Windows, run:"
echo "  .\\.venv\\Scripts\\python.exe -m kee.main voice"
echo "Should log: Wake model loaded: D:\\Kee\\models\\wakeword\\kee.onnx (key=kee, threshold=0.50)"
