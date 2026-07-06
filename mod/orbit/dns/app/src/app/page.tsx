"use client"

import { useState, useEffect, useCallback } from "react"
import dynamic from "next/dynamic"

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  (process.env.NODE_ENV === "production"
    ? "https://modc2.com/api/dns"
    : "http://localhost:5380")

interface DnsRecord {
  name: string
  rtype: string
  value: string
  ttl: number
  node_id?: string
  updated_at?: string
}

interface Stats {
  zones?: number
  records?: number
  peers?: number
  uptime_secs?: number
  [key: string]: any
}

async function api(method: string, path: string, data?: any) {
  const opts: RequestInit = {
    method,
    headers: { "Content-Type": "application/json" },
  }
  if (data) opts.body = JSON.stringify(data)
  const res = await fetch(`${API_URL}${path}`, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error || err.detail || "Request failed")
  }
  return res.json()
}

const RECORD_TYPES = ["A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV"]

function DnsApp() {
  const [zones, setZones] = useState<string[]>([])
  const [zone, setZone] = useState("modc2.com")
  const [records, setRecords] = useState<DnsRecord[]>([])
  const [stats, setStats] = useState<Stats>({})
  const [peerCount, setPeerCount] = useState(0)
  const [nodeId, setNodeId] = useState("")

  const [name, setName] = useState("")
  const [rtype, setRtype] = useState("A")
  const [value, setValue] = useState("")
  const [ttl, setTtl] = useState(300)

  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")
  const [loading, setLoading] = useState(true)

  const flash = (msg: string, type: "error" | "success") => {
    if (type === "error") {
      setError(msg)
      setTimeout(() => setError(""), 4000)
    } else {
      setSuccess(msg)
      setTimeout(() => setSuccess(""), 3000)
    }
  }

  const fetchAll = useCallback(async () => {
    try {
      const [z, r, s, p] = await Promise.all([
        api("GET", "/zones").catch(() => ({ zones: [] })),
        api("GET", `/zones/${zone}/records`).catch(() => ({ records: [] })),
        api("GET", "/stats").catch(() => ({})),
        api("GET", "/peers").catch(() => ({ peer_count: 0 })),
      ])
      setZones(z.zones || [])
      setRecords(r.records || [])
      setStats(s)
      setPeerCount(p.peer_count || 0)
      setNodeId(p.node_id || "")
    } catch {
      flash("Failed to connect to DNS server", "error")
    } finally {
      setLoading(false)
    }
  }, [zone])

  useEffect(() => {
    fetchAll()
    const iv = setInterval(fetchAll, 10000)
    return () => clearInterval(iv)
  }, [fetchAll])

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name || !value) return flash("Name and value required", "error")
    try {
      await api("PUT", `/zones/${zone}/records`, {
        name,
        rtype,
        value,
        ttl,
      })
      flash(`${rtype} record added: ${name}`, "success")
      setName("")
      setValue("")
      fetchAll()
    } catch (err: any) {
      flash(err.message, "error")
    }
  }

  const handleDelete = async (rec: DnsRecord) => {
    try {
      await api("DELETE", `/zones/${zone}/records/${rec.name}/${rec.rtype}`)
      flash(`Deleted ${rec.rtype} ${rec.name}`, "success")
      fetchAll()
    } catch (err: any) {
      flash(err.message, "error")
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0a0a0f] text-white/60 font-mono flex items-center justify-center">
        <div className="animate-pulse">connecting to mod-dns...</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-[#e5e5e5] font-mono">
      <div className="max-w-5xl mx-auto p-4 md:p-6 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between border border-white/10 rounded-lg p-4 bg-white/[0.03]">
          <div>
            <h1 className="text-lg font-bold text-white">mod-dns</h1>
            <p className="text-xs text-white/40 mt-1">
              decentralized authoritative DNS — P2P via Kademlia + GossipSub
            </p>
          </div>
          <div className="flex items-center gap-4 text-xs text-white/40">
            <span>{peerCount} peer{peerCount !== 1 ? "s" : ""}</span>
            <span className="w-2 h-2 rounded-full bg-emerald-400 inline-block" />
          </div>
        </div>

        {/* Alerts */}
        {error && (
          <div className="border border-red-500/30 bg-red-500/10 text-red-300 rounded-lg p-3 text-sm">
            {error}
          </div>
        )}
        {success && (
          <div className="border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 rounded-lg p-3 text-sm">
            {success}
          </div>
        )}

        {/* Stats bar */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: "zones", val: stats.zones ?? zones.length },
            { label: "records", val: stats.records ?? records.length },
            { label: "peers", val: peerCount },
            {
              label: "uptime",
              val: stats.uptime_secs
                ? `${Math.floor(stats.uptime_secs / 3600)}h ${Math.floor(
                    (stats.uptime_secs % 3600) / 60
                  )}m`
                : "--",
            },
          ].map((s) => (
            <div
              key={s.label}
              className="border border-white/10 rounded-lg p-3 bg-white/[0.03] text-center"
            >
              <div className="text-lg font-bold text-white">{s.val}</div>
              <div className="text-xs text-white/40">{s.label}</div>
            </div>
          ))}
        </div>

        {/* Zone selector */}
        {zones.length > 1 && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-white/40">zone:</span>
            {zones.map((z) => (
              <button
                key={z}
                onClick={() => setZone(z)}
                className={`px-3 py-1 rounded border text-xs ${
                  z === zone
                    ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-300"
                    : "border-white/10 text-white/40 hover:text-white/60"
                }`}
              >
                {z}
              </button>
            ))}
          </div>
        )}

        {/* Add record form */}
        <form
          onSubmit={handleAdd}
          className="border border-white/10 rounded-lg p-4 bg-white/[0.03] space-y-3"
        >
          <h2 className="text-sm font-bold text-white/80">add record</h2>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
            <input
              placeholder="name (e.g. www)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="bg-white/5 border border-white/10 rounded px-3 py-2 text-sm text-white placeholder-white/20 focus:outline-none focus:border-emerald-500/50"
            />
            <select
              value={rtype}
              onChange={(e) => setRtype(e.target.value)}
              className="bg-white/5 border border-white/10 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-emerald-500/50"
            >
              {RECORD_TYPES.map((t) => (
                <option key={t} value={t} className="bg-[#0a0a0f]">
                  {t}
                </option>
              ))}
            </select>
            <input
              placeholder="value (e.g. 1.2.3.4)"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className="bg-white/5 border border-white/10 rounded px-3 py-2 text-sm text-white placeholder-white/20 focus:outline-none focus:border-emerald-500/50 md:col-span-2"
            />
            <input
              type="number"
              placeholder="TTL"
              value={ttl}
              onChange={(e) => setTtl(Number(e.target.value))}
              className="bg-white/5 border border-white/10 rounded px-3 py-2 text-sm text-white placeholder-white/20 focus:outline-none focus:border-emerald-500/50"
            />
          </div>
          <button
            type="submit"
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm rounded transition-colors"
          >
            add record
          </button>
        </form>

        {/* Records table */}
        <div className="border border-white/10 rounded-lg bg-white/[0.03] overflow-hidden">
          <div className="flex items-center justify-between p-4 border-b border-white/10">
            <h2 className="text-sm font-bold text-white/80">
              records — {zone}
            </h2>
            <span className="text-xs text-white/30">{records.length} total</span>
          </div>
          {records.length === 0 ? (
            <div className="p-8 text-center text-white/20 text-sm">
              no records in this zone
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-white/30 text-xs border-b border-white/10">
                    <th className="text-left p-3 font-normal">name</th>
                    <th className="text-left p-3 font-normal">type</th>
                    <th className="text-left p-3 font-normal">value</th>
                    <th className="text-left p-3 font-normal">ttl</th>
                    <th className="text-right p-3 font-normal" />
                  </tr>
                </thead>
                <tbody>
                  {records.map((rec, i) => (
                    <tr
                      key={`${rec.name}-${rec.rtype}-${i}`}
                      className="border-b border-white/5 hover:bg-white/[0.02]"
                    >
                      <td className="p-3 text-emerald-300">{rec.name}</td>
                      <td className="p-3">
                        <span className="px-2 py-0.5 rounded bg-white/5 text-xs text-white/60">
                          {rec.rtype}
                        </span>
                      </td>
                      <td className="p-3 text-white/70 break-all max-w-xs">
                        {rec.value}
                      </td>
                      <td className="p-3 text-white/40">{rec.ttl}s</td>
                      <td className="p-3 text-right">
                        <button
                          onClick={() => handleDelete(rec)}
                          className="text-red-400/60 hover:text-red-400 text-xs transition-colors"
                        >
                          delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Node info */}
        {nodeId && (
          <div className="text-xs text-white/20 text-center truncate">
            node: {nodeId}
          </div>
        )}
      </div>
    </div>
  )
}

export default dynamic(() => Promise.resolve(DnsApp), { ssr: false })
