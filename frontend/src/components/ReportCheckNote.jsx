import { useState } from 'react';
import api from '../api/client';
import { useToast } from '../context/ToastContext';
import { Alert, Button, Icon } from '../ui';
import { openFindings, unreadable } from '../utils/reportCheck';

/**
 * What clinical/report_check.py found when this report was filed - another
 * child's name, a case number, age or birthday that is not this child's - or
 * that no text could be read from the file at all. Shown wherever the report
 * is listed, so whoever reads it next sees it too, until somebody who may edit
 * the report marks it looked at. The findings stay on the record either way.
 */
export default function ReportCheckNote({ report, canReview, onReviewed }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const findings = report.check_findings || [];

  const review = async () => {
    setBusy(true);
    try {
      await api.post(`/report-files/${report.id}/review-check/`);
      onReviewed?.();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Could not mark this report as looked at.');
    } finally {
      setBusy(false);
    }
  };

  if (openFindings(report)) {
    return (
      <Alert tone="warning" title="Check this report." icon={<Icon name="alert-triangle" size={16} />}
             style={{ marginTop: 8 }}>
        <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
          {findings.map((f) => <li key={f.message} style={{ marginBottom: 2 }}>{f.message}</li>)}
        </ul>
        {canReview && (
          <div style={{ marginTop: 8 }} className="racco-no-print">
            <Button size="sm" variant="secondary" disabled={busy} onClick={review}>
              Mark as looked at
            </Button>
          </div>
        )}
      </Alert>
    );
  }
  if (unreadable(report)) {
    return (
      <p style={{ margin: '6px 0 0', fontSize: 12, color: 'var(--text-muted)' }}>
        No text could be read from this file, so it has not been checked and cannot be
        summarised. Saved again as .docx, or as a PDF with real text rather than a
        scan, it can be.
      </p>
    );
  }
  return null;
}
