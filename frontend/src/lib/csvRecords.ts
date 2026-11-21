// How many trade rows a downloaded CSV actually contains, excluding its header line.
//
// Two producers make this a contract rather than a convenience:
//   - `/journal/trades.csv` answers a filter that matches nothing with a 200 whose
//     body is the header line alone
//     (`backend/tests/test_journal_api.py::TestCsvExport::test_csv_export_matching_nothing_is_header_only`).
//     A successful request therefore says nothing about whether anything was exported.
//   - counting physical lines is wrong: the writer quotes any field containing a
//     newline (`csv.QUOTE_MINIMAL` in `backend/app/services/journal_service.py`) and
//     the export carries free-text reason columns, so one record can span several
//     lines.

export function csvDataRowCount(text: string): number {
  let records = 0;
  let hasContent = false;
  let inQuotes = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];

    if (inQuotes) {
      hasContent = true;
      if (char === '"') {
        if (text[index + 1] === '"') index += 1;
        else inQuotes = false;
      }
      continue;
    }

    if (char === '"') {
      inQuotes = true;
      hasContent = true;
    } else if (char === '\n') {
      if (hasContent) records += 1;
      hasContent = false;
    } else if (char !== '\r') {
      hasContent = true;
    }
  }

  if (hasContent) records += 1;
  return records === 0 ? 0 : records - 1;
}

// --- what the server measured while it wrote the file ----------------------
//
// The export stops at a row limit, so a file that parses perfectly can still be
// a prefix of the filter that produced it. Only the response says which of the
// two it is, and that message lives in headers the endpoint sets per response.
// A missing or malformed header is not a zero: it means this response never told
// us, which is a different thing from having told us the file is complete.

export const CSV_EXPORT_HEADERS = {
  rowsExported: 'x-export-rows-exported',
  rowsMatched: 'x-export-rows-matched',
  rowLimit: 'x-export-row-limit',
  truncated: 'x-export-truncated',
} as const;

export interface CsvExportMeta {
  rowsExported: number | null;
  rowsMatched: number | null;
  rowLimit: number | null;
  truncated: boolean;
}

function nonNegativeInt(raw: string | null): number | null {
  if (raw === null || raw.trim() === '') return null;
  const value = Number(raw);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

export function readCsvExportMeta(
  get: (name: string) => string | null,
): CsvExportMeta {
  return {
    rowsExported: nonNegativeInt(get(CSV_EXPORT_HEADERS.rowsExported)),
    rowsMatched: nonNegativeInt(get(CSV_EXPORT_HEADERS.rowsMatched)),
    rowLimit: nonNegativeInt(get(CSV_EXPORT_HEADERS.rowLimit)),
    truncated: get(CSV_EXPORT_HEADERS.truncated) === 'true',
  };
}

/**
 * The sentence a truncated export owes the person holding the file, or null
 * when this response did not report truncation. `rowsInFile` is counted from
 * the body, so what the notice claims about the file is checked against it.
 */
export function csvTruncationNotice(
  meta: CsvExportMeta,
  rowsInFile: number,
): string | null {
  if (!meta.truncated) return null;
  if (meta.rowsMatched === null) {
    return (
      `This export stopped at its row limit, so the ${rowsInFile} trades in the ` +
      'file are fewer than your filters matched. Narrow the filters and export again.'
    );
  }
  const missing = Math.max(meta.rowsMatched - rowsInFile, 0);
  return (
    `This file holds the first ${rowsInFile} of the ${meta.rowsMatched} trades ` +
    `these filters matched; ${missing} are missing from it. Narrow the filters ` +
    'and export again to get the rest.'
  );
}
