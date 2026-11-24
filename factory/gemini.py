"""Gemini API (REST): script writing with Google Search grounding, and image generation.

Model names change often, so "auto" picks the newest available model of each kind.
Override with config.json -> gemini.text_model / gemini.image_model.
"""
import base64
import functools
import json
import os
import re
import urllib.error
import urllib.request

from pydantic import ValidationError

BASE = "https://generativelanguage.googleapis.com/v1beta"
DISABLED = {}  # set after a quota error so one run does not keep hitting the API
CONFIG = {"text_model": "auto", "image_model": "auto"}


def configure(cfg):
    CONFIG.update(cfg.get("gemini", {}))


def _req(path, body=None, timeout=300):
    if "reason" in DISABLED:
        raise RuntimeError(f"Gemini skipped for this run: {DISABLED['reason']}")
    req = urllib.request.Request(
        f"{BASE}/{path}", data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            msg = json.loads(raw)["error"]["message"]
        except Exception:
            msg = raw[:1500]
        if e.code == 429 and "quota" in msg.lower():
            DISABLED["reason"] = f"quota exceeded ({msg[:200]})"
        raise RuntimeError(f"Gemini API {e.code}: {msg}") from None


@functools.lru_cache(None)
def _models():
    out, tok = [], ""
    while True:
        r = _req("models?pageSize=1000" + (f"&pageToken={tok}" if tok else ""))
        out += r.get("models", [])
        tok = r.get("nextPageToken")
        if not tok:
            return out


def _version(name):
    m = re.match(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


@functools.lru_cache(None)
def pick(kind):
    """Newest 'flash' text model, or newest 'flash' image-generation model."""
    want = CONFIG.get(f"{kind}_model", "auto")
    if want != "auto":
        return want
    skip = ("tts", "live", "audio", "embedding", "lite", "thinking", "computer", "robotics", "exp", "learnlm")
    cands = []
    for m in _models():
        n = m["name"].split("/")[-1]
        if not n.startswith("gemini-") or "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        if any(s in n for s in skip):
            continue
        if kind == "image" and "image" not in n:
            continue
        if kind == "text" and ("image" in n or "flash" not in n):
            continue
        # images: prefer the cheaper "flash" image models over "pro"
        cheap = "flash" in n if kind == "image" else True
        cands.append(((cheap, _version(n), "preview" not in n, -len(n)), n))
    if not cands:
        raise RuntimeError(f"no Gemini {kind} model available")
    name = max(cands)[1]
    print(f"  [gemini] {kind} model: {name}")
    return name


def _generate(model, body, timeout=300):
    return _req(f"models/{model}:generateContent", body, timeout)


def _text(res):
    cand = res["candidates"][0]
    return "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))


def write_script(prompt, format_prompt, schema_cls):
    model = pick("text")
    # 1) research + draft with Google Search grounding
    res = _generate(model, {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                            "tools": [{"google_search": {}}]})
    draft = _text(res)
    chunks = res["candidates"][0].get("groundingMetadata", {}).get("groundingChunks", [])
    refs = [f"- {c['web'].get('title', '')} {c['web']['uri']}" for c in chunks if c.get("web")]
    if refs:
        draft += "\n\n# 検索で参照したページ\n" + "\n".join(refs)

    # 2) convert to JSON matching the schema
    schema = json.dumps(schema_cls.model_json_schema(), ensure_ascii=False)
    ask = (format_prompt.format(draft=draft) +
           "\n\nすべての項目を必ず含めた JSON オブジェクト1つだけを出力してください。\n# JSON Schema\n" + schema)
    contents = [{"role": "user", "parts": [{"text": ask}]}]
    for _ in range(3):
        res = _generate(model, {"contents": contents,
                                "generationConfig": {"responseMimeType": "application/json"}})
        out = _text(res)
        try:
            return schema_cls.model_validate_json(out)
        except ValidationError as e:
            contents += [{"role": "model", "parts": [{"text": out}]},
                         {"role": "user", "parts": [{"text": f"スキーマに合いません。修正して JSON 全体を出し直してください:\n{e}"}]}]
    raise RuntimeError("Gemini did not return valid JSON")


def generate_image(prompt, aspect="9:16"):
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect}}}
    res = _generate(pick("image"), body, timeout=180)
    for p in res["candidates"][0]["content"]["parts"]:
        if "inlineData" in p:
            return base64.b64decode(p["inlineData"]["data"])
    raise RuntimeError("no image in Gemini response")
