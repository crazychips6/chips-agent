import type { NextConfig } from 'next'

const isDev = process.env.NODE_ENV === 'development'

const nextConfig: NextConfig = {
  // 开发模式：完整 Next.js 服务器（支持 API 代理）
  // 生产模式：静态导出（由 chips web 托管）
  output: isDev ? undefined : 'export',
  devIndicators: false,
  images: {
    unoptimized: true,
  },

  // 开发模式：前端 :3000 → API 代理到后端 :8648
  async rewrites() {
    if (!isDev) return []
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:8648/api/:path*',
      },
      {
        source: '/metrics',
        destination: 'http://localhost:8648/metrics',
      },
    ]
  },
}

export default nextConfig
