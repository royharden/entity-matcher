"""In-memory comparison service; no paths, persistence, human rulings or providers."""

import re
import threading
from collections import OrderedDict

from ..io.catalog import MAX_FILE_BYTES
from ..pipeline.review_page import encode_review_page, make_review_page
from ..pipeline.workspace import WorkspaceError, create_workspace
from ..shared.match.aliases import AliasError
from .models import ComparisonInputError, decode_catalog_bytes, parse_upload


class ComparisonServiceError(ValueError):
    """Fixed codes only; framework handlers must never expose underlying exception text."""


class ComparisonService:
    """Retain at most two immutable workspaces with a bounded source-byte ledger.

    The byte ledger tracks original uploaded bytes, not measured process memory.
    Smaller trusted retention limits are available for bounded mechanism controls.
    An HTTP caller cannot change policy, caps, storage paths or retention limits.
    """

    def __init__(self, *, retained_byte_limit: int = MAX_FILE_BYTES):
        if type(retained_byte_limit) is not int or not 1 <= retained_byte_limit <= MAX_FILE_BYTES:
            raise ValueError("bounded_retention_limit_required")
        self._limit = retained_byte_limit
        self._workspaces = OrderedDict()
        self._sizes = {}
        self._lock = threading.RLock()

    def status(self):
        with self._lock:
            return {
                "kind": "local_read_only_comparison_service",
                "workspace_count": len(self._workspaces),
                "retained_input_bytes": sum(self._sizes.values()),
                "max_workspaces": 2,
                "retained_input_byte_limit": self._limit,
                "upload_encoded_byte_limit": MAX_FILE_BYTES,
                "human_rulings_available": False,
                "provider_dispatch_available": False,
            }

    def create(self, body: bytes) -> bytes:
        upload = parse_upload(body)
        left, right, aliases = decode_catalog_bytes(upload)
        source_size = len(left) + len(right) + (len(aliases) if aliases is not None else 0)
        if source_size > self._limit:
            raise ComparisonInputError("comparison_retention_bound")
        # Keep admission and replacement deterministic across worker threads.
        # Complete page serialization before publishing a new workspace.
        with self._lock:
            try:
                workspace = create_workspace(
                    left,
                    right,
                    dataset_a=upload.dataset_a,
                    dataset_b=upload.dataset_b,
                    encoding_a=upload.encoding_a,
                    encoding_b=upload.encoding_b,
                    aliases_csv=aliases,
                )
                page = encode_review_page(make_review_page(workspace, method=upload.method))
            except (WorkspaceError, AliasError, ValueError) as error:
                raise ComparisonServiceError("comparison_input_invalid") from error
            identity = workspace.workspace_id
            if identity not in self._workspaces:
                while self._workspaces and (
                    len(self._workspaces) >= 2
                    or sum(self._sizes.values()) + source_size > self._limit
                ):
                    oldest, _ = self._workspaces.popitem(last=False)
                    del self._sizes[oldest]
                self._workspaces[identity] = workspace
                self._sizes[identity] = source_size
            return page

    def page(self, workspace_id: str, *, method="rules", offset=0, limit=50) -> bytes:
        if type(workspace_id) is not str or re.fullmatch(r"s4-[0-9a-f]{64}", workspace_id) is None:
            raise ComparisonServiceError("comparison_not_found")
        if (
            type(method) is not str
            or method not in {"rules", "fuzzy"}
            or type(offset) is not int
            or type(limit) is not int
        ):
            raise ComparisonServiceError("invalid_page_options")
        with self._lock:
            workspace = self._workspaces.get(workspace_id)
            if workspace is None:
                raise ComparisonServiceError("comparison_not_found")
            try:
                return encode_review_page(
                    make_review_page(workspace, method=method, offset=offset, limit=limit)
                )
            except ValueError as error:
                raise ComparisonServiceError("invalid_page_options") from error
