import os
from pathlib import Path


def is_compressed_file(file_path: str | Path) -> bool:
    """Best-effort compression detection by extension and magic bytes."""
    path = Path(file_path)
    lower_name = str(path).lower()

    # Extension-based quick check
    if any(lower_name.endswith(ext) for ext in [".gz", ".bgz", ".zip", ".bz2"]):
        return True

    # Magic bytes check for a few common formats
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic.startswith(b"\x1f\x8b"):  # gzip
                return True
            if magic.startswith(b"PK"):  # zip
                return True
            if magic.startswith(b"BZ"):  # bzip2
                return True
    except Exception:
        # If file can't be read, fall back to extension-only signal
        return any(lower_name.endswith(ext) for ext in [".gz", ".bgz", ".zip", ".bz2"])

    return False

# TODO: check inside zipped file for index file
def has_index_file(file_path: str | Path) -> bool:
    """Check for a common set of genomic index file siblings."""
    path = Path(file_path)
    index_extensions = [".tbi", ".csi", ".bai", ".fai", ".crai"]
    for ext in index_extensions:
        sibling = path.parent / f"{path.stem}{ext}"
        if sibling.exists():
            return True
    return False


