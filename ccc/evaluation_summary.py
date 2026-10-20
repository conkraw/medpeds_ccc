"""Lossless narrative rows for the CCC evaluation-summary tables.

The report caller selects the resident, specialty, category, and reporting
window. This module formats already selected evaluations or self-assessments;
it never combines separate evaluations or calculates ratings.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from ccc import model as m


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _is_convenience_text(text: str, comments: list[str], overall: list[str]) -> bool:
    """Recognize complete copies of structured comments, never remove substrings.

    NI importers retain comments in their original question fields and also put
    a convenience concatenation in ``record.text``. Repeated question comments
    and repeated paragraphs are still rendered from the original fields.
    """
    fields = [value for value in [*comments, *overall] if value]
    if not text or not fields:
        return False
    # The current importer deduplicates complete comment fields. Older records
    # may contain a straight concatenation or separate overall-comment parts.
    overall_parts = [part for value in overall for part in re.split(r"\n\s*\n", value) if part]
    candidates = {
        "\n\n".join(fields),
        "\n\n".join(_unique(fields)),
        "\n\n".join(_unique([*comments, *overall_parts])),
    }
    return text in candidates


def _narrative(record: dict) -> str:
    questions = record.get("questions") if isinstance(record.get("questions"), list) else []
    questions = [question for question in questions if isinstance(question, dict)]
    values = record.get("values") if isinstance(record.get("values"), dict) else {}
    overall = _unique([_text(record.get("overall_comment")), _text(values.get("overall_comments"))])
    comments = [_text(question.get("comment")) for question in questions if _text(question.get("comment"))]
    text = _text(record.get("text"))
    blocks = []

    if text and not _is_convenience_text(text, comments, overall):
        blocks.append(text)

    for position, question in enumerate(questions, 1):
        number = _text(question.get("number")) or str(position)
        prompt = _text(question.get("text"))
        response = _text(question.get("response"))
        comment = _text(question.get("comment"))
        if not any((prompt, response, comment)):
            continue
        lines = [f"Question {number}: {prompt}" if prompt else f"Question {number}"]
        if response:
            lines.append(f"Response: {response}")
        if comment:
            lines.append(f"Comment: {comment}")
        blocks.append("\n".join(lines))

    for value in overall:
        blocks.append(f"Overall comments: {value}")

    # Generic long exports may supply a source value instead of question fields.
    # Keep that value as source data, while excluding the importer's count=1.
    metric = _text(record.get("metric"))
    unit = _text(record.get("unit"))
    source_value = _text(record.get("value"))
    count_units = {"assessment", "assessments", "count", "counts", "evaluation", "evaluations"}
    if source_value and unit.casefold() not in count_units:
        label = f"Source value ({metric})" if metric else "Source value"
        blocks.append(f"{label}: {source_value}" + (f" {unit}" if unit else ""))

    return "\n\n".join(blocks)


def evaluation_rows(records: list[dict]) -> list[dict]:
    """Return chronological metadata and complete narrative for each assessment.

    The caller must supply the intended resident, specialty, and date window.
    Existing order is retained for same-date records. Missing or invalid dates
    sort last without dropping the entry. Empty metadata/narrative remain empty
    so the table renderer can preserve its own missing-data presentation.
    """
    selected = [record for record in records if record.get("category") in {"evaluations", "self_assessments"}]
    ordered = sorted(selected, key=lambda record: m.parse_date(record.get("date")) or date.max)
    rows = []
    for record in ordered:
        evaluator = _text(record.get("evaluator"))
        status = _text(record.get("evaluator_status"))
        if status and not evaluator.casefold().endswith(f"[{status}]".casefold()):
            evaluator = f"{evaluator} [{status}]".strip()
        rows.append({
            "date": _text(record.get("date")),
            "rotation": _text(record.get("rotation")),
            "evaluator": evaluator,
            "narrative": _narrative(record),
            "source_locator": _text(record.get("source_locator")),
            "source_name": _text(record.get("source_name")),
            "form": _text(record.get("form")),
        })
    return rows
