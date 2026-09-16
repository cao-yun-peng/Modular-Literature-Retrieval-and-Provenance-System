"""Explicit ingestion protection for a frozen local retrieval baseline."""

from src.core.settings import resolve_path


def baseline_marker(settings, collection):
    # Hash names so a collection identifier cannot introduce path components.
    import hashlib

    key = hashlib.sha256(collection.encode()).hexdigest()[:24]
    return resolve_path(settings.vector_store.persist_directory) / f"baseline-{key}.json"


def assert_baseline_writable(settings, collection):
    if baseline_marker(settings, collection).exists():
        raise RuntimeError("Collection is a frozen baseline; create a separate collection for changes")
