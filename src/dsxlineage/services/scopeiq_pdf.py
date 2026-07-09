"""Per-job ScopeIQ delivery estimate (PDF).

Renders the persisted output of `agents.scopeiq_agent`: four fixed research
dimensions, each with a role-level day breakdown, complexity uplift
signals, and risk adjustments, aggregated into a final estimate — the
"complete estimate document" handed to Delivery Lead / PMO for a single job.
"""
import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
)

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

_ROLE_ORDER = ["data_architect", "ai_engineer", "compliance_lead", "migration_engineer", "engagement_lead"]
_ROLE_LABELS = {
    "data_architect": "Data Architect",
    "ai_engineer": "AI Engineer",
    "compliance_lead": "Compliance Lead",
    "migration_engineer": "Migration Engineer",
    "engagement_lead": "Engagement Lead",
}
_DIMENSION_ORDER = ["tech_stack", "compliance_regulatory", "integration_patterns", "delivery_risk"]
_DIMENSION_LABELS = {
    "tech_stack": "Technology Stack",
    "compliance_regulatory": "Compliance & Regulatory",
    "integration_patterns": "Integration Patterns",
    "delivery_risk": "Delivery Risk",
}
_TIER_COLORS = {"low": (_GREEN, _GREEN_BG), "medium": (_AMBER, _AMBER_BG), "high": (_RED, _RED_BG)}


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("ScopeIQTitle", parent=base["Title"], textColor=colors.white, fontSize=20, leading=24),
        "subtitle": ParagraphStyle("ScopeIQSubtitle", parent=base["BodyText"], textColor=colors.white, fontSize=10),
        "heading": ParagraphStyle("ScopeIQHeading", parent=base["Heading2"], textColor=_NAVY, spaceBefore=4, spaceAfter=6),
        "subheading": ParagraphStyle("ScopeIQSubheading", parent=base["Heading3"], textColor=_NAVY, spaceBefore=2, spaceAfter=4),
        "body": ParagraphStyle("ScopeIQBody", parent=base["BodyText"], fontSize=9.5, leading=13.5),
        "caption": ParagraphStyle("ScopeIQCaption", parent=base["BodyText"], fontSize=9, textColor=_SLATE, leading=12.5, spaceAfter=6),
        "cell": ParagraphStyle("ScopeIQCell", parent=base["BodyText"], fontSize=8, leading=10),
        "cellCenter": ParagraphStyle("ScopeIQCellCenter", parent=base["BodyText"], fontSize=8, leading=10, alignment=1),
    }


