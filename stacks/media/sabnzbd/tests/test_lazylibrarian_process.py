#!/usr/bin/env python3
# ruff: noqa
"""Offline unit tests for the SABnzbd -> LazyLibrarian post-processing trigger.

No network and no SABnzbd: urllib.request.urlopen is mocked, time.sleep is
stubbed so retries do not wait. Run from anywhere:

    python3 stacks/media/sabnzbd/tests/test_lazylibrarian_process.py
    # or: python3 -m unittest -v (discovered from this directory)

Covers:
  * success (status 0)      -> exactly one forceProcess call, with the right URL;
  * failure status (3)      -> no immediate call; a detached follow-up is
                               scheduled (fork mocked) so LazyLibrarian marks
                               the snatch Failed and the book Wanted again;
  * {"Success": false} reply -> failure, not retried, no "requested" line;
  * HTTP error              -> retried RETRIES times, then main() exits cleanly
                               (the module always exits 0 for SABnzbd);
  * 401 (bad key)           -> one call, not retried.
"""

import importlib.util
import io
import os
import pathlib
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

# Load the hyphenated script file as a module.
_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lazylibrarian-process.py"
_spec = importlib.util.spec_from_file_location("ll_process", _SCRIPT)
llp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(llp)


def _ok_response(status=200, body=b"OK"):
    """A context-manager stand-in for urlopen()'s return value."""
    resp = mock.MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


# SABnzbd positional args: [0]=script [1]=dir [2]=nzb [3]=cleanname [4]=report
# [5]=category [6]=group [7]=status [8]=failure-url.
def _argv(status):
    return [
        "lazylibrarian-process.py",
        "/data/media/staging/lazylibrarian/Mavericks",
        "Mavericks.nzb",
        "Mavericks",
        "",
        "lazylibrarian",
        "alt.binaries.e-book",
        str(status),
        "",
    ]


ENV_OK = {"LL_URL": "http://lazylibrarian:5299", "LL_API_KEY": "secret-key"}


class StatusParsing(unittest.TestCase):
    def test_env_status_wins(self):
        self.assertEqual(llp.job_status(_argv(3), {"SAB_PP_STATUS": "0"}), 0)

    def test_argv_fallback(self):
        self.assertEqual(llp.job_status(_argv(0), {}), 0)

    def test_unparseable_is_none(self):
        self.assertIsNone(llp.job_status(["s"], {"SAB_PP_STATUS": "x"}))


class SuccessCallsForceProcess(unittest.TestCase):
    def test_single_call_on_success(self):
        with mock.patch.object(llp.urllib.request, "urlopen",
                               return_value=_ok_response(200)) as uo:
            with redirect_stdout(io.StringIO()) as out:
                llp.main(_argv(0), {**ENV_OK, "SAB_FINAL_NAME": "Mavericks"})
        self.assertEqual(uo.call_count, 1)  # exactly one call, no retries
        sent = uo.call_args.args[0].full_url
        self.assertIn("cmd=forceProcess", sent)
        self.assertIn("apikey=secret-key", sent)
        last_line = out.getvalue().strip().splitlines()[-1]
        self.assertIn("forceProcess requested", last_line)
        self.assertNotIn("secret-key", last_line)  # key never printed


