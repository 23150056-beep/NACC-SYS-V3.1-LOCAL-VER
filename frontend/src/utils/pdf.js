// A blob: URL for bytes that are to be shown as a PDF and as nothing else.
// The type is set here, whatever the server said, because a blob inherits
// this page's origin: only something typed as a PDF may be framed
// (components/PdfFrame.jsx).
export function pdfObjectUrl(data) {
  return URL.createObjectURL(new Blob([data], { type: 'application/pdf' }));
}
