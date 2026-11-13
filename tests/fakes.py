from agenda_watcher.judge import JudgeDown, JudgeError, Review


class FakeJudge:
    def __init__(self, keep=(), fail_triage=False, review=None, review_failures=0, down=0, fatal=False):
        self.keep, self.fail_triage = set(keep), fail_triage
        self.review_result = review or Review(True, "animal_agriculture", 0.9, "because", "A summary.")
        self.review_failures = review_failures
        self.down, self.fatal = down, fatal
        self.triage_calls, self.review_calls = [], []

    def _maybe_down(self):
        if self.down > 0:
            self.down -= 1
            raise JudgeDown("service down", fatal=self.fatal)

    def triage(self, items):
        self.triage_calls.append(list(items))
        self._maybe_down()
        if self.fail_triage:
            raise JudgeError("down")
        return {i.matter_id for i in items if i.matter_id in self.keep}

    def review(self, title, type, body_name, text, attachment_names, attachment_text):
        self.review_calls.append(dict(title=title, type=type, body_name=body_name, text=text,
                                      attachment_names=attachment_names, attachment_text=attachment_text))
        self._maybe_down()
        if self.review_failures > 0:
            self.review_failures -= 1
            raise JudgeError("down")
        return self.review_result


class FakeLegistar:
    """Legistar stand-in for review tests: serves matter text and attachment lists."""

    def __init__(self, text="matter text", attachments=None, fail_attachments=False, fail_text=False):
        self.text, self.attachments, self.fail_attachments = text, attachments or {}, fail_attachments
        self.fail_text = fail_text
        self.get_calls = []

    def matter_text(self, matter_id):
        from agenda_watcher.legistar import LegistarError
        if self.fail_text:
            raise LegistarError("boom")
        return self.text

    def get(self, path, params=None):
        from agenda_watcher.legistar import LegistarError
        self.get_calls.append(path)
        if self.fail_attachments:
            raise LegistarError("boom")
        return self.attachments.get(int(path.split("/")[2]), [])
