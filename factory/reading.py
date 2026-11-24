"""Catch TTS misreadings before rendering.

1. Ask VOICEVOX how it will read each line (AudioQuery "kana").
2. Claude compares text vs. reading and lists misread words.
3. Context-free fixes (朝→アサ) go into the VOICEVOX user dictionary and are saved to
   dictionary.json for every future video; context-dependent ones (一日→ついたち) are
   replaced with hiragana in that line only.
4. The fixed lines are read back and checked once more.

Run directly to test:  python -m factory.reading "朝が2回しか来ない" "輸出用はほぼこれ一択です"
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
DICT_FILE = ROOT / "dictionary.json"
MODEL = "claude-opus-5-5"
KATAKANA = re.compile(r"^[ァ-ヴー]+$")


class Fix(BaseModel):
    surface: str
    reading: str
    accent_type: int
    always: bool


class Review(BaseModel):
    fixes: List[Fix]


PROMPT = """以下は、日本語の文と、音声合成エンジン（VOICEVOX）が実際に読み上げる読みの組です。
読みは AquesTalk 記法のカタカナです（' はアクセント核、/ と 、 は区切り、_ は無声化、? は疑問）。

読み間違いをすべて見つけてください。例:
- 朝が → チョウガ（正: アサ）、一択 → イチヨウ（正: イッタク）
- 数字＋助数詞の誤読（3匹 → サンヒキ（正: サンビキ））、桁の読み違い
- 固有名詞・外来語・アルファベット略語の誤読、送り仮名の誤読

各誤読について:
- surface: 誤読された語を文中の表記どおりに。できるだけ短い単語単位で。
- reading: その surface 全体の正しい読み（全角カタカナのみ、記号なし）
- accent_type: アクセント核の位置（0=平板、1=頭高、2=2拍目の後で下がる…）
- always: どんな文脈でもこの読みなら true。文脈で読みが変わる語（一日、今日、上手、
  人気、大事 など）は false

アクセントの違いだけのもの、自然な揺れ（「〜という」→トユウ 等）、記法上の違いは誤読に含めない。
誤読がなければ fixes は空配列にしてください。

{pairs}"""


def _hira(kata):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in kata)


class Engine:
    def __init__(self, url, speaker):
        self.base, self.spk = url.rstrip("/"), speaker

    def _call(self, path, params=None, body=None, method="POST"):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        raw = urllib.request.urlopen(req, timeout=60).read()
        return json.loads(raw) if raw else None

    def alive(self):
        try:
            self._call("/version", method="GET")
            return True
        except Exception:
            return False

    def kana(self, text):
        return self._call("/audio_query", {"text": text, "speaker": self.spk})["kana"]

    def words(self):
        return self._call("/user_dict", method="GET") or {}

    def add(self, surface, reading, accent):
        for uid, w in self.words().items():  # replace an older entry for the same surface
            if w.get("surface") in (surface, _zenkaku(surface)):
                self._call(f"/user_dict_word/{uid}", method="DELETE")
        self._call("/user_dict_word", {"surface": surface, "pronunciation": reading,
                                        "accent_type": accent, "word_type": "COMMON_NOUN", "priority": 8})


def _zenkaku(s):
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in s)


def load_dictionary():
    return json.loads(DICT_FILE.read_text(encoding="utf-8")) if DICT_FILE.exists() else {}


def _review(pairs):
    import anthropic
    client = anthropic.Anthropic()
    listing = "\n".join(f"{i + 1}. 文: {t}\n   読み: {k}" for i, (t, k) in enumerate(pairs))
    res = client.messages.parse(
        model=MODEL, max_tokens=8000, output_format=Review, output_config={"effort": "medium"},
        messages=[{"role": "user", "content": PROMPT.format(pairs=listing)}])
    if res.stop_reason == "refusal" or res.parsed_output is None:
        return []
    return [f for f in res.parsed_output.fixes
            if f.surface and KATAKANA.match(f.reading) and 0 <= f.accent_type <= len(f.reading)]


def prepare(lines, cfg):
    """Return {line: text_to_speak}. Only lines that need a change are included."""
    if cfg["tts"]["backend"] != "voicevox":
        return {}
    vc = cfg["tts"]["voicevox"]
    eng = Engine(vc["url"], vc.get("speaker", 3))
    if not eng.alive():
        return {}

    # saved dictionary -> engine
    saved = load_dictionary()
    have = {w.get("surface") for w in eng.words().values()}
    for surface, w in saved.items():
        if surface not in have and _zenkaku(surface) not in have:
            try:
                eng.add(surface, w["reading"], w["accent_type"])
            except Exception as e:
                print(f"  [reading] could not load {surface}: {e}")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}
    lines = list(dict.fromkeys(l for l in lines if l.strip()))
    out = {l: l for l in lines}
    learned = 0
    for round_ in range(2):
        pairs = [(out[l], eng.kana(out[l])) for l in lines]
        fixes = _review(pairs)
        if not fixes:
            break
        for f in fixes:
            print(f"  [reading] {f.surface} → {f.reading}" + (" (辞書に登録)" if f.always else " (この文だけ)"))
            if f.always and round_ == 0:
                try:
                    eng.add(f.surface, f.reading, f.accent_type)
                    saved[f.surface] = {"reading": f.reading, "accent_type": f.accent_type}
                    learned += 1
                    continue
                except Exception as e:
                    print(f"  [reading] dictionary add failed ({e}); replacing in text instead")
            for l in lines:  # context-dependent, or still wrong after the dictionary fix
                if f.surface in out[l]:
                    out[l] = out[l].replace(f.surface, _hira(f.reading))
    if learned:
        DICT_FILE.write_text(json.dumps(dict(sorted(saved.items())), ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return {l: t for l, t in out.items() if t != l}


if __name__ == "__main__":
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    eng = Engine(cfg["tts"]["voicevox"]["url"], cfg["tts"]["voicevox"].get("speaker", 3))
    lines = sys.argv[1:]
    print("before:")
    for l in lines:
        print(f"  {l}\n    {eng.kana(l)}")
    changed = prepare(lines, cfg)
    print("after:")
    for l in lines:
        t = changed.get(l, l)
        print(f"  {t}\n    {eng.kana(t)}")
