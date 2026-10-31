"""Vikunja approvals client implementing the job contract in
docs/runbooks/approval-tasks.md. The job ONLY creates a task and reads its
outcome; Home Assistant owns the notified/approved/skipped labels and the
decision. Token: op://kdk-cluster/vikunja-api-rw/credential (Bearer).
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("grocery.vikunja")


class Vikunja:
    def __init__(self, url, token, project_id, timeout=20):
        self.url = url.rstrip("/")
        self.token = token
        self.project_id = project_id
        self.timeout = timeout

    def _req(self, method, path, body=None):
        r = requests.request(method, f"{self.url}/api/v1{path}",
                             headers={"Authorization": f"Bearer {self.token}"},
                             json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json() if r.text else None

    def create_approval(self, title, description_html, due_date=None, reminders=None):
        """Create a NON-recurring approval task (repeat_after:0 is mandatory).
        due_date/reminders are RFC3339 strings. Returns the task dict."""
        body = {"title": title, "description": description_html, "repeat_after": 0}
        if due_date:
            body["due_date"] = due_date
        if reminders:
            body["reminders"] = [{"reminder": r} for r in reminders]
        return self._req("PUT", f"/projects/{self.project_id}/tasks", body)

    def get_task(self, task_id):
        return self._req("GET", f"/tasks/{task_id}")

    def find_by_title(self, substring):
        """Return the newest task in the approvals project whose title contains
        `substring` (used by the poll pass to re-find the week's task without
        persisting its id). None if not found."""
        tasks = self._req("GET", f"/projects/{self.project_id}/tasks?per_page=100") or []
        matches = [t for t in tasks if substring in (t.get("title") or "")]
        matches.sort(key=lambda t: t.get("id", 0), reverse=True)
        return matches[0] if matches else None

    @staticmethod
    def outcome(task):
        """Return (done, choice) from a polled task. choice is the
        APPROVALS-RESULT id (e.g. 'all'/'newpi'/'target') or 'skipped'/None."""
        if not task or not task.get("done"):
            return False, None
        labels = {l.get("title") for l in (task.get("labels") or [])}
        desc = task.get("description") or ""
        choice = None
        marker = "APPROVALS-RESULT:"
        if marker in desc:
            choice = desc.split(marker, 1)[1].strip().split("<")[0].strip().split()[0]
        if choice is None and "skipped" in labels:
            choice = "skipped"
        return True, choice
