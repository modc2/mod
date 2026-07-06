import { Play, CheckCircle, XCircle, StopCircle, Clock } from 'lucide-react'
import { STATUS_CONFIG } from './shared'

export function StatusIcon({ status, size = 12 }: { status: string; size?: number }) {
  const color = STATUS_CONFIG[status]?.color || '#fbbf24'
  switch (status) {
    case 'running':   return <Play size={size} style={{ color }} />
    case 'completed': return <CheckCircle size={size} style={{ color }} />
    case 'failed':    return <XCircle size={size} style={{ color }} />
    case 'cancelled': return <StopCircle size={size} style={{ color }} />
    default:          return <Clock size={size} style={{ color }} />
  }
}
