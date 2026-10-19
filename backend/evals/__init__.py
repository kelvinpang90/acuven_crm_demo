"""Local-only AI quality evaluation (docs/ai-eval.md); run with ``python -m evals``.

Never imported by the app: it seeds demo samples into in-memory SQLite, calls the
existing AI business functions, and writes a report and a score sheet outside
the repository.
"""

DASHBOARD_FEATURE = "dashboard_insight"

# Order of the report sections and of the summary; each name is also the sample
# file name under backend/evals/samples/.
FEATURE_NAMES = (
    "conversation_brief",
    "reply_draft",
    "translate",
    "contact_summary",
    "followup_draft",
    "deal_next_step",
    DASHBOARD_FEATURE,
)
