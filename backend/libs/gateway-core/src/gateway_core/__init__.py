"""Retrieval service shared by the REST API and the MCP server."""

from gateway_core.auth import AuthError, issue_token, principal_from_token

__all__ = ["AuthError", "issue_token", "principal_from_token"]
