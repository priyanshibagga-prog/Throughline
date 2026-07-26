import { useState, useRef, useEffect } from "react"

// ─── API config ─────────────────────────────────────────────────────────────

const API_BASE = "http://localhost:8000"

// ─── Types ────────────────────────────────────────────────────────────────────

type Screen = "signin" | "sources" | "topics" | "readtime" | "newspaper"

interface Article {
  id: string
  source: string
  sourceLogo: string
  section: string
  headline: string
  deck: string
  byline?: string
  readMinutes: number
  url: string
  aiSummary: string
  historicalContext?: TimelineEvent[]
  recentEvents?: TimelineEvent[]
  status?: string
  image?: string
}

interface TimelineEvent {
  date: string
  text: string
}

// ─── Static onboarding option lists (mirrors the backend's fixed lists) ──────

const SOURCES = [
  { id: "BBC", name: "BBC", desc: "UK-based, broad international coverage." },
  { id: "Al Jazeera", name: "Al Jazeera", desc: "Strong regional depth on the Middle East and Global South." },
  { id: "The Guardian", name: "The Guardian", desc: "UK-based, analysis-driven, long-form leaning." },
  { id: "NPR", name: "NPR", desc: "US public radio, in-depth domestic reporting." },
]

const TOPICS = [
  "Headlines", "World", "U.S. News", "Politics", "Middle East",
  "Europe", "Asia", "Africa", "Americas", "Technology",
  "Business & Economy", "Markets & Finance", "Science", "Health",
  "Climate & Environment", "Culture", "Arts & Books", "Film & TV",
  "Sports", "Opinion & Analysis", "Education", "Travel", "Food & Drink",
]

const READ_TIMES = [
  { id: "15", label: "15 min", desc: "a few essential stories" },
  { id: "30", label: "30 min", desc: "a well-rounded edition" },
  { id: "45", label: "45 min", desc: "deeper, more complete coverage" },
  { id: "60", label: "1 hour", desc: "the full picture" },
]

// ─── Helpers ──────────────────────────────────────────────────────────────────

function todayIso() {
  return new Date().toISOString().split("T")[0]
}

function formatDateLabel(iso: string) {
  return new Date(iso + "T00:00:00").toLocaleDateString("en-US", {
    weekday: "long", month: "long", day: "numeric",
  })
}

function mapStoryToArticle(story: any): Article {
  const wordCount = (story.full_text || story.summary || "").split(/\s+/).filter(Boolean).length
  return {
    id: String(story.id),
    source: story.source || "Unknown source",
    sourceLogo: (story.source || "??").slice(0, 3).toUpperCase(),
    section: (story.topics && story.topics[0]) || "General",
    headline: story.headline,
    deck: story.summary,
    byline: story.source ? `${story.source}` : undefined,
    readMinutes: Math.max(1, Math.round(wordCount / 200)),
    url: story.url || "#",
    aiSummary: story.summary,
    historicalContext: story.historical_context && story.historical_context.length > 0 ? story.historical_context : undefined,
    recentEvents: story.recent_timeline && story.recent_timeline.length > 0 ? story.recent_timeline : undefined,
    status: story.status,
  }
}

// ─── Shared paper layout wrapper ──────────────────────────────────────────────

function PaperShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="paper-bg min-h-screen" style={{ color: "#1a1505" }}>
      <div className="border-b border-[#b8a888]">
        <div className="max-w-5xl mx-auto px-6 py-2 flex items-center justify-end">
          <div className="w-6 h-6 rounded-full bg-[#5a4a2a] flex items-center justify-center">
            <span className="font-mono-data text-[9px] text-[#f2e8d0]">Y</span>
          </div>
        </div>
      </div>
      <div className="border-b-4 border-double border-[#1a1505] py-5 text-center">
        <div className="font-blackletter text-6xl md:text-7xl text-[#1a1505] leading-none tracking-wide">
          Throughline
        </div>
        <div className="flex items-center gap-0 mt-2">
          <div className="flex-1 h-px bg-[#b8a888]" />
          <span className="font-mono-data text-[9px] tracking-[0.35em] text-[#8a7855] uppercase px-4">
            Your personalised daily edition
          </span>
          <div className="flex-1 h-px bg-[#b8a888]" />
        </div>
      </div>
      {children}
    </div>
  )
}

// ─── Newspaper components ─────────────────────────────────────────────────────

