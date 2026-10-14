'use client'

import { useEffect, useMemo, useRef, useState } from 'react'

type Caption = { id: string; text: string; timestamp: number; speaker?: string | null }
type Session = { id: string; title: string; subject: string; source: string; status: string; audioStatus: string; startedAt: string; captions: Caption[] }
type Tab = 'Live classroom' | 'Class summary' | 'Full transcript' | 'Previous classes'
const API = process.env.NEXT_PUBLIC_CAPTION_API_URL || 'http://localhost:8000'
const tabs: Tab[] = ['Live classroom', 'Class summary', 'Full transcript', 'Previous classes']
const time = (seconds: number) => new Date(seconds * 1000).toISOString().slice(14, 19)

export default function Classroom() {
  const [tab, setTab] = useState<Tab>('Live classroom')
  const [session, setSession] = useState<Session | null>(null)
  const [captions, setCaptions] = useState<Caption[]>([])
  const [recording, setRecording] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [message, setMessage] = useState('Turn on the microphone to begin.')
  const [error, setError] = useState('')
  const [size, setSize] = useState(28)
  const [query, setQuery] = useState('')
  const socket = useRef<WebSocket | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const uploadQueue = useRef(Promise.resolve())

  const receive = (event: any) => {
    if (event.type === 'caption') setCaptions(old => old.some(c => c.id === event.id) ? old : [...old, event])
    if (event.type === 'status') {
      setSession(old => old ? { ...old, status: event.state, audioStatus: event.audioStatus } : old)
      setMessage(event.message)
    }
    if (event.type === 'error') setError(event.message)
  }
  const connect = (id: string) => {
    socket.current?.close()
    const ws = new WebSocket(API.replace(/^http/, 'ws') + `/ws/captions/${id}`)
    socket.current = ws
    ws.onmessage = ({ data }) => receive(JSON.parse(data))
    ws.onerror = () => setError('Caption connection failed. Check that the caption API is running.')
  }
  const getOrCreate = async () => {
    if (session) return session
    const response = await fetch(`${API}/api/captions/sessions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source: 'microphone-questions', title: 'Classroom discussion', subject: 'Live captions' }) })
    if (!response.ok) throw new Error((await response.json()).detail || 'Could not start the classroom.')
    const created = await response.json()
    setSession(created); setCaptions(created.captions || []); connect(created.id)
    history.replaceState({}, '', `?session=${created.id}`)
    return created as Session
  }
  useEffect(() => {
    const id = new URLSearchParams(location.search).get('session')
    if (id) fetch(`${API}/api/captions/sessions/${id}`).then(r => r.ok ? r.json() : Promise.reject(new Error('Classroom session was not found.'))).then(found => { setSession(found); setCaptions(found.captions || []); connect(found.id) }).catch(reason => setError(reason.message))
    return () => socket.current?.close()
  }, [])

  const sendChunk = (blob: Blob, active: Session) => {
    uploadQueue.current = uploadQueue.current.catch(() => undefined).then(async () => {
      setProcessing(true)
      const body = new FormData()
      body.append('audio', blob, 'live-caption.webm')
      const response = await fetch(`${API}/api/captions/sessions/${active.id}/microphone`, { method: 'POST', body })
      if (!response.ok) throw new Error((await response.json()).detail || 'A microphone chunk could not be transcribed.')
    }).catch(reason => setError(reason.message)).finally(() => { if (!recording) setProcessing(false) })
  }
  const toggleMicrophone = async () => {
    if (recording) { recorder.current?.stop(); return }
    try {
      setError('')
      const active = await getOrCreate()
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const type = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : undefined
      const mediaRecorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined)
      recorder.current = mediaRecorder
      mediaRecorder.ondataavailable = event => { if (event.data.size) sendChunk(event.data, active) }
      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach(track => track.stop())
        setRecording(false)
        setMessage('Finishing the last caption…')
        await uploadQueue.current
        setProcessing(false); setMessage('Ready for the next speaker.')
      }
      // A short chunk is transcribed approximately every 2.2 seconds while
      // the mic stays on. This is low-latency chunked transcription.
      mediaRecorder.start(2200)
      setRecording(true); setProcessing(false)
      setMessage('Microphone is on. Captions are being sent while you speak.')
    } catch (reason: any) { setError(reason.name === 'NotAllowedError' ? 'Microphone permission was denied. Allow it in your browser and try again.' : reason.message) }
  }
  const copyLink = async () => {
    try { await navigator.clipboard.writeText(location.href); setMessage('Classroom link copied. Everyone on this link can read captions or use the microphone.') }
    catch { setError('Could not copy the classroom link.') }
  }
  const filtered = useMemo(() => captions.filter(c => c.text.toLowerCase().includes(query.toLowerCase())), [captions, query])
  const status = recording ? 'Microphone on · captioning live' : processing ? 'Transcribing latest audio' : session?.status === 'listening' ? 'Ready for speech' : 'Not connected'

  return <div className="dashboard">
    <aside><div className="brand"><span>CA</span><b>ClassAccess<small>Classroom captions</small></b></div><nav>{tabs.map(item => <button key={item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>{item}</button>)}</nav><p className="side-note">No admin setup needed.<br/>The first microphone use creates the class.</p></aside>
    <main className="main-content">
      {tab === 'Live classroom' && <><header className="top"><div><p className="eyebrow">LIVE CLASSROOM</p><h1>{session?.title || 'Classroom captions'}</h1><p>{message}</p></div><span className={'status ' + (session?.status === 'listening' ? 'ready' : '')}>{status}</span></header>{error && <div className="alert" role="alert">{error}</div>}
        <section className="actions"><button className={'mic-button ' + (recording ? 'recording' : '')} disabled={processing && !recording} onClick={toggleMicrophone}>{recording ? '● Turn microphone off' : processing ? 'Transcribing…' : '🎙 Turn microphone on'}</button>{session && <button className="secondary" onClick={copyLink}>Copy classroom link</button>}</section><p className="help">While the microphone is on, the app sends short audio chunks for captions. Every person using this classroom link has the same control.</p>
        <section className="captions" aria-live="polite">{captions.length ? captions.map(c => <article key={c.id}><time>{time(c.timestamp)}</time><p style={{ fontSize: size }}>{c.text}</p></article>) : <div className="empty"><b>Captions will appear here</b><p>Turn the microphone on and start speaking.</p></div>}</section><label className="size">Caption size <input aria-label="Caption size" type="range" min="20" max="42" step="2" value={size} onChange={e => setSize(+e.target.value)} /></label></>}
      {tab === 'Class summary' && <section className="page"><p className="eyebrow">CLASS SUMMARY</p><h1>{session?.title || 'No class selected'}</h1>{captions.length ? <><p className="warning">Automatic summaries are not configured for this local prototype.</p><article className="summary-card"><h2>Recent discussion</h2><p>{captions.slice(-4).map(c => c.text).join(' ')}</p></article><button className="secondary" onClick={() => setTab('Full transcript')}>View full transcript</button></> : <div className="empty">Captions from the live class will be available here.</div>}</section>}
      {tab === 'Full transcript' && <section className="page"><p className="eyebrow">FULL TRANSCRIPT</p><h1>{session?.title || 'Transcript'}</h1><input className="search" placeholder="Search transcript..." value={query} onChange={e => setQuery(e.target.value)} />{captions.length ? <div className="transcript">{filtered.length ? filtered.map(c => <article key={c.id}><time>{time(c.timestamp)}</time><p>{c.text}</p></article>) : <div className="empty">No captions match “{query}”.</div>}</div> : <div className="empty">No transcript is available yet.</div>}</section>}
      {tab === 'Previous classes' && <section className="page"><p className="eyebrow">PREVIOUS CLASSES</p><h1>Class archive</h1>{session && captions.length ? <article className="summary-card"><h2>{session.title}</h2><p>{new Date(session.startedAt).toLocaleString()} · Current browser session</p><button className="secondary" onClick={() => setTab('Full transcript')}>Open transcript</button></article> : <div className="empty">No previous classes have been saved in this browser.</div>}</section>}
    </main>
  </div>
}
