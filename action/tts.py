# Action: voiceover -- cosyvoice-v3-flash 配音合成 (见 model-choose).
"""
synthesize: DashScope cosyvoice REST 接口, 输出 wav。
instruction 必须中文且按官方格式, 例:
    "你正在进行广告促销，你说话的情感是happy。"
"""

import base64
import os

import requests

from agent import config

TTS_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"


def synthesize(text: str, output: str, voice: str = "longanyang",
               instruction: str = None) -> str:
    if not text.strip():
        return "Error: empty text"
    out_fp = config.safe_path(output)
    out_fp.parent.mkdir(parents=True, exist_ok=True)

    payload = {"model": config.tts_model(),
               "input": {"text": text, "voice": voice,
                         "format": "wav", "sample_rate": 24000}}
    if instruction:
        payload["input"]["instruction"] = instruction

    r = requests.post(
        TTS_URL, json=payload, timeout=120,
        headers={"Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}",
                 "Content-Type": "application/json"})
    ctype = r.headers.get("Content-Type", "")
    if r.status_code != 200:
        return f"Error: TTS HTTP {r.status_code}: {r.text[:300]}"

    if "audio" in ctype or "octet-stream" in ctype:
        out_fp.write_bytes(r.content)
    else:
        data = r.json()
        out = data.get("output", {})
        audio = out.get("audio", {}) if isinstance(out.get("audio"), dict) else {}
        if audio.get("url"):
            out_fp.write_bytes(requests.get(audio["url"], timeout=120).content)
        elif audio.get("data"):
            out_fp.write_bytes(base64.b64decode(audio["data"]))
        else:
            return f"Error: unexpected TTS response: {str(data)[:300]}"

    from perception.probe import media_duration
    try:
        dur = media_duration(out_fp)
    except Exception:
        dur = 0
    return (f"TTS saved: {output} ({dur:.2f}s, voice={voice}). "
            f"在 timeline 的 audio.voiceover 里引用, 注意给字幕留同样的时间区间。")
