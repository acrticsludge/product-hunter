"""Score the extractor against a hand-labelled review sheet.

The model proposes candidates and a human labels them. This module turns that
sheet into numbers: quote fidelity, precision and recall, severity agreement,
normalization agreement, and the reasons behind false positives.

Recall here is relative to the candidates the model returned. A pain point the
model never proposed cannot be counted as a miss, so the report says so.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from product_hunter.extraction import (
    ExtractedCandidate,
    Frequency,
    FrequencyField,
    PainReason,
    PainValidity,
    ReasonField,
    ValidityField,
)
from product_hunter.quote import MatchMode

# The columns of the review sheet, in order. The first half is what the
# extractor produced; the second half is what the human adds.
COLUMNS = (
    "candidate_id",
    "pain_id",
    "doc_id",
    "source_id",
    "quote",
    "char_start",
    "char_end",
    "match_mode",
    "verbatim_ok",
    "model_validity",
    "model_reason",
    "model_severity",
    "model_frequency",
    "model_confidence",
    "model_domain",
    "model_normalized_key",
    "human_valid_pain",
    "human_severity",
    "human_frequency",
    "human_normalization_ok",
    "human_normalized_key",
    "notes",
)

# The columns a sheet may leave out. A sheet filled before the normalization
# columns existed still reads: the missing cells count as blank.
OPTIONAL_COLUMNS = (
    "model_domain",
    "model_normalized_key",
    "human_normalization_ok",
    "human_normalized_key",
)
REQUIRED_COLUMNS = tuple(
    name for name in COLUMNS if name not in OPTIONAL_COLUMNS
)

# What a human may write in the verdict column.
HUMAN_VALIDITY = ("yes", "no", "weak")
# The frequencies a human may pick: the same set the model uses.
FREQUENCIES = tuple(value.value for value in Frequency)
# The reasons the model may give.
REASONS = tuple(value.value for value in PainReason)
# The validities the model may give.
VALIDITIES = tuple(value.value for value in PainValidity)
# The quote match modes a row may carry.
MATCH_MODES = ("exact", "canonical")
# The answers a human may give to "did the model normalize this correctly?".
NORMALIZATION_LABELS = ("yes", "no")

RECALL_LIMITATION = (
    "Recall is relative to the candidates the model returned, not to the whole "
    "corpus: a pain point the model never proposed cannot be counted as a miss."
)


class ReviewRow(BaseModel):
    """One row of the review sheet: the model's verdict and the human's."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    pain_id: str | None = None
    doc_id: str
    source_id: str
    quote: str
    char_start: int
    char_end: int
    match_mode: MatchMode
    verbatim_ok: bool
    model_validity: ValidityField
    model_reason: ReasonField
    model_severity: int = Field(ge=0, le=5)
    model_frequency: FrequencyField
    model_confidence: float = Field(ge=0, le=1)
    model_domain: str | None = None
    model_normalized_key: str | None = None
    human_valid_pain: str | None = None
    human_severity: int | None = Field(default=None, ge=0, le=5)
    human_frequency: str | None = None
    human_normalization_ok: str | None = None
    human_normalized_key: str | None = None
    notes: str = ""


class EvalReport(BaseModel):
    """The numbers a filled review sheet produces."""

    total: int
    labeled: int
    coverage: float
    quote_exact: int
    quote_canonical: int
    quote_failed: int
    quote_ok_rate: float | None
    model_genuine: int
    true_positive: int
    false_positive: int
    false_negative: int
    weak: int
    precision: float | None
    recall: float | None
    severity_n: int
    severity_exact: int
    severity_within_one: int
    normalization_n: int = 0
    normalization_ok: int = 0
    normalization_rate: float | None = None
    normalized_key_n: int = 0
    normalized_key_exact: int = 0
    normalized_key_agreement: float | None = None
    false_positive_reasons: dict[str, int] = Field(default_factory=dict)
    per_source: dict[str, dict[str, int | float | None]] = Field(default_factory=dict)
    notes: str = ""


# --- the review sheet ------------------------------------------------------


def write_review_sheet(
    candidates: list[ExtractedCandidate], path: Path, limit: int = 100
) -> int:
    """Write a review sheet and return how many rows it holds.

    Every accepted candidate is in the sheet, because those are the ones the
    model claims are real pain points. The rest of the sheet is filled with
    candidates the model did not accept, so the human also sees the text that
    only sounds negative. When there are more candidates than the limit, the
    accepted candidates take the room first, up to the limit, and the rest is
    filled with the others. When the accepted candidates alone fill the limit,
    the overflow is not included.
    """
    accepted, others = _select(candidates, limit)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for candidate in accepted + others:
            writer.writerow(_values(_row(candidate)))
    return len(accepted) + len(others)


