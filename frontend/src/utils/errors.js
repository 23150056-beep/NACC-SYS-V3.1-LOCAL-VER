/* DRF answers {field: ["message"]}, and rendering that raw put a JSON array on
   screen. Take the first readable sentence, whatever shape it arrives in. */
export function firstError(data, fallback = 'Booking failed.') {
  if (!data) return fallback;
  if (typeof data === 'string') return data;
  for (const key of ['start', 'child', 'psychologist', 'detail', 'non_field_errors']) {
    const v = data[key];
    if (Array.isArray(v) && v.length) return String(v[0]);
    if (typeof v === 'string' && v) return v;
  }
  const first = Object.values(data)[0];
  if (Array.isArray(first) && first.length) return String(first[0]);
  return typeof first === 'string' ? first : fallback;
}
