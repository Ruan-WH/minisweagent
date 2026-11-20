"""Conservative Markdown-only compatibility for public evidence citations."""

import re

from evaluation.utils.simple_followup import public_evidence_parts


def markdown_evidence_parts(excerpt, exchanges, allow_composite=False):
    exact = public_evidence_parts(excerpt, exchanges, allow_composite=allow_composite)
    if exact:
        return exact

    def normalize(text):
        # Only the language/info string on a fence line is presentation metadata.
        # Preserve the fence itself, all code tokens, punctuation and factual words.
        text = re.sub(r'(?m)^([ \t]*(?:`{3,}|~{3,}))[^\n]*$', r'\1', text)
        return ' '.join(text.split())

    query = normalize(excerpt)
    if not query:
        return []
    for exchange in exchanges:
        if query in normalize(exchange['answer']):
            # Return original public text, never a model-rewritten quote.
            return [exchange['answer']]
    return []
