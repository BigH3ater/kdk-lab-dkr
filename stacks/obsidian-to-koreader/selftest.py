#!/usr/bin/env python3
"""Local self-test for obsidian-to-koreader's clean_md image-path encoding.

Not shipped in the image (the Dockerfile copies only obsidian-to-koreader.py). Run it
locally after changing clean_md:

    python3 selftest.py

It builds a throwaway vault whose image paths contain spaces and unicode, runs clean_md,
and asserts the emitted image target (a) contains no raw space (pandoc's markdown reader
would truncate a bare URL there and silently drop the image) and (b) percent-decodes back
to the real, existing file. If a working pandoc is reachable (the PANDOC env var, e.g.
"docker run --rm -i -v <dir>:/w pandoc/typst:3.5.0.0" or a local "pandoc"), it also does a
markdown->html round trip and asserts the <img> survives with the file's basename.
"""
import importlib.util, pathlib, tempfile, os, urllib.parse, sys, subprocess, shlex


def load_module(vault, state):
    os.environ["VAULT_DIR"] = str(vault)
    os.environ["STATE_DIR"] = str(state)
    here = pathlib.Path(__file__).with_name("obsidian-to-koreader.py")
    spec = importlib.util.spec_from_file_location("otk", here)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    with tempfile.TemporaryDirectory() as d:
        vault = pathlib.Path(d) / "vault"
        state = pathlib.Path(d) / "state"
        # paths with spaces (the real breakage) and unicode
        cases = {
            "diagram-gh plan.svg": "3. Resources/Garage-Workshop/diagrams/diagram-gh plan.svg",
            "café résumé.png": "10.Homelab/Explained/café résumé.png",
        }
        for rel in cases.values():
            p = vault / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")  # content irrelevant; it just has to exist for the index
        m = load_module(vault, state)

        failures = 0
        emitted = {}
        for name, rel in cases.items():
            out = m.clean_md(f"intro ![[{name}]] outro\n")
            # extract the ![](...) target
            import re
            mt = re.search(r"!\[\]\(([^)]*)\)", out)
            assert mt, f"no image emitted for {name}: {out!r}"
            target = mt.group(1)
            emitted[name] = target
            abspath = (vault / rel).as_posix()
            ok_space = " " not in target
            ok_decode = urllib.parse.unquote(target) == abspath
            ok_exists = pathlib.Path(urllib.parse.unquote(target)).is_file()
            print(f"[{name}]")
            print(f"  emitted : {target}")
            print(f"  no raw space      : {ok_space}")
            print(f"  decodes to file   : {ok_decode}")
            print(f"  file exists       : {ok_exists}")
            if not (ok_space and ok_decode and ok_exists):
                failures += 1

        # optional pandoc markdown->html round trip
        pandoc = os.environ.get("PANDOC")
        if pandoc:
            for name, target in emitted.items():
                md = f"![]({target})\n"
                argv = shlex.split(pandoc) + ["-f", "markdown", "-t", "html"]
                r = subprocess.run(argv, input=md, capture_output=True, text=True)
                html = r.stdout
                base = pathlib.Path(name).name
                # the <img> must survive and still reference the file (encoded or decoded)
                has_img = "<img" in html and (
                    urllib.parse.quote(base) in html or base in html
                )
                print(f"[pandoc md->html: {name}] <img survives: {has_img}")
                print(f"  html: {html.strip()[:200]}")
                if not has_img:
                    failures += 1
        else:
            print("PANDOC not set -> skipped the live pandoc round trip (run with PANDOC=...)")

        if failures:
            print(f"\nSELFTEST FAILED: {failures} case(s)")
            sys.exit(1)
        print("\nSELFTEST OK")


if __name__ == "__main__":
    main()
