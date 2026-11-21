"""Row counts for a CSV download, carried in the response headers.

A trade export stops at a row limit, so a 200 whose file parses cleanly can
still be missing trades. Nothing in the body says so: the file holds exactly
the rows that were written. These headers carry what the server measured while
building it, and ``EXPORT_METADATA_HEADERS`` is what CORS has to expose for a
browser to read them at all — an unexposed header is simply absent client-side,
which would put the silent truncation right back where it started.
"""

EXPORT_ROWS_EXPORTED_HEADER = "X-Export-Rows-Exported"
EXPORT_ROWS_MATCHED_HEADER = "X-Export-Rows-Matched"
EXPORT_ROW_LIMIT_HEADER = "X-Export-Row-Limit"
EXPORT_TRUNCATED_HEADER = "X-Export-Truncated"

# Every header a browser needs to read off an export response. Adding a name
# here is what makes it readable cross-origin; adding it only at the endpoint
# would ship a header no client can see.
EXPORT_METADATA_HEADERS = [
    EXPORT_ROWS_EXPORTED_HEADER,
    EXPORT_ROWS_MATCHED_HEADER,
    EXPORT_ROW_LIMIT_HEADER,
    EXPORT_TRUNCATED_HEADER,
]
