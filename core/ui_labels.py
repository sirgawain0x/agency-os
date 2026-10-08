"""Human-readable labels for pipeline stages and prospect sources (web UI)."""

from __future__ import annotations

# Order used in filters and stage pickers.
PIPELINE_STAGES: tuple[str, ...] = (
    "cold",
    "contacted",
    "engaged",
    "demo_scheduled",
    "proposal_sent",
    "closed_won",
    "closed_lost",
    "nurture",
)

STAGE_LABELS: dict[str, str] = {
    "cold": "Not contacted",
    "contacted": "Contacted",
    "engaged": "In conversation",
    "demo_scheduled": "Demo booked",
    "proposal_sent": "Proposal sent",
    "closed_won": "Won",
    "closed_lost": "Lost",
    "nurture": "Nurture",
}

SOURCE_LABELS: dict[str, str] = {
    "irs_bmf": "IRS BMF",
    "sos_partners": "Secretary of State partners",
    "oia_grantees": "OIA grantees",
    "miv_partners": "MIV partners",
    "npi_registry": "NPI registry",
    "pacer_attorneys": "PACER attorneys",
    "courtlistener_attorneys": "CourtListener attorneys",
}


def stage_label(stage: str | None) -> str:
    key = (stage or "cold").strip()
    return STAGE_LABELS.get(key, key.replace("_", " ").title())


def source_label(source: str | None) -> str:
    key = (source or "").strip()
    if not key:
        return "—"
    if key in SOURCE_LABELS:
        return SOURCE_LABELS[key]
    return key.replace("_", " ").title()
