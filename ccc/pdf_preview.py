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

    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.code = code
        super().__init__(message + (f" Reference: {code}." if code else ""))


def _validate_docx(data: bytes) -> None:
    if not isinstance(data, bytes) or not data:
        raise PreviewError("Prepare the Word report again before creating a PDF preview.", code="PDF_INPUT_INVALID")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if not {"[Content_Types].xml", "word/document.xml"}.issubset(archive.namelist()):
                raise ValueError("Not a Word document")
    except (OSError, ValueError, zipfile.BadZipFile):
        raise PreviewError("Prepare the Word report again before creating a PDF preview.", code="PDF_INPUT_INVALID") from None


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
        if not re.match(rb"%PDF-[12]\.\d", data) or b"%%EOF" not in data[-1024:]:
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
            "The converter returned an unreadable PDF. Try preparing the report again or download the Word report.",
            code="PDF_OUTPUT_INVALID",
        ) from None


def _conversion_failure(log_path: Path, return_code: int, *, output_missing: bool) -> PreviewError:
    """Map temporary converter diagnostics to fixed, non-sensitive messages.

    The log can include document or server details. Never return, print, or
    persist it. Only these fixed messages and codes may leave this function.
    """
    try:
        with log_path.open("rb") as stream:
            diagnostic = stream.read(65536).decode("utf-8", errors="replace").casefold()
    except OSError:
        diagnostic = ""
    reasons = (
        (("cannot open display", "can't open display", "failed to open display"),
         "PDF_DISPLAY_SETUP", "The converter could not start in server mode. Replace pdf_preview.py with the latest update and reboot the app."),
        (("error while loading shared libraries", "cannot open shared object file", "failed to load shared library"),
         "PDF_CONVERTER_DEPENDENCY", "The server is missing part of the PDF converter. Check packages.txt and reboot the app."),
        (("no space left on device", "disk full"),
         "PDF_TEMP_SPACE", "The server has run out of temporary space. Reboot the app and try one report again."),
        (("permission denied", "access denied"),
         "PDF_TEMP_ACCESS", "The server could not write the temporary PDF. Reboot the app and try again."),
        (("user installation could not be completed",),
         "PDF_PROFILE_SETUP", "The PDF converter could not initialize its temporary profile. Reboot the app and try again."),
        (("no export filter", "no filter for"),
         "PDF_EXPORT_FILTER", "The Word-to-PDF converter is incomplete. Check packages.txt and reboot the app."),
        (("source file could not be loaded", "error loading document"),
         "PDF_WORD_LOAD", "The converter could not open the generated Word report. Prepare the report again, or download the Word file."),
    )
    for patterns, code, message in reasons:
        if any(pattern in diagnostic for pattern in patterns):
            return PreviewError(message + " The Word download remains available.", code=code)
    if return_code < 0 or return_code in {137, 139}:
        return PreviewError("The server stopped PDF conversion. Try one report again, or download the Word report.",
                            code="PDF_PROCESS_STOPPED")
    if output_missing and return_code == 0:
        return PreviewError("The converter finished without creating a PDF. Reboot the app and try again. The Word download remains available.",
                            code="PDF_OUTPUT_MISSING")
    return PreviewError("The PDF converter could not complete this report. Reboot the app and try again, or download the Word file.",
                        code="PDF_CONVERTER_FAILED")


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
            "You can still download the Word report.", code="PDF_CONVERTER_MISSING",
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
                # gen is the X11 desktop backend and can fail on Community
                # Cloud even with --headless. svp is LibreOffice's server
                # renderer and needs neither an X11 nor a Wayland display.
                "SAL_USE_VCLPLUGIN": "svp",
            })
            for key in ("DISPLAY", "WAYLAND_DISPLAY", "SESSION_MANAGER", "DBUS_SESSION_BUS_ADDRESS"):
                environment.pop(key, None)
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
            log_path = workspace / "converter.log"
            with log_path.open("wb") as diagnostic_log:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=diagnostic_log,
                    stderr=subprocess.STDOUT,
                    cwd=workspace,
                    env=environment,
                    start_new_session=(os.name == "posix"),
                )
                try:
                    return_code = process.wait(timeout=_CONVERSION_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    _stop_process(process)
                    raise PreviewError(
                        "The PDF preview took too long. Try again or download the Word report.",
                        code="PDF_CONVERSION_TIMEOUT",
                    ) from None
                except BaseException:
                    _stop_process(process)
                    raise

            if return_code != 0 or not output.is_file():
                raise _conversion_failure(log_path, return_code, output_missing=not output.is_file())
            return _read_pdf(output)
    except PreviewError:
        raise
    except OSError:
        raise PreviewError(
            "The PDF converter could not run. Reboot the Streamlit app and try again. "
            "You can still download the Word report.", code="PDF_CONVERTER_START",
        ) from None
