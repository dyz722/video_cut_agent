# Perception: hearing -- 转写稿是视频的文本索引, agent 靠它定位 90% 的剪辑点.
"""
transcribe: DashScope fun-asr 语音转写, 句级时间戳。
    本地文件 -> ffmpeg 抽 16k 单声道 wav -> qwen3-asr-flash 识别
    http(s) URL -> fun-asr / qwen3-asr-flash-filetrans 录音文件识别 (异步任务)
产物:
    analysis/<name>.transcript.json   结构化 (秒级时间戳)
    analysis/<name>.transcript.txt    "[12.50-15.20] 文本" 行格式, 方便 grep
"""

import json
import subprocess
from pathlib import Path

from agent import config


def _extract_audio(video_path, wav_path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1",
         "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path)],
        capture_output=True, text=True, timeout=1800, check=True)


def _sentences_from_transcription_json(data: dict) -> list:
    sentences = []
    for tr in data.get("transcripts", []):
        for s in tr.get("sentences", []):
            text = (s.get("text") or "").strip()
            if not text:
                continue
            sentences.append({
                "start": round(s.get("begin_time", 0) / 1000, 2),
                "end": round(s.get("end_time", 0) / 1000, 2),
                "text": text,
            })
    return sentences


def _extract_asr_text(response) -> str:
    output = getattr(response, "output", None)
    if isinstance(output, dict):
        choices = output.get("choices") or []
    else:
        choices = getattr(output, "choices", []) if output is not None else []
    parts = []
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or ""))
                else:
                    parts.append(str(getattr(item, "text", "") or getattr(item, "content", "")))
    return "".join(p for p in parts if p).strip()


def _audio_duration_seconds(path: Path) -> float:
    from perception.probe import media_duration
    try:
        return media_duration(path)
    except Exception:
        return 0.0


def _recognize_local(wav_path) -> list:
    """qwen3-asr-flash 识别本地 wav, 返回 [{start,end,text}] (秒)。"""
    import dashscope

    config.apply_dashscope_config()
    response = dashscope.MultiModalConversation.call(
        api_key=config.dashscope_api_key(),
        model=config.asr_local_model(),
        messages=[{"role": "user", "content": [{"audio": f"file://{Path(wav_path).resolve()}"}]}],
        result_format="message",
        asr_options={"enable_itn": False},
    )
    if getattr(response, "status_code", 200) != 200:
        raise RuntimeError(f"ASR failed: {response.status_code} {getattr(response, 'message', '')}")
    text = _extract_asr_text(response)
    if not text:
        raise RuntimeError(f"ASR returned empty text: {response}")
    return [{"start": 0.0, "end": round(_audio_duration_seconds(Path(wav_path)), 2), "text": text}]


def _recognize_url(url: str) -> list:
    """fun-asr / qwen3-asr-flash-filetrans 录音文件识别 (公网 URL, 异步任务)。"""
    import dashscope
    import requests

    config.apply_dashscope_config()
    model = config.asr_file_model()
    if model.startswith("qwen3-asr"):
        try:
            from dashscope.audio.qwen_asr import QwenTranscription
        except Exception as e:
            raise RuntimeError(f"dashscope qwen_asr SDK unavailable: {e}")
        task = QwenTranscription.async_call(
            model=model, file_url=url, enable_itn=False, enable_words=False)
        result = QwenTranscription.wait(task=task.output.task_id)
        if result.status_code != 200:
            raise RuntimeError(f"ASR failed: {result.status_code} {result.message}")
        output = getattr(result, "output", None)
        task_result = output.get("result") if isinstance(output, dict) else getattr(output, "result", None)
        if not task_result:
            task_result = getattr(result, "result", None)
        transcription_url = (task_result.get("transcription_url") if isinstance(task_result, dict)
                             else getattr(task_result, "transcription_url", None))
        if not transcription_url:
            raise RuntimeError(f"ASR result missing transcription_url: {result}")
        data = requests.get(transcription_url, timeout=60).json()
        return _sentences_from_transcription_json(data)

    from dashscope.audio.asr import Transcription
    task = Transcription.async_call(
        model=model, file_urls=[url], language_hints=["zh", "en"])
    result = Transcription.wait(task=task.output.task_id)
    if result.status_code != 200:
        raise RuntimeError(f"ASR failed: {result.status_code} {result.message}")
    sentences = []
    for item in result.output.get("results", []):
        if item.get("subtask_status") != "SUCCEEDED":
            raise RuntimeError(f"ASR subtask failed: {item}")
        data = requests.get(item["transcription_url"], timeout=60).json()
        sentences.extend(_sentences_from_transcription_json(data))
    return sentences


def transcribe(path: str) -> str:
    if path.startswith(("http://", "https://")):
        name = path.rstrip("/").split("/")[-1].split(".")[0] or "remote"
        sentences = _recognize_url(path)
    else:
        fp = config.safe_path(path)
        if not fp.exists():
            return f"Error: file not found: {path}"
        name = fp.stem
        wav = config.PROJECT_DIR / ".cache" / f"{name}.16k.wav"
        wav.parent.mkdir(exist_ok=True)
        try:
            _extract_audio(fp, wav)
            sentences = _recognize_local(wav)
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"
        finally:
            wav.unlink(missing_ok=True)

    analysis = config.PROJECT_DIR / "analysis"
    json_path = analysis / f"{name}.transcript.json"
    txt_path = analysis / f"{name}.transcript.txt"
    analysis.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(
        {"source": path, "sentence_count": len(sentences), "sentences": sentences},
        ensure_ascii=False, indent=1))
    txt_path.write_text("\n".join(
        f"[{s['start']:.2f}-{s['end']:.2f}] {s['text']}" for s in sentences))

    preview = "\n".join(f"[{s['start']:.2f}-{s['end']:.2f}] {s['text']}"
                        for s in sentences[:10])
    total = sentences[-1]["end"] if sentences else 0
    return (f"Transcribed {path}: {len(sentences)} sentences, ~{total:.0f}s speech.\n"
            f"Saved: analysis/{json_path.name}, analysis/{txt_path.name} (grep-friendly)\n"
            f"Preview (first 10):\n{preview}")
