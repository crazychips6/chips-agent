'use client'

const ChatBlankState = () => {
  return (
    <section
      className="flex flex-col items-center justify-center text-center font-geist flex-1"
      aria-label="Welcome message"
    >
      <div className="flex max-w-lg flex-col gap-y-4">
        <h1 className="text-4xl font-bold gradient-text">
          Chips Agent
        </h1>
        <p className="text-muted text-base">
          A general-purpose agent harness with web interface
        </p>
        <div className="mt-4 flex items-center justify-center gap-6 text-xs text-muted">
          <span className="flex items-center gap-1">
            <span className="text-primary">●</span> FastAPI
          </span>
          <span className="flex items-center gap-1">
            <span className="text-brand">●</span> Next.js
          </span>
          <span className="flex items-center gap-1">
            <span className="text-primaryAccent">●</span> Tailwind CSS
          </span>
        </div>
      </div>
    </section>
  )
}

export default ChatBlankState
