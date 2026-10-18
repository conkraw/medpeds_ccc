"""Password-to-open protection for generated Word documents, entirely in memory.

This uses Office Agile OOXML encryption, not Word's editing restrictions. The
upstream msoffcrypto-tool project labels its encryption support experimental.
Each result is therefore decrypted with password and integrity verification
and compared with its input before any downloadable bytes are returned.
Actual compatibility with the organization's target Word version must still
be checked before routine use. The password is never persisted or logged.
"""

from __future__ import annotations

import hmac
import io
import threading
import zipfile

import msoffcrypto
from msoffcrypto.format.ooxml import OOXMLFile

MIN_EXPORT_PASSWORD_LENGTH = 14
MAX_EXPORT_PASSWORD_LENGTH = 1024
MAX_DOCX_BYTES = 15 * 1024 * 1024
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_EXPORT_SLOTS = threading.BoundedSemaphore(2)


class ExportSecurityError(Exception):
    """Safe-to-display failure; a failure never returns the unprotected input."""


def protect_docx(docx_bytes: bytes, password: str) -> bytes:
    """Return a verified password-protected .docx, or raise ExportSecurityError.

    The caller obtains and confirms a separate export passphrase. Do not reuse
    the application's login password or keep this passphrase in session state
    after preparing the download. A download filename should use an opaque
    resident ID, because file names themselves are not encrypted by Office.
    """
    if (not isinstance(password, str)
            or not MIN_EXPORT_PASSWORD_LENGTH <= len(password) <= MAX_EXPORT_PASSWORD_LENGTH
            or not password.strip()):
        raise ExportSecurityError("Use an export passphrase of at least 14 characters (maximum 1,024).")
    if not isinstance(docx_bytes, bytes) or not docx_bytes or len(docx_bytes) > MAX_DOCX_BYTES:
        raise ExportSecurityError("This Word report is empty or exceeds the 15 MiB export limit.")
    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as package:
            names = package.namelist()
            if ("[Content_Types].xml" not in names or "word/document.xml" not in names
                    or len(names) != len(set(names))):
                raise ValueError
    except (zipfile.BadZipFile, ValueError, OSError):
        raise ExportSecurityError("The report is not a valid standard Word document. Generate it again.") from None
    try:
        with _EXPORT_SLOTS, io.BytesIO(docx_bytes) as plain, io.BytesIO() as encrypted:
            office_file = OOXMLFile(plain)
            if office_file.is_encrypted():
                raise ValueError
            office_file.encrypt(password, encrypted)
            result = encrypted.getvalue()
            if not result.startswith(_OLE_MAGIC) or len(result) > MAX_DOCX_BYTES + 1024 * 1024:
                raise ValueError
            with io.BytesIO(result) as check_input, io.BytesIO() as verified:
                check_file = msoffcrypto.OfficeFile(check_input)
                if not check_file.is_encrypted():
                    raise ValueError
                check_file.load_key(password=password, verify_password=True)
                check_file.decrypt(verified, verify_integrity=True)
                if not hmac.compare_digest(verified.getvalue(), docx_bytes):
                    raise ValueError
            return result
    except Exception:
        # Third-party library failures can carry document/parser details. Return
        # a generic message and never provide readable output on this path.
        raise ExportSecurityError("The protected Word report could not be verified. Generate the report again; no unprotected file was released.") from None
