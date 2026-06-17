# Harness: on-demand knowledge -- 赛道剪辑策略按需加载, 不塞 system prompt. (port of s05)
"""
SkillLoader: 两层注入
    Layer 1: skill 名称+描述 进 system prompt (~100 token/skill)
    Layer 2: load_skill(name) 时全文通过 tool_result 注入
skill 还可以携带 references/ 等附属文件, 由 agent 用 read_file 按需读取。
"""

import re
from pathlib import Path


class SkillLoader:
    def __init__(self, skills_dir: Path | list[Path]):
        if isinstance(skills_dir, (list, tuple)):
            self.skill_dirs = [Path(p) for p in skills_dir]
        else:
            self.skill_dirs = [Path(skills_dir)]
        self.skills = {}
        self.reload()

    def reload(self):
        self.skills = {}
        for skills_dir in self.skill_dirs:
            if not skills_dir.exists():
                continue
            for f in sorted(skills_dir.rglob("SKILL.md")):
                text = f.read_text()
                match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
                meta, body = {}, text
                if match:
                    for line in match.group(1).strip().splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            meta[k.strip()] = v.strip()
                    body = match.group(2).strip()
                name = meta.get("name", f.parent.name)
                self.skills[name] = {"meta": meta, "body": body, "dir": str(f.parent)}

    def descriptions(self) -> str:
        if not self.skills:
            return "(no skills)"
        return "\n".join(
            f"  - {n}: {s['meta'].get('description', '-')}" for n, s in self.skills.items()
        )

    def load(self, name: str) -> str:
        s = self.skills.get(name)
        if not s:
            return (f"Error: Unknown skill '{name}'. "
                    f"Available: {', '.join(self.skills.keys())}")
        return (f"<skill name=\"{name}\" dir=\"{s['dir']}\">\n{s['body']}\n</skill>")
