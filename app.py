"""CyberSlooth Stage 0.2: bounded public-page evidence retrieval."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
MAX_REQUEST_BYTES = 8 * 1024
MAX_URL_LENGTH = 2048
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_REDIRECTS = 3
MAX_CANDIDATE_LINKS = 10
TEXT_EXCERPT_LENGTH = 1200
ALLOWED_CONTENT_TYPES = {"text/html", "text/plain", "application/xhtml+xml"}
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp")
BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "metadata", "metadata.google.internal"}
USER_AGENT = "CyberSlooth-Prototype/0.2 (+bounded public-page evidence retrieval)"

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config.update(MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES, JSON_SORT_KEYS=False)


class IngestError(Exception):
    """An expected, browser-safe ingestion failure."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class PageEvidenceParser(HTMLParser):
    """Extract visible text, a title, and links without executing page content."""

    ignored_tags = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self._ignored_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.ignored_tags:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._in_title:
            self.title_parts.append(cleaned)
        else:
            self.text_parts.append(cleaned)


def _host_is_obviously_internal(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    return (
        host in BLOCKED_HOSTNAMES
        or host.endswith(BLOCKED_HOST_SUFFIXES)
        or "." not in host
    )


def _ip_is_public(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_unspecified
        and not address.is_reserved
    )


def resolve_public_host(hostname: str, port: int) -> list[str]:
    """Resolve every address for a host and fail closed if any address is non-public."""

    try:
        results = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as exc:
        raise IngestError("dns_failed", "The source hostname could not be resolved.", 422) from exc

    addresses = sorted({entry[4][0] for entry in results})
    if not addresses or any(not _ip_is_public(address) for address in addresses):
        raise IngestError("unsafe_address", "The source resolves to a non-public network address.", 400)
    return addresses


def validate_public_url(value: Any, *, resolve: bool = True) -> str:
    """Validate a single fetchable public HTTP(S) URL."""

    if not isinstance(value, str):
        raise IngestError("invalid_url", "URL must be a string.")
    value = value.strip()
    if not value or len(value) > MAX_URL_LENGTH:
        raise IngestError("invalid_url", f"URL must be between 1 and {MAX_URL_LENGTH} characters.")
    if re.search(r"[\x00-\x20\\]", value):
        raise IngestError("invalid_url", "URL contains unsupported whitespace or characters.")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise IngestError("invalid_url", "URL is malformed.") from exc

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise IngestError("invalid_url", "Only complete HTTP or HTTPS URLs are accepted.")
    if parsed.username is not None or parsed.password is not None:
        raise IngestError("embedded_credentials", "URLs containing credentials are not accepted.")
    if parsed.scheme.lower() == "http" and port not in {None, 80}:
        raise IngestError("unsupported_port", "HTTP sources must use the standard port.")
    if parsed.scheme.lower() == "https" and port not in {None, 443}:
        raise IngestError("unsupported_port", "HTTPS sources must use the standard port.")

    hostname = parsed.hostname.lower().rstrip(".")
    if _host_is_obviously_internal(hostname):
        raise IngestError("unsafe_host", "Local and internal hostnames are not accepted.")

    try:
        literal_address = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        literal_address = None
    if literal_address is not None and not _ip_is_public(str(literal_address)):
        raise IngestError("unsafe_address", "The source address is not public.")

    if resolve:
        resolve_public_host(hostname, port or (443 if parsed.scheme.lower() == "https" else 80))
    return value


def _normalized_candidate_links(base_url: str, hrefs: list[str]) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        try:
            absolute = urljoin(base_url, href.strip())
            parsed = urlsplit(absolute)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                continue
            if parsed.username is not None or parsed.password is not None:
                continue
            hostname = parsed.hostname.lower().rstrip(".")
            if _host_is_obviously_internal(hostname):
                continue
            try:
                literal = ipaddress.ip_address(hostname.split("%", 1)[0])
            except ValueError:
                literal = None
            if literal is not None and not _ip_is_public(str(literal)):
                continue
            normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))
        except (TypeError, ValueError):
            continue
        if normalized not in seen:
            seen.add(normalized)
            links.append(normalized)
    return links


