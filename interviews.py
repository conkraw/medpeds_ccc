"""Streamlit interview PDF preparation tool.

Run with:
    pip install -r requirements.txt
    streamlit run interviews.py

The uploaded PDF and all generated results are processed in memory.
"""

from dataclasses import dataclass
from html import escape
import io
from pathlib import Path
import re

import fitz  # PyMuPDF
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)
import streamlit as st


POINTS_PER_INCH = 72
CLERKSHIPS = ("Family Practice", "Internal Medicine", "Pediatrics")


@dataclass(frozen=True)
class TrimResult:
    pdf_bytes: bytes
    source_pages_kept: int
    certification_page: int | None  # One-based page number in original PDF
    certification_section_cropped: bool


def _certification_heading_y(page: fitz.Page) -> float | None:
    """Find the first whole-word 'Certification' in selectable page text."""
    matches = []
    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        normalized = re.sub(r"^[^a-zA-Z]+|[^a-zA-Z]+$", "", word)
        if normalized.casefold() == "certification":
            # Text extraction boxes are unrotated, while crop positions are
            # measured from the top of the page as it appears in a viewer.
            visual_box = fitz.Rect(x0, y0, x1, y1) * page.rotation_matrix
            matches.append(float(visual_box.y0))
    return min(matches) if matches else None


def trim_pdf(pdf_bytes: bytes, top_inches: float = 1.0) -> TrimResult:
    """Remove cover/end pages, top margin, and the final Certification section.

    Searches only the final two original pages for the Certification heading.
    If found on the penultimate page, content from the heading onward is hidden.
    If found on the last page, the usual last-page removal handles it.
    """
    if top_inches < 0:
        raise ValueError("The top crop cannot be negative.")

    try:
        source = fitz.open(stream=pdf_bytes, filetype="pdf")
    except (fitz.FileDataError, ValueError) as exc:
        raise ValueError("Please upload a readable PDF file.") from exc

    with source:
        if source.needs_pass:
            raise ValueError("This PDF is password-protected. Upload an unlocked copy.")
        if source.page_count < 3:
            raise ValueError("The PDF must have at least 3 pages.")

        crop_points = top_inches * POINTS_PER_INCH

        # Check the final two pages BEFORE removing the original last page.
        certification_page = None
        certification_y = None
        for page_index in range(source.page_count - 2, source.page_count):
            heading_y = _certification_heading_y(source[page_index])
            if heading_y is not None:
                certification_page = page_index + 1
                certification_y = heading_y
                break

        first_kept = 1
        last_kept = source.page_count - 2
        certification_cropped = False

        for source_index in range(first_kept, last_kept + 1):
            source_page = source[source_index]
            original = source_page.rect

            if crop_points >= original.height:
                raise ValueError(
                    f"Page {source_index + 1} is too short for a "
                    f"{top_inches:g}-inch top crop."
                )

            bottom = original.height
            if certification_page == source_index + 1 and certification_y is not None:
                certification_cropped = True
                # End just above the heading, removing the heading and all
                # content beneath it, including text on the same line.
                bottom = max(0.0, certification_y - 1.0)

                if bottom <= crop_points:
                    # Certification begins before any useful content on this
                    # page. Drop the whole penultimate page instead.
                    last_kept = source_index - 1
                    break

            visible_area = fitz.Rect(0, crop_points, original.width, bottom)

            # Crop in visual coordinates even if a source page is rotated or
            # has an existing offset cropbox.
            page_crop = visible_area * source_page.derotation_matrix
            offset = source_page.cropbox_position
            page_crop = fitz.Rect(
                page_crop.x0 + offset.x,
                page_crop.y0 + offset.y,
                page_crop.x1 + offset.x,
                page_crop.y1 + offset.y,
            )
            source_page.set_cropbox(page_crop)

            # Some PDF renderers display the MediaBox rather than the CropBox.
            # Match the physical page boundary to the visible crop so both
            # types of viewers show the same trimmed page. PyMuPDF's CropBox
            # uses top-left coordinates; MediaBox uses PDF bottom-left ones.
            media = source_page.mediabox
            visible_crop = source_page.cropbox
            source_page.set_mediabox(
                fitz.Rect(
                    media.x0 + visible_crop.x0,
                    media.y1 - visible_crop.y1,
                    media.x0 + visible_crop.x1,
                    media.y1 - visible_crop.y0,
                )
            )

        if last_kept < first_kept:
            raise ValueError(
                "No PDF content remains after removing the cover, ending, "
                "and Certification section."
            )

        with fitz.open() as output:
            output.insert_pdf(
                source,
                from_page=first_kept,
                to_page=last_kept,
                links=True,
                annots=True,
                widgets=True,
            )
            return TrimResult(
                pdf_bytes=output.tobytes(garbage=4, deflate=True),
                source_pages_kept=output.page_count,
                certification_page=certification_page,
                certification_section_cropped=certification_cropped,
            )


