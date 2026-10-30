#!/usr/bin/env python3
"""Local self-test for obsidian-to-koreader (no tablet, no Kroki, no pandoc needed).

Not shipped in the image (the Dockerfile copies only obsidian-to-koreader.py). Run it
locally after changing clean_md / the delivery layout / the Mermaid handling:

    python3 selftest.py

It covers, against throwaway temp vaults:
  1. clean_md image-path percent-encoding (spaces + unicode survive pandoc's reader);
  2. the vault-mirroring delivery layout -- _jobs_for returns the right (rel_dir, name)
     for note/folder scopes keeping REAL names, _remote_path builds the tablet path, and
     the shell quoting of the mkdir/cat commands round-trips for spaces, apostrophes and
     unicode (so the remote shell sees exactly the intended path);
  3. the Mermaid LR->TD / direction LR->TB rewrite; and
  4. the Mermaid fallback to source text when Kroki is unreachable (KROKI_URL is pointed
     at a closed port here, so no Kroki is required to run the test).

If a working pandoc is reachable (PANDOC env var, e.g.
"docker run --rm -i -v <dir>:/w pandoc/typst:3.5.0.0" or a local "pandoc"), test 1 also
does a markdown->html round trip and asserts the <img> survives with the file's basename.
"""
import importlib.util, pathlib, tempfile, os, urllib.parse, sys, subprocess, shlex, re

# Point Kroki at a closed port so Mermaid always falls back (keeps the test offline), and
# keep the LR->TD rewrite on (the default) so test 3 exercises it.
os.environ.setdefault("KROKI_URL", "http://127.0.0.1:1")
os.environ.setdefault("KROKI_TIMEOUT", "2")
os.environ.setdefault("MERMAID_FORCE_TD", "1")


def load_module(vault, state):
    os.environ["VAULT_DIR"] = str(vault)
    os.environ["STATE_DIR"] = str(state)
    os.environ["RM_DEST"] = "/home/root/books/kodiak-codex"
    here = pathlib.Path(__file__).with_name("obsidian-to-koreader.py")
    spec = importlib.util.spec_from_file_location("otk", here)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _mkvault(d):
    vault = pathlib.Path(d) / "vault"
    state = pathlib.Path(d) / "state"
    vault.mkdir(parents=True, exist_ok=True)
    return vault, state


def test_image_encoding():
    print("\n== test 1: clean_md image-path encoding ==")
    failures = 0
    with tempfile.TemporaryDirectory() as d:
        vault, state = _mkvault(d)
        cases = {
            "diagram-gh plan.svg": "3. Resources/Garage-Workshop/diagrams/diagram-gh plan.svg",
            "café résumé.png": "10.Homelab/Explained/café résumé.png",
        }
        for rel in cases.values():
            p = vault / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
        m = load_module(vault, state)
        emitted = {}
        for name, rel in cases.items():
            out = m.clean_md(f"intro ![[{name}]] outro\n")
            mt = re.search(r"!\[\]\(([^)]*)\)", out)
            assert mt, f"no image emitted for {name}: {out!r}"
            target = mt.group(1)
            emitted[name] = target
            abspath = (vault / rel).as_posix()
            ok = (" " not in target
                  and urllib.parse.unquote(target) == abspath
                  and pathlib.Path(urllib.parse.unquote(target)).is_file())
            print(f"  [{name}] {target}  ok={ok}")
            failures += not ok
        pandoc = os.environ.get("PANDOC")
        if pandoc:
            for name, target in emitted.items():
                r = subprocess.run(shlex.split(pandoc) + ["-f", "markdown", "-t", "html"],
                                   input=f"![]({target})\n", capture_output=True, text=True)
                base = pathlib.Path(name).name
                has_img = "<img" in r.stdout and (urllib.parse.quote(base) in r.stdout or base in r.stdout)
                print(f"  [pandoc md->html {name}] <img survives: {has_img}")
                failures += not has_img
        else:
            print("  PANDOC not set -> skipped the live pandoc round trip")
    return failures


