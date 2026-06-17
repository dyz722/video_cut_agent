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
import shutil
import subprocess
import sys
import tempfile
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
    import agent.loop  # noqa
    import agent.subagent  # noqa
    from agent.tools import TOOLS, TOOL_HANDLERS
    import perception.probe, perception.scenes, perception.transcribe, perception.watch  # noqa
    from action import timeline as tl_mod
    from action.review import review_timeline, review_render, summarize_review_feedback
    from action.render import render_file
    from action.qc import qc_check
    from action.ass_effects import build_ass
    check("all modules import", True)
    check("tool schema/handler 一一对应",
          {t["name"] for t in TOOLS} == set(TOOL_HANDLERS.keys()),
          str({t["name"] for t in TOOLS} ^ set(TOOL_HANDLERS.keys())))
    pyproject = (ROOT / "pyproject.toml").read_text()
    check("veoai CLI entry registered", 'veoai = "main:main"' in pyproject)
    check("legacy CLI aliases removed",
          'video-agent = "main:main"' not in pyproject
          and 'video-cut-agent = "main:main"' not in pyproject)

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