def fetch_public_page(requested_url: str) -> dict[str, Any]:
    """Perform one bounded GET, validating the initial URL and every redirect."""

    current_url = validate_public_url(requested_url)
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9,application/xhtml+xml;q=0.8"})

    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            try:
                response = session.get(
                    current_url,
                    allow_redirects=False,
                    stream=True,
                    timeout=(4, 8),
                )
            except requests.Timeout as exc:
                raise IngestError("source_timeout", "The public source did not respond within the retrieval limit.", 504) from exc
            except requests.RequestException as exc:
                raise IngestError("source_unavailable", "The public source could not be retrieved.", 502) from exc

            try:
                if response.status_code in REDIRECT_STATUS_CODES:
                    location = response.headers.get("Location")
                    if not location:
                        raise IngestError("invalid_redirect", "The source returned a redirect without a destination.", 502)
                    if redirect_count >= MAX_REDIRECTS:
                        raise IngestError("redirect_limit", "The source exceeded the redirect limit.", 422)
                    current_url = validate_public_url(urljoin(current_url, location))
                    continue

                if not 200 <= response.status_code < 300:
                    raise IngestError("source_status", f"The source returned HTTP {response.status_code}.", 422)

                raw_content_type = response.headers.get("Content-Type", "")
                content_type = raw_content_type.split(";", 1)[0].strip().lower()
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise IngestError("unsupported_content_type", "The source is not an allowed HTML or plain-text page.", 415)

                try:
                    declared_length = int(response.headers.get("Content-Length", "0"))
                except ValueError:
                    declared_length = 0
                if declared_length > MAX_RESPONSE_BYTES:
                    raise IngestError("response_too_large", "The source exceeds the 1 MB retrieval limit.", 413)

                body = bytearray()
                for chunk in response.iter_content(chunk_size=16 * 1024):
                    if not chunk:
                        continue
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise IngestError("response_too_large", "The source exceeds the 1 MB retrieval limit.", 413)

                encoding = response.encoding or "utf-8"
                try:
                    text = bytes(body).decode(encoding, errors="replace")
                except LookupError:
                    text = bytes(body).decode("utf-8", errors="replace")

                return {
                    "requested_url": requested_url,
                    "final_url": current_url,
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "body": text,
                }
            finally:
                response.close()
    finally:
        session.close()

    raise IngestError("retrieval_failed", "The source could not be retrieved.", 502)


def build_research_evidence(fetched: dict[str, Any]) -> dict[str, Any]:
    """Normalize fetched material into the Stage 0.3-ready evidence contract.

    The contract deliberately separates immutable source provenance, extracted
    page content, and unvisited candidate links. It contains no AI conclusions.
    """

    content_type = fetched["content_type"]
    body = fetched["body"]
    title = ""
    hrefs: list[str] = []

    if content_type in {"text/html", "application/xhtml+xml"}:
        parser = PageEvidenceParser()
        parser.feed(body)
        parser.close()
        title = " ".join(parser.title_parts)
        visible_text = " ".join(parser.text_parts)
        hrefs = parser.links
    else:
        visible_text = " ".join(body.split())

    visible_text = " ".join(visible_text.split())
    normalized_links = _normalized_candidate_links(fetched["final_url"], hrefs)
    if not title:
        title = urlsplit(fetched["final_url"]).hostname or "Untitled public page"

    return {
        "source": {
            "requested_url": fetched["requested_url"],
            "final_url": fetched["final_url"],
            "retrieved_at": fetched["retrieved_at"],
            "status_code": fetched["status_code"],
            "content_type": content_type,
        },
        "content": {
            "title": title[:300],
            "text_excerpt": visible_text[:TEXT_EXCERPT_LENGTH],
            "text_length": len(visible_text),
        },
        "links": {
            "found": len(normalized_links),
            "candidates": normalized_links[:MAX_CANDIDATE_LINKS],
            "followed": False,
        },
        "analysis": {
            "performed": False,
            "label": "No AI analysis has been performed yet.",
        },
    }


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return response


@app.get("/")
def index():
    return send_from_directory(PROJECT_ROOT, "index.html")


@app.get("/data/<path:filename>")
def data_file(filename: str):
    return send_from_directory(os.path.join(PROJECT_ROOT, "data"), filename)


@app.post("/api/ingest")
def ingest():
    if not request.is_json:
        raise IngestError("invalid_request", "Request body must be JSON.", 415)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"url"}:
        raise IngestError("invalid_request", "JSON must contain exactly one field named url.")

    fetched = fetch_public_page(payload["url"])
    evidence = build_research_evidence(fetched)
    return jsonify({"ok": True, "evidence": evidence})


@app.errorhandler(IngestError)
def handle_ingest_error(error: IngestError):
    return jsonify({"ok": False, "error": {"code": error.code, "message": error.message}}), error.status


@app.errorhandler(RequestEntityTooLarge)
def handle_large_request(_error):
    return jsonify({"ok": False, "error": {"code": "request_too_large", "message": "Request body exceeds the size limit."}}), 413


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception):
    if isinstance(error, HTTPException):
        return error
    if request.path.startswith("/api/"):
        app.logger.error("Unexpected ingestion failure: %s", type(error).__name__)
        return jsonify({"ok": False, "error": {"code": "internal_error", "message": "The source could not be inspected safely."}}), 500
    app.logger.error("Unexpected application failure: %s", type(error).__name__)
    return "Internal server error", 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
