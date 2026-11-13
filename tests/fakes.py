from agenda_watcher.judge import JudgeError, Review


class FakeJudge:
    def __init__(self, keep=(), fail_triage=False, review=None, review_failures=0):
        self.keep, self.fail_triage = set(keep), fail_triage
        self.review_result = review or Review(True, "animal_agriculture", 0.9, "because", "A summary.")
        self.review_failures = review_failures
        self.triage_calls, self.review_calls = [], []

    def triage(self, items):
        self.triage_calls.append(list(items))
        if self.fail_triage:
            raise JudgeError("down")
        return {i.matter_id for i in items if i.matter_id in self.keep}

    def review(self, title, type, body_name, text, attachment_names, attachment_text):
        self.review_calls.append(dict(title=title, type=type, body_name=body_name, text=text,
                                      attachment_names=attachment_names, attachment_text=attachment_text))
        if self.review_failures > 0:
            self.review_failures -= 1
            raise JudgeError("down")
        return self.review_result


class FakeLegistar:
    """Legistar stand-in for review tests: serves matter text and attachment lists."""

    def __init__(self, text="matter text", attachments=None, fail_attachments=False):
        self.text, self.attachments, self.fail_attachments = text, attachments or {}, fail_attachments
        self.get_calls = []

    def matter_text(self, matter_id):
        return self.text

    def get(self, path, params=None):
        from agenda_watcher.legistar import LegistarError
        self.get_calls.append(path)
        if self.fail_attachments:
            raise LegistarError("boom")
        return self.attachments.get(int(path.split("/")[2]), [])
