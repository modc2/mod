import { useState, useCallback, useEffect } from 'react'
import { Job, ChatMessage, JOBS_API } from './shared'

interface UseEditorJobsOptions {
  modName: string | null
  token: string
  enabled?: boolean
  pollInterval?: number
}

export function useEditorJobs({ modName, token, enabled = true, pollInterval = 3000 }: UseEditorJobsOptions) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [jobsError, setJobsError] = useState(false)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [activeJobOutput, setActiveJobOutput] = useState('')

  const jobsFetch = useCallback(async (endpoint: string, options: RequestInit = {}) => {
    return fetch(`${JOBS_API}${endpoint}`, {
      ...options,
      headers: {
        ...options.headers,
        'token': token,
        'Content-Type': 'application/json',
      },
    })
  }, [token])

  const fetchJobs = useCallback(async (
    onStatusUpdate?: (jobId: string, status: 'completed' | 'failed') => void
  ) => {
    if (!modName) return
    try {
      const res = await jobsFetch('/jobs')
      if (!res.ok) throw new Error('Failed')
      const data = await res.json()
      const allJobs = data.jobs || []
      const modJobs = allJobs.filter((j: Job) => {
        if (!j.work_dir) return false
        const wd = j.work_dir.toLowerCase()
        return wd.includes(`/${modName}/`) || wd.endsWith(`/${modName}`)
      })
      setJobs(modJobs)
      setJobsError(false)

      if (activeJobId) {
        const active = allJobs.find((j: Job) => j.id === activeJobId)
        if (active) {
          setActiveJobOutput(active.output || '')
          if (active.status === 'completed' || active.status === 'failed') {
            onStatusUpdate?.(activeJobId, active.status)
          }
        }
      }
    } catch {
      setJobsError(true)
    }
  }, [activeJobId, modName, jobsFetch])

  const handleCancel = useCallback(async (jobId: string) => {
    try {
      await jobsFetch(`/jobs/${jobId}/cancel`, { method: 'POST' })
      fetchJobs()
    } catch {}
  }, [jobsFetch, fetchJobs])

  const selectJob = useCallback((jobId: string, output?: string) => {
    setActiveJobId(jobId)
    setActiveJobOutput(output || '')
  }, [])

  // Polling
  useEffect(() => {
    if (!enabled || !modName) return
    fetchJobs()
    const interval = setInterval(() => fetchJobs(), pollInterval)
    return () => clearInterval(interval)
  }, [fetchJobs, enabled, modName, pollInterval])

  const activeJob = jobs.find(j => j.id === activeJobId)
  const isJobRunning = activeJob?.status === 'running' || activeJob?.status === 'pending'
  const recentJobs = jobs.slice(0, 20)
  const runningCount = jobs.filter(j => j.status === 'running').length

  return {
    jobs,
    jobsError,
    activeJobId,
    activeJobOutput,
    activeJob,
    isJobRunning,
    recentJobs,
    runningCount,
    fetchJobs,
    handleCancel,
    selectJob,
  }
}
