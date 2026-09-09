import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';

// Public, token-gated child opinionnaire. No login, no app shell — opened by
// scanning the QR code on a secondary device. Big, friendly controls.
const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api';

const wrap = { minHeight: '100%', background: 'var(--bg-app)', display: 'flex', justifyContent: 'center', padding: '28px 16px', fontFamily: 'var(--font-sans)' };
const card = { width: 560, maxWidth: '100%', background: 'var(--surface)', borderRadius: 'var(--radius-xl)', boxShadow: 'var(--shadow-lg)', padding: 26, height: 'fit-content' };
const bigInput = { width: '100%', padding: '13px 15px', borderRadius: 'var(--radius-lg)', border: '1.5px solid var(--border-strong)', fontFamily: 'var(--font-sans)', fontSize: 16, lineHeight: 1.55 };

export default function Survey() {
  const { token } = useParams();
  const [state, setState] = useState('loading'); // loading | ready | done | error
  const [message, setMessage] = useState('');
  const [survey, setSurvey] = useState(null);
  const [answers, setAnswers] = useState({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    axios.get(`${baseURL}/opinionnaire/${token}/`)
      .then((r) => { setSurvey(r.data); setState('ready'); })
      .catch((err) => {
        setMessage(err.response?.data?.detail || 'This survey link is not valid.');
        setState('error');
      });
  }, [token]);

  const submit = async () => {
    setBusy(true);
    try {
      await axios.post(`${baseURL}/opinionnaire/${token}/submit/`, { answers });
      setState('done');
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Could not send your answers. Please ask for help.');
      setState('error');
    } finally { setBusy(false); }
  };

  const answered = survey ? survey.fields.filter((f) => (answers[f.label] || '').toString().trim()).length : 0;

  if (state === 'loading') return <div style={wrap}><div style={card}>Loading…</div></div>;

  if (state === 'error') return (
    <div style={wrap}><div style={{ ...card, textAlign: 'center' }}>
      <div style={{ fontSize: 44, marginBottom: 10 }}>😕</div>
      <div style={{ fontWeight: 700, fontSize: 17, color: 'var(--text-strong)' }}>{message}</div>
      <p style={{ fontSize: 14, color: 'var(--text-muted)' }}>Please tell the social worker or psychologist.</p>
    </div></div>
  );

  if (state === 'done') return (
    <div style={wrap}><div style={{ ...card, textAlign: 'center' }}>
      <div style={{ fontSize: 52, marginBottom: 10 }}>🎉</div>
      <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 22, color: 'var(--text-strong)' }}>All done — thank you!</div>
      <p style={{ fontSize: 15, color: 'var(--text-muted)' }}>You can give the device back now.</p>
    </div></div>
  );

  return (
    <div style={wrap}>
      <div style={card}>
        <div style={{ marginBottom: 18 }}>
          <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 22, color: 'var(--blue-700)' }}>Hi {survey.first_name}! 👋</div>
          <p style={{ fontSize: 14.5, color: 'var(--text-muted)', margin: '6px 0 0', lineHeight: 1.5 }}>
            {survey.title} — there are no right or wrong answers. Just tell us how you really feel.
          </p>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {survey.fields.map((f, i) => (
            <div key={f.label}>
              <div style={{ fontWeight: 700, fontSize: 15.5, color: 'var(--text-strong)', marginBottom: 8 }}>
                {i + 1}. {f.label}
              </div>
              {f.field_type === 'yes_no' ? (
                <div style={{ display: 'flex', gap: 10 }}>
                  {[['Yes', '👍 Yes'], ['No', '👎 No']].map(([v, label]) => (
                    <button key={v} onClick={() => setAnswers({ ...answers, [f.label]: v })}
                      style={{ flex: 1, padding: '14px 0', fontSize: 17, fontWeight: 700, borderRadius: 'var(--radius-lg)', cursor: 'pointer', fontFamily: 'var(--font-sans)', border: `2px solid ${answers[f.label] === v ? 'var(--blue-500)' : 'var(--border)'}`, background: answers[f.label] === v ? 'var(--blue-50)' : 'var(--surface)', color: answers[f.label] === v ? 'var(--blue-700)' : 'var(--text-body)' }}>
                      {label}
                    </button>
                  ))}
                </div>
              ) : f.field_type === 'choice' ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {(f.options || []).map((o) => (
                    <button key={o} onClick={() => setAnswers({ ...answers, [f.label]: o })}
                      style={{ textAlign: 'left', padding: '12px 15px', fontSize: 15.5, fontWeight: 600, borderRadius: 'var(--radius-lg)', cursor: 'pointer', fontFamily: 'var(--font-sans)', border: `2px solid ${answers[f.label] === o ? 'var(--blue-500)' : 'var(--border)'}`, background: answers[f.label] === o ? 'var(--blue-50)' : 'var(--surface)', color: answers[f.label] === o ? 'var(--blue-700)' : 'var(--text-body)' }}>
                      {o}
                    </button>
                  ))}
                </div>
              ) : f.field_type === 'date' ? (
                <input type="date" value={answers[f.label] || ''} onChange={(e) => setAnswers({ ...answers, [f.label]: e.target.value })} style={bigInput} />
              ) : f.field_type === 'long_text' ? (
                <textarea rows={4} value={answers[f.label] || ''} onChange={(e) => setAnswers({ ...answers, [f.label]: e.target.value })} style={{ ...bigInput, resize: 'vertical' }} placeholder="Type here…" />
              ) : (
                <input value={answers[f.label] || ''} onChange={(e) => setAnswers({ ...answers, [f.label]: e.target.value })} style={bigInput} placeholder="Type here…" />
              )}
            </div>
          ))}
        </div>

        <div style={{ marginTop: 24, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 13, color: 'var(--text-muted)', textAlign: 'center' }}>
            {answered} of {survey.fields.length} answered
          </div>
          <button onClick={submit} disabled={busy || answered === 0}
            style={{ width: '100%', padding: '15px 0', fontSize: 17, fontWeight: 800, borderRadius: 'var(--radius-pill)', border: 'none', cursor: busy || answered === 0 ? 'default' : 'pointer', fontFamily: 'var(--font-sans)', background: busy || answered === 0 ? 'var(--divider-row)' : 'var(--blue-600)', color: busy || answered === 0 ? 'var(--text-faint)' : '#fff' }}>
            {busy ? 'Sending…' : 'I’m finished ✅'}
          </button>
        </div>
      </div>
    </div>
  );
}
