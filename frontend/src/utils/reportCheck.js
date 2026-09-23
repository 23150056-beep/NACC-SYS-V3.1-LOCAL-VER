/**
 * Whether a report has something to say under it: what the check found and
 * nobody has looked at yet, or that its text could not be read at all.
 *
 * A .docx with no text is neither: it was uploaded before Word files could be
 * read, and it is read the first time something needs it.
 */
export function openFindings(report) {
  return (report.check_findings || []).length > 0 && !report.check_reviewed;
}

export function unreadable(report) {
  const name = (report.original_filename || '').toLowerCase();
  return report.has_text === false && !name.endsWith('.docx');
}

export function hasCheckNote(report) {
  return openFindings(report) || unreadable(report);
}