def _text_for_pdf(value: str) -> str:
    """Escape free text so punctuation and line breaks render safely."""
    return escape(value.strip()).replace("\n", "<br/>")


def build_grade_summary(
    applicant_name: str,
    grades: dict[str, str],
    comments: dict[str, str],
) -> bytes:
    """Create a selectable-text PDF with grade and comment blocks."""
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.7 * inch,
        leftMargin=0.7 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        title="Interview Clerkship Grades and Comments",
    )
    title_style = ParagraphStyle(
        "InterviewTitle",
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=22,
        textColor=colors.HexColor("#18344F"),
        spaceAfter=12,
    )
    name_style = ParagraphStyle(
        "Applicant",
        fontName="Helvetica",
        fontSize=10.5,
        leading=15,
        textColor=colors.HexColor("#4A5C6B"),
        spaceAfter=13,
    )
    heading_style = ParagraphStyle(
        "ClerkshipHeading",
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#18344F"),
        spaceAfter=5,
    )
    body_style = ParagraphStyle(
        "ClerkshipBody",
        fontName="Helvetica",
        fontSize=10.5,
        leading=15.5,
        alignment=TA_LEFT,
        spaceAfter=6,
        splitLongWords=True,
    )
    label_style = ParagraphStyle(
        "ClerkshipLabel",
        parent=body_style,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#576B7D"),
        spaceAfter=3,
    )

    story = [Paragraph("Clerkship Grades and Comments", title_style)]
    if applicant_name.strip():
        story.append(
            Paragraph(f"Applicant: {_text_for_pdf(applicant_name)}", name_style)
        )

    for subject in CLERKSHIPS:
        grade = _text_for_pdf(grades.get(subject, "")) or "Not entered"
        comment_text = _text_for_pdf(comments.get(subject, ""))
        story.append(
            KeepTogether(
                [
                    Paragraph(subject, heading_style),
                    Paragraph(f"Grade: {grade}", body_style),
                    Paragraph("Comments", label_style),
                ]
            )
        )
        if comment_text:
            story.append(Paragraph(comment_text, body_style))
        else:
            story.append(Paragraph("No comments entered.", body_style))
        story.append(Spacer(1, 7))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#D7E0E9")))
        story.append(Spacer(1, 12))

    document.build(story)
    return buffer.getvalue()


def merge_pdfs(trimmed_pdf: bytes, summary_pdf: bytes | None, summary_first: bool) -> bytes:
    """Merge the generated summary and trimmed PDF, preserving vector text."""
    if summary_pdf is None:
        return trimmed_pdf

    with fitz.open(stream=trimmed_pdf, filetype="pdf") as cleaned:
        with fitz.open(stream=summary_pdf, filetype="pdf") as summary:
            with fitz.open() as combined:
                if summary_first:
                    combined.insert_pdf(summary)
                    combined.insert_pdf(cleaned, links=True, annots=True, widgets=True)
                else:
                    combined.insert_pdf(cleaned, links=True, annots=True, widgets=True)
                    combined.insert_pdf(summary)
                return combined.tobytes(garbage=4, deflate=True)


