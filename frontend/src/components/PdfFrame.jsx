/* A PDF shown inside the page - a report in ReportViewer, a consent scan on
 * Pre-assessment.
 *
 * NOT sandboxed, and that is measured rather than overlooked. Both previews
 * used `sandbox=""`, and Chrome refuses to show a PDF in a sandboxed frame at
 * all: its PDF viewer counts as a plugin, a sandboxed frame may run none, and
 * no sandbox flag lets one back in. The frame showed a grey broken-file icon
 * (headful Chromium 141, 24 Sep 2026) - the consent preview had done so since
 * 2 Sep.
 *
 * What the sandbox was for still holds, by a different lock: the src is
 * always a blob this page typed as application/pdf itself (utils/pdf.js), so
 * the browser hands it to its PDF viewer and never renders it as a page, and
 * a PDF viewer runs nothing with this origin's access. Anything that is not
 * meant to be a PDF never reaches this component - the callers check first.
 * Do not pass it a URL from anywhere else.
 */
export default function PdfFrame({ url, title }) {
  return (
    <iframe title={title} src={url} referrerPolicy="no-referrer"
            style={{ width: '100%', height: '100%', border: 'none' }} />
  );
}
