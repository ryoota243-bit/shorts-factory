"""Text-to-speech backends. All return float32 mono audio at SR."""
import base64
import io
import json
import os
import urllib.parse
import urllib.request
import wave

import numpy as np

SR = 48000


def _resample(x, sr_in):
    if sr_in == SR:
        return x
    n = int(len(x) * SR / sr_in)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def _decode_wav(data):
    with wave.open(io.BytesIO(data)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return _resample(x, sr)


def _openjtalk(text, c):
    import pyopenjtalk
    x, sr = pyopenjtalk.tts(text, speed=c.get("speed", 1.0), half_tone=c.get("half_tone", 0.0))
    return _resample((x / 32768.0).astype(np.float32), sr)


def _voicevox(text, c):
    base, spk = c["url"].rstrip("/"), c.get("speaker", 3)
    q = urllib.parse.urlencode({"text": text, "speaker": spk})
    req = urllib.request.Request(f"{base}/audio_query?{q}", method="POST")
    query = json.loads(urllib.request.urlopen(req, timeout=60).read())
    query["speedScale"] = c.get("speed", 1.0)
    req = urllib.request.Request(
        f"{base}/synthesis?speaker={spk}", data=json.dumps(query).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return _decode_wav(urllib.request.urlopen(req, timeout=120).read())


def _google(text, c):
    key = os.environ["GOOGLE_TTS_API_KEY"]
    body = {
        "input": {"text": text},
        "voice": {"languageCode": "ja-JP", "name": c.get("voice", "ja-JP-Neural2-B")},
        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": SR,
                        "speakingRate": c.get("rate", 1.0)},
    }
    req = urllib.request.Request(
        f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    res = json.loads(urllib.request.urlopen(req, timeout=60).read())
    return _decode_wav(base64.b64decode(res["audioContent"]))


BACKENDS = {"openjtalk": _openjtalk, "voicevox": _voicevox, "google": _google}


USED = set()


def synth(text, cfg):
    name = cfg["tts"]["backend"]
    try:
        x = BACKENDS[name](text, cfg["tts"].get(name, {}))
    except Exception as e:
        if name == "openjtalk":
            raise
        print(f"  [tts] {name} failed ({e}); falling back to openjtalk")
        name = "openjtalk"
        x = _openjtalk(text, cfg["tts"].get(name, {}))
    USED.add(name)
    # trim leading/trailing near-silence
    idx = np.where(np.abs(x) > 0.01)[0]
    if len(idx):
        x = x[max(0, idx[0] - 480): idx[-1] + 2400]
    return x.astype(np.float32)
