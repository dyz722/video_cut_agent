#!/usr/bin/env python3
"""
离线 smoke test (不需要任何 API key):
  1. 全模块导入
  2. ffmpeg 生成合成素材 (彩条+扫频音 / 渐变+正弦音 / BGM / banner)
  3. timeline 校验 (合法 + 各类非法)
  4. 渲染器全功能端到端: 剪切/变速/转场/字幕动效/横幅/画中画/BGM闪避
  5. qc_check 检查成片

用法: python tests/test_smoke.py
"""

import json
import http.client
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def sh(cmd):
    subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)


def main():
    print("[1] imports")
    from agent import config
    from agent.todo import TodoManager
    from agent.skills import SkillLoader
    from agent.experience import record_experience
    from agent.model_client import (
        ToolUseBlock,
        anthropic_messages_to_openai,
        anthropic_tools_to_openai,
    )
    import agent.loop  # noqa
    import agent.subagent  # noqa
    from agent.events import EVENTS
    from agent import log_store
    from agent.log_view import open_log_view
    from agent import session as session_store
    from agent.events import RunEvent, format_event
    import main as cli
    from agent.tools import TOOLS, TOOL_HANDLERS
    import perception.probe, perception.scenes, perception.transcribe, perception.watch  # noqa
    from action import timeline as tl_mod
    from action.review import review_timeline, review_render, summarize_review_feedback
    from action.render import render_file, render_request
    from action.qc import qc_check
    from action.ass_effects import build_ass
    check("all modules import", True)
    check("tool schema/handler 一一对应",
          {t["name"] for t in TOOLS} == set(TOOL_HANDLERS.keys()),
          str({t["name"] for t in TOOLS} ^ set(TOOL_HANDLERS.keys())))
    watch_schema = next(t for t in TOOLS if t["name"] == "watch_video")["input_schema"]
    check("watch_video supports visual mode selection",
          "mode" in watch_schema["properties"]
          and set(watch_schema["properties"]["mode"]["enum"]) == {"auto", "video", "frames"})
    check("watch_video accepts timestamp strings",
          "anyOf" in watch_schema["properties"]["start"]
          and "anyOf" in watch_schema["properties"]["end"])
    check("watch_video parses flexible timestamps",
          perception.watch.seconds("9.5s") == 9.5
          and perception.watch.seconds("00:01:02.5") == 62.5
          and perception.watch.seconds("3秒") == 3.0)
    pyproject = (ROOT / "pyproject.toml").read_text()
    check("veoai CLI entry registered", 'veoai = "main:main"' in pyproject)
    check("legacy CLI aliases removed",
          'video-agent = "main:main"' not in pyproject
          and 'video-cut-agent = "main:main"' not in pyproject)
    openai_tools = anthropic_tools_to_openai([{
        "name": "demo_tool",
        "description": "demo",
        "input_schema": {"type": "object", "properties": {"x": {"type": "string"}},
                         "required": ["x"]},
    }])
    check("OpenAI-compatible tool schema conversion",
          openai_tools[0]["function"]["name"] == "demo_tool"
          and openai_tools[0]["function"]["parameters"]["required"] == ["x"])
    openai_msgs = anthropic_messages_to_openai([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            ToolUseBlock(type="tool_use", id="call_1", name="demo_tool", input={"x": "1"}),
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "ok"},
        ]},
    ], system="sys")
    check("OpenAI-compatible message conversion",
          openai_msgs[0]["role"] == "system"
          and openai_msgs[2]["tool_calls"][0]["function"]["name"] == "demo_tool"
          and openai_msgs[3]["role"] == "tool")
    old_dash_env = {k: os.environ.get(k) for k in (
        "DASHSCOPE_REGION", "DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY_CN",
        "DASHSCOPE_API_KEY_INTL", "DASHSCOPE_BASE_URL_CN", "DASHSCOPE_BASE_URL_INTL")}
    try:
        os.environ["DASHSCOPE_REGION"] = "intl"
        os.environ["DASHSCOPE_API_KEY_INTL"] = "intl-key"
        os.environ["DASHSCOPE_BASE_URL_INTL"] = "https://dashscope-intl.example/api/v1"
        check("DashScope intl key selected", config.dashscope_api_key() == "intl-key")
        check("DashScope intl base URL selected",
              config.dashscope_base_url() == "https://dashscope-intl.example/api/v1")
        check("DashScope TTS URL derived",
              config.dashscope_tts_url().endswith("/services/audio/tts/SpeechSynthesizer"))
    finally:
        for k, v in old_dash_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("veoai update dry-run", cli.main(["update", "--dry-run"]) == 0)
    splash = cli.welcome_screen()
    check("welcome screen renders", "Welcome back!" in splash and "Shortcuts" in splash)
    err = cli.format_cli_error(RuntimeError(
        'OpenAI-compatible API error 502: {"error":{"message":"Upstream access forbidden"}}'))
    check("model API error is user friendly", "/model" in err and "没有退出" in err)
    check("status context usable", hasattr(agent.loop, "status"))
    old_project = config.PROJECT_DIR
    event_proj = Path(tempfile.mkdtemp(prefix="veoai_events_test_"))
    try:
        config.set_project(str(event_proj))
        log_store.clear_jsonl(log_store.EVENT_LOG)
        log_store.clear_jsonl(log_store.TOOL_LOG)
        EVENTS.clear()
        EVENTS.emit("tool", "probe materials/a.mp4", name="probe_media", print_event=False)
        check("live events render", "probe materials/a.mp4" in EVENTS.render())
        old_no_color = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"
        try:
            plan_line = format_event(RunEvent(1, "12:00:00", "plan", "检查素材并确认用途"))
            tool_line = format_event(RunEvent(2, "12:00:01", "tool", "probe materials/a.mp4",
                                              name="probe_media"))
            check("terminal events use readable labels",
                  "计划 检查素材" in plan_line
                  and "工具 probe_media probe materials/a.mp4" in tool_line)
        finally:
            if old_no_color is None:
                os.environ.pop("NO_COLOR", None)
            else:
                os.environ["NO_COLOR"] = old_no_color
        check("run events persisted",
              log_store.read_jsonl(log_store.EVENT_LOG)[-1]["summary"] == "probe materials/a.mp4")
        agent.loop.clear_tool_logs()
        agent.loop.record_tool_log("bash", {"command": "ls"}, "a\nb\n")
        check("tool logs persisted",
              log_store.read_jsonl(log_store.TOOL_LOG)[-1]["name"] == "bash")
        log_view = open_log_view(open_browser=False)
        check("web log viewer starts", "Log viewer ready:" in log_view)
        log_url = log_view.splitlines()[0].replace("Log viewer ready: ", "").strip()
        parsed = urllib.parse.urlparse(log_url)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        conn.request("GET", "/")
        page = conn.getresponse().read().decode("utf-8")
        conn.request("GET", "/api/events")
        api = conn.getresponse().read().decode("utf-8")
        conn.close()
        check("web log viewer serves html/api", "veoai logs" in page and "probe materials" in api)
        check("run status idle", "No agent run" in EVENTS.status_text())
    finally:
        config.PROJECT_DIR = old_project
        shutil.rmtree(event_proj, ignore_errors=True)
    with cli.esc_interrupt_monitor():
        pass
    check("esc interrupt monitor usable", True)
    tool_proj = Path(tempfile.mkdtemp(prefix="veoai_tool_logs_test_"))
    try:
        config.set_project(str(tool_proj))
        agent.loop.clear_tool_logs()
        log_entry = agent.loop.record_tool_log("bash", {"command": "ls"}, "a\nb\n")
        check("tool log records summary", "bash completed" in log_entry["summary"])
        check("tool logs render summary", "bash:" in agent.loop.render_tool_logs())
        check("tool logs render full", "output:" in agent.loop.render_tool_logs(full=True))
        agent.loop.set_verbose_tools(True)
        check("verbose toggle on", agent.loop.VERBOSE_TOOLS is True)
        agent.loop.set_verbose_tools(False)
        check("verbose toggle off", agent.loop.VERBOSE_TOOLS is False)
        agent.loop.clear_tool_logs()
        check("tool logs clear", "No tool logs" in agent.loop.render_tool_logs())
    finally:
        config.PROJECT_DIR = old_project
        shutil.rmtree(tool_proj, ignore_errors=True)
    check("slash command completion /m", cli.complete_slash_command("/m", 0) == "/model")
    slash_matches = []
    i = 0
    while True:
        item = cli.complete_slash_command("/", i)
        if item is None:
            break
        slash_matches.append(item)
        i += 1
    check("slash command completion lists commands",
          "/model" in slash_matches and "/dashscope" in slash_matches
          and "/logview" in slash_matches and "/live" in slash_matches and "/status" in slash_matches
          and "/stop" in slash_matches and "/quit" in slash_matches)
    check("prompt status includes project",
          str(cli.config.PROJECT_DIR) in cli.prompt_status())
    check("prompt session optional",
          cli.create_prompt_session() is not None or cli.setup_readline_completion())
    old_user_data_dir = cli.config.USER_DATA_DIR
    hist_root = Path(tempfile.mkdtemp(prefix="veoai_history_test_"))
    try:
        cli.config.USER_DATA_DIR = hist_root
        history_supported = cli.setup_readline_completion()
        if history_supported:
            check("repl history stores prompts", cli.add_repl_history("剪一条带货视频"))
            check("repl history file written",
                  "剪一条带货视频" in (hist_root / "history").read_text())
        else:
            check("repl history gracefully optional", True)
    finally:
        cli.config.USER_DATA_DIR = old_user_data_dir
        shutil.rmtree(hist_root, ignore_errors=True)

    print("[2] skills")
    sk = SkillLoader(config.SKILLS_DIRS)
    for name in ("timeline-format", "visual-review-protocol",
                 "ecommerce-clip", "manju-compilation"):
        check(f"skill {name}", name in sk.skills)
        check(f"skill {name} loadable", sk.load(name).startswith("<skill"))
    learned_dir = config.LEARNED_SKILLS_DIR / "learned-smoke-test"
    shutil.rmtree(learned_dir, ignore_errors=True)
    rec = record_experience("smoke-test", "Prefer a strong 3-second hook.", "accepted")
    sk.reload()
    check("record_experience creates learned skill", "learned-smoke-test" in sk.skills, rec)
    check("learned skill loadable", sk.load("learned-smoke-test").startswith("<skill"))
    shutil.rmtree(learned_dir, ignore_errors=True)
    sk.reload()

    print("[3] synthetic materials")
    shutil.rmtree(config.WORKSPACE_ROOT / "_smoke", ignore_errors=True)
    proj = config.set_project("_smoke")
    mats = proj / "materials"
    external_dir = Path(tempfile.mkdtemp(prefix="video_agent_smoke_external_"))
    sh(f'ffmpeg -y -f lavfi -i "testsrc2=duration=6:size=640x360:rate=30" '
       f'-f lavfi -i "sine=frequency=440:duration=6" '
       f'-c:v libx264 -c:a aac -shortest "{mats}/a.mp4"')
    sh(f'ffmpeg -y -f lavfi -i "gradients=duration=6:size=640x360:rate=30" '
       f'-f lavfi -i "sine=frequency=880:duration=6" '
       f'-c:v libx264 -c:a aac -shortest "{mats}/b.mp4"')
    sh(f'ffmpeg -y -f lavfi -i "smptebars=duration=4:size=640x360:rate=30" '
       f'-an -c:v libx264 "{mats}/noaudio.mp4"')  # 无音轨素材
    sh(f'ffmpeg -y -f lavfi -i "sine=frequency=220:duration=3" '
       f'-c:a libmp3lame "{mats}/bgm.mp3"')  # 短 BGM, 测 stream_loop
    sh(f'ffmpeg -y -f lavfi -i "color=red@0.6:size=600x120:duration=1" '
       f'-frames:v 1 "{mats}/banner.png"')
    sh(f'ffmpeg -y -f lavfi -i "testsrc2=duration=1:size=320x180:rate=15" '
       f'-an -c:v libx264 "{external_dir}/external.mp4"')
    (mats / "external_link.mp4").symlink_to(external_dir / "external.mp4")
    check("materials generated", all((mats / f).exists() for f in
          ("a.mp4", "b.mp4", "noaudio.mp4", "bgm.mp3", "banner.png")))
    check("symlinked materials are readable",
          "duration:" in perception.probe.probe_media("materials/external_link.mp4"))
    short_frames = perception.watch.extract_frames(
        mats / "a.mp4", 0, 1, proj / "analysis" / "frames", "short_vl_smoke")
    check("watch_video extracts DashScope minimum sequence frames", len(short_frames) >= 4)
    short_clip = perception.watch.extract_video_segment(
        mats / "a.mp4", 0, 1, proj / "analysis" / "vl_segments", "short_vl_smoke")
    check("watch_video can create direct VL video segment", short_clip.exists())

    print("[3b] sessions")
    sid1 = session_store.new_session()
    sid2 = session_store.new_session()
    session_store.save_session(sid1, [
        {"role": "user", "content": "会话一"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "我会先检查素材。"},
            {"type": "tool_use", "id": "tool_1", "name": "bash", "input": {"command": "ls"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tool_1", "content": "hidden output"},
        ]},
    ])
    session_store.save_session(sid2, [{"role": "user", "content": "会话二"}])
    rendered_sessions = session_store.render_sessions()
    check("sessions listed", sid1 in rendered_sessions and sid2 in rendered_sessions)
    check("sessions prompt selectable", "choose an item" in rendered_sessions)
    loaded = session_store.load_session(sid1)
    check("session loads isolated messages", loaded["messages"][0]["content"] == "会话一")
    transcript = session_store.render_conversation(loaded["messages"])
    check("session conversation replay renders user/assistant",
          "› user" in transcript and "会话一" in transcript
          and "assistant" in transcript and "我会先检查素材" in transcript)
    check("session conversation replay hides tool results", "hidden output" not in transcript)
    check("session resolve by id", session_store.resolve_session(sid2) == sid2)
    check("session resolve by number", session_store.resolve_session("1") in {sid1, sid2})

    print("[4] timeline validation")
    tl = {
        "version": 1,
        "output": {"file": "output/smoke.mp4", "width": 540, "height": 960, "fps": 30},
        "clips": [
            {"source": "materials/a.mp4", "in": 0.5, "out": 3.5,
             "transition": {"type": "fade", "duration": 0.4}},
            {"source": "materials/b.mp4", "in": 1.0, "out": 4.0, "speed": 1.5},
            {"source": "materials/noaudio.mp4", "in": 0.0, "out": 2.0},
        ],
        "subtitles": [
            {"start": 0.2, "end": 1.8, "text": "三秒钩子!", "style": "hook",
             "effect": "pop_in"},
            {"start": 2.0, "end": 4.0, "text": "正文字幕样式", "style": "default",
             "effect": "fade_in"},
            {"start": 4.2, "end": 6.0, "text": "逐字卡拉OK", "style": "promo",
             "effect": "karaoke"},
        ],
        "overlays": [
            {"image": "materials/banner.png", "start": 0, "end": 3,
             "x": "center", "y": 60, "width": 400},
            {"video": "materials/b.mp4", "start": 3.5, "end": 5.5,
             "x": 20, "y": 600, "width": 200},  # 画中画
        ],
        "audio": {
            "bgm": {"file": "materials/bgm.mp3", "volume": 0.3, "ducking": True},
        },
        "subtitle_styles": {"hook": {"fontsize": 72}},
    }
    (proj / "timeline_smoke.json").write_text(json.dumps(tl, ensure_ascii=False))
    v = tl_mod.validate_file("timeline_smoke.json")
    check("valid timeline passes", v.startswith("VALID"), v)
    render_gate = {}
    t = threading.Thread(
        target=lambda: render_gate.update(
            result=render_request("timeline_smoke.json", background=False)))
    t.start()
    t.join(timeout=3)
    check("render_request in worker asks for approval",
          not t.is_alive()
          and str(render_gate.get("result", "")).startswith("HUMAN APPROVAL REQUIRED"))
    rv = review_timeline("timeline_smoke.json", open_browser=False, start_server=False)
    review_dir = proj / "review" / "timeline_smoke"
    check("review_timeline generates html", (review_dir / "index.html").exists(), rv)
    check("review_timeline stores original", (review_dir / "original.json").exists(), rv)
    (review_dir / "review_log.json").write_text(json.dumps({
        "kind": "timeline",
        "status": "approved",
        "source_path": "timeline_smoke.json",
        "changed_sections": ["clips", "subtitles"],
        "user_notes": "钩子要更短，字幕要更大",
    }, ensure_ascii=False))

    bad = json.loads(json.dumps(tl))
    bad["clips"][0]["out"] = 99  # 超源时长
    bad["clips"][1]["speed"] = 9  # 超速
    bad["subtitles"][0]["effect"] = "explode"  # 未知动效
    bad["clips"][2]["source"] = "materials/nope.mp4"  # 不存在
    (proj / "timeline_bad.json").write_text(json.dumps(bad))
    vb = tl_mod.validate_file("timeline_bad.json")
    check("invalid timeline rejected", vb.startswith("INVALID"))
    for frag in ("exceeds source", "out of range", "effect", "not found"):
        check(f"  catches: {frag}", frag in vb)

    print("[5] render (full feature)")
    r = render_file("timeline_smoke.json")
    check("render completes", "Rendered" in r, r[:300])
    out_fp = proj / "output" / "smoke.mp4"
    check("output exists", out_fp.exists())
    if out_fp.exists():
        from perception.probe import ffprobe_json, media_duration
        dur = media_duration(out_fp)
        exp = tl_mod.total_duration(tl)
        check(f"duration ~{exp:.1f}s (got {dur:.1f}s)", abs(dur - exp) < 0.6)
        info = ffprobe_json(out_fp)
        v_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        check("resolution 540x960",
              (v_stream["width"], v_stream["height"]) == (540, 960))
        check("has audio",
              any(s["codec_type"] == "audio" for s in info["streams"]))

    print("[6] qc_check")
    q = qc_check("output/smoke.mp4", sample_frames=3)
    check("qc report", q.startswith("QC report"), q[:200])
    check("qc loudness measured", "LUFS" in q)
    rr = review_render("output/smoke.mp4", q, open_browser=False, start_server=False)
    render_review_dir = proj / "review" / "render_smoke"
    check("review_render generates html", (render_review_dir / "index.html").exists(), rr)
    check("review_render stores input", (render_review_dir / "render_input.json").exists(), rr)
    (render_review_dir / "render_review_log.json").write_text(json.dumps({
        "kind": "render",
        "status": "needs_revision",
        "output_path": "output/smoke.mp4",
        "issue_tags": ["subtitle_issue"],
        "user_notes": "字幕太小，底部有点挡画面",
    }, ensure_ascii=False))
    summary = summarize_review_feedback(scenario="smoke-test")
    candidates = proj / "analysis" / "review_experience_candidates.md"
    check("summarize_review_feedback writes candidates", candidates.exists(), summary)
    check("review feedback mentions subtitle issue", "字幕" in candidates.read_text(), summary)
    print("\n" + q)

    print(f"\n==== {PASS} passed, {FAIL} failed ====")
    shutil.rmtree(external_dir, ignore_errors=True)
    if FAIL == 0:
        shutil.rmtree(proj, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
