'use client'
import { Button } from '@/components/ui/button'
import useChatActions from '@/hooks/useChatActions'
import { useStore } from '@/store'
import { motion, AnimatePresence } from 'framer-motion'
import { useState, useEffect } from 'react'

const SidebarHeader = () => (
  <div className="flex items-center gap-2 px-1">
    <span className="text-sm font-bold gradient-text">Chips Agent</span>
  </div>
)

const NewChatButton = ({
  disabled,
  onClick,
}: {
  disabled: boolean
  onClick: () => void
}) => (
  <Button
    onClick={onClick}
    disabled={disabled}
    size="lg"
    className="h-9 w-full rounded-xl bg-primary text-xs font-medium text-white hover:bg-primary/80"
  >
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="mr-1">
      <path d="M5 12h14M12 5v14" />
    </svg>
    <span className="uppercase">New Chat</span>
  </Button>
)

interface SidebarProps {
  isMobileOpen: boolean
  onMobileClose: () => void
}

const Sidebar = ({ isMobileOpen, onMobileClose }: SidebarProps) => {
  const [isCollapsed, setIsCollapsed] = useState(false)
  const { clearChat, focusChatInput, initialize } = useChatActions()
  const { messages, isEndpointActive } = useStore()
  const [isMounted, setIsMounted] = useState(false)

  useEffect(() => {
    setIsMounted(true)
    initialize()
  }, [initialize])

  const handleNewChat = () => {
    clearChat()
    focusChatInput()
    onMobileClose()
  }

  // Sidebar content
  const sidebarContent = (
    <div className="w-60 space-y-5">
      <SidebarHeader />
      <NewChatButton disabled={messages.length === 0} onClick={handleNewChat} />
      {isMounted && (
        isEndpointActive ? (
          <div className="flex items-center gap-2 rounded-xl bg-positive/10 px-3 py-2">
            <div className="size-2 rounded-full bg-positive" />
            <span className="text-xs font-medium text-positive">Connected</span>
          </div>
        ) : (
          <div className="flex items-center gap-2 rounded-xl bg-destructive/10 px-3 py-2">
            <div className="size-2 rounded-full bg-destructive" />
            <span className="text-xs font-medium text-destructive">Connecting...</span>
          </div>
        )
      )}
    </div>
  )

  return (
    <>
      {/* Desktop sidebar */}
      <motion.aside
        className="relative hidden md:flex h-screen shrink-0 grow-0 flex-col overflow-hidden border-r border-primary/10 bg-white/50 backdrop-blur-sm px-2 py-3"
        initial={{ width: '16rem' }}
        animate={{ width: isCollapsed ? '2.5rem' : '16rem' }}
        transition={{ type: 'spring', stiffness: 300, damping: 30 }}
      >
        <motion.button
          onClick={() => setIsCollapsed(!isCollapsed)}
          className="absolute right-2 top-2 z-10 p-1 text-muted hover:text-primary"
          aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          type="button"
          whileTap={{ scale: 0.95 }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            className={`transform transition-transform ${isCollapsed ? 'rotate-180' : 'rotate-0'}`}
          >
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
            <line x1="9" y1="3" x2="9" y2="21" />
          </svg>
        </motion.button>
        <motion.div
          className="w-60 space-y-5"
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: isCollapsed ? 0 : 1, x: isCollapsed ? -20 : 0 }}
          transition={{ duration: 0.3, ease: 'easeInOut' }}
          style={{ pointerEvents: isCollapsed ? 'none' : 'auto' }}
        >
          {sidebarContent}
        </motion.div>
      </motion.aside>

      {/* Mobile sidebar overlay */}
      <AnimatePresence>
        {isMobileOpen && (
          <>
            <motion.div
              className="fixed inset-0 z-40 bg-black/30 md:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={onMobileClose}
            />
            <motion.aside
              className="fixed left-0 top-0 z-50 flex h-screen flex-col overflow-hidden border-r border-primary/10 bg-white/95 backdrop-blur-sm px-2 py-3 md:hidden"
              initial={{ x: '-100%' }}
              animate={{ x: 0 }}
              exit={{ x: '-100%' }}
              transition={{ type: 'spring', stiffness: 300, damping: 30 }}
            >
              <div className="flex items-center justify-between px-1 pb-3">
                <SidebarHeader />
                <button onClick={onMobileClose} className="p-1 text-muted hover:text-secondary">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M18 6L6 18M6 6l12 12" />
                  </svg>
                </button>
              </div>
              {sidebarContent}
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </>
  )
}

export default Sidebar
