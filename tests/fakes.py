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
