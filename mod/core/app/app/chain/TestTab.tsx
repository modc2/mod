"use client"

// TEST — run the open project's Mocha/Chai tests against Hardhat's in-process
// EVM. Nothing touches a real network and no wallet is involved: the chain
// module compiles the contracts, runs the suite, and hands back one row per
// test. Failures come back with the assertion that broke.

import { useState, useCallback, useEffect } from 'react'
import { toast } from 'react-toastify'
import { TERM_FONT, ACCENT, DANGER, chainApi } from './shared'
import { Label, Btn, Input, Empty, panelStyle } from './ui'
import { isTest, stem, STARTER_TEST, type ProjectsApi } from './projects'

interface TestRow {
  title: string
  suite: string
  duration: number | null
  passed: boolean
  error?: string
}

interface RunResult {
  ok: boolean
  passing: number
  failing: number
  duration: number
  tests: TestRow[]
  output: string
  error?: string
}

export function TestTab({ projects, address }: { projects: ProjectsApi; address: string }) {
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<RunResult | null>(null)
  const [grep, setGrep] = useState('')
  const [showOutput, setShowOutput] = useState(false)

  const project = projects.project
  const testFiles = Object.keys(project?.files || {}).filter(isTest)

  // Results belong to the project that produced them.
  useEffect(() => { setResult(null) }, [project?.name])

  const run = useCallback(async () => {
    if (!project) return
    setRunning(true)
    setResult(null)
    try {
      // Save first so what runs is what's on screen.
      await projects.save().catch(() => {})
      const res = await chainApi('/build/test', {
        body: { address: address || undefined, files: project.files, grep: grep.trim() || undefined },
      })
      setResult(res)
      setShowOutput(!res.ok && !res.tests?.length)
      if (res.ok) toast.success(`${res.passing} passing`)
      else if (res.failing) toast.error(`${res.failing} failing`)
    } catch (e: any) {
      toast.error(e?.message || 'test run failed')
      setResult({
        ok: false, passing: 0, failing: 0, duration: 0, tests: [],
        output: '', error: e?.message || 'test run failed',
      })
      setShowOutput(true)
    } finally {
      setRunning(false)
    }
  }, [project, projects, address, grep])

  if (!project) return <Empty>No project open — start one from the sidebar.</Empty>

  const addTest = () => {
    const contract = Object.keys(project.files).find(p => p.endsWith('.sol'))
    const name = contract ? stem(contract) : 'Contract'
    projects.addFile(`test/${name}.test.js`, STARTER_TEST(name))
    toast.success(`test/${name}.test.js added`)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

      <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
        <Btn onClick={run} disabled={running || testFiles.length === 0} full>
          {running ? 'RUNNING…' : '▶ RUN TESTS'}
        </Btn>
        <div style={{ width: '100%', maxWidth: '220px' }}>
          <Input value={grep} onChange={setGrep} placeholder="only tests matching…" onEnter={run} />
        </div>
        <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)' }}>
          {testFiles.length
            ? `${testFiles.length} test file${testFiles.length === 1 ? '' : 's'} · hardhat + chai · in-process EVM`
            : 'this project has no tests yet'}
        </span>
        {testFiles.length === 0 && (
          <Btn size="sm" active={false} onClick={addTest}>+ ADD A TEST</Btn>
        )}
      </div>

      {running && (
        <div style={{ ...panelStyle, padding: '14px', fontFamily: TERM_FONT, fontSize: '14px', color: ACCENT }}>
          {'> '}compiling contracts and running the suite…
        </div>
      )}

      {result && (
        <>
          <div style={{
            ...panelStyle, padding: '12px 16px', display: 'flex', gap: '18px',
            alignItems: 'center', flexWrap: 'wrap',
            borderColor: result.ok ? ACCENT : DANGER,
          }}>
            <span style={{
              fontFamily: TERM_FONT, fontSize: '14px', letterSpacing: '0.1em',
              color: result.ok ? ACCENT : DANGER,
            }}>
              {result.ok ? '✓ PASSING' : '✗ FAILING'}
            </span>
            <span style={{ fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-secondary)' }}>
              {result.passing} passed
            </span>
            {result.failing > 0 && (
              <span style={{ fontFamily: TERM_FONT, fontSize: '14px', color: DANGER }}>
                {result.failing} failed
              </span>
            )}
            {result.duration > 0 && (
              <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)' }}>
                {result.duration}ms
              </span>
            )}
            <Btn size="sm" active={showOutput} style={{ marginLeft: 'auto' }}
              onClick={() => setShowOutput(o => !o)}>
              RAW OUTPUT
            </Btn>
          </div>

          {result.error && result.tests.length === 0 && (
            <div style={{
              ...panelStyle, padding: '12px', borderColor: DANGER,
              fontFamily: TERM_FONT, fontSize: '14px', color: DANGER,
              maxHeight: '320px', overflow: 'auto',
            }}>
              <Label style={{ color: DANGER }} note="usually a compile error">THE SUITE NEVER RAN</Label>
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{result.error}</pre>
            </div>
          )}

          {result.tests.length > 0 && (
            <div>
              <Label>RESULTS</Label>
              {result.tests.map((t, i) => (
                <div key={i} style={{
                  ...panelStyle, padding: '10px 14px', marginBottom: '6px',
                  borderColor: t.passed ? 'var(--border-color)' : DANGER,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                    <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: t.passed ? ACCENT : DANGER }}>
                      {t.passed ? '✓' : '✗'}
                    </span>
                    {t.suite && (
                      <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)' }}>
                        {t.suite}
                      </span>
                    )}
                    <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-primary)' }}>
                      {t.title}
                    </span>
                    {t.duration !== null && (
                      <span style={{
                        fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', marginLeft: 'auto',
                      }}>
                        {t.duration}ms
                      </span>
                    )}
                  </div>
                  {!t.passed && t.error && (
                    <pre style={{
                      margin: '8px 0 0', fontFamily: TERM_FONT, fontSize: '14px', color: DANGER,
                      whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                    }}>
                      {t.error}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          )}

          {showOutput && result.output && (
            <div style={{
              ...panelStyle, padding: '14px', fontFamily: TERM_FONT, fontSize: '14px',
              color: 'var(--text-secondary)', maxHeight: '360px', overflow: 'auto',
            }}>
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{result.output}</pre>
            </div>
          )}
        </>
      )}
    </div>
  )
}
