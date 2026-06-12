'use client'
import { Button } from '@/components/ui/button'
import useChatActions from '@/hooks/useChatActions'
import { useStore } from '@/store'
import { motion, AnimatePresence } from 'framer-motion'
import { useState, useEffect, useCallback } from 'react'
import { isValidUrl } from '@/lib/utils'
import { toast } from 'sonner'

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

const DEFAULT_ENDPOINT = 'http://localhost:8648'

const Endpoint = () => {
  const { selectedEndpoint, isEndpointActive, setSelectedEndpoint, setMessages } = useStore()
  const { initialize } = useChatActions()
  const [isEditing, setIsEditing] = useState(false)
  const [endpointValue, setEndpointValue] = useState('')
  const [isMounted, setIsMounted] = useState(false)
  const [isHovering, setIsHovering] = useState(false)
  const [isRotating, setIsRotating] = useState(false)

  useEffect(() => {
    setEndpointValue(selectedEndpoint)
    setIsMounted(true)
  }, [selectedEndpoint])

  const getStatusColor = (isActive: boolean) =>
    isActive ? 'bg-positive' : 'bg-destructive'

  const handleSave = async () => {
    if (!isValidUrl(endpointValue)) {
      toast.error('Please enter a valid URL')
      return
    }
    const cleanEndpoint = endpointValue.replace(/\/$/, '').trim()
    setSelectedEndpoint(cleanEndpoint)
    setIsEditing(false)
    setIsHovering(false)
    setMessages([])
  }

  const handleCancel = () => {
    setEndpointValue(selectedEndpoint)
    setIsEditing(false)
    setIsHovering(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleSave()
    else if (e.key === 'Escape') handleCancel()
  }

  const handleRefresh = async () => {
    setIsRotating(true)
    await initialize()
    setTimeout(() => setIsRotating(false), 500)
  }

  return (
    <div className="flex flex-col items-start gap-2">
      <div className="text-xs font-medium uppercase text-muted">Server</div>
      {isEditing ? (
        <div className="flex w-full items-center gap-1">
          <input
            type="text"
            value={endpointValue}
            onChange={(e) => setEndpointValue(e.target.value)}
            onKeyDown={handleKeyDown}
            className="flex h-9 w-full items-center text-ellipsis rounded-xl border border-primary/15 bg-white p-3 text-xs font-medium text-secondary"
            autoFocus
          />
          <Button variant="ghost" size="icon" onClick={handleSave} className="hover:cursor-pointer hover:bg-transparent">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6B46C1" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 6L9 17l-5-5" />
            </svg>
          </Button>
        </div>
      ) : (
        <div className="flex w-full items-center gap-1">
          <motion.div
            className="relative flex h-9 w-full cursor-pointer items-center justify-between rounded-xl border border-primary/15 bg-white p-3"
            onMouseEnter={() => setIsHovering(true)}
            onMouseLeave={() => setIsHovering(false)}
            onClick={() => setIsEditing(true)}
            transition={{ type: 'spring', stiffness: 400, damping: 10 }}
          >
            <AnimatePresence mode="wait">
              {isHovering ? (
                <motion.div
                  key="endpoint-hover"
                  className="absolute inset-0 flex items-center justify-center"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <p className="flex items-center gap-2 whitespace-nowrap text-xs font-medium text-primary">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                    </svg>
                    EDIT
                  </p>
                </motion.div>
              ) : (
                <motion.div
                  key="endpoint-display"
                  className="absolute inset-0 flex items-center justify-between px-3"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <p className="text-xs font-medium text-muted truncate">
                    {isMounted ? endpointValue || 'No endpoint' : DEFAULT_ENDPOINT}
                  </p>
                  <div className={`size-2 shrink-0 rounded-full ${getStatusColor(isEndpointActive)}`} />
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
          <Button variant="ghost" size="icon" onClick={handleRefresh} className="hover:cursor-pointer hover:bg-transparent">
            <motion.div
              key={isRotating ? 'rotating' : 'idle'}
              animate={{ rotate: isRotating ? 360 : 0 }}
              transition={{ duration: 0.5, ease: 'easeInOut' }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#718096" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2" />
              </svg>
            </motion.div>
          </Button>
        </div>
      )}
    </div>
  )
}

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

  // Sidebar content - shared between desktop and mobile
  const sidebarContent = (
    <motion.div
      className="w-60 space-y-5"
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: isCollapsed ? 0 : 1, x: isCollapsed ? -20 : 0 }}
      transition={{ duration: 0.3, ease: 'easeInOut' }}
      style={{ pointerEvents: isCollapsed ? 'none' : 'auto' }}
    >
      <SidebarHeader />
      <NewChatButton disabled={messages.length === 0} onClick={handleNewChat} />
      {isMounted && (
        <>
          <Endpoint />
          {isEndpointActive ? (
            <div className="flex items-center gap-2 rounded-xl bg-positive/10 px-3 py-2">
              <div className="size-2 rounded-full bg-positive" />
              <span className="text-xs font-medium text-positive">Connected</span>
            </div>
          ) : (
            <div className="flex items-center gap-2 rounded-xl bg-destructive/10 px-3 py-2">
              <div className="size-2 rounded-full bg-destructive" />
              <span className="text-xs font-medium text-destructive">Disconnected</span>
            </div>
          )}
        </>
      )}
    </motion.div>
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
        {sidebarContent}
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
