"""Offline-only ordering of RSS topic groups before editorial research.

Heuristic signals are not proof, independent corroboration, or a risk assessment.
No candidate is rejected here; the existing editorial gates remain authoritative.
"""

import re
from urllib.parse import urlsplit

# Conservative, approximate signals from headlines/summaries only.
# Always keep selected high-risk topics; postpone them when simpler candidates exist.
_HIGH_RISK = re.compile(
    r"\b(?:atak\w*|zamach\w*|wojn\w*|ostrza\w*|bomb\w*|wybuch\w*|"
    r"dywersj\w*|oskarż\w*|zarzut\w*|areszt\w*|zatrzyman\w*|"
    r"immunitet\w*|zderzeni\w*|katastrof\w*|wypadk\w*|"
    r"rann\w*|śmierteln\w*|zgin\w*|zabój\w*|ofiary|ofiar\w*)\b",
    flags=re.IGNORECASE,
)

# Textual mention suggests a possible document, NOT that one has been retrieved.
_DOCUMENT_HINT = re.compile(
    r"\b(?:nbp|gus|nasa|esa|eurostat|raport\w*|komunikat\w*|"
    r"dane|statystyk\w*|badani\w*|wynik\w*|opublikowan\w*)\b",
    flags=re.IGNORECASE,
)

_OFFICIAL_HOSTS = ("gov.pl", "nbp.pl", "stat.gov.pl", "sejm.gov.pl",
                   "europa.eu", "nasa.gov", "esa.int")


def _official_url(url):
    """Recognize only a directly supplied official URL, never a headline claim."""
    try:
        host = (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return False
    return any(host == domain or host.endswith("." + domain)
               for domain in _OFFICIAL_HOSTS)


def topic_priority_key(items, source_group):
    """Lower scores are inspected first; never equate rank with verification."""
    excerpt = " ".join(str(item.get(field) or "") for item in items
                       for field in ("title", "summary"))
    likely_sensitive = bool(_HIGH_RISK.search(excerpt))
    has_official_url = any(_official_url(item.get("url", "")) for item in items)
    publishers = {source_group(item.get("url", "")) for item in items}
    multiple_publishers = len(publishers) >= 2
    possible_document = bool(_DOCUMENT_HINT.search(excerpt))
    # Safety-first scheduling, never an alternative to EXTRACT/VERIFY checks.
    return (int(likely_sensitive), -int(has_official_url),
            -int(multiple_publishers), -int(possible_document))


def rank_topics(topics, source_group):
    """Stable reordering; preserve all selected groups without any new API call."""
    return sorted(topics, key=lambda items: topic_priority_key(items, source_group))