class FailureStatusSchedulesFollowup(unittest.TestCase):
    def test_no_immediate_call_but_followup_scheduled(self):
        with mock.patch.object(llp.urllib.request, "urlopen") as uo, \
             mock.patch.object(llp, "schedule_failed_followup",
                               return_value=True) as sched:
            with redirect_stdout(io.StringIO()) as out:
                llp.main(_argv(1), {**ENV_OK, "LL_FAIL_DELAY": "5"})
        uo.assert_not_called()  # SABnzbd history isn't written yet
        sched.assert_called_once_with("http://lazylibrarian:5299", "secret-key", 5)
        last_line = out.getvalue().strip().splitlines()[-1]
        self.assertIn("returns to Wanted", last_line)
        self.assertNotIn("secret-key", last_line)

    def test_fork_parent_returns_immediately(self):
        with mock.patch.object(llp.os, "fork", return_value=4242) as fk, \
             mock.patch.object(llp, "force_process") as fp:
            self.assertTrue(llp.schedule_failed_followup("http://x", "k", 90))
        fk.assert_called_once()
        fp.assert_not_called()  # only the child calls LazyLibrarian

    def test_fork_child_waits_then_calls(self):
        with mock.patch.object(llp.os, "fork", return_value=0), \
             mock.patch.object(llp.os, "setsid"), \
             mock.patch.object(llp.os, "open", return_value=99), \
             mock.patch.object(llp.os, "dup2"), \
             mock.patch.object(llp.time, "sleep") as slept, \
             mock.patch.object(llp, "force_process") as fp, \
             mock.patch.object(llp.os, "_exit", side_effect=SystemExit) as ex:
            with self.assertRaises(SystemExit):
                llp.schedule_failed_followup("http://x", "k", 90)
        slept.assert_called_once_with(90)
        fp.assert_called_once_with("http://x", "k")
        ex.assert_called_once_with(0)

    def test_fork_error_is_reported_not_raised(self):
        with mock.patch.object(llp.os, "fork", side_effect=OSError), \
             redirect_stdout(io.StringIO()) as out:
            llp.main(_argv(2), ENV_OK)
        self.assertIn("PostProcessor will catch it", out.getvalue())

    def test_bad_delay_falls_back(self):
        self.assertEqual(llp.fail_delay({"LL_FAIL_DELAY": "x"}), llp.FAIL_DELAY)
        self.assertEqual(llp.fail_delay({}), llp.FAIL_DELAY)

    def test_no_call_when_creds_missing(self):
        with mock.patch.object(llp.urllib.request, "urlopen") as uo, \
             mock.patch.object(llp, "schedule_failed_followup") as sched:
            with redirect_stdout(io.StringIO()):
                llp.main(_argv(0), {"LL_URL": "http://lazylibrarian:5299"})  # no key
                llp.main(_argv(1), {"LL_URL": "http://lazylibrarian:5299"})
        uo.assert_not_called()
        sched.assert_not_called()


class ErrorReplyIsFailure(unittest.TestCase):
    BODY = b'{"Success": false, "Data": "", "Error": {"Code": 405, "Message": "Method not allowed"}}'

    def test_success_false_is_failure_not_retried(self):
        with mock.patch.object(llp.urllib.request, "urlopen",
                               return_value=_ok_response(200, self.BODY)) as uo, \
             mock.patch.object(llp.time, "sleep"):
            ok, detail = llp.force_process("http://lazylibrarian:5299", "k")
        self.assertFalse(ok)
        self.assertEqual(uo.call_count, 1)
        self.assertIn("405", detail)

    def test_main_reports_failed(self):
        with mock.patch.object(llp.urllib.request, "urlopen",
                               return_value=_ok_response(200, self.BODY)):
            with redirect_stdout(io.StringIO()) as out:
                llp.main(_argv(0), ENV_OK)
        last_line = out.getvalue().strip().splitlines()[-1]
        self.assertIn("FAILED", last_line)
        self.assertNotIn("requested", last_line)

    def test_reply_error_variants(self):
        self.assertIsNone(llp.reply_error("OK"))
        self.assertIsNone(llp.reply_error('{"Success": true}'))
        self.assertIsNone(llp.reply_error("[1, 2]"))
        self.assertIn("405", llp.reply_error(self.BODY.decode()))


class HttpErrorRetries(unittest.TestCase):
    def test_retries_then_gives_up(self):
        err = urllib.error.HTTPError("http://x", 500, "boom", {}, None)
        with mock.patch.object(llp.urllib.request, "urlopen", side_effect=err) as uo, \
             mock.patch.object(llp.time, "sleep") as slept:
            ok, detail = llp.force_process("http://lazylibrarian:5299", "k")
        self.assertFalse(ok)
        self.assertEqual(uo.call_count, llp.RETRIES)      # tried RETRIES times
        self.assertEqual(slept.call_count, llp.RETRIES - 1)  # no sleep after last
        self.assertIn("after", detail)

    def test_main_never_raises_on_http_error(self):
        err = urllib.error.HTTPError("http://x", 500, "boom", {}, None)
        with mock.patch.object(llp.urllib.request, "urlopen", side_effect=err), \
             mock.patch.object(llp.time, "sleep"):
            with redirect_stdout(io.StringIO()) as out:
                llp.main(_argv(0), ENV_OK)  # must return, not raise
        self.assertIn("FAILED", out.getvalue())

    def test_401_not_retried(self):
        err = urllib.error.HTTPError("http://x", 401, "nope", {}, None)
        with mock.patch.object(llp.urllib.request, "urlopen", side_effect=err) as uo, \
             mock.patch.object(llp.time, "sleep"):
            ok, detail = llp.force_process("http://lazylibrarian:5299", "k")
        self.assertFalse(ok)
        self.assertEqual(uo.call_count, 1)  # bad key -> no retry
        self.assertIn("401", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
