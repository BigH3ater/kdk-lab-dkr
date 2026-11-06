"""gen-workout-guide.py on a tiny fake vault: the range-of-motion PNG wins over the
SVG, and workout.json carries the steps (with slugs) the /workout/ sidecar reads."""
import json
import subprocess
import sys
from pathlib import Path

GEN = Path(__file__).resolve().parents[1] / "guides" / "gen-workout-guide.py"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def fake_vault(root: Path) -> Path:
    ex = root / "3. Resources" / "Exercise"
    (ex / "Days").mkdir(parents=True)
    (ex / "diagrams").mkdir()
    (ex / "Exercise.README.md").write_text(
        "# Plan\n\n## The week\n| Day | Session |\n|---|---|\n| **Mon** | Lower A |\n", encoding="utf-8")
    for d in DAYS:
        (ex / "Days" / f"{d}.README.md").write_text(
            f"# {d} — Test\n\n**legs · 30 min**\n**You need:** bench\n\n"
            "## 1 · Box squat\n**3 × 8–12**\n\n![[ex-box-squat.svg]]\n\nLog in today's Daily note: `squat_lb`\n\n"
            "## 2 · Calf raise\n**3 × 12**\n\n![[ex-calf-raise.svg]]\n\n"
            "## 3 · Every day: routine\n**~9 min** — any time\n\n![[Every Day.README#The routine]]\n",
            encoding="utf-8")
    (ex / "Days" / "Every Day.README.md").write_text(
        "# Every Day\n\n## The routine\n\n### 1 · Tendon glides\n**5 ×**\n\n![[ex-hand-glides.svg]]\n", encoding="utf-8")
    for n in ("ex-box-squat.svg", "ex-calf-raise.svg", "ex-hand-glides.svg"):
        (ex / "diagrams" / n).write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (ex / "diagrams" / "ex-box-squat-rom.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return root


def test_rom_png_preferred_and_json_written(tmp_path):
    vault = fake_vault(tmp_path / "vault")
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(GEN), "--vault", str(vault), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    page = (out / "workout.html").read_text(encoding="utf-8")
    assert 'src="exercise/ex-box-squat-rom.png"' in page
    assert 'src="exercise/ex-calf-raise.svg"' in page
    assert (out / "exercise" / "ex-box-squat-rom.png").is_file()
    data = json.loads((out / "workout.json").read_text(encoding="utf-8"))
    mon = data["days"][0]
    assert mon["id"] == "mon" and mon["youneed"] == "bench"
    assert [s["slug"] for s in mon["steps"]] == ["box-squat", "calf-raise", ""]
    assert mon["steps"][0]["image"] == "exercise/ex-box-squat-rom.png"
    assert mon["steps"][0]["dose"] == "3 × 8–12"
    assert mon["steps"][2]["everyday"] is True
    assert data["everyday"][0]["slug"] == "hand-glides"


def test_everyday_heading_and_minutes_come_from_the_note(tmp_path):
    vault = fake_vault(tmp_path / "vault")
    (vault / "3. Resources" / "Exercise" / "Days" / "Every Day.README.md").write_text(
        "# Every Day — PT stretches\n\n~6 minutes (both legs), every day, any time. Tick **PT stretches**.\n\n"
        "## The routine\n\n### 1 · Tendon glides\n**5 ×**\n\n![[ex-hand-glides.svg]]\n", encoding="utf-8")
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(GEN), "--vault", str(vault), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    page = (out / "workout.html").read_text(encoding="utf-8")
    assert '<h2 class="dayhead">Every day — PT stretches</h2>' in page
    assert "~6 minutes (both legs), every day, any time." in page
    assert "hand routine</h2>" not in page and "~9 minutes" not in page


def test_v2_cues_and_sets_from_the_card_generator(tmp_path):
    vault = fake_vault(tmp_path / "vault")
    scripts = vault / "3. Resources" / "Exercise" / "scripts"
    scripts.mkdir()
    (scripts / "ex_days.py").write_text(
        'C = {"box-squat": ("Box squat", "3 × 8–12", ["Feet shoulder-width.", "Tap the bench."], None, {})}\n',
        encoding="utf-8")
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(GEN), "--vault", str(vault), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    data = json.loads((out / "workout.json").read_text(encoding="utf-8"))
    assert data["version"] == 2
    sq, calf = data["days"][0]["steps"][:2]
    assert sq["cues"] == ["Feet shoulder-width.", "Tap the bench."] and sq["sets"] == 3
    assert calf["cues"] == [] and calf["sets"] == 3
    assert data["everyday"][0]["sets"] == 5            # "5 ×"
    assert not list(scripts.glob("__pycache__"))        # the vault is never written to


def test_missing_card_generator_still_builds(tmp_path):
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(GEN), "--vault", str(fake_vault(tmp_path / "vault")), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "no cues" in r.stderr
    assert json.loads((out / "workout.json").read_text(encoding="utf-8"))["days"][0]["steps"][0]["cues"] == []
