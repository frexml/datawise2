"""Regulatory lineage evidence pack (PDF).

Follows the DataWise Platform Development Guide's generate_evidence_pack
pattern: column-level lineage for a job, packaged with its human-review
audit trail and governed (or explicitly not-yet-governed) summaries —
suitable for submission to a regulatory examiner on demand.
"""
import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, HRFlowable,
)
from sqlalchemy.orm import Session

from dsxlineage.db import models
from dsxlineage.services.pdf_text import markdown_to_reportlab

_PAGE_SIZE = landscape(letter)
_PAGE_WIDTH = _PAGE_SIZE[0]
_MARGIN = 0.6 * inch
_CONTENT_WIDTH = _PAGE_WIDTH - 2 * _MARGIN

_NAVY = colors.HexColor("#1A1C2B")
_SLATE = colors.HexColor("#475569")
_GREEN = colors.HexColor("#15803D")
_GREEN_BG = colors.HexColor("#DCFCE7")
_RED = colors.HexColor("#B91C1C")
_RED_BG = colors.HexColor("#FEE2E2")
_AMBER = colors.HexColor("#92400E")
_AMBER_BG = colors.HexColor("#FEF3C7")
_INDIGO = colors.HexColor("#3730A3")
_INDIGO_BG = colors.HexColor("#E0E7FF")
_GRID = colors.HexColor("#CBD5E1")
_ZEBRA = colors.HexColor("#F1F5F9")


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("EvidenceTitle", parent=base["Title"], textColor=colors.white, fontSize=20, leading=24),
        "subtitle": ParagraphStyle("EvidenceSubtitle", parent=base["BodyText"], textColor=colors.white, fontSize=10),
        "heading": ParagraphStyle("EvidenceHeading", parent=base["Heading2"], textColor=_NAVY, spaceBefore=4, spaceAfter=6),
        "body": ParagraphStyle("EvidenceBody", parent=base["BodyText"], fontSize=9.5, leading=13.5),
        "caption": ParagraphStyle("EvidenceCaption", parent=base["BodyText"], fontSize=8.5, textColor=_SLATE, spaceAfter=6),
        "cell": ParagraphStyle("EvidenceCell", parent=base["BodyText"], fontSize=7.5, leading=9.5),
        "footer": ParagraphStyle("EvidenceFooter", parent=base["BodyText"], fontSize=7.5, textColor=_SLATE),
    }


def _header_banner(job: models.Job, styles: dict) -> Table:
    banner = Table(
        [[
            Paragraph("DataWise Regulatory Lineage Evidence Pack", styles["title"]),
        ], [
            Paragraph(
                f"Job: {job.filename} (ID {job.id}) &nbsp;·&nbsp; "
                f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
                styles["subtitle"],
            ),
        ]],
        colWidths=[_CONTENT_WIDTH],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _NAVY),
        ("TOPPADDING", (0, 0), (0, 0), 14),
        ("BOTTOMPADDING", (0, 0), (0, 0), 2),
        ("TOPPADDING", (0, 1), (0, 1), 2),
        ("BOTTOMPADDING", (0, 1), (0, 1), 14),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
    ]))
    return banner


def _governance_box(review: models.Review | None, styles: dict) -> Table:
    if review and review.status == "approved":
        edited_note = " (hand-edited by the reviewer before approval)" if review.edited_by_reviewer else ""
        reviewed = review.reviewed_at.strftime("%Y-%m-%d %H:%M UTC") if review.reviewed_at else "an unrecorded date"
        text = f"✓ &nbsp; <b>Approved</b> by <b>{review.reviewer or 'an unrecorded reviewer'}</b> on {reviewed}{edited_note}."
        bg, fg = _GREEN_BG, _GREEN
    elif review and review.status == "rejected":
        text = (
            f"✗ &nbsp; <b>Rejected</b> by <b>{review.reviewer or 'an unrecorded reviewer'}</b> — not governed. "
            f"Reason on file: “{review.feedback or 'none recorded'}”"
        )
        bg, fg = _RED_BG, _RED
    elif review and review.status == "regenerating":
        text = "⏳ &nbsp; <b>Pending</b> — a corrected summary is being regenerated and re-queued for review."
        bg, fg = _INDIGO_BG, _INDIGO
    else:
        text = "⏳ &nbsp; <b>Pending review</b> — this job's summary has not yet been approved by a named reviewer."
        bg, fg = _AMBER_BG, _AMBER

    style = ParagraphStyle("GovText", parent=styles["body"], textColor=fg, fontSize=10)
    box = Table([[Paragraph(text, style)]], colWidths=[_CONTENT_WIDTH])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.75, fg),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return box


