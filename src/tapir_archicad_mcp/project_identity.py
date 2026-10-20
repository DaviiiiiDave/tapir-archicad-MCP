"""A stable text identity for the project open in an Archicad instance."""

from typing import Any

from multiconn_archicad import APIResponseError, ConnHeader, SoloProjectID, TeamworkProjectID, UntitledProjectID

UNTITLED = "untitled"
UNKNOWN = "unknown"
IDENTITY_TIMEOUT_SECONDS = 5.0


def project_fingerprint(project_id: Any) -> str:
    """Solo path, Teamwork server + path, or 'untitled'. All untitled projects share one fingerprint."""
    if isinstance(project_id, TeamworkProjectID):
        return f"teamwork://{project_id.serverAddress}/{project_id.projectPath}"
    if isinstance(project_id, SoloProjectID):
        return project_id.projectPath
    return UNTITLED


def read_project_fingerprint(conn_header: ConnHeader) -> tuple[str, str | None]:
    """Ask Archicad now (one GetProjectInfo request; the header's cached identity can be stale).

    Returns (fingerprint, None), or ("unknown", reason) when the identity cannot be read.
    """
    try:
        project_id = conn_header.get_archicad_id(timeout=IDENTITY_TIMEOUT_SECONDS)
    except Exception as e:
        return UNKNOWN, f"{type(e).__name__}: {e}"
    if isinstance(project_id, APIResponseError):
        return UNKNOWN, project_id.message
    if not isinstance(project_id, (SoloProjectID, TeamworkProjectID, UntitledProjectID)):
        return UNKNOWN, f"Unexpected project identity: {type(project_id).__name__}"
    return project_fingerprint(project_id), None
