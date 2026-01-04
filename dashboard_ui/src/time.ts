export function formatLocalTime(isoTs: string): string {
  const d = new Date(isoTs);
  if (Number.isNaN(d.getTime())) return isoTs;
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

export function formatLocalDateTime(isoTs: string): string {
  const d = new Date(isoTs);
  if (Number.isNaN(d.getTime())) return isoTs;
  const yyyy = String(d.getFullYear()).padStart(4, "0");
  const mo = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mo}-${dd} ${formatLocalTime(isoTs)}`;
}

