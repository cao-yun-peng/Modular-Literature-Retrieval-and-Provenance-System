"""MCP Tool: list_collections

This tool provides collection listing capabilities through the MCP protocol.
It lists all available collections in the vector store with statistics.

Usage via MCP:
    Tool name: list_collections
    Input schema:
        - include_stats (boolean, optional): Include statistics for each collection
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from mcp import types

if TYPE_CHECKING:
    from src.mcp_server.protocol_handler import ProtocolHandler
    from src.core.settings import Settings

logger = logging.getLogger(__name__)


# Tool metadata
TOOL_NAME = "list_collections"
TOOL_DESCRIPTION = """List all available document collections in the knowledge base.

Returns information about each collection including:
- Collection name
- Document count (if include_stats=true)
- Collection metadata

Use this tool to discover available collections before querying.
"""

TOOL_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "include_stats": {
            "type": "boolean",
            "description": "Whether to include statistics (document count) for each collection.",
            "default": True,
        },
    },
    "required": [],
}


@dataclass
class CollectionInfo:
    """Information about a single collection.
    
    Attributes:
        name: Collection name
        count: Number of documents/chunks in the collection (optional)
        metadata: Collection metadata dictionary
    """
    name: str
    count: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        result: Dict[str, Any] = {"name": self.name}
        if self.count is not None:
            result["count"] = self.count
        if self.metadata:
            result["metadata"] = self.metadata
        return result


@dataclass
class ListCollectionsConfig:
    """Configuration for list_collections tool.
    
    Attributes:
        persist_directory: Path to ChromaDB storage directory
        include_stats_default: Default value for include_stats parameter
    """
    persist_directory: str = ""
    include_stats_default: bool = True


class ListCollectionsTool:
    """MCP Tool for listing knowledge base collections.
    
    This class encapsulates the list_collections tool logic,
    querying the vector store to enumerate available collections.
    
    Design Principles:
    - Config-Driven: Paths from settings.yaml
    - Error Resilience: Graceful handling of missing directories
    - Observable: Logging for debugging
    
    Example:
        >>> tool = ListCollectionsTool(settings)
        >>> result = await tool.execute(include_stats=True)
        >>> print(result)
    """
    
    def __init__(
        self,
        settings: Optional[Settings] = None,
        config: Optional[ListCollectionsConfig] = None,
    ) -> None:
        """Initialize ListCollectionsTool.
        
        Args:
            settings: Application settings. If None, loaded from default path.
            config: Tool configuration. If None, derived from settings.
        """
        self._settings = settings
        self._config = config
        
    @property
    def settings(self) -> Settings:
        """Get settings, loading if necessary."""
        if self._settings is None:
            from src.core.settings import load_settings
            self._settings = load_settings()
        return self._settings
    
    @property
    def config(self) -> ListCollectionsConfig:
        """Get configuration, deriving from settings if necessary."""
        if self._config is None:
            from src.core.settings import get_vector_store_persist_dir
            persist_dir = str(get_vector_store_persist_dir(self.settings))
            self._config = ListCollectionsConfig(
                persist_directory=persist_dir
            )
        return self._config
    
    def _get_chroma_client(self) -> Any:
        """Get or create ChromaDB client.
        
        Returns:
            ChromaDB PersistentClient instance.
            
        Raises:
            ImportError: If chromadb is not installed.
            RuntimeError: If client creation fails.
        """
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
        except ImportError:
            raise ImportError(
                "chromadb package is required for list_collections. "
                "Install it with: pip install chromadb"
            )
        
        from src.core.settings import resolve_path
        persist_path = resolve_path(self.config.persist_directory)
        
        if not persist_path.exists():
            logger.warning(f"ChromaDB directory does not exist: {persist_path}")
            # Return client anyway - it will just have no collections
            persist_path.mkdir(parents=True, exist_ok=True)
        
        try:
            client = chromadb.PersistentClient(
                path=str(persist_path),
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                )
            )
            return client
        except Exception as e:
            raise RuntimeError(
                f"Failed to initialize ChromaDB client at '{persist_path}': {e}"
            ) from e
    
    def list_collections(
        self,
        include_stats: bool = True
    ) -> List[CollectionInfo]:
        """List all available collections.
        
        Args:
            include_stats: Whether to include document counts.
            
        Returns:
            List of CollectionInfo objects.
        """
        try:
            client = self._get_chroma_client()
        except (ImportError, RuntimeError) as e:
            logger.error(f"Failed to get ChromaDB client: {e}")
            return []
        
        collections_info: List[CollectionInfo] = []
        
        try:
            # Get all collections from ChromaDB
            collections = client.list_collections()
            
            for collection in collections:
                info = CollectionInfo(
                    name=collection.name,
                    metadata=collection.metadata
                )
                
                if include_stats:
                    try:
                        info.count = collection.count()
                    except Exception as e:
                        logger.warning(
                            f"Failed to get count for collection '{collection.name}': {e}"
                        )
                        info.count = None
                    source_stats = self._source_statistics(collection)
                    sync_stats = self._zotero_sync_statistics(collection.name)
                    if source_stats or sync_stats:
                        info.metadata = {
                            **(info.metadata or {}),
                            "source_stats": source_stats,
                            "zotero_sync": sync_stats,
                        }
                
                collections_info.append(info)
                
        except Exception as e:
            logger.error(f"Failed to list collections: {e}")
            return []
        
        logger.info(f"Found {len(collections_info)} collections")
        return collections_info

    @staticmethod
    def _source_statistics(collection: Any) -> Dict[str, Any]:
        """Aggregate source identities without returning private file paths."""
        try:
            payload = collection.get(include=["metadatas"])
        except Exception:
            return {}
        if not isinstance(payload, dict) or not payload.get("metadatas"):
            return {}
        source_types: Dict[str, int] = {}
        document_ids: set[str] = set()
        zotero_collections: set[str] = set()
        for metadata in payload.get("metadatas") or []:
            metadata = metadata or {}
            source_type = str(metadata.get("source_type", "manual"))
            source_types[source_type] = source_types.get(source_type, 0) + 1
            document_id = metadata.get("document_id", metadata.get("source_ref"))
            if document_id:
                document_ids.add(str(document_id))
            raw_keys = metadata.get("zotero_collection_keys", "")
            if isinstance(raw_keys, str):
                zotero_collections.update(
                    key.strip() for key in raw_keys.split(",") if key.strip()
                )
            elif isinstance(raw_keys, list):
                zotero_collections.update(str(key) for key in raw_keys if key)
        return {
            "chunk_count_by_source_type": source_types,
            "document_count": len(document_ids),
            "zotero_collection_keys": sorted(zotero_collections),
        }

    def _zotero_sync_statistics(self, collection_name: str) -> Dict[str, Any]:
        try:
            from src.core.settings import resolve_path
            from src.integrations.zotero.state import ZoteroSyncStateStore

            state_path = resolve_path(self.settings.sources.zotero.sync_state_db)
            if not state_path.exists():
                return {}
            return ZoteroSyncStateStore(state_path).collection_summary(collection_name)
        except Exception:
            logger.debug("Unable to read Zotero sync statistics", exc_info=True)
            return {}
    
    def format_response(
        self,
        collections: List[CollectionInfo]
    ) -> str:
        """Format collections list as a readable string.
        
        Args:
            collections: List of CollectionInfo objects.
            
        Returns:
            Formatted string suitable for MCP response.
        """
        if not collections:
            return "No collections found in the knowledge base."
        
        lines = [
            f"## Available Collections ({len(collections)} total)\n"
        ]
        
        for i, coll in enumerate(collections, 1):
            line = f"{i}. **{coll.name}**"
            
            if coll.count is not None:
                line += f" - {coll.count} documents"
            
            if coll.metadata:
                # Filter out internal metadata
                user_metadata = {
                    k: v for k, v in coll.metadata.items()
                    if not k.startswith('_') and not k.startswith('hnsw:')
                }
                if user_metadata:
                    meta_str = ", ".join(f"{k}={v}" for k, v in user_metadata.items())
                    line += f" ({meta_str})"
            
            lines.append(line)
        
        return "\n".join(lines)
    
    async def execute(
        self,
        include_stats: bool = True,
    ) -> types.CallToolResult:
        """Execute the list_collections tool.
        
        Args:
            include_stats: Whether to include statistics for each collection.
            
        Returns:
            CallToolResult with formatted collection list.
        """
        logger.info(f"Executing list_collections (include_stats={include_stats})")
        
        try:
            # NOTE (Windows): importing numpy/chromadb inside a worker thread can
            # intermittently hang during native extension initialization.
            # Warm up heavy imports on the main thread first, then run the
            # blocking I/O in a background thread.
            try:
                import numpy  # noqa: F401
                import chromadb  # noqa: F401
            except Exception as warmup_exc:
                logger.warning(
                    "Warm-up import for numpy/chromadb failed (will retry in tool execution): %s",
                    warmup_exc,
                )

            # Run blocking ChromaDB I/O in a thread to avoid blocking
            # the async event loop / MCP stdio transport
            collections = await asyncio.to_thread(
                self.list_collections, include_stats,
            )
            response_text = self.format_response(collections)
            
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=response_text,
                    )
                ],
                isError=False,
            )
            
        except Exception as e:
            logger.exception("Error executing list_collections")
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Error listing collections: {str(e)}",
                    )
                ],
                isError=True,
            )


def register_tool(protocol_handler: ProtocolHandler) -> None:
    """Register the list_collections tool with the protocol handler.
    
    This function is called by _register_default_tools() in protocol_handler.py
    to register this tool when the MCP server starts.
    
    Args:
        protocol_handler: ProtocolHandler instance to register with.
    """
    tool = ListCollectionsTool()
    
    async def handler(
        include_stats: bool = True,
    ) -> types.CallToolResult:
        """Handler function for MCP tool calls."""
        return await tool.execute(include_stats=include_stats)
    
    protocol_handler.register_tool(
        name=TOOL_NAME,
        description=TOOL_DESCRIPTION,
        input_schema=TOOL_INPUT_SCHEMA,
        handler=handler,
    )
    
    logger.info(f"Registered MCP tool: {TOOL_NAME}")
