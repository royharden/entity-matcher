# Derived CSV inspection views

`component_csv` and `pair_csv` return UTF8 CSV bytes and row/text-prefix counts from an existing product projection. They write no files and alter no source record or journal. The byte ceiling is20MiB; exceeding it fails the whole export rather than silently truncating rows. Component rows retain single records, contested components and unsupported identity shapes. Pair rows retain review barriers, missing human evidence, outside-queue status and issue codes.

Every body cell has a literal apostrophe prefix; all fields are quoted and embedded quotes are escaped by the CSV writer. This includes empty cells, formulas, full-width prefixes, control characters and already-prefixed input. The prefix is intentionally part of the derived bytes. These are human inspection views: importing their altered strings as raw IDs or authoritative ruling events is unsupported. Exact source CSVs and the canonical JSONL journal retain their original identity strings.

Spreadsheet behavior varies, including after saving and reopening CSVs. This format makes no universal spreadsheet-safety claim; actual Excel interaction has not been tested. See [OWASP CSV Injection](https://community.owasp.org/attacks/CSV_Injection) for the application-dependent behavior and limits of text-prefix/quoting techniques.
