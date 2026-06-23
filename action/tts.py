# Action: voiceover -- cosyvoice-v3-flash 配音合成 (见 model-choose).
"""
synthesize: DashScope cosyvoice SDK(WebSocket) 优先, 输出 wav。
instruction 必须中文且按官方格式, 例:
    "你正在进行广告促销，你说话的情感是happy。"
"""

import base64

import requests

from agent import config


def _synthesize_sdk(text: str, out_fp, voice: str, instruction: str | None):
    from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

    config.apply_dashscope_config()
    synthesizer = SpeechSynthesizer(
        model=config.tts_model(),
        voice=voice,
        format=AudioFormat.WAV_24000HZ_MONO_16BIT,
        instruction=instruction,
    )
    # The SDK defaults to async callback mode. Force a blocking call that returns bytes.
    synthesizer.async_call = False
    audio = synthesizer.call(text, timeout_millis=120000)
    if not audio:
        raise RuntimeError("DashScope TTS SDK returned empty audio.")
    out_fp.write_bytes(audio)


def _synthesize_http(text: str, out_fp, voice: str, instruction: str | None):
    payload = {"model": config.tts_model(),
               "input": {"text": text, "voice": voice,
                         "format": "wav", "sample_rate": 24000}}
    if instruction:
        payload["input"]["instruction"] = instruction

    api_key, _ = config.apply_dashscope_config()
    r = requests.post(
        config.dashscope_tts_url(), json=payload, timeout=120,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"})
    ctype = r.headers.get("Content-Type", "")
    if r.status_code != 200:
        if "does not support http call" in r.text:
            return ("Error: TTS HTTP is not enabled for this DashScope account/endpoint. "
                    "The SDK WebSocket path also failed; run /dashscope to verify region/key, "
                    "or check whether the account has cosyvoice-v3-flash access. "
                    f"HTTP detail: {r.text[:300]}")
        return f"Error: TTS HTTP {r.status_code}: {r.text[:300]}"

    if "audio" in ctype or "octet-stream" in ctype:
        out_fp.write_bytes(r.content)
        return None
    data = r.json()
    out = data.get("output", {})
    audio = out.get("audio", {}) if isinstance(out.get("audio"), dict) else {}
    if audio.get("url"):
        out_fp.write_bytes(requests.get(audio["url"], timeout=120).content)
    elif audio.get("data"):
        out_fp.write_bytes(base64.b64decode(audio["data"]))
    else:
        return f"Error: unexpected TTS response: {str(data)[:300]}"
    return None


def synthesize(text: str, output: str, voice: str = "longanyang",
               instruction: str = None) -> str:
    if not text.strip():
        return "Error: empty text"
    out_fp = config.safe_path(output)
    out_fp.parent.mkdir(parents=True, exist_ok=True)

    sdk_error = None
    try:
        _synthesize_sdk(text, out_fp, voice, instruction)
    except Exception as e:
        sdk_error = f"{type(e).__name__}: {e}"
        try:
            http_error = _synthesize_http(text, out_fp, voice, instruction)
        except Exception as http_exc:
            return (f"Error: TTS failed via SDK and HTTP fallback. "
                    f"SDK detail: {sdk_error}; HTTP detail: "
                    f"{type(http_exc).__name__}: {http_exc}")
        if http_error:
            return f"{http_error}\nSDK detail: {sdk_error}"

    from perception.probe import media_duration
    try:
        dur = media_duration(out_fp)
    except Exception:
        dur = 0
    return (f"TTS saved: {output} ({dur:.2f}s, voice={voice}). "
            f"在 timeline 的 audio.voiceover 里引用, 注意给字幕留同样的时间区间。")