def _header_banner(job: models.Job, estimate: models.ScopeIQEstimate, styles: dict) -> Table:
    banner = Table(
        [[
            Paragraph("DataWise ScopeIQ Delivery Estimate", styles["title"]),
        ], [
            Paragraph(
                f"Job: {job.filename} (ID {job.id}) &nbsp;·&nbsp; Package {estimate.package_id} &nbsp;·&nbsp; "
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


def _role_days_table(role_days: dict, styles: dict, total_label: str = "Total") -> Table:
    header = [Paragraph("Role", ParagraphStyle("h", parent=styles["cell"], textColor=colors.white))]
    header += [
        Paragraph(f"<b>{_ROLE_LABELS[role]}</b>", ParagraphStyle("h", parent=styles["cellCenter"], textColor=colors.white))
        for role in _ROLE_ORDER
    ]
    header.append(Paragraph(f"<b>{total_label}</b>", ParagraphStyle("h", parent=styles["cellCenter"], textColor=colors.white)))

    total = sum(role_days.get(role, 0) or 0 for role in _ROLE_ORDER)
    data_row = [Paragraph("Days", styles["cell"])]
    data_row += [Paragraph(f"{role_days.get(role, 0) or 0:.1f}", styles["cellCenter"]) for role in _ROLE_ORDER]
    data_row.append(Paragraph(f"<b>{total:.1f}</b>", styles["cellCenter"]))

    col_widths = [0.14 * _CONTENT_WIDTH] + [0.15 * _CONTENT_WIDTH] * len(_ROLE_ORDER) + [0.11 * _CONTENT_WIDTH]
    table = Table([header, data_row], colWidths=col_widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
        ("GRID", (0, 0), (-1, -1), 0.5, _GRID),
        ("BACKGROUND", (0, 1), (-1, 1), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _tier_badge(tier: str, styles: dict) -> Table:
    fg, bg = _TIER_COLORS.get(tier, (_SLATE, _ZEBRA))
    style = ParagraphStyle("Tier", parent=styles["body"], textColor=fg, fontSize=11)
    box = Table([[Paragraph(f"Complexity Tier: <b>{(tier or 'unknown').upper()}</b>", style)]], colWidths=[_CONTENT_WIDTH])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.75, fg),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return box


def _adjustment_summary_table(estimate: models.ScopeIQEstimate, styles: dict) -> Table:
    total_uplift_pct = sum((s.get("uplift_pct") or 0) for s in (estimate.uplift_adjustments or []))
    total_risk_days = sum((r.get("impact_days") or 0) for r in (estimate.risk_adjustments or []))
    rows = [
        ["Base effort (role-days summed across dimensions)", f"{estimate.total_days_base:.1f} days"],
        ["Complexity uplift applied", f"+{total_uplift_pct:.0f}%"],
        ["Risk-adjustment days added", f"+{total_risk_days:.1f} days"],
        ["Adjusted total estimate", f"{estimate.total_days_adjusted:.1f} days"],
    ]
    data = [[Paragraph(k, styles["body"]), Paragraph(f"<b>{v}</b>", styles["body"])] for k, v in rows]
    table = Table(data, colWidths=[0.7 * _CONTENT_WIDTH, 0.3 * _CONTENT_WIDTH])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, _GRID),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, _ZEBRA]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def _callout_list(items: list[str], fg, bg, styles: dict) -> Table:
    style = ParagraphStyle("Callout", parent=styles["body"], textColor=fg, fontSize=9)
    rows = [[Paragraph(item, style)] for item in items]
    box = Table(rows, colWidths=[_CONTENT_WIDTH])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.75, fg),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return box


def _dimension_section(dim: dict, styles: dict) -> list:
    name = dim.get("dimension", "")
    label = _DIMENSION_LABELS.get(name, name.replace("_", " ").title())

    story = [
        Paragraph(label, styles["heading"]),
        Paragraph(f"<i>Scope: {markdown_to_reportlab(dim.get('scope_brief'), strip_leading_title=False)}</i>", styles["caption"]),
        Paragraph(markdown_to_reportlab(dim.get("findings"), strip_leading_title=False), styles["body"]),
        Spacer(1, 6),
        _role_days_table(dim.get("role_days") or {}, styles),
        Spacer(1, 6),
    ]

    uplift_signals = dim.get("uplift_signals") or []
    if uplift_signals:
        items = [
            f"<b>+{s['uplift_pct']:.0f}% — {s['signal']}</b>: {markdown_to_reportlab(s['rationale'], strip_leading_title=False)}"
            for s in uplift_signals
        ]
        story.append(_callout_list(items, _INDIGO, _INDIGO_BG, styles))
        story.append(Spacer(1, 6))

    risks = dim.get("risks") or []
    if risks:
        items = [
            f"<b>Risk ({r['likelihood']} likelihood, +{r['impact_days']:.1f}d)</b>: {markdown_to_reportlab(r['description'], strip_leading_title=False)}"
            for r in risks
        ]
        story.append(_callout_list(items, _AMBER, _AMBER_BG, styles))
        story.append(Spacer(1, 6))

    story.append(HRFlowable(width=_CONTENT_WIDTH, color=_GRID, thickness=0.5))
    story.append(Spacer(1, 10))
    return story


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(_SLATE)
    canvas.drawString(_MARGIN, 0.35 * inch, "DataWise · ScopeIQ Estimate · Internal — Delivery Lead / PMO")
    canvas.drawRightString(_PAGE_WIDTH - _MARGIN, 0.35 * inch, f"Page {doc.page}")
    canvas.restoreState()


def generate_scopeiq_estimate_pdf(job: models.Job, estimate: models.ScopeIQEstimate) -> io.BytesIO:
    styles = _styles()

    dimensions_by_name = {d["dimension"]: d for d in (estimate.dimensions or [])}
    ordered_dimensions = [dimensions_by_name[name] for name in _DIMENSION_ORDER if name in dimensions_by_name]

    story = [
        _header_banner(job, estimate, styles),
        Spacer(1, 16),
        Paragraph(
            "<b>Methodology.</b> This estimate is produced by the ScopeIQ agent: the job is decomposed into "
            "four fixed research dimensions (technology stack, compliance &amp; regulatory, integration "
            "patterns, delivery risk) so estimates are comparable across an engagement's full portfolio. "
            "Each dimension is researched independently against this job's actual lineage, transformation, "
            "and inefficiency data, producing a role-level day breakdown, complexity uplift signals, and risk "
            "adjustments — aggregated below into a single delivery-effort estimate.",
            styles["body"],
        ),
        Spacer(1, 12),
        Paragraph("Estimate Summary", styles["heading"]),
        _tier_badge(estimate.complexity_tier, styles),
        Spacer(1, 8),
        _role_days_table(estimate.role_day_totals or {}, styles, total_label="Base Total"),
        Spacer(1, 8),
        _adjustment_summary_table(estimate, styles),
        Spacer(1, 14),
        HRFlowable(width=_CONTENT_WIDTH, color=_GRID, thickness=0.75),
        Spacer(1, 10),
        Paragraph("Research Dimensions", styles["heading"]),
    ]

    for dim in ordered_dimensions:
        story.extend(_dimension_section(dim, styles))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=_PAGE_SIZE,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        title="DataWise ScopeIQ Delivery Estimate",
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer
