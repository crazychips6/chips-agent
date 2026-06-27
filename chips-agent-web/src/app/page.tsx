'use client'
import Sidebar from '@/components/chat/Sidebar/Sidebar'
import { ChatArea } from '@/components/chat/ChatArea'
import { Suspense, useState } from 'react'

function HomeContent() {
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false)

  return (
    <div className="flex h-screen flex-col">
      <div className="flex flex-1 overflow-hidden">
        <Sidebar
          isMobileOpen={isMobileSidebarOpen}
          onMobileClose={() => setIsMobileSidebarOpen(false)}
        />

        {/* Mobile menu toggle button */}
        <button
          onClick={() => setIsMobileSidebarOpen(true)}
          className="fixed left-3 top-3 z-30 flex h-9 w-9 items-center justify-center rounded-xl bg-white/80 shadow-sm border border-primary/10 md:hidden backdrop-blur-sm"
          aria-label="Open menu"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#6B46C1" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 12h18M3 6h18M3 18h18" />
          </svg>
        </button>

        <ChatArea />
      </div>

      <footer className="flex shrink-0 items-center border-t border-primary/5 px-4 py-1.5">
        <a
          href="https://beian.miit.gov.cn/"
          target="_blank"
          rel="noopener noreferrer"
          className="text-[10px] text-muted-foreground/50 hover:text-muted-foreground/80 transition-colors"
        >
          沪ICP备2026028452号
        </a>
      </footer>
    </div>
  )
}

export default function Home() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center gradient-text text-2xl font-bold">Loading...</div>}>
      <HomeContent />
    </Suspense>
  )
}
