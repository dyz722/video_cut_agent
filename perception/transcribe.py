# Perception: hearing -- 转写稿是视频的文本索引, agent 靠它定位 90% 的剪辑点.
"""
transcribe: DashScope fun-asr 语音转写, 句级时间戳。
    本地文件 -> ffmpeg 抽 16k 单声道 wav -> fun-asr-realtime 识别
    http(s) URL -> fun-asr 录音文件识别 (异步任务)
产物:
    analysis/<name>.transcript.json   结构化 (秒级时间戳)
    analysis/<name>.transcript.txt    "[12.50-15.20] 文本" 行格式, 方便 grep
"""

import json
import os
import subprocess

from agent import config


def _extract_audio(video_path, wav_path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1",
         "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path)],
        capture_output=True, text=True, timeout=1800, check=True)


def _recognize_local(wav_path) -> list:
    """fun-asr-realtime 识别本地 wav, 返回 [{start,end,text}] (秒)。"""
    import dashscope
    from dashscope.audio.asr import Recognition

    dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]
    rec = Recognition(model=config.asr_realtime_model(), format="wav",
                      sample_rate=16000, callback=None)
    result = rec.call(str(wav_path))
    if result.status_code != 200:
        raise RuntimeError(f"ASR failed: {result.status_code} {result.message}")
    sentences = []
    for s in (result.get_sentence() or []):
        if not s.get("text"):
            continue
        sentences.append({
            "start": round(s.get("begin_time", 0) / 1000, 2),
            "end": round(s.get("end_time", 0) / 1000, 2),
            "text": s["text"].strip(),
        })
    return sentences


def _recognize_url(url: str) -> list:
    """fun-asr 录音文件识别 (公网 URL, 异步任务)。"""
    import dashscope
    import requests
    from dashscope.audio.asr import Transcription

    dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]
    task = Transcription.async_call(model=config.asr_file_model(), file_urls=[url])
    result = Transcription.wait(task=task.output.task_id)
    if result.status_code != 200:
        raise RuntimeError(f"ASR failed: {result.status_code} {result.message}")
    sentences = []
    for item in result.output.get("results", []):
        if item.get("subtask_status") != "SUCCEEDED":
            raise RuntimeError(f"ASR subtask failed: {item}")
        data = requests.get(item["transcription_url"], timeout=60).json()
        for tr in data.get("transcripts", []):
            for s in tr.get("sentences", []):
                sentences.append({
                    "start": round(s.get("begin_time", 0) / 1000, 2),
                    "end": round(s.get("end_time", 0) / 1000, 2),
                    "text": s.get("text", "").strip(),
                })
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