def _lineage_table(rows: list[models.Lineage], styles: dict) -> Table:
    cols = ["Source Table", "Source Field", "Target Table", "Target Field", "Transformation", "Cardinality", "Hops"]
    # Wider columns for identifier-heavy fields (DataStage names run long),
    # narrower for the short fixed-vocabulary ones.
    weights = [0.22, 0.19, 0.22, 0.19, 0.12, 0.08, 0.06]
    col_widths = [w * _CONTENT_WIDTH for w in weights]

    header = [Paragraph(f"<b>{c}</b>", ParagraphStyle("th", parent=styles["cell"], textColor=colors.white)) for c in cols]
    data = [header]
    for r in rows:
        data.append([
            Paragraph(r.source_table or "–", styles["cell"]),
            Paragraph(r.source_field or "–", styles["cell"]),
            Paragraph(r.target_table or "–", styles["cell"]),
            Paragraph(r.target_field or "–", styles["cell"]),
            Paragraph(r.transformation_type or "–", styles["cell"]),
            Paragraph(r.cardinality or "–", styles["cell"]),
            Paragraph(str(r.total_hops) if r.total_hops is not None else "–", styles["cell"]),
        ])

    table = Table(data, repeatRows=1, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
        ("GRID", (0, 0), (-1, -1), 0.5, _GRID),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ZEBRA]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(_SLATE)
    canvas.drawString(_MARGIN, 0.35 * inch, "DataWise · Confidential · Regulatory Submission")
    canvas.drawRightString(_PAGE_WIDTH - _MARGIN, 0.35 * inch, f"Page {doc.page}")
    canvas.restoreState()


def generate_evidence_pack(job_id: int, db: Session) -> io.BytesIO:
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise ValueError(f"Job {job_id} not found")

    result = db.query(models.Result).filter(models.Result.job_id == job_id).first()
    lineage_rows = db.query(models.Lineage).filter(models.Lineage.job_id == job_id).all()
    review = (
        db.query(models.Review)
        .filter(models.Review.job_id == job_id, models.Review.target_type == "executive_summary")
        .order_by(models.Review.created_at.desc())
        .first()
    )

    styles = _styles()

    story = [
        _header_banner(job, styles),
        Spacer(1, 16),
        Paragraph(
            "<b>Methodology.</b> Lineage and transformation logic below were extracted deterministically "
            "from the source .dsx export (no live production access). Technical and business summaries "
            "were generated by an LLM and are not considered governed until a named human reviewer has "
            "approved them — see governance status below.",
            styles["body"],
        ),
        Spacer(1, 12),
        Paragraph("Governance Status", styles["heading"]),
        _governance_box(review, styles),
        Spacer(1, 14),
        HRFlowable(width=_CONTENT_WIDTH, color=_GRID, thickness=0.75),
        Spacer(1, 10),
        Paragraph("Technical Summary", styles["heading"]),
        Paragraph(markdown_to_reportlab(result.llm_explanation if result else None), styles["body"]),
        Spacer(1, 12),
        Paragraph("Business Summary", styles["heading"]),
        Paragraph(markdown_to_reportlab(result.business_summary if result else None), styles["body"]),
        PageBreak(),
        Paragraph("Column-Level Lineage", styles["heading"]),
    ]

    if lineage_rows:
        story.append(Paragraph(f"{len(lineage_rows)} source-to-target path(s), traced end to end.", styles["caption"]))
        story.append(_lineage_table(lineage_rows, styles))
    else:
        story.append(Paragraph("No lineage records available for this job.", styles["body"]))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=_PAGE_SIZE,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        title="DataWise Regulatory Lineage Evidence Pack",
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer
