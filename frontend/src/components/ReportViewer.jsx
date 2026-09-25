import { useEffect, useState } from 'react';
import api from '../api/client';
import { Alert, Button, Icon, Modal } from '../ui';
import PdfFrame from './PdfFrame';
import { pdfObjectUrl } from '../utils/pdf';

/* Reading a psychologist's report on screen instead of downloading it
 * (24 Sep 2026: staff asked to see the psychologist's reports).
 *
 * A PDF is shown as itself. A Word file cannot be shown by a browser, so its
 * text is shown instead - the same text the report check and the summaries
 * read, with the headings found in the file marked `## ` (clinical/services.py).
 * A Word 97-2003 file cannot be read at all; that says so and offers the file.
 *
 * Both come from endpoints scoped exactly like the download, so this shows
 * nobody anything the Download button would not have handed them.
 */
const isPdf = (name = '') => /\.pdf$/i.test(name);

function ReportText({ text }) {
  // `## ` marks a heading found in the file; everything else is a paragraph.
  const blocks = text.split(/\n{1,}/).map((l) => l.trim()).filter(Boolean);
  return (
    <div style={{ fontFamily: "Georgia, 'Times New Roman', serif", fontSize: 14.5, lineHeight: 1.65, color: 'var(--text-body)' }}>
      {blocks.map((b, i) => (b.startsWith('## ')
        ? <h3 key={i} style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, fontWeight: 800, color: 'var(--text-strong)', margin: '18px 0 6px', textTransform: 'uppercase', letterSpacing: '0.02em' }}>{b.slice(3)}</h3>
        : <p key={i} style={{ margin: '0 0 10px' }}>{b}</p>))}
    </div>
  );
}

export default function ReportViewer({ report, onClose, onDownload }) {
  const pdf = isPdf(report.original_filename);
  const [state, setState] = useState({ loading: true });

  useEffect(() => {
    let url = null;
    let live = true;
    (async () => {
      try {
        if (pdf) {
          const res = await api.get(`/report-files/${report.id}/download/`, { responseType: 'blob' });
          // Typed as a PDF here, whatever came back: a blob inherits this
          // page's origin, so it must never be something a browser runs.
          url = pdfObjectUrl(res.data);
          if (live) setState({ url });
        } else {
          const { data } = await api.get(`/report-files/${report.id}/text/`);
          if (live) setState({ text: data.text, readable: data.readable });
        }
      } catch {
        if (live) setState({ error: true });
      }
    })();
    return () => { live = false; if (url) URL.revokeObjectURL(url); };
  }, [report.id, pdf]);

  return (
    <Modal
      open onClose={onClose} width={860} tone="brand"
      icon={<Icon name="file-text" size={20} />}
      title={report.original_filename || 'Report'}
      subtitle={[report.child_name, report.author_name ? `by ${report.author_name}` : null].filter(Boolean).join(' · ') || null}
      footer={<>
        {state.url && (
          <Button variant="ghost" onClick={() => window.open(state.url, '_blank', 'noopener')}
                  iconLeft={<Icon name="external-link" size={15} />}>Open in new tab</Button>
        )}
        <Button variant="secondary" onClick={() => onDownload(report)} iconLeft={<Icon name="download" size={15} />}>Download</Button>
        <Button variant="primary" onClick={onClose}>Close</Button>
      </>}
    >
      <div style={{ height: '64vh', minHeight: 260, overflowY: pdf ? 'hidden' : 'auto', background: pdf ? 'var(--ink-50)' : 'var(--surface)', borderRadius: 'var(--radius-md)' }}
           className="racco-scroll">
        {state.loading && <div style={{ padding: 16, color: 'var(--text-muted)' }}>Opening the report…</div>}
        {state.error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>The report could not be opened. Try downloading it instead.</Alert>}
        {state.url && <PdfFrame url={state.url} title={`Report: ${report.original_filename}`} />}
        {state.text !== undefined && (state.readable
          ? <div style={{ padding: '4px 6px' }}><ReportText text={state.text} /></div>
          : (
            <Alert tone="info" icon={<Icon name="info" size={18} />} title="This file cannot be shown here.">
              Its text cannot be read on screen - a Word 97-2003 file (.doc), or a document
              that holds only pictures of pages. Download it to open it.
            </Alert>
          ))}
      </div>
    </Modal>
  );
}
