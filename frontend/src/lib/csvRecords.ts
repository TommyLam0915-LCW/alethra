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