def read_review_sheet(path: Path) -> list[ReviewRow]:
    """Read a filled review sheet. A malformed row is refused, not guessed at."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        missing = [name for name in REQUIRED_COLUMNS if name not in header]
        if missing:
            raise ValueError(
                f"{path}: the sheet is missing the column(s) {', '.join(missing)}"
            )
        rows = []
        # The number is the data row, counted from one under the header, so
        # it stays right even when a quote carries a line break.
        for number, raw in enumerate(reader, start=1):
            if raw.get(None) is not None:
                raise ValueError(
                    f"{path}: row {number} has more values than the header has columns"
                )
            rows.append(_row_from_raw(raw, number, path))
    return rows


def _select(
    candidates: list[ExtractedCandidate], limit: int
) -> tuple[list[ExtractedCandidate], list[ExtractedCandidate]]:
    """Split the candidates into the ones to review and the ones to fill with.

    The accepted candidates take the room first, up to the limit, and the
    rest of the room is filled with the others. When the accepted candidates
    alone fill the limit, the overflow is not included.
    """
    limit = max(0, limit)
    accepted = sorted(
        (c for c in candidates if _is_accepted(c)), key=_sort_key
    )
    others = sorted((c for c in candidates if not _is_accepted(c)), key=_sort_key)
    taken_accepted = accepted[:limit]
    return taken_accepted, others[: limit - len(taken_accepted)]


def _is_accepted(candidate: ExtractedCandidate) -> bool:
    """A candidate is accepted when it is stored as a genuine pain point."""
    return candidate.accepted or candidate.pain_validity is PainValidity.genuine


def _sort_key(candidate: ExtractedCandidate) -> tuple[str, str, str]:
    return (candidate.source_id, candidate.doc_id, candidate.candidate_id)


def _row(candidate: ExtractedCandidate) -> ReviewRow:
    """Build the review row of one candidate. The human columns stay empty."""
    return ReviewRow(
        candidate_id=candidate.candidate_id,
        pain_id=candidate.pain_id,
        doc_id=candidate.doc_id,
        source_id=candidate.source_id,
        quote=candidate.quote,
        char_start=candidate.char_start,
        char_end=candidate.char_end,
        match_mode=candidate.match_mode,
        # A stored candidate already carries a verbatim span.
        verbatim_ok=True,
        model_validity=candidate.pain_validity,
        model_reason=candidate.reason,
        model_severity=candidate.severity,
        model_frequency=candidate.frequency,
        model_confidence=candidate.confidence,
        model_domain=candidate.domain,
        model_normalized_key=candidate.normalized_key,
    )


def _values(row: ReviewRow) -> dict[str, object]:
    """The CSV cell values of one review row."""
    return {
        "candidate_id": row.candidate_id,
        "pain_id": row.pain_id or "",
        "doc_id": row.doc_id,
        "source_id": row.source_id,
        "quote": row.quote,
        "char_start": row.char_start,
        "char_end": row.char_end,
        "match_mode": row.match_mode,
        "verbatim_ok": "true" if row.verbatim_ok else "false",
        "model_validity": row.model_validity.value,
        "model_reason": row.model_reason.value,
        "model_severity": row.model_severity,
        "model_frequency": row.model_frequency.value,
        "model_confidence": row.model_confidence,
        "model_domain": row.model_domain or "",
        "model_normalized_key": row.model_normalized_key or "",
        "human_valid_pain": row.human_valid_pain or "",
        "human_severity": "" if row.human_severity is None else row.human_severity,
        "human_frequency": row.human_frequency or "",
        "human_normalization_ok": row.human_normalization_ok or "",
        "human_normalized_key": row.human_normalized_key or "",
        "notes": row.notes,
    }


def _row_from_raw(raw: dict[str, str | None], number: int, path: Path) -> ReviewRow:
    """Turn one CSV row into a review row, or refuse it."""

    def cell(name: str) -> str:
        value = raw.get(name)
        if value is None:
            raise ValueError(f"{path}: row {number} has no value for {name!r}")
        return value

    def required(name: str) -> str:
        value = cell(name).strip()
        if not value:
            raise ValueError(f"{path}: row {number} has an empty {name!r}")
        return value

    def choice(name: str, allowed: tuple[str, ...]) -> str:
        value = cell(name).strip()
        if value not in allowed:
            raise ValueError(
                f"{path}: row {number} has {name}={value!r}, "
                f"expected one of {', '.join(allowed)}"
            )
        return value

    def integer(name: str) -> int:
        text = cell(name).strip()
        try:
            return int(text)
        except ValueError:
            raise ValueError(
                f"{path}: row {number} has {name}={text!r}, expected an integer"
            ) from None

    def decimal(name: str) -> float:
        text = cell(name).strip()
        try:
            return float(text)
        except ValueError:
            raise ValueError(
                f"{path}: row {number} has {name}={text!r}, expected a number"
            ) from None

    human_validity = cell("human_valid_pain").strip().lower()
    if human_validity and human_validity not in HUMAN_VALIDITY:
        raise ValueError(
            f"{path}: row {number} has human_valid_pain={human_validity!r}, "
            f"expected one of {', '.join(HUMAN_VALIDITY)}"
        )
    human_severity_text = cell("human_severity").strip()
    human_severity = None
    if human_severity_text:
        try:
            human_severity = int(human_severity_text)
        except ValueError:
            raise ValueError(
                f"{path}: row {number} has human_severity={human_severity_text!r}, "
                "expected an integer"
            ) from None
        if not 0 <= human_severity <= 5:
            raise ValueError(
                f"{path}: row {number} has human_severity={human_severity}, "
                "expected 0 to 5"
            )
    human_frequency = cell("human_frequency").strip().lower()
    if human_frequency and human_frequency not in FREQUENCIES:
        raise ValueError(
            f"{path}: row {number} has human_frequency={human_frequency!r}, "
            f"expected one of {', '.join(FREQUENCIES)}"
        )

    # The normalization columns are optional: a sheet filled before they
    # existed still reads, and the missing cells count as blank.
    model_domain = (raw.get("model_domain") or "").strip()
    model_normalized_key = (raw.get("model_normalized_key") or "").strip()
    human_normalization_ok = (raw.get("human_normalization_ok") or "").strip().lower()
    if human_normalization_ok and human_normalization_ok not in NORMALIZATION_LABELS:
        raise ValueError(
            f"{path}: row {number} has "
            f"human_normalization_ok={human_normalization_ok!r}, "
            f"expected one of {', '.join(NORMALIZATION_LABELS)}"
        )
    human_normalized_key = (raw.get("human_normalized_key") or "").strip()

    values = {
        "candidate_id": required("candidate_id"),
        "pain_id": cell("pain_id").strip() or None,
        "doc_id": required("doc_id"),
        "source_id": required("source_id"),
        "quote": required("quote"),
        "char_start": integer("char_start"),
        "char_end": integer("char_end"),
        "match_mode": choice("match_mode", MATCH_MODES),
        "verbatim_ok": _boolean(cell("verbatim_ok"), number, path),
        "model_validity": choice("model_validity", VALIDITIES),
        "model_reason": choice("model_reason", REASONS),
        "model_severity": integer("model_severity"),
        "model_frequency": choice("model_frequency", FREQUENCIES),
        "model_confidence": decimal("model_confidence"),
        "model_domain": model_domain or None,
        "model_normalized_key": model_normalized_key or None,
        "human_valid_pain": human_validity or None,
        "human_severity": human_severity,
        "human_frequency": human_frequency or None,
        "human_normalization_ok": human_normalization_ok or None,
        "human_normalized_key": human_normalized_key or None,
        "notes": cell("notes"),
    }
    try:
        return ReviewRow(**values)
    except ValidationError as exc:
        raise ValueError(
            f"{path}: row {number} is not a valid review row: {exc}"
        ) from exc


def _boolean(text: str, number: int, path: Path) -> bool:
    """Read a CSV cell as a boolean."""
    value = text.strip().lower()
    if value in ("true", "yes", "1"):
        return True
    if value in ("false", "no", "0"):
        return False
    raise ValueError(
        f"{path}: row {number} has verbatim_ok={text!r}, expected true or false"
    )


def _filled(value: str | None) -> bool:
    """A label counts as filled in when it carries something but whitespace."""
    return bool(value and value.strip())


def _answer(value: str | None) -> str:
    """A human answer, folded to its comparable form."""
    return (value or "").strip().lower()


# --- the report ------------------------------------------------------------


def evaluate(rows: list[ReviewRow], *, quote_failures: int = 0) -> EvalReport:
    """Score a filled review sheet.

    ``quote_failures`` counts quotes the extractor never produced, so the
    failure rate covers the rejected spans as well as the stored ones.
    """
    total = len(rows)
    labeled_rows = [row for row in rows if row.human_valid_pain is not None]
    labeled = len(labeled_rows)
    coverage = labeled / total if total else 0.0

    quote_exact = sum(1 for row in rows if row.match_mode == "exact")
    quote_canonical = sum(1 for row in rows if row.match_mode == "canonical")
    failed = quote_failures + sum(1 for row in rows if not row.verbatim_ok)
    ok = quote_exact + quote_canonical
    quote_ok_rate = ok / (ok + failed) if ok + failed else None

    genuine = [
        row for row in labeled_rows if row.model_validity is PainValidity.genuine
    ]
    true_positive = sum(1 for row in genuine if row.human_valid_pain == "yes")
    false_positive = sum(1 for row in genuine if row.human_valid_pain in ("no", "weak"))
    false_negative = sum(
        1
        for row in labeled_rows
        if row.model_validity is not PainValidity.genuine
        and row.human_valid_pain == "yes"
    )
    weak = sum(1 for row in rows if row.human_valid_pain == "weak")

    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else None
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else None
    )

    severity_rows = [
        row
        for row in genuine
        if row.human_valid_pain == "yes" and row.human_severity is not None
    ]
    severity_exact = sum(
        1 for row in severity_rows if row.human_severity == row.model_severity
    )
    severity_within_one = sum(
        1 for row in severity_rows if abs(row.human_severity - row.model_severity) <= 1
    )

    reasons = Counter(
        row.model_reason.value
        for row in genuine
        if row.human_valid_pain in ("no", "weak")
    )

    # A normalization label only counts on a pain point the model called
    # genuine and the human confirmed: on anything else there was no
    # normalized key to judge.
    normalization_rows = [
        row
        for row in labeled_rows
        if row.model_validity is PainValidity.genuine
        and row.human_valid_pain == "yes"
        and _filled(row.human_normalization_ok)
    ]
    normalization_n = len(normalization_rows)
    normalization_ok = sum(
        1
        for row in normalization_rows
        if _answer(row.human_normalization_ok) == "yes"
    )
    normalization_rate = normalization_ok / normalization_n if normalization_n else None

    # A key is compared only where both sides carry one. A row the model
    # called weak or not_pain has no model key, so there is nothing for the
    # human key to agree or disagree with.
    key_rows = [
        row
        for row in rows
        if _filled(row.model_normalized_key) and _filled(row.human_normalized_key)
    ]
    normalized_key_n = len(key_rows)
    normalized_key_exact = sum(
        1
        for row in key_rows
        if (row.model_normalized_key or "").strip() == row.human_normalized_key.strip()
    )
    normalized_key_agreement = (
        normalized_key_exact / normalized_key_n if normalized_key_n else None
    )

    return EvalReport(
        total=total,
        labeled=labeled,
        coverage=coverage,
        quote_exact=quote_exact,
        quote_canonical=quote_canonical,
        quote_failed=failed,
        quote_ok_rate=quote_ok_rate,
        model_genuine=len(genuine),
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        weak=weak,
        precision=precision,
        recall=recall,
        severity_n=len(severity_rows),
        severity_exact=severity_exact,
        severity_within_one=severity_within_one,
        normalization_n=normalization_n,
        normalization_ok=normalization_ok,
        normalization_rate=normalization_rate,
        normalized_key_n=normalized_key_n,
        normalized_key_exact=normalized_key_exact,
        normalized_key_agreement=normalized_key_agreement,
        false_positive_reasons=dict(reasons),
        per_source=_per_source(rows),
        notes=(
            f"{RECALL_LIMITATION} Coverage is the share of sheet rows that "
            "carry a human verdict, so an unlabelled row counts for nothing."
        ),
    )


def _per_source(rows: list[ReviewRow]) -> dict[str, dict[str, int | float | None]]:
    """Count the reviewed rows per source."""
    per_source: dict[str, dict[str, int | float | None]] = {}
    for source_id in sorted({row.source_id for row in rows}):
        source_rows = [row for row in rows if row.source_id == source_id]
        labeled = [row for row in source_rows if row.human_valid_pain is not None]
        true_positive = sum(
            1
            for row in labeled
            if row.model_validity is PainValidity.genuine
            and row.human_valid_pain == "yes"
        )
        false_positive = sum(
            1
            for row in labeled
            if row.model_validity is PainValidity.genuine
            and row.human_valid_pain in ("no", "weak")
        )
        denominator = true_positive + false_positive
        per_source[source_id] = {
            "total": len(source_rows),
            "labeled": len(labeled),
            "tp": true_positive,
            "fp": false_positive,
            "precision": true_positive / denominator if denominator else None,
        }
    return per_source


# --- the markdown report ---------------------------------------------------


def render_markdown(report: EvalReport) -> str:
    """Render the report as markdown."""
    lines: list[str] = []
    lines.append("# Extraction evaluation")
    lines.append("")
    lines.append(_summary(report))
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    for name, value in (
        ("Candidates in the sheet", report.total),
        ("Labelled", report.labeled),
        ("Coverage", _percent(report.coverage)),
        ("Quote matched exactly", report.quote_exact),
        ("Quote matched canonically", report.quote_canonical),
        ("Quote failed", report.quote_failed),
        ("Quote ok rate", _percent(report.quote_ok_rate)),
        ("Model called genuine", report.model_genuine),
        ("True positives", report.true_positive),
        ("False positives", report.false_positive),
        ("False negatives", report.false_negative),
        ("Marked weak", report.weak),
        ("Precision", _decimal(report.precision)),
        ("Recall", _decimal(report.recall)),
    ):
        lines.append(f"| {name} | {value} |")
    lines.append("")
    lines.append("## Severity")
    lines.append("")
    lines.append(
        "Only candidates the model called genuine, the human confirmed, and the "
        "human gave a severity are compared."
    )
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Compared | {report.severity_n} |")
    lines.append(f"| Exact match | {report.severity_exact} |")
    lines.append(f"| Within one point | {report.severity_within_one} |")
    lines.append("")
    lines.append("## Normalization")
    lines.append("")
    if report.normalization_n == 0:
        lines.append(
            "The rows are not labelled for normalization, so no rate is shown."
        )
        lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Rows compared | {report.normalization_n} |")
    lines.append(f"| Normalized correctly | {report.normalization_ok} |")
    lines.append(f"| Normalization rate | {_percent(report.normalization_rate)} |")
    lines.append(f"| Keys compared | {report.normalized_key_n} |")
    lines.append(f"| Keys identical | {report.normalized_key_exact} |")
    lines.append(f"| Key agreement | {_percent(report.normalized_key_agreement)} |")
    lines.append("")
    lines.append("## False-positive reasons")
    lines.append("")
    if report.false_positive_reasons:
        lines.append("| Reason | Count |")
        lines.append("| --- | --- |")
        for reason, count in sorted(
            report.false_positive_reasons.items(), key=lambda item: (-item[1], item[0])
        ):
            lines.append(f"| {reason} | {count} |")
    else:
        lines.append("No false positives were recorded.")
    lines.append("")
    lines.append("## Per source")
    lines.append("")
    if report.per_source:
        lines.append(
            "| Source | Candidates | Labelled | True positives | False positives | "
            "Precision |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for source_id, stats in report.per_source.items():
            lines.append(
                f"| {source_id} | {stats['total']} | {stats['labeled']} | "
                f"{stats['tp']} | {stats['fp']} | {_decimal(stats['precision'])} |"
            )
    else:
        lines.append("No source was reviewed.")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(report.notes)
    lines.append("")
    return "\n".join(lines)


def write_report(report: EvalReport, path: Path) -> None:
    """Write the markdown report, creating the parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8", newline="\n")


def _summary(report: EvalReport) -> str:
    return (
        f"The sheet holds {_count(report.total, 'candidate', 'candidates')}, of "
        f"which {_count(report.labeled, 'row carries', 'rows carry')} a human "
        f"verdict, so coverage is {report.coverage * 100:.1f}%. The model called "
        f"{_count(report.model_genuine, 'reviewed candidate', 'reviewed candidates')} "
        f"genuine: {_count(report.true_positive, 'was', 'were')} confirmed as a "
        f"pain point, {_count(report.false_positive, 'was', 'were')} not. "
        f"{_count(report.false_negative, 'candidate', 'candidates')} the model did "
        f"not call genuine {_count(report.false_negative, 'was', 'were')} confirmed "
        f"as a pain point. "
        f"{_count(report.weak, 'candidate was', 'candidates were')} marked weak."
    )


def _count(count: int, singular: str, plural: str) -> str:
    """A count in the right number."""
    return f"{count} {singular if count == 1 else plural}"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _decimal(value: float | int | None) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"
