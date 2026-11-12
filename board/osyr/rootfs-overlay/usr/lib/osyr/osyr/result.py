"""Common result of the commands and its output (JSON or text)."""
import json
import sys


class Result:
    def __init__(self, ok=True, code="OK", data=None, hint=None, confirm=None, text=None):
        self.ok = ok
        self.code = code
        self.data = data if data is not None else {}
        self.hint = hint
        self.confirm = confirm
        self.text = text  # output for people; if missing, the JSON is used

    def as_dict(self):
        return {"ok": self.ok, "code": self.code, "data": self.data,
                "hint": self.hint, "confirm": self.confirm}

    def emit(self, as_json):
        if as_json:
            print(json.dumps(self.as_dict(), ensure_ascii=False))
        else:
            out = sys.stdout if self.ok else sys.stderr
            if self.text:
                print(self.text, file=out)
            elif not self.ok:
                print(f"ERROR {self.code}", file=out)
            if self.hint:
                print(self.hint, file=out)
        return 0 if self.ok else 1


def fail(code, hint=None, data=None, text=None):
    return Result(ok=False, code=code, data=data, hint=hint, text=text)
