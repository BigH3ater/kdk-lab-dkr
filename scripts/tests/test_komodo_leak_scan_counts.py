"""Self-test for scripts/komodo-leak-scan-counts.py against a synthetic fixture.

Run: python3 -m unittest discover -s scripts/tests -p 'test_komodo_leak*'  (or pytest)
"""
import contextlib
import importlib.util
import io
import json
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "komodo-leak-scan-counts.py"
FIXTURE = HERE / "fixtures" / "komodo-leak-scan-fixture.json"

spec = importlib.util.spec_from_file_location("komodo_leak_scan_counts", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(list(argv))
    return rc, buf.getvalue()


class FixtureScan(unittest.TestCase):
    def test_counts(self):
        rc, out = run("--fixture", str(FIXTURE), "--collections", "Update,Stack,Variable", "--json")
        self.assertEqual(rc, 1)
        res = json.loads(out)
        u, s = res["Update"], res["Stack"]
        self.assertEqual(u["docs"], 4)
        self.assertEqual(u["compose_config_logs"], 2)
        self.assertEqual(u["redacted_markers"], 2)
        self.assertEqual(u["assign"], 2)
        self.assertEqual(u["url_credential"], 1)
        self.assertEqual(u["known_token"], 0)
        self.assertEqual(u["docs_with_hits"], 2)
        self.assertEqual(s["docs"], 2)
        self.assertEqual(s["redacted_markers"], 1)
        self.assertEqual(s["assign"], 1)
        self.assertEqual(s["docs_with_hits"], 1)
        self.assertEqual(res["Variable"], {"docs": 0, "docs_with_hits": 0, "redacted_markers": 0,
                                           "assign": 0, "known_token": 0, "url_credential": 0})

    def test_never_prints_values_or_names(self):
        for fmt in ((), ("--json",)):
            _, out = run("--fixture", str(FIXTURE), *fmt)
            for leaked in ("hunter2", "fixtureabcdefgh", "fixture-not-real", "fixturevalue123",
                           "DB_PASSWORD", "WEBUI_PASSWORD", "fixture-a", "fixture-b"):
                self.assertNotIn(leaked, out)

    def test_clean_redacted_only_exits_zero(self):
        doc = {"logs": [{"stage": "Compose Config", "stdout": "  X_TOKEN: <redacted>\n"}]}
        st = mod.scan_collection("Update", [doc])
        self.assertEqual(mod.secret_total(st), 0)
        self.assertEqual(st["redacted_markers"], 1)

    def test_known_token_shapes(self):
        # Built at runtime so no token-shaped literal sits in the repo.
        samples = ["gh" + "p_" + "A" * 36, "github" + "_pat_" + "B" * 50,
                   "AK" + "IA" + "C" * 16, "-----BEGIN " + "OPENSSH PRIVATE KEY-----"]
        st = mod.scan_collection("Update", [{"logs": [{"stdout": " ".join(samples)}]}])
        self.assertEqual(st["known_token"], 4)

    def test_placeholders_ignored(self):
        text = ("A_PASSWORD=${A_PASSWORD} B_TOKEN={{ op://v/i/f }} C_SECRET=/run/secrets/c "
                "D_API_KEY=true E_PASSWORD: <redacted>")
        self.assertEqual(mod.classify(text)["assign"], 0)


if __name__ == "__main__":
    unittest.main()
