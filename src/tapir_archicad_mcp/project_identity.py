"""A stable text identity for the project open in an Archicad instance."""

from typing import Any

from multiconn_archicad import SoloProjectID, TeamworkProjectID

UNTITLED = "untitled"


def project_fingerprint(project_id: Any) -> str:
    """Solo path, Teamwork server + path, or 'untitled'. All untitled projects share one fingerprint."""
    if isinstance(project_id, TeamworkProjectID):
        return f"teamwork://{project_id.serverAddress}/{project_id.projectPath}"
    if isinstance(project_id, SoloProjectID):
        return project_id.projectPath
    return UNTITLED
