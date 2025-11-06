// Safe copy util (SSR/insecure context/older browsers)
// no "use client" needed; we guard for window
export async function copyToClipboard(text: string): Promise<boolean> {
  if (typeof window === 'undefined' || typeof document === 'undefined') return false;
  try {
    if (window.isSecureContext && navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {}
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}


export const shorten = (str: string, len: number = 4): string => {
  if (!str || str.length <= len) return str
  return `${str.slice(0, len)}...`
}

export const time2str = (time: number): string => {
  const now = Math.floor(Date.now() / 1000)
  const diff = Math.floor(now - time)
  // round down to nearest second/minute/hour/day
  
  
  if (diff < 60) return `${diff} second${diff !== 1 ? 's' : ''} ago`
  
  const minutes = Math.floor(diff / 60)
  if (minutes < 60) return `${minutes} minute${minutes !== 1 ? 's' : ''} ago`
  
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hour${hours !== 1 ? 's' : ''} ago`
  
  const days = Math.floor(hours / 24)
  return `${days} day${days !== 1 ? 's' : ''} ago`
}


export const time2utc = (time: number): string => {
  const d = new Date(time * 1000)
  return d.toISOString().replace('T', ' ').substring(0, 19) 
}


export const text2color = (text: string): string => {
  if (!text) return '#00ff00'
  let hash = 0
  for (let i = 0; i < text.length; i++) hash = text.charCodeAt(i) + ((hash << 5) - hash)
  const golden_ratio = 0.618033988749895
  const hue = (hash * golden_ratio * 360) % 360
  const saturation = 65 + (Math.abs(hash >> 8) % 35)
  const lightness = 50 + (Math.abs(hash >> 16) % 20)
  return `hsl(${hue}, ${saturation}%, ${lightness}%)`
}


