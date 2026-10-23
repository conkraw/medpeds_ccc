"""Streamlit app to remove PDF cover/end pages and trim the top margin.

Install with: pip install -r requirements.txt
Run with:     streamlit run app.py
"""

import io
from pathlib import Path

import fitz  # PyMuPDF
import streamlit as st


POINTS_PER_INCH = 72


def trim_pdf(pdf_bytes: bytes, top_inches: float = 1.0) -> bytes:
    """Remove page 1 and the final page, then crop top_inches off every other page.

Pages are copied into a new PDF at their original scale, without rasterizing.
The output is created in memory and the input PDF is left untouched.
    """
    if top_inches < 0:
        raise ValueError("The crop amount cannot be negative.")

    try:
        source = fitz.open(stream=pdf_bytes, filetype="pdf")
    except (fitz.FileDataError, ValueError) as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc

    with source:
        if source.needs_pass:
            raise ValueError("This PDF is password-protected. Please upload an unlocked PDF.")
        if source.page_count < 3:
            raise ValueError("The PDF needs at least 3 pages to remove the first and last.")

        crop_points = top_inches * POINTS_PER_INCH
        for source_index in range(1, source.page_count - 1):
            source_page = source[source_index]
            original = source_page.rect

            if crop_points >= original.height:
                raise ValueError(
                    f"Page {source_index + 1} is too short for a "
                    f"{top_inches:g}-inch top crop."
                )

            # Crop in visible page coordinates, including PDFs whose pages are
            # rotated. Convert back to unrotated page coordinates for cropbox.
            visible_area = fitz.Rect(0, crop_points, original.width, original.height)
            page_crop = visible_area * source_page.derotation_matrix
            offset = source_page.cropbox_position
            page_crop = fitz.Rect(
                page_crop.x0 + offset.x,
                page_crop.y0 + offset.y,
                page_crop.x1 + offset.x,
                page_crop.y1 + offset.y,
            )
            source_page.set_cropbox(page_crop)

        with fitz.open() as output:
            # Retain native PDF text and graphics, plus annotations and links.
            output.insert_pdf(
                source,
                from_page=1,
                to_page=source.page_count - 2,
                links=True,
                annots=True,
                widgets=True,
            )
            return output.tobytes(garbage=4, deflate=True)


def preview_page(pdf_bytes: bytes, page_index: int) -> bytes:
    """Render one processed page at a comfortable on-screen resolution."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as result:
        page = result[page_index]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
        return pixmap.tobytes("png")


def main() -> None:
    st.set_page_config(page_title="PDF Page Trimmer", layout="centered")

    st.title("PDF Page Trimmer")
    st.write(
        "Upload a PDF to remove its first and last pages, then trim the top "
        "of every remaining page. Download the finished PDF below."
    )

    upload = st.file_uploader("Choose a PDF", type=["pdf"])
    crop_inches = st.number_input(
        "Inches to remove from the top of each remaining page",
        min_value=0.0,
        max_value=10.0,
        value=1.0,
        step=0.25,
        format="%.2f",
    )

    if upload is None:
        st.info("Choose a PDF to get started. Nothing will be changed in the original file.")
        return

    uploaded_bytes = upload.getvalue()
    try:
        with st.spinner("Preparing your trimmed PDF..."):
            result_bytes = trim_pdf(uploaded_bytes, crop_inches)
    except ValueError as exc:
        st.error(str(exc))
        return
    except Exception:
        st.error("This PDF could not be processed. Try exporting it as a new PDF first.")
        return

    with fitz.open(stream=result_bytes, filetype="pdf") as result:
        processed_count = result.page_count

    st.success(f"Done. Your output PDF has {processed_count} page(s).")
    output_name = f"{Path(upload.name).stem}_trimmed.pdf"
    st.download_button(
        "Download trimmed PDF",
        data=io.BytesIO(result_bytes),
        file_name=output_name,
        mime="application/pdf",
        type="primary",
        use_container_width=True,
    )

    st.subheader("Preview")
    page_number = st.selectbox(
        "Processed page",
        options=list(range(1, processed_count + 1)),
        format_func=lambda number: f"Page {number}",
    )
    st.image(preview_page(result_bytes, page_number - 1), use_container_width=True)
    st.caption(
        "Pages stay at their original scale. PDF links and annotations are "
        "retained where supported. Cropping hides the top area, but is not secure "
        "redaction. Files are processed in memory."
    )


if __name__ == "__main__":
    main()
