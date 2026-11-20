"""Explicit completion markers shared by native messages and command outputs."""

SUBMISSION_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


def submission_text(text):
    """Return text after a standalone marker, or None when no marker is present."""
    if not isinstance(text, str):
        return None
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == SUBMISSION_MARKER:
            return "".join(lines[index + 1:])
    return None


def native_text_final(response):
    """A native stop/text reply is an end of turn, not evidence that tests passed."""
    choices = response.get('choices', []) if isinstance(response, dict) else []
    if len(choices) != 1 or choices[0].get('finish_reason') != 'stop':
        return None
    message = choices[0].get('message') or {}
    text = message.get('content')
    if message.get('tool_calls') or message.get('function_call'):
        return None
    return text.strip() if isinstance(text, str) and text.strip() else None