function NpTimeline({ events, onClose }: { events: TimelineEvent[]; onClose: () => void }) {
  return (
    <div className="mt-3 border border-[#b8a888] bg-[#ede3c8] px-4 py-4">
      <div className="flex items-center justify-between mb-4">
        <div className="ornament-rule flex-1 mr-4">
          <span className="font-playfair text-[10px] tracking-[0.25em] text-[#5a4a2a] uppercase px-2">
            Timeline
          </span>
        </div>
        <button
          onClick={onClose}
          className="font-mono-data text-[9px] text-[#8a7855] hover:text-[#2a1e08] transition-colors tracking-widest"
        >
          CLOSE ×
        </button>
      </div>
      <div>
        {events.map((ev, i) => (
          <div key={i} className="relative pl-5 pb-5 np-timeline-line last:pb-0">
            <div className="absolute left-0 top-[3px] w-3.5 h-3.5 rounded-full border border-[#5a4a2a] bg-[#ede3c8] flex items-center justify-center">
              <div className="w-1 h-1 rounded-full bg-[#5a4a2a]" />
            </div>
            <div className="font-mono-data text-[9px] text-[#8a7855] mb-0.5 tracking-widest uppercase">
              {ev.date}
            </div>
            <div className="np-body text-[12.5px]">{ev.text}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function NpArticle({ article, index }: { article: Article; index: number }) {
  const [showHistory, setShowHistory] = useState(false)
  const [showRecent, setShowRecent] = useState(false)
  const [showSummary, setShowSummary] = useState(false)

  const toggleHistory = () => { setShowHistory(v => !v); if (showRecent) setShowRecent(false) }
  const toggleRecent = () => { setShowRecent(v => !v); if (showHistory) setShowHistory(false) }

  const isLead = index === 0

  return (
    <article className={`${isLead ? "col-span-full border-b-2 border-[#2a1e08] pb-6 mb-2" : "border-b border-[#b8a888] pb-5"}`}>
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <span className="font-mono-data text-[9px] tracking-[0.3em] text-[#8a7855] uppercase border border-[#b8a888] px-1.5 py-0.5">
          {article.section}
        </span>
        <span className="font-mono-data text-[9px] text-[#b8a888]">{article.source}</span>
        <span className="font-mono-data text-[9px] text-[#b8a888]">· {article.readMinutes} min</span>
        {article.status && (
          <span className="font-mono-data text-[9px] tracking-widest text-[#4a6741] uppercase">
            {article.status === "update" ? "Updated" : "New"}
          </span>
        )}
      </div>

      {isLead && article.image && (
        <div className="mb-4 overflow-hidden bg-[#d4c8a8]">
          <img src={article.image} alt={article.headline} className="w-full max-h-64 object-cover mix-blend-multiply opacity-90" />
        </div>
      )}

      <h2 className={`font-playfair font-black text-[#1a1505] leading-tight mb-1 ${isLead ? "text-4xl md:text-5xl" : "text-xl"}`}>
        {article.headline}
      </h2>

      {isLead && (
        <p className="np-body text-[14px] text-[#3a2e18] mb-2">{article.deck}</p>
      )}

      <div className="flex items-center gap-3 mb-3 mt-1">
        {article.byline && (
          <>
            <span className="font-mono-data text-[9px] text-[#8a7855] tracking-wider">{article.byline}</span>
            <span className="text-[#b8a888]">·</span>
          </>
        )}
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono-data text-[9px] text-[#5a4a2a] hover:text-[#1a1505] underline underline-offset-2 decoration-[#b8a888] tracking-wider transition-colors"
        >
          Read full story ↗
        </a>
      </div>

      <button onClick={() => setShowSummary(v => !v)} className="w-full text-left mb-2">
        <div className={`border px-3 py-2.5 transition-colors ${showSummary ? "border-[#5a4a2a] bg-[#e8dfc4]" : "border-[#c4b896] bg-[#ede3c8] hover:border-[#8a7855]"}`}>
          <div className="flex items-center justify-between">
            <span className="font-mono-data text-[8px] tracking-[0.3em] text-[#5a4a2a] uppercase font-medium">✦ AI Summary</span>
            <span className="font-mono-data text-[9px] text-[#8a7855]">{showSummary ? "▲ collapse" : "▼ expand"}</span>
          </div>
          {showSummary && (
            <p className="np-body text-[12.5px] mt-2.5 border-t border-[#c4b896] pt-2.5">{article.aiSummary}</p>
          )}
        </div>
      </button>

      <div className="flex flex-wrap gap-2">
        {article.historicalContext && (
          <button
            onClick={toggleHistory}
            className={`font-mono-data text-[9px] tracking-[0.15em] uppercase px-2.5 py-1.5 border transition-all ${showHistory ? "border-[#5a4a2a] bg-[#5a4a2a] text-[#f2e8d0]" : "border-[#c4b896] text-[#5a4a2a] hover:border-[#5a4a2a]"}`}
          >
            ↳ Historical context
          </button>
        )}
        {article.recentEvents && (
          <button
            onClick={toggleRecent}
            className={`font-mono-data text-[9px] tracking-[0.15em] uppercase px-2.5 py-1.5 border transition-all ${showRecent ? "border-[#5a4a2a] bg-[#5a4a2a] text-[#f2e8d0]" : "border-[#c4b896] text-[#5a4a2a] hover:border-[#5a4a2a]"}`}
          >
            ↳ Catch up — last few days
          </button>
        )}
      </div>

      {showHistory && article.historicalContext && (
        <NpTimeline events={article.historicalContext} onClose={() => setShowHistory(false)} />
      )}
      {showRecent && article.recentEvents && (
        <NpTimeline events={article.recentEvents} onClose={() => setShowRecent(false)} />
      )}
    </article>
  )
}

// ─── Screens ──────────────────────────────────────────────────────────────────

function SignInScreen({ onNext }: { onNext: (email: string) => void }) {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")

  return (
    <PaperShell>
      <div className="max-w-md mx-auto px-6 py-16">
        <div className="font-mono-data text-[9px] tracking-[0.3em] text-[#8a7855] uppercase mb-1">
          Welcome
        </div>
        <h1 className="font-playfair text-4xl font-black text-[#1a1505] mb-2 leading-tight">
          Sign in to your edition
        </h1>
        <p className="np-body text-[14px] text-[#5a4a2a] mb-10">
          Your personalised newspaper is waiting.
        </p>

        <div className="space-y-5">
          <div>
            <label className="font-mono-data text-[9px] tracking-[0.25em] text-[#8a7855] uppercase block mb-2">
              Email address
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className="w-full bg-[#ede3c8] border border-[#b8a888] text-[#1a1505] text-sm px-4 py-3 focus:outline-none focus:border-[#5a4a2a] transition-colors placeholder:text-[#b8a888] font-playfair"
            />
          </div>
          <div>
            <label className="font-mono-data text-[9px] tracking-[0.25em] text-[#8a7855] uppercase block mb-2">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="w-full bg-[#ede3c8] border border-[#b8a888] text-[#1a1505] text-sm px-4 py-3 focus:outline-none focus:border-[#5a4a2a] transition-colors placeholder:text-[#b8a888]"
            />
          </div>
        </div>

        <button
          onClick={() => onNext(email || "test@example.com")}
          className="w-full mt-8 py-3.5 bg-[#5a4a2a] text-[#f2e8d0] font-playfair font-bold text-sm tracking-wider hover:bg-[#3a2e18] transition-colors duration-200"
        >
          Open my edition →
        </button>
      </div>
    </PaperShell>
  )
}

function SourcesScreen({ onNext }: { onNext: (selected: string[]) => void }) {
  const [selected, setSelected] = useState<string[]>([])
  const toggle = (id: string) =>
    setSelected((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id])

  return (
    <PaperShell>
      <div className="max-w-4xl mx-auto px-6 py-12">
        <div className="font-mono-data text-[9px] tracking-[0.3em] text-[#8a3a2a] uppercase mb-2">
          Step 1 of 3
        </div>
        <h1 className="font-playfair text-4xl font-black text-[#1a1505] mb-3 leading-tight">
          Which outlets do you trust?
        </h1>
        <p className="np-body text-[14px] text-[#5a4a2a] mb-10 max-w-lg">
          Every story in your paper is pulled only from the outlets you select here.
          You can change this anytime.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4 mb-10">
          {SOURCES.map((s) => {
            const active = selected.includes(s.id)
            return (
              <button
                key={s.id}
                onClick={() => toggle(s.id)}
                className={`text-left p-5 border-2 transition-all duration-200 relative bg-[#ede3c8] hover:border-[#5a4a2a] ${
                  active ? "border-[#5a4a2a]" : "border-[#c4b896]"
                }`}
              >
                <div className={`absolute top-4 right-4 w-6 h-6 rounded-full border-2 flex items-center justify-center transition-all ${active ? "border-[#4a6741] bg-[#4a6741]" : "border-[#c4b896] bg-transparent"}`}>
                  {active && (
                    <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
                      <path d="M1.5 4L4 6.5L8.5 1.5" stroke="#f2e8d0" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </div>
                <div className="font-playfair text-xl font-black text-[#1a1505] mb-2 pr-8">{s.name}</div>
                <div className="np-body text-[13px] text-[#5a4a2a]">{s.desc}</div>
              </button>
            )
          })}
        </div>

        <button
          onClick={() => onNext(selected)}
          disabled={selected.length === 0}
          className="px-10 py-3.5 bg-[#5a4a2a] text-[#f2e8d0] font-playfair font-bold text-sm tracking-wider hover:bg-[#3a2e18] transition-colors duration-200 disabled:opacity-30 disabled:cursor-not-allowed"
        >
          Continue →
        </button>
      </div>
    </PaperShell>
  )
}

function TopicsScreen({ onNext }: { onNext: (topics: string[]) => void }) {
  const [selected, setSelected] = useState<string[]>(["Headlines"])
  const toggle = (t: string) => {
    if (t === "Headlines") return
    setSelected((prev) => prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t])
  }

  return (
    <PaperShell>
      <div className="max-w-4xl mx-auto px-6 py-12">
        <div className="font-mono-data text-[9px] tracking-[0.3em] text-[#8a3a2a] uppercase mb-2">
          Step 2 of 3
        </div>
        <h1 className="font-playfair text-4xl font-black text-[#1a1505] mb-3 leading-tight">
          What do you want to read?
        </h1>
        <p className="np-body text-[14px] text-[#5a4a2a] mb-10 max-w-lg">
          We care about what matters to you. You can change this anytime.
        </p>

        <div className="flex flex-wrap gap-2.5 mb-12">
          {TOPICS.map((t) => {
            const active = selected.includes(t)
            const locked = t === "Headlines"
            return (
              <button
                key={t}
                onClick={() => toggle(t)}
                className={`px-4 py-2 rounded-full border text-sm font-playfair transition-all duration-200 ${
                  active
                    ? "border-[#5a4a2a] bg-[#5a4a2a] text-[#f2e8d0]"
                    : "border-[#c4b896] bg-[#ede3c8] text-[#3a2e18] hover:border-[#5a4a2a]"
                } ${locked ? "cursor-default" : ""}`}
              >
                {t}
                {locked && <span className="ml-1.5 font-mono-data text-[8px] opacity-60 tracking-wider">✦</span>}
              </button>
            )
          })}
        </div>

        <button
          onClick={() => onNext(selected)}
          className="px-10 py-3.5 bg-[#5a4a2a] text-[#f2e8d0] font-playfair font-bold text-sm tracking-wider hover:bg-[#3a2e18] transition-colors duration-200"
        >
          Continue →
        </button>
      </div>
    </PaperShell>
  )
}

function ReadTimeScreen({ onNext, submitting, error }: { onNext: (time: string) => void; submitting: boolean; error: string | null }) {
  const [selected, setSelected] = useState("30")

  return (
    <PaperShell>
      <div className="max-w-xl mx-auto px-6 py-12">
        <div className="font-mono-data text-[9px] tracking-[0.3em] text-[#8a3a2a] uppercase mb-2">
          Step 3 of 3
        </div>
        <h1 className="font-playfair text-4xl font-black text-[#1a1505] mb-3 leading-tight">
          How long do you have?
        </h1>
        <p className="np-body text-[14px] text-[#5a4a2a] mb-10 max-w-md">
          We size your daily edition to fit exactly. Never more, never less.
        </p>

        <div className="space-y-3 mb-10">
          {READ_TIMES.map((rt) => {
            const active = selected === rt.id
            return (
              <button
                key={rt.id}
                onClick={() => setSelected(rt.id)}
                className={`w-full text-left px-5 py-4 border-2 bg-[#ede3c8] transition-all duration-200 flex items-center justify-between ${
                  active ? "border-[#5a4a2a]" : "border-[#c4b896] hover:border-[#8a7855]"
                }`}
              >
                <div>
                  <div className={`font-playfair text-2xl font-black mb-0.5 ${active ? "text-[#1a1505]" : "text-[#5a4a2a]"}`}>
                    {rt.label}
                  </div>
                  <div className="font-mono-data text-[10px] text-[#8a7855] tracking-wider">{rt.desc}</div>
                </div>
                <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-all ${active ? "border-[#4a6741] bg-[#4a6741]" : "border-[#c4b896]"}`}>
                  {active && <div className="w-2 h-2 rounded-full bg-[#f2e8d0]" />}
                </div>
              </button>
            )
          })}
        </div>

        {error && (
          <p className="np-body text-[13px] text-[#8a3a2a] mb-4">{error}</p>
        )}

        <button
          onClick={() => onNext(selected)}
          disabled={submitting}
          className="px-10 py-3.5 bg-[#5a4a2a] text-[#f2e8d0] font-playfair font-bold text-sm tracking-wider hover:bg-[#3a2e18] transition-colors duration-200 disabled:opacity-50"
        >
          {submitting ? "Building your edition…" : "Open my edition →"}
        </button>
      </div>
    </PaperShell>
  )
}

function NewspaperScreen({ userId }: { userId: number }) {
  const [stories, setStories] = useState<Article[] | null>(null)
  const [readingMinutes, setReadingMinutes] = useState<number>(30)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dates, setDates] = useState<string[]>([])
  const [selectedDateValue, setSelectedDateValue] = useState<string>("") // "" = today
  const [datePickerOpen, setDatePickerOpen] = useState(false)
  const pickerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setDatePickerOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [])

  async function fetchPaper(dateValue: string) {
    setLoading(true)
    setError(null)
    try {
      const url = dateValue
        ? `${API_BASE}/api/paper/${userId}?edition_date=${dateValue}`
        : `${API_BASE}/api/paper/${userId}`
      const res = await fetch(url)
      if (!res.ok) throw new Error(`API returned ${res.status}`)
      const data = await res.json()
      setReadingMinutes(data.reading_time_minutes)
      setStories((data.stories || []).map(mapStoryToArticle))

      const editionsRes = await fetch(`${API_BASE}/api/editions/${userId}`)
      const editionsData = await editionsRes.json()
      setDates(editionsData.dates || [])
    } catch (err) {
      console.error(err)
      setError("Couldn't reach the API. Make sure it's running: uvicorn api:app --reload")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchPaper(selectedDateValue)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDateValue, userId])

  const visibleArticles = stories || []
  const today = todayIso()
  const displayDateIso = selectedDateValue || today
  const displayDateLabel = formatDateLabel(displayDateIso)

  if (loading && !stories) {
    return (
      <PaperShell>
        <div className="text-center py-24 font-mono-data text-[11px] tracking-widest text-[#8a7855] uppercase">
          Fetching today's paper…
        </div>
      </PaperShell>
    )
  }

  if (error) {
    return (
      <PaperShell>
        <div className="text-center py-24 px-6">
          <p className="np-body text-[14px] text-[#8a3a2a]">{error}</p>
        </div>
      </PaperShell>
    )
  }

  return (
    <div className="paper-bg min-h-screen" style={{ color: "#1a1505" }}>
      <div className="border-b border-[#b8a888] bg-[#f2e8d0]">
        <div className="max-w-5xl mx-auto px-6 py-2 flex items-center justify-end">
          <div className="w-6 h-6 rounded-full bg-[#5a4a2a] flex items-center justify-center">
            <span className="font-mono-data text-[9px] text-[#f2e8d0]">Y</span>
          </div>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-6">
        <div className="border-b-4 border-double border-[#1a1505] py-5 text-center">
          <div className="font-blackletter text-6xl md:text-7xl text-[#1a1505] leading-none tracking-wide">
            Throughline
          </div>
          <div className="flex items-center gap-0 mt-2">
            <div className="flex-1 h-px bg-[#8a7855]" />
            <span className="font-mono-data text-[9px] tracking-[0.35em] text-[#8a7855] uppercase px-4">
              Your personalised daily edition · {readingMinutes} min read
            </span>
            <div className="flex-1 h-px bg-[#8a7855]" />
          </div>
        </div>

        <div className="border-b border-[#b8a888] py-2 flex items-center justify-between">
          <span className="font-mono-data text-[9px] tracking-widest text-[#8a7855]">
            {visibleArticles.length} STORIES
          </span>

          <div className="relative" ref={pickerRef}>
            <button onClick={() => setDatePickerOpen((v) => !v)} className="flex items-center gap-2 group">
              <span className="font-mono-data text-[10px] tracking-[0.2em] text-[#1a1505] uppercase hover:text-[#5a4a2a] transition-colors">
                {selectedDateValue === "" ? "Today" : displayDateLabel}
              </span>
              <span className="font-mono-data text-[9px] text-[#8a7855] group-hover:text-[#5a4a2a] transition-colors">
                {datePickerOpen ? "▲" : "▼"}
              </span>
            </button>

            {datePickerOpen && (
              <div className="absolute left-1/2 -translate-x-1/2 top-full mt-1 bg-[#f2e8d0] border border-[#b8a888] shadow-lg z-20 min-w-56">
                {dates.map((d) => {
                  const isToday = d === today
                  const value = isToday ? "" : d
                  const isSelected = value === selectedDateValue
                  return (
                    <button
                      key={d}
                      onClick={() => { setSelectedDateValue(value); setDatePickerOpen(false) }}
                      className={`w-full text-left px-4 py-2.5 font-mono-data text-[10px] tracking-wider flex items-center justify-between border-b border-[#e0d6bc] last:border-b-0 transition-colors ${
                        isSelected ? "bg-[#5a4a2a] text-[#f2e8d0]" : "text-[#5a4a2a] hover:bg-[#e8dfc4]"
                      }`}
                    >
                      <span>{isToday ? "Today" : formatDateLabel(d)}</span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          <span className="font-mono-data text-[9px] tracking-widest text-[#8a7855]">
            {displayDateLabel}
          </span>
        </div>

        {visibleArticles.length === 0 ? (
          <div className="text-center py-24">
            <p className="np-body text-[14px] text-[#5a4a2a]">Nothing new right now — check back later.</p>
          </div>
        ) : (
          <div className="py-6 grid grid-cols-1 md:grid-cols-3 gap-x-6">
            {visibleArticles[0] && (
              <div className="md:col-span-3 mb-4">
                <NpArticle article={visibleArticles[0]} index={0} />
              </div>
            )}
            {visibleArticles.slice(1).map((article, i) => (
              <div
                key={article.id}
                className={`${i < visibleArticles.slice(1).length - 1 ? "col-rule pr-6" : ""} mb-4 md:mb-0`}
              >
                <NpArticle article={article} index={i + 1} />
              </div>
            ))}
          </div>
        )}

        <div className="border-t-2 border-double border-[#1a1505] py-4 text-center">
          <div className="font-mono-data text-[9px] tracking-[0.35em] text-[#8a7855] uppercase">
            Throughline · {displayDateLabel} · End of edition
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Root ─────────────────────────────────────────────────────────────────────

export default function App() {
  const [screen, setScreen] = useState<Screen>("signin")
  const [email, setEmail] = useState("")
  const [sources, setSources] = useState<string[]>([])
  const [topics, setTopics] = useState<string[]>(["Headlines"])
  const [userId, setUserId] = useState<number | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  async function handleFinish(readTime: string) {
    setSubmitting(true)
    setSubmitError(null)
    try {
      const topicWeights: Record<string, number> = {}
      topics.forEach((t) => { topicWeights[t] = 2 })

      const res = await fetch(`${API_BASE}/api/users`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          sources,
          topic_weights: topicWeights,
          reading_time_minutes: parseInt(readTime, 10),
        }),
      })
      if (!res.ok) throw new Error(`Signup failed: ${res.status}`)
      const data = await res.json()
      setUserId(data.user_id)
      setScreen("newspaper")
    } catch (err) {
      console.error(err)
      setSubmitError("Couldn't save your preferences — is the API running? (uvicorn api:app --reload)")
    } finally {
      setSubmitting(false)
    }
  }

  switch (screen) {
    case "signin":
      return <SignInScreen onNext={(e) => { setEmail(e); setScreen("sources") }} />
    case "sources":
      return <SourcesScreen onNext={(s) => { setSources(s); setScreen("topics") }} />
    case "topics":
      return <TopicsScreen onNext={(t) => { setTopics(t); setScreen("readtime") }} />
    case "readtime":
      return <ReadTimeScreen onNext={handleFinish} submitting={submitting} error={submitError} />
    case "newspaper":
      return userId ? <NewspaperScreen userId={userId} /> : null
  }
}
