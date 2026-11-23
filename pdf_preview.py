"""Convert an app-generated Word report into a temporary, private PDF preview.

Only the returned bytes are retained. Each conversion has its own LibreOffice
profile and temporary directories, and never calls an external conversion API.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import zipfile


# Leave up to five seconds to reap a timed-out process after sending SIGKILL.
_CONVERSION_TIMEOUT_SECONDS = 55
_PROCESS_CLEANUP_SECONDS = 5


class PreviewError(ValueError):
    """A safe, user-facing explanation for an unavailable PDF preview."""


def _validate_docx(data: bytes) -> None:
    if not isinstance(data, bytes) or not data:
        raise PreviewError("Prepare the Word report again before creating a PDF preview.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if not {"[Content_Types].xml", "word/document.xml"}.issubset(archive.namelist()):
                raise ValueError("Not a Word document")
    except (OSError, ValueError, zipfile.BadZipFile):
        raise PreviewError("Prepare the Word report again before creating a PDF preview.") from None


def _stop_process(process: subprocess.Popen) -> None:
    """Stop the isolated conversion group before its private files are removed."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        # The converter may have exited just after its wait timed out.
        pass
    try:
        process.wait(timeout=_PROCESS_CLEANUP_SECONDS)
    except subprocess.TimeoutExpired:
        # SIGKILL is already pending for every member of the isolated group.
        pass


def _read_pdf(path: Path) -> bytes:
    try:
        data = path.read_bytes()
        if not re.match(rb"%PDF-[12]\.\d", data) or not data.rstrip().endswith(b"%%EOF"):
            raise ValueError("Incomplete PDF")
        # pdfplumber is already an application dependency. Check the page tree
        # too, rather than accepting arbitrary output with a PDF-like header.
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as document:
            if not document.pages:
                raise ValueError("PDF has no pages")
        return data
    except Exception:
        raise PreviewError(
            "The PDF preview could not be created. Try again or download the Word report."
        ) from None


def docx_to_pdf(docx_bytes: bytes) -> bytes:
    """Render the exact generated DOCX bytes using the local LibreOffice Writer.

    The caller should keep results in its own session, not a shared data cache.
    LibreOffice Writer is installed on Streamlit Cloud through packages.txt.
    """
    _validate_docx(docx_bytes)
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if not executable:
        raise PreviewError(
            "PDF preview needs LibreOffice Writer. Add the supplied packages.txt "
            "beside app.py in GitHub, then reboot the Streamlit app. "
            "You can still download the Word report."
        )

    try:
        with tempfile.TemporaryDirectory(prefix="ccc-report-preview-") as directory:
            workspace = Path(directory)
            folders = {
                name: workspace / name
                for name in ("profile", "home", "cache", "config", "runtime", "tmp", "output")
            }
            for folder in folders.values():
                folder.mkdir(mode=0o700)

            source = workspace / "report.docx"
            source.write_bytes(docx_bytes)
            output = folders["output"] / "report.pdf"

            # These settings apply only to the child process. The application's
            # environment, profile, and existing sessions remain unchanged.
            environment = os.environ.copy()
            environment.update({
                "HOME": str(folders["home"]),
                "XDG_CACHE_HOME": str(folders["cache"]),
                "XDG_CONFIG_HOME": str(folders["config"]),
                "XDG_RUNTIME_DIR": str(folders["runtime"]),
                "TMPDIR": str(folders["tmp"]),
                "TMP": str(folders["tmp"]),
                "TEMP": str(folders["tmp"]),
                "SAL_USE_VCLPLUGIN": "gen",
            })
            command = [
                executable,
                f"-env:UserInstallation={folders['profile'].as_uri()}",
                "--headless",
                "--nologo",
                "--nodefault",
                "--norestore",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(folders["output"]),
                str(source),
            ]
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=workspace,
                env=environment,
                start_new_session=(os.name == "posix"),
            )
            try:
                return_code = process.wait(timeout=_CONVERSION_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                _stop_process(process)
                raise PreviewError(
                    "The PDF preview took too long. Try again or download the Word report."
                ) from None
            except BaseException:
                _stop_process(process)
                raise

            if return_code != 0:
                raise PreviewError(
                    "The PDF preview could not be created. Try again or download the Word report."
                )
            return _read_pdf(output)
    except PreviewError:
        raise
    except OSError:
        raise PreviewError(
            "The PDF converter could not run. Reboot the Streamlit app and try again. "
            "You can still download the Word report."
        ) from None