def preview_pdf_page(pdf_bytes: bytes, page_number: int) -> bytes:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        pixmap = pdf[page_number].get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False)
        return pixmap.tobytes("png")


def main() -> None:
    st.set_page_config(page_title="Interview PDF Preparation", layout="wide")
    st.title("Interview PDF Preparation")
    st.write(
        "Remove the first and last pages, trim the top margin, and remove the "
        "Certification section near the end. Enter clerkship grades and comments "
        "to add a summary to the finished PDF."
    )

    uploaded_file = st.file_uploader("Upload applicant PDF", type=["pdf"])
    applicant_name = st.text_input("Applicant name (optional)", placeholder="Name for summary PDF")

    st.subheader("Clerkship grades and comments")
    grades = {}
    comments = {}
    columns = st.columns(3)
    for column, subject in zip(columns, CLERKSHIPS):
        with column:
            st.markdown(f"#### {subject}")
            grades[subject] = st.text_input(
                f"{subject} grade",
                placeholder="Honors, High Pass, Pass...",
                key=f"grade_{subject}",
            )
            comments[subject] = st.text_area(
                f"{subject} comments",
                placeholder="Enter clerkship comments here",
                height=155,
                key=f"comments_{subject}",
            )

    st.subheader("PDF options")
    crop_inches = st.number_input(
        "Trim this many inches from the top of remaining PDF pages",
        min_value=0.0,
        max_value=10.0,
        value=1.0,
        step=0.25,
        format="%.2f",
    )
    summary_position = st.radio(
        "Place the grade/comment summary",
        options=("At the beginning", "At the end"),
        horizontal=True,
    )

    if uploaded_file is None:
        st.info("Upload a PDF to prepare your document.")
        return

    include_summary = bool(
        applicant_name.strip()
        or any(value.strip() for value in grades.values())
        or any(value.strip() for value in comments.values())
    )

    try:
        with st.spinner("Preparing your interview PDF..."):
            trimmed = trim_pdf(uploaded_file.getvalue(), crop_inches)
            summary = (
                build_grade_summary(applicant_name, grades, comments)
                if include_summary
                else None
            )
            final_pdf = merge_pdfs(
                trimmed.pdf_bytes,
                summary,
                summary_first=(summary_position == "At the beginning"),
            )
    except ValueError as exc:
        st.error(str(exc))
        return
    except Exception:
        st.error("This file could not be processed. Try exporting a new PDF and uploading it again.")
        return

    with fitz.open(stream=final_pdf, filetype="pdf") as finished:
        output_pages = finished.page_count

    st.success(f"Finished PDF: {output_pages} page(s).")
    if not include_summary:
        st.caption("No grade, comment, or name was entered, so no summary page was added.")

    if trimmed.certification_page is None:
        st.warning(
            "No selectable 'Certification' heading was found on the last two "
            "original pages. The last page was still removed. Review the preview "
            "if the PDF was scanned."
        )
    elif trimmed.certification_section_cropped:
        st.info(
            f"Removed content beginning at 'Certification' on original "
            f"page {trimmed.certification_page}."
        )
    else:
        st.info(
            f"Found 'Certification' on original page {trimmed.certification_page}, "
            "which was removed as the last page."
        )

    output_name = f"{Path(uploaded_file.name).stem}_interview.pdf"
    st.download_button(
        "Download interview PDF",
        data=io.BytesIO(final_pdf),
        file_name=output_name,
        mime="application/pdf",
        type="primary",
        use_container_width=True,
    )

    st.subheader("Preview final document")
    preview_number = st.selectbox(
        "Page to preview",
        options=list(range(1, output_pages + 1)),
        format_func=lambda number: f"Page {number} of {output_pages}",
    )
    st.image(preview_pdf_page(final_pdf, preview_number - 1), use_container_width=True)
    st.caption(
        "Cropping hides content but is not secure redaction. Text-based PDF "
        "Certification headings are detected automatically. Files are processed "
        "in memory."
    )


if __name__ == "__main__":
    main()