def test_hierarchy():
    print("\n== test 2: vault-mirroring delivery layout + shell quoting ==")
    failures = 0
    with tempfile.TemporaryDirectory() as d:
        vault, state = _mkvault(d)
        notes = [
            "1. Projects/garage-gladiator-husky/Bench Layout.md",   # spaces
            "20.Work/notes/Mike's weekly.md",                       # apostrophe
            "20.Work/café/résumé plan.md",                          # unicode + space
        ]
        for rel in notes:
            p = vault / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"# {pathlib.Path(rel).stem}\n\nbody\n", encoding="utf-8")
        m = load_module(vault, state)

        def check(label, got_dir, got_name, exp_dir, exp_name):
            nonlocal failures
            remote = m._remote_path(m.RM_DEST, got_dir, got_name)
            exp_remote = f"{m.RM_DEST}/{exp_dir + '/' if exp_dir else ''}{exp_name}.pdf"
            # the remote shell must see EXACTLY these argv after parsing the quoted command
            mkdir_argv = shlex.split("mkdir -p " + shlex.quote(m._remote_dir(m.RM_DEST, got_dir)))
            cat_argv = shlex.split("cat > " + shlex.quote(remote))
            ok = (got_dir == exp_dir and got_name == exp_name
                  and remote == exp_remote
                  and mkdir_argv == ["mkdir", "-p", m._remote_dir(m.RM_DEST, got_dir)]
                  and cat_argv == ["cat", ">", remote])
            print(f"  [{label}] -> {remote}  ok={ok}")
            failures += not ok

        # note scope: real dir + real name (with spaces)
        (rd, name, _md), = m._jobs_for(
            {"scope": "note", "path": "1. Projects/garage-gladiator-husky/Bench Layout.md"})
        check("note spaces", rd, name, "1. Projects/garage-gladiator-husky", "Bench Layout")

        # folder scope, per-note: each note keeps its real dir + name (apostrophe, unicode)
        jobs = {name: rd for rd, name, _ in m._jobs_for(
            {"scope": "folder", "path": "20.Work", "mode": "per-note"})}
        check("note apostrophe", jobs.get("Mike's weekly", "<missing>"), "Mike's weekly",
              "20.Work/notes", "Mike's weekly")
        check("note unicode", jobs.get("résumé plan", "<missing>"), "résumé plan",
              "20.Work/café", "résumé plan")

        # folder scope, combined: one PDF inside the folder it covers
        (rd, name, _md), = m._jobs_for({"scope": "folder", "path": "20.Work", "mode": "combined"})
        check("folder combined", rd, name, "20.Work", "20.Work")
    return failures


def test_mermaid_rewrite():
    print("\n== test 3: Mermaid LR->TD rewrite ==")
    failures = 0
    with tempfile.TemporaryDirectory() as d:
        vault, state = _mkvault(d)
        m = load_module(vault, state)
        cases = [
            ("flowchart LR\n  A --> B", "flowchart TD\n  A --> B"),
            ("graph LR\n  A --> B", "graph TD\n  A --> B"),
            ("  direction LR", "  direction TB"),
            ("flowchart TD\n  A --> B", "flowchart TD\n  A --> B"),   # already TD: unchanged
            ("graph RL\n  A --> B", "graph TD\n  A --> B"),           # RL is wide too
        ]
        for src, exp in cases:
            got = m._mermaid_td(src)
            ok = got == exp
            print(f"  {src!r:40} -> {got!r}  ok={ok}")
            failures += not ok
    return failures


def test_mermaid_fallback():
    print("\n== test 4: Mermaid fallback when Kroki is unreachable ==")
    failures = 0
    with tempfile.TemporaryDirectory() as d:
        vault, state = _mkvault(d)
        m = load_module(vault, state)
        # a diagram with a %% mermaid comment: the comment must survive to the fallback
        # (clean_md must park the block before it strips %% Obsidian comments)
        src = "flowchart LR\n  %% keep me\n  A-->B"
        out = m.clean_md(f"before\n\n```mermaid\n{src}\n```\n\nafter\n")
        ok = ("not rendered" in out
              and "flowchart LR" in out        # original orientation preserved in fallback
              and "%% keep me" in out          # %% comment not stripped
              and "![](" not in out)           # no SVG image when Kroki is down
        print(f"  fallback contains source + note, no image: ok={ok}")
        if not ok:
            print("  ---- output ----\n" + out)
        failures += not ok
    return failures


def main():
    failures = (test_image_encoding() + test_hierarchy()
                + test_mermaid_rewrite() + test_mermaid_fallback())
    if failures:
        print(f"\nSELFTEST FAILED: {failures} case(s)")
        sys.exit(1)
    print("\nSELFTEST OK")


if __name__ == "__main__":
    main()
